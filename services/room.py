"""房间管理与流程驱动。

一个 QQ 群 = 最多一个活跃房间。房间持有 GameState、定时器、发送通道。
"""
from __future__ import annotations

import asyncio
import base64
import time
from typing import Dict, List, Optional, Sequence, Tuple

from astrbot.api import logger

from ..engine import cards as C
from ..engine import combos as P
from ..engine import ai as AI
from ..engine.game import GameState, GameError, PH_BID, PH_GRAB, PH_DOUBLE, PH_PLAY, PH_END
from . import render
from .economy import Economy, today_str


class RoomError(Exception):
    """面向用户的房间错误。"""
    pass


class Room:
    def __init__(self, gid: str, mgr: "RoomManager", game: GameState,
                 conf: dict):
        self.gid = gid
        self.mgr = mgr
        self.game = game
        self.conf = conf
        self.msg_origin = mgr.msg_origins.get(gid)
        self.bot = mgr.bot
        self.timer_task: Optional[asyncio.Task] = None
        self.deadline: float = 0
        self.created_at = time.time()
        self.settled = False
        self.auto: Dict[str, bool] = {}    # uid -> 托管中
        self.bot_sender = mgr.sender

    # ------------------------------------------------------------------
    # 发送
    # ------------------------------------------------------------------
    async def send_group(self, text: str):
        if not self.msg_origin:
            logger.warning(f"[斗地主] 无 msg_origin，无法发群消息: {text[:50]}")
            return
        try:
            await self.mgr.send_group(self.gid, text)
        except Exception as e:
            logger.error(f"[斗地主] 群消息发送失败: {e}", exc_info=True)

    async def send_group_image(self, path: str):
        try:
            await self.mgr.send_group_image(self.gid, path)
        except Exception as e:
            logger.error(f"[斗地主] 群图片发送失败: {e}", exc_info=True)

    async def send_private_image(self, uid: str, path: str) -> bool:
        """群临时会话发图（失败自动退化为普通私聊）。"""
        return await self.mgr.send_private_image(self.gid, uid, path)

    async def send_private_text(self, uid: str, text: str) -> bool:
        return await self.mgr.send_private_text(self.gid, uid, text)

    async def send_private_hand(self, uid: str, text: str, path: str) -> bool:
        """手牌「图文」一起发（同一条消息）。"""
        return await self.mgr.send_private_hand(self.gid, uid, text, path)

    # ------------------------------------------------------------------
    # 定时器
    # ------------------------------------------------------------------
    def arm_timer(self, seconds: Optional[int] = None):
        self.cancel_timer()
        sec = int(seconds if seconds is not None else self.conf.get("timeout", 45))
        self.deadline = time.time() + sec
        self.timer_task = asyncio.create_task(self._timeout_watch(sec))

    def cancel_timer(self):
        t = self.timer_task
        if t and not t.done() and t is not asyncio.current_task():
            t.cancel()
        self.timer_task = None

    async def _timeout_watch(self, sec: int):
        try:
            await asyncio.sleep(sec)
        except asyncio.CancelledError:
            return
        try:
            await self.on_timeout()
        except Exception as e:
            logger.error(f"[斗地主] 超时处理异常: {e}", exc_info=True)

    async def on_timeout(self):
        g = self.game
        if g.phase == PH_END:
            return
        seat = None
        if g.phase == PH_BID:
            seat = g.bid_turn
        elif g.phase == PH_GRAB:
            seat = g.grab_turn
        elif g.phase == PH_DOUBLE:
            seat = g.double_turn
        elif g.phase == PH_PLAY:
            seat = g.current
        if seat is None:
            return
        # AI 行动者（真机器人或托管玩家）：直接走 AI 流程
        if g.players[seat].is_bot or g.players[seat].auto:
            await self.tick_bot()
            return
        # 人类超时：叫分/抢/加倍=弃权；出牌=能不出就不出，先手则 AI 代出
        p = g.players[seat]
        try:
            if g.phase == PH_BID:
                out = g.bid(seat, 0)
                action = "不叫"
            elif g.phase == PH_GRAB:
                out = g.grab(seat, False)
                action = "不抢"
            elif g.phase == PH_DOUBLE:
                out = g.double(seat, 0)
                action = "不加倍"
            elif g.phase == PH_PLAY:
                prev = g._prev_combo()
                if prev is None:
                    cs = AI.choose_play(p.hand, g.wild_ranks, None, self._ai_ctx(seat))
                    if not cs:
                        cs = [C.sort_cards(p.hand)[0]]
                    out = g.play(seat, cs)[1]
                    action = "托管出牌"
                else:
                    out = g.pass_turn(seat)
                    action = "不出"
            else:
                return
        except GameError:
            return
        await self.send_group(f"⏰ {p.name} 思考超时，自动{action}")
        await self._after_step(out)

    # ------------------------------------------------------------------
    # 流程推进
    # ------------------------------------------------------------------
    def _ai_ctx(self, seat: int) -> dict:
        g = self.game
        lp = g.landlord
        return {
            "role": g.players[seat].role or "farmer",
            "my_seat": seat,
            "last_seat": g.last_play.seat if g.last_play else None,
            "last_role": g.players[g.last_play.seat].role if g.last_play else None,
            "others_counts": {q.seat: len(q.hand) for q in g.players if q.seat != seat},
            "landlord_count": len(g.players[lp].hand) if lp is not None else None,
        }

    async def _after_step(self, out: str):
        """每次状态推进后：播报、发牌、布置下一步。"""
        g = self.game
        if out:
            await self.send_group(out)

        if g.phase == "redeal":
            await self._redeal()
            return

        if g.phase == PH_END:
            await self._finish()
            return

        pfx = self.mgr.cmd_prefix
        if g.phase == PH_PLAY:
            seat = g.current
            p = g.players[seat]
            if p.is_bot or p.auto:
                self.arm_timer(2)   # AI 快速行动
            else:
                self.arm_timer()
                await self.send_hand_to(seat)
                counts = "\n".join(f"{q.name} {len(q.hand)}张" for q in g.players)
                role_txt = "地主" if p.role == "landlord" else "农民"
                prev = g._prev_combo()
                if prev is not None:
                    last = next((r for r in reversed(g.history) if r.kind == "play"), None)
                    who = g.players[last.seat].name if last else ""
                    need_txt = f"{C.fmt_cards(prev.cards)}（{who}）"
                else:
                    need_txt = "🟢 本轮你先出（自由出牌）"
                await self.send_group(
                    f"🎯 轮到 {p.name}（{role_txt}）出牌（{self.conf.get('timeout', 45)}s）\n\n"
                    f"📊 剩牌：\n{counts}\n\n"
                    f"{need_txt}\n\n"
                    f"💬 出牌指令：{pfx}出 xxx\n"
                    f"💬 操作：\n"
                    f"{pfx}不出 - 本回合不出牌\n"
                    f"{pfx}提示 - 私聊给予提示\n"
                    f"{pfx}托管 - 机器代打，输了不负责")
        elif g.phase == PH_BID:
            seat = g.bid_turn
            p = g.players[seat]
            if p.is_bot or p.auto:
                self.arm_timer(3)
            else:
                self.arm_timer()
                await self.send_group(
                    f"🎲 轮到 {p.name} 叫分（{self.conf.get('timeout', 45)}s）\n\n"
                    f"💬 操作：\n"
                    f"{pfx}叫分 1 - 叫 1 分\n"
                    f"{pfx}叫分 2 - 叫 2 分\n"
                    f"{pfx}叫分 3 - 叫 3 分\n"
                    f"{pfx}不叫 - 本局不叫分")
        elif g.phase == PH_GRAB:
            seat = g.grab_turn
            if seat is not None:
                p = g.players[seat]
                if p.is_bot or p.auto:
                    self.arm_timer(3)
                else:
                    self.arm_timer()
                    await self.send_group(
                        f"🔥 轮到 {p.name} 抢地主（{self.conf.get('timeout', 45)}s）\n\n"
                        f"💬 操作：\n"
                        f"{pfx}抢 - 抢地主（倍数 ×2）\n"
                        f"{pfx}不抢 - 不抢")
        elif g.phase == PH_DOUBLE:
            seat = g.double_turn
            if seat is not None:
                p = g.players[seat]
                if p.is_bot or p.auto:
                    self.arm_timer(3)
                else:
                    self.arm_timer()
                    await self.send_group(
                        f"💰 轮到 {p.name} 加倍（{self.conf.get('timeout', 45)}s）"
                        f"｜ 当前倍数 ×{g.multiplier}\n\n"
                        f"💬 操作：\n"
                        f"{pfx}加倍 - 加倍（×2）\n"
                        f"{pfx}超级加倍 - 超级加倍（×4）\n"
                        f"{pfx}不加倍 - 不加倍")

    async def tick_bot(self):
        """AI 行动（由定时器触发后调用）。"""
        g = self.game
        if g.phase == PH_END:
            return
        if g.phase == PH_BID:
            seat = g.bid_turn
            if not (g.players[seat].is_bot or g.players[seat].auto):
                return
            pts = AI.suggest_bid(g.players[seat].hand, g.wild_ranks, g._bid_current)
            out = g.bid(seat, pts)
        elif g.phase == PH_GRAB:
            seat = g.grab_turn
            if seat is None or not (g.players[seat].is_bot or g.players[seat].auto):
                return
            out = g.grab(seat, AI.suggest_grab(g.players[seat].hand, g.wild_ranks))
        elif g.phase == PH_DOUBLE:
            seat = g.double_turn
            if seat is None or not (g.players[seat].is_bot or g.players[seat].auto):
                return
            out = g.double(seat, AI.suggest_double(g.players[seat].hand, g.wild_ranks))
        elif g.phase == PH_PLAY:
            seat = g.current
            if not (g.players[seat].is_bot or g.players[seat].auto):
                return
            p = g.players[seat]
            prev = g._prev_combo()
            cs = AI.choose_play(p.hand, g.wild_ranks, prev, self._ai_ctx(seat))
            if cs is None:
                out = g.pass_turn(seat)
            else:
                try:
                    out = g.play(seat, cs)[1]
                except GameError:
                    if prev is None:
                        out = g.play(seat, [C.sort_cards(p.hand)[0]])[1]
                    else:
                        out = g.pass_turn(seat)
        else:
            return
        await self._after_step(out)

    async def _redeal(self):
        """三家都不叫：同玩家、同模式重新发牌。"""
        g = self.game
        old_players = [(p.uid, p.name) for p in g.players]
        old_bots = [p.is_bot for p in g.players]
        old_autos = [bool(p.auto) for p in g.players]
        conf = self.conf
        from ..engine.game import GameState
        new_g = GameState(old_players, mode=g.mode,
                          base_per_point=int(conf.get("base", 100)), bots=old_bots)
        for pp, aa in zip(new_g.players, old_autos):
            pp.auto = aa
        self.game = new_g
        self.settled = False
        wild_txt = f"，癞子为 {next(iter(new_g.wild_ranks))}" if new_g.wild_ranks else ""
        await self.send_group(f"🔄 三家都不叫，重新发牌{wild_txt}")
        await self.broadcast_hands()
        seat = new_g.bid_turn
        p = new_g.players[seat]
        if p.is_bot or p.auto:
            self.arm_timer(3)
        else:
            self.arm_timer()
            pfx = self.mgr.cmd_prefix
            await self.send_group(
                f"🎲 轮到 {p.name} 叫分（{conf.get('timeout', 45)}s）\n\n"
                f"💬 操作：\n"
                f"{pfx}叫分 1 - 叫 1 分\n"
                f"{pfx}叫分 2 - 叫 2 分\n"
                f"{pfx}叫分 3 - 叫 3 分\n"
                f"{pfx}不叫 - 本局不叫分")

    async def _finish(self):
        if self.settled:
            return
        self.settled = True
        self.cancel_timer()
        g = self.game
        try:
            await self.send_group(g.settlement_text())
            # 乐豆结算 + 战绩（机器人乐豆无限：不入账、不上榜）
            eco: Economy = self.mgr.economy
            deltas = {p.uid: p.score for p in g.players if not p.is_bot}
            eco.settle(deltas)
            for p in g.players:
                if not p.is_bot:
                    eco.record_game(p.uid, p.is_winner, p.role == "landlord",
                                    g.multiplier, name=p.name)
            # 结果图
            lines = []
            for p in g.players:
                role = "地主" if p.role == "landlord" else "农民"
                if p.is_bot:
                    lines.append(f"{p.name}（{role}）：∞ 乐豆")
                else:
                    lines.append(f"{p.name}（{role}）："
                                 f"{'+' if p.score >= 0 else ''}{p.score} 乐豆")
            try:
                path = render.render_result("对局结束", lines)
                await self.send_group_image(path)
                render.cleanup(path)
            except Exception as e:
                logger.warning(f"[斗地主] 结算图渲染失败: {e}")
        finally:
            self.mgr.finish_room(self.gid, self)

    # ------------------------------------------------------------------
    # 私聊手牌
    # ------------------------------------------------------------------
    def situation_text(self, seat: int) -> str:
        """给某个玩家的「现在的情况」文本：手牌 + 需大过/自由出牌。"""
        g = self.game
        p = g.players[seat]
        role_txt = {"landlord": "地主", "farmer": "农民", "": ""}.get(p.role, "")
        bits = [f"🃏 你的手牌（{len(p.hand)} 张）"]
        if role_txt:
            bits.append(f"你是{role_txt}")
        bits.append(f"第 {p.play_count} 手")
        wild = next(iter(g.wild_ranks)) if g.wild_ranks else None
        if wild:
            bits.append(f"癞子 {wild}")
        prev = g._prev_combo()
        if prev is not None:
            last = next((r for r in reversed(g.history) if r.kind == "play"), None)
            who = f"（{g.players[last.seat].name}）" if last is not None else ""
            need = f"⚔️ 需大过：{C.fmt_cards(prev.cards)}{who}"
        elif g.phase == PH_PLAY:
            need = "🟢 本轮你先出（自由出牌）"
        else:
            need = ""
        blocks = [" · ".join(bits), C.fmt_cards(p.hand)]
        if need:
            blocks.append(need)
        return "\n\n".join(blocks)

    async def send_hand_to(self, seat: int, force: bool = False):
        """每轮都推送：同一条消息里「文字 + 手牌图片」（force 保留兼容参数）。"""
        g = self.game
        p = g.players[seat]
        if p.is_bot or p.auto:
            return
        wild = next(iter(g.wild_ranks)) if g.wild_ranks else None
        role_txt = {"landlord": "你是地主", "farmer": "你是农民", "": ""}.get(p.role, "")
        subtitle_bits = [b for b in (role_txt, f"第 {p.play_count} 手",
                                     (f"癞子 {wild}" if wild else "")) if b]
        try:
            path = render.render_hand(
                p.hand, wild_rank=wild,
                title=f"你的手牌（{len(p.hand)} 张）",
                subtitle=" · ".join(subtitle_bits),
            )
            ok = await self.send_private_hand(p.uid, self.situation_text(seat), path)
            render.cleanup(path)
            if not ok:
                await self.send_group(f"⚠️ 发给 {p.name} 的手牌失败（可能未允许私聊/临时会话）；"
                                      f"可发送「我的牌」重试")
        except Exception as e:
            logger.error(f"[斗地主] 手牌推送失败: {e}", exc_info=True)

    async def broadcast_hands(self):
        for p in self.game.players:
            await self.send_hand_to(p.seat)


class RoomManager:
    """按群管理房间 + 提供发送通道（由 main.py 实现 sender 注入）。"""

    def __init__(self, economy: Economy, sender):
        self.rooms: Dict[str, Room] = {}
        self.economy = economy
        self.sender = sender          # 需实现 send_group/send_group_image/send_private_*
        self.bot = None
        self.msg_origins: Dict[str, str] = {}
        self.cmd_prefix = ""          # 唤醒前缀（由插件注入，用于提示文案）

    # 发送通道代理（Room 调用）
    async def send_group(self, gid: str, text: str):
        await self.sender.send_group(gid, text)

    async def send_group_image(self, gid: str, path: str):
        await self.sender.send_group_image(gid, path)

    async def send_private_image(self, gid: str, uid: str, path: str) -> bool:
        return await self.sender.send_private_image(gid, uid, path)

    async def send_private_text(self, gid: str, uid: str, text: str) -> bool:
        return await self.sender.send_private_text(gid, uid, text)

    async def send_private_hand(self, gid: str, uid: str, text: str, path: str) -> bool:
        return await self.sender.send_private_hand(gid, uid, text, path)

    # ------------------------------------------------------------------
    def get_room(self, gid: str) -> Optional[Room]:
        room = self.rooms.get(gid)
        if room and (room.game.phase == "ended" or room.game.phase == "redeal"):
            return None
        return room

    def finish_room(self, gid: str, room: Optional["Room"] = None):
        """结束房间。指定 room 时仅当仍是同一个房间才移除（防止误删新开的局）。"""
        cur = self.rooms.get(gid)
        if room is not None and cur is not None and cur is not room:
            return
        self.rooms.pop(gid, None)

    def create_room(self, gid: str, players: Sequence[tuple], mode: str,
                    bots: Sequence[bool], msg_origin: str, bot) -> Room:
        old = self.rooms.get(gid)
        if old is not None:
            old.cancel_timer()   # 一群一局：兜底清理残留定时器（正常流程不会走到）
        conf = self.economy.group_conf(gid)
        if mode == "speed":
            conf = dict(conf, timeout=20)   # 极速场：20 秒超时
        g = GameState(players, mode=mode, base_per_point=int(conf.get("base", 100)), bots=bots)
        room = Room(gid, self, g, conf)
        room.msg_origin = msg_origin
        room.bot = bot
        self.msg_origins[gid] = msg_origin
        self.rooms[gid] = room
        return room

    def destroy_room(self, gid: str, reason: str = ""):
        room = self.rooms.pop(gid, None)
        if room:
            room.cancel_timer()
