"""斗地主插件 for AstrBot —— 完整功能版。

功能总览：
- 3 人开局（群内上桌），人不够可用 AI 补位（人机模式：1真+2机 / 2真+1机 / 3机观战）
- 完整规则：叫分→抢地主→加倍→出牌；单/对/三/三带/顺子/连对/飞机/四带二/炸弹/王炸
- 癞子玩法（欢乐斗地主规则：软炸/硬炸/纯癞子炸、癞子不可代王）
- 私聊/群临时会话发牌（手牌图片）；「我的牌」可重发
- 乐豆系统：底分×倍数结算、签到、救济金、乐豆榜、战绩
- 群主/管理员配置：模式、底分、超时、人机开关
- 超时托管：叫分/抢/加倍弃权，出牌不出，先手AI代出

命令（唤醒前缀 #）：
  斗地主 / 斗地主帮助        — 帮助
  上桌 [经典|癞子]           — 加入牌桌（凑满3人可含AI）
  下桌                      — 离开牌桌
  人机                      — 机器人补位补满并开局
  开局                      — 房主/管理提前开局（不满3人自动AI补位）
  叫分 N / 不叫             — 叫分阶段
  抢 / 不抢                 — 抢地主阶段
  加倍 / 不加倍             — 加倍阶段
  出 <牌> / 不出            — 出牌 （如：#出 34567 / #出 对3 / #出 王炸）
  提示                      — AI 提示出牌
  我的牌                    — 重发手牌图
  乐豆 / 签到 / 救济金       — 经济系统
  乐豆榜                    — 排行榜
  战绩                      — 个人战绩
  设置斗地主 <项> <值>        — 群主/管理配置（模式/底分/超时/人机/开关）
"""
from __future__ import annotations

import asyncio
import base64
from typing import Dict, Optional

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.star.filter.command import GreedyStr
from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path

from .engine import cards as C
from .engine import combos as P
from .engine import ai as AI
from .engine.game import PH_BID, PH_DOUBLE, PH_END, PH_GRAB, PH_PLAY
from .services.economy import Economy, today_str
from .services import render
from .services.room import Room, RoomManager

PLUGIN_NAME = "astrbot_plugin_doudizhu"
PLUGIN_VERSION = "v1.0.0"

HELP_TEXT = """🃏 斗地主 v1.0.0 —— 完整欢乐斗地主玩法

【基本流程】
1. 发送「上桌」加入牌桌（满 3 人自动开始；不满时可加 AI）
2. 叫分 → 抢地主 → 加倍 → 出牌
3. 地主先出，先出完手牌的一方获胜

【常用命令】
· 上桌 [经典|癞子]  — 加入（懒人推荐直接「上桌」）
· 下桌            — 退出牌桌
· 人机            — AI 补位并立刻开局（人不齐也能玩）
· 开局            — 发起人/管理强制开局
· 叫分 1/2/3 · 不叫
· 抢 / 不抢        — 抢地主阶段
· 加倍 / 不加倍    — 加倍阶段
· 出 <牌> / 不出   — 出牌阶段（♠ 3 4 5 6 7 / 对3 / 三个4带5 / 王炸……）
· 提示            — 不知道出什么？AI 给你提示
· 我的牌          — 重发手牌图片到私聊/临时会话
· 乐豆 · 签到 · 救济金 · 乐豆榜 · 战绩
· 设置斗地主 <项> <值> — 群主/管理员配置

【出牌写法示例】
出 34567        单顺
出 对3 或 33    对子
出 三个4带5     三带一
出 三连对 或 334455
出 飞机带单 33344457
出 王炸
（花色可省略，癞子场只需说点数）"""


@register(PLUGIN_NAME, "Wolfe", "斗地主：完整欢乐斗地主玩法（乐豆/癞子/人机模式/群内发牌）", PLUGIN_VERSION,
          "https://github.com/WolfeOvO/astrbot_plugin_doudizhu")
class DoudizhuPlugin(Star):
    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        self.config = config or {}
        self.data_dir = get_astrbot_plugin_data_path()
        import os
        os.makedirs(self.data_dir, exist_ok=True)
        self.economy = Economy(self.data_dir)
        self.mgr = RoomManager(self.economy, sender=self)
        self.pending: Dict[str, dict] = {}      # 上桌缓冲：gid -> {"uids":[], "names":[], "mode":}
        self._lock = asyncio.Lock()

    # ==================================================================
    # 发送通道（RoomManager.sender 的实现）
    # ==================================================================
    async def send_group(self, gid: str, text: str):
        origin = self.mgr.msg_origins.get(str(gid)) or self._origin_for(gid)
        if not origin:
            logger.warning(f"[斗地主] 群 {gid} 无发送通道")
            return
        try:
            await self.context.send_message(origin, MessageChain().message(text))
        except Exception as e:
            logger.error(f"[斗地主] send_group失败 {gid}: {e}")

    def _origin_for(self, gid: str) -> Optional[str]:
        return self.mgr.msg_origins.get(str(gid))

    async def send_group_image(self, gid: str, path: str):
        origin = self.mgr.msg_origins.get(str(gid))
        if not origin:
            return
        try:
            chain = MessageChain().file_image(path)
            await self.context.send_message(origin, chain)
        except Exception as e:
            logger.error(f"[斗地主] send_group_image失败 {gid}: {e}")

    async def _bot_call(self, action: str, **params):
        bot = self.mgr.bot
        if bot is None:
            raise RuntimeError("无可用 bot 实例")
        return await bot.call_action(action, **params)

    async def send_private_image(self, gid: str, uid: str, path: str) -> bool:
        """群临时会话发图（NapCat 支持 user_id+group_id）；失败回退普通私聊。"""
        try:
            with open(path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode()
            seg = {"type": "image", "data": {"file": f"base64://{b64}"}}
            try:
                await self._bot_call("send_private_msg",
                                     user_id=int(uid), group_id=int(gid), message=[seg])
                return True
            except Exception:
                await self._bot_call("send_private_msg", user_id=int(uid), message=[seg])
                return True
        except Exception as e:
            logger.warning(f"[斗地主] 私聊图片失败 {uid}: {e}")
            return False

    async def send_private_text(self, gid: str, uid: str, text: str) -> bool:
        try:
            try:
                await self._bot_call("send_private_msg",
                                     user_id=int(uid), group_id=int(gid),
                                     message=[{"type": "text", "data": {"text": text}}])
                return True
            except Exception:
                await self._bot_call("send_private_msg", user_id=int(uid),
                                     message=[{"type": "text", "data": {"text": text}}])
                return True
        except Exception as e:
            logger.warning(f"[斗地主] 私聊文本失败 {uid}: {e}")
            return False

    # ==================================================================
    # 工具方法
    # ==================================================================
    def _gid(self, event: AstrMessageEvent) -> str:
        gid = event.get_group_id()
        if not gid:
            raise ValueError("斗地主只能在群聊里玩哦～")
        return str(gid)

    def _uid(self, event: AstrMessageEvent) -> str:
        return str(event.get_sender_id())

    def _name(self, event: AstrMessageEvent) -> str:
        try:
            sender = event.message_obj.sender
            return str(getattr(sender, "card", "") or getattr(sender, "nickname", "") or self._uid(event))
        except Exception:
            return self._uid(event)

    async def _is_group_admin(self, event: AstrMessageEvent) -> bool:
        """群主/管理员判定（fail-closed）。AstrBot 全局管理员放行。"""
        uid = self._uid(event)
        try:
            if event.is_admin():
                return True
        except Exception:
            pass
        gid = event.get_group_id()
        if not gid:
            return True   # 私聊场景（不用于群配置）
        try:
            r = await self._bot_call("get_group_member_info", group_id=int(gid), user_id=int(uid))
            role = str((r or {}).get("data", {}).get("role", "") if isinstance(r, dict) else "")
            return role in ("admin", "owner")
        except Exception as e:
            logger.warning(f"[斗地主] 权限查询失败: {e}")
            return False

    def _room(self, gid: str) -> Optional[Room]:
        return self.mgr.get_room(gid)

    def _seat_of(self, room: Room, uid: str) -> Optional[int]:
        for p in room.game.players:
            if p.uid == uid:
                return p.seat
        return None

    # ==================================================================
    # 上桌 / 下桌
    # ==================================================================
    @filter.command("上桌", alias={"斗地主", "来一局", "打牌"})
    async def cmd_sit(self, event: AstrMessageEvent, mode: str = ""):
        try:
            gid = self._gid(event)
        except ValueError as e:
            yield event.plain_result(str(e))
            return
        uid, name = self._uid(event), self._name(event)
        conf = self.economy.group_conf(gid)
        if not conf.get("enabled", True):
            yield event.plain_result("本群斗地主未启用～群主可用「设置斗地主 开关 开」启用")
            return
        room = self._room(gid)
        if room is not None:
            if self._seat_of(room, uid) is not None:
                yield event.plain_result(f"{name} 已经在桌上啦～")
                return
            yield event.plain_result("本群已有一局进行中，等结束后再上桌吧～")
            return
        beans = self.economy.beans(uid)
        if beans < int(conf.get("min_beans", 100)):
            yield event.plain_result(f"乐豆不足（{beans} < {conf.get('min_beans', 100)}），"
                                     f"签到或领救济金攒一攒吧～")
            return
        b = self.pending.setdefault(gid, {"uids": [], "names": [], "mode": conf.get("mode", "classic")})
        if mode in ("经典", "癞子"):
            b["mode"] = "classic" if mode == "经典" else "leizi"
        if uid in b["uids"]:
            yield event.plain_result(f"{name} 已经坐好啦～还差 {3 - len(b['uids'])} 人")
            return
        if len(b["uids"]) >= 3:
            yield event.plain_result("桌子满了～")
            return
        b["uids"].append(uid)
        b["names"].append(name)
        n = len(b["uids"])
        if n == 3:
            await self._start_game(gid, event)
            yield event.plain_result("🃏 三人到齐，开局！")
        else:
            yield event.plain_result(
                f"✅ {name} 坐上牌桌（{n}/3）\n还差 {3 - n} 人，其他朋友发送「上桌」加入；"
                f"不等了可直接发「人机」让 AI 补位开局～")

    @filter.command("下桌", alias={"不玩了", "退出"})
    async def cmd_leave(self, event: AstrMessageEvent):
        try:
            gid = self._gid(event)
        except ValueError as e:
            yield event.plain_result(str(e))
            return
        uid, name = self._uid(event), self._name(event)
        b = self.pending.get(gid)
        if b and uid in b["uids"]:
            i = b["uids"].index(uid)
            b["uids"].pop(i)
            b["names"].pop(i)
            yield event.plain_result(f"👋 {name} 离开了牌桌（{len(b['uids'])}/3）")
            return
        yield event.plain_result("你不在牌桌上～")

    @filter.command("人机")
    async def cmd_bot_fill(self, event: AstrMessageEvent):
        """AI 补位补满并开局（人机模式）。"""
        try:
            gid = self._gid(event)
        except ValueError as e:
            yield event.plain_result(str(e))
            return
        conf = self.economy.group_conf(gid)
        if not conf.get("allow_bot", True):
            yield event.plain_result("本群已禁用 AI 补位～")
            return
        b = self.pending.get(gid)
        if not b or not b["uids"]:
            yield event.plain_result("还没有人上桌呢，先发「上桌」吧～")
            return
        need = 3 - len(b["uids"])
        for _ in range(need):
            idx = len(b["uids"])
            b["uids"].append(f"__bot{idx}__")
            b["names"].append(f"机器人{['甲', '乙', '丙'][idx]}")
        await self._start_game(gid, event)
        yield event.plain_result(f"🤖 已用 {need} 个机器人补位，开局！")

    @filter.command("开局", alias={"开始"})
    async def cmd_start(self, event: AstrMessageEvent):
        try:
            gid = self._gid(event)
        except ValueError as e:
            yield event.plain_result(str(e))
            return
        b = self.pending.get(gid)
        if not b or len(b["uids"]) < 2:
            yield event.plain_result("至少 2 个人上桌才能开局（不足 3 人将用 AI 补位）～")
            return
        if not (await self._is_group_admin(event)):
            # 非管理仅允许发起人（第一个上桌者）
            if b["uids"][0] != self._uid(event):
                yield event.plain_result("只有发起人（第一个上桌的）或群管可以提前开局～")
                return
        conf = self.economy.group_conf(gid)
        if not conf.get("allow_bot", True) and len(b["uids"]) < 3:
            yield event.plain_result("本群已禁用 AI 补位，请等真人补满 3 人～")
            return
        while len(b["uids"]) < 3:
            idx = len(b["uids"])
            b["uids"].append(f"__bot{idx}__")
            b["names"].append(f"机器人{['甲', '乙', '丙'][idx]}")
        await self._start_game(gid, event)
        yield event.plain_result("🃏 开局！")

    async def _start_game(self, gid: str, event: AstrMessageEvent):
        b = self.pending.pop(gid, None)
        if not b:
            return
        conf = self.economy.group_conf(gid)
        bots = [uid.startswith("__bot") for uid in b["uids"]]
        room = self.mgr.create_room(gid, list(zip(b["uids"], b["names"])),
                                    mode=b.get("mode", "classic"), bots=bots,
                                    msg_origin=event.unified_msg_origin, bot=event.bot)
        self.mgr.bot = event.bot
        g = room.game
        wild_txt = f"，癞子为 {next(iter(g.wild_ranks))}" if g.wild_ranks else ""
        await room.send_group(
            f"🎮 对局开始！{'经典' if g.mode == 'classic' else '癞子'}场{wild_txt}\n"
            f"玩家：" + "、".join(f"{p.name}{'(AI)' if p.is_bot else ''}" for p in g.players) +
            f"\n底分 {conf.get('base', 100)} 乐豆/分 · 超时 {conf.get('timeout', 45)}s\n"
            "机器人将私发各位手牌 📩")
        await room.broadcast_hands()
        await room._after_step("")

    # ==================================================================
    # 叫分 / 抢 / 加倍
    # ==================================================================
    @filter.command("叫分", alias={"叫"})
    async def cmd_bid(self, event: AstrMessageEvent, points: str = ""):
        try:
            gid = self._gid(event)
        except ValueError:
            return
        room = self._room(gid)
        if not room or room.game.phase != PH_BID:
            return
        seat = self._seat_of(room, self._uid(event))
        if seat is None:
            return
        try:
            pts = int(points) if str(points).strip().isdigit() else None
        except Exception:
            pts = None
        if pts is None:
            yield event.plain_result("用法：叫分 1 / 2 / 3（或「不叫」）")
            return
        await self._do_bid(room, seat, pts)

    @filter.command("不叫")
    async def cmd_no_bid(self, event: AstrMessageEvent):
        try:
            gid = self._gid(event)
        except ValueError:
            return
        room = self._room(gid)
        if room and room.game.phase == PH_BID:
            seat = self._seat_of(room, self._uid(event))
            if seat is not None:
                await self._do_bid(room, seat, 0)

    async def _do_bid(self, room: Room, seat: int, pts: int):
        try:
            out = room.game.bid(seat, pts)
        except Exception as e:
            await room.send_group(f"❌ {e}")
            return
        await room._after_step(out)

    @filter.command("抢", alias={"抢地主"})
    async def cmd_grab(self, event: AstrMessageEvent):
        try:
            gid = self._gid(event)
        except ValueError:
            return
        room = self._room(gid)
        if room and room.game.phase == PH_GRAB:
            seat = self._seat_of(room, self._uid(event))
            if seat is not None:
                await self._do_grab(room, seat, True)

    @filter.command("不抢")
    async def cmd_no_grab(self, event: AstrMessageEvent):
        try:
            gid = self._gid(event)
        except ValueError:
            return
        room = self._room(gid)
        if room and room.game.phase == PH_GRAB:
            seat = self._seat_of(room, self._uid(event))
            if seat is not None:
                await self._do_grab(room, seat, False)

    async def _do_grab(self, room: Room, seat: int, do: bool):
        try:
            out = room.game.grab(seat, do)
        except Exception as e:
            await room.send_group(f"❌ {e}")
            return
        await room._after_step(out)

    @filter.command("加倍", alias={"抢加倍"})
    async def cmd_double(self, event: AstrMessageEvent):
        try:
            gid = self._gid(event)
        except ValueError:
            return
        room = self._room(gid)
        if room and room.game.phase == PH_DOUBLE:
            seat = self._seat_of(room, self._uid(event))
            if seat is not None:
                await self._do_double(room, seat, True)

    @filter.command("不加倍")
    async def cmd_no_double(self, event: AstrMessageEvent):
        try:
            gid = self._gid(event)
        except ValueError:
            return
        room = self._room(gid)
        if room and room.game.phase == PH_DOUBLE:
            seat = self._seat_of(room, self._uid(event))
            if seat is not None:
                await self._do_double(room, seat, False)

    async def _do_double(self, room: Room, seat: int, do: bool):
        try:
            out = room.game.double(seat, do)
        except Exception as e:
            await room.send_group(f"❌ {e}")
            return
        await room._after_step(out)

    # ==================================================================
    # 出牌
    # ==================================================================
    @filter.command("出", alias={"出牌", "打"})
    async def cmd_play(self, event: AstrMessageEvent, content: GreedyStr = None):
        try:
            gid = self._gid(event)
        except ValueError:
            return
        room = self._room(gid)
        if not room or room.game.phase != PH_PLAY:
            return
        uid = self._uid(event)
        seat = self._seat_of(room, uid)
        if seat is None or seat != room.game.current:
            yield event.plain_result("还没轮到你～")
            return
        raw = str(content or "").strip()
        if not raw:
            yield event.plain_result("用法：#出 34567 / #出 对3 / #出 王炸 …（不知道出啥可发「提示」）")
            return
        hand = room.game.players[seat].hand
        cards, err = C.resolve(raw, hand)
        if err:
            yield event.plain_result(f"❌ {err}")
            return
        try:
            combo, msg = room.game.play(seat, cards)
        except Exception as e:
            yield event.plain_result(f"❌ {e}")
            return
        await room._after_step(msg)

    @filter.command("不出", alias={"过", "不要"})
    async def cmd_pass(self, event: AstrMessageEvent):
        try:
            gid = self._gid(event)
        except ValueError:
            return
        room = self._room(gid)
        if not room or room.game.phase != PH_PLAY:
            return
        seat = self._seat_of(room, self._uid(event))
        if seat is None or seat != room.game.current:
            yield event.plain_result("还没轮到你～")
            return
        try:
            out = room.game.pass_turn(seat)
        except Exception as e:
            yield event.plain_result(f"❌ {e}")
            return
        await room._after_step(out)

    @filter.command("提示")
    async def cmd_hint(self, event: AstrMessageEvent):
        try:
            gid = self._gid(event)
        except ValueError:
            return
        room = self._room(gid)
        if not room or room.game.phase != PH_PLAY:
            return
        seat = self._seat_of(room, self._uid(event))
        if seat is None or seat != room.game.current:
            yield event.plain_result("还没轮到你～")
            return
        p = room.game.players[seat]
        sug = AI.suggest_hint(p.hand, room.game.wild_ranks, room.game._prev_combo())
        if sug is None:
            yield event.plain_result("没有能出的牌，建议「不出」～")
            return
        combo, err = P.choose(sug, room.game.wild_ranks, room.game._prev_combo())
        txt = combo.text() if combo else C.fmt_cards(sug)
        yield event.plain_result(f"💡 提示：{txt}\n直接回复：出 {''.join(C.card_rank(c) for c in sug)}")

    @filter.command("我的牌", alias={"手牌", "重发"})
    async def cmd_my_hand(self, event: AstrMessageEvent):
        try:
            gid = self._gid(event)
        except ValueError:
            return
        room = self._room(gid)
        if not room:
            yield event.plain_result("当前没有进行中的对局～")
            return
        seat = self._seat_of(room, self._uid(event))
        if seat is None:
            yield event.plain_result("你不在对局中～")
            return
        await room.send_hand_to(seat, force=True)
        yield event.plain_result("📩 手牌已发（收不到就检查是否允许私聊/临时会话）")

    @filter.command("托管")
    async def cmd_auto(self, event: AstrMessageEvent):
        try:
            gid = self._gid(event)
        except ValueError:
            return
        room = self._room(gid)
        if not room:
            return
        seat = self._seat_of(room, self._uid(event))
        if seat is None:
            return
        p = room.game.players[seat]
        p.is_bot = True
        yield event.plain_result(f"🤖 {p.name} 开启托管，本局由机器人代打")

    # ==================================================================
    # 经济系统
    # ==================================================================
    @filter.command("乐豆")
    async def cmd_beans(self, event: AstrMessageEvent):
        uid = self._uid(event)
        beans = self.economy.beans(uid)
        u = self.economy.user(uid)
        yield event.plain_result(
            f"💰 {self._name(event)} 的乐豆：{beans}\n"
            f"战绩：{u['wins']} 胜 {u['losses']} 负（共 {u['games']} 局）\n"
            f"最高倍数：x{u['max_mult']}\n"
            f"签到：{'已签（连签 %d 天）' % u['sign_streak'] if u['last_sign'] == today_str() else '今日未签，发「豆签到」'}"
        )

    @filter.command("豆签到", alias={"领乐豆"})
    async def cmd_sign(self, event: AstrMessageEvent):
        gid = self._gid(event) if event.get_group_id() else "0"
        conf = self.economy.group_conf(gid) if gid != "0" else {}
        bonus = int(conf.get("sign_bonus", 500))
        r = self.economy.sign_in(self._uid(event), today_str(), bonus)
        if r["already"]:
            yield event.plain_result(f"今天已经签过啦～连签 {r['streak']} 天，明天再来！")
        else:
            yield event.plain_result(
                f"✅ 签到成功！+{r['reward']} 乐豆（连签 {r['streak']} 天）\n当前乐豆：{r['beans']}")

    @filter.command("救济金", alias={"领救济"})
    async def cmd_relief(self, event: AstrMessageEvent):
        r = self.economy.claim_relief(self._uid(event), today_str())
        if r["ok"]:
            yield event.plain_result(f"🆘 救济金 +{r['reward']} 乐豆，当前 {r['beans']}。省着点花～")
        else:
            yield event.plain_result(f"❌ {r['reason']}")

    @filter.command("乐豆榜", alias={"排行榜", "财富榜"})
    async def cmd_top(self, event: AstrMessageEvent):
        top = self.economy.leaderboard(10)
        if not top:
            yield event.plain_result("暂无数据～")
            return
        lines = ["🏆 乐豆排行榜"]
        for i, (uid, u) in enumerate(top, 1):
            lines.append(f"{i}. {uid}：{u.get('beans', 0)} 乐豆（{u.get('wins', 0)} 胜）")
        yield event.plain_result("\n".join(lines))

    @filter.command("战绩")
    async def cmd_stats(self, event: AstrMessageEvent):
        u = self.economy.user(self._uid(event))
        total = max(1, u["games"])
        wr = round(u["wins"] / total * 100)
        yield event.plain_result(
            f"📊 {self._name(event)} 的斗地主战绩\n"
            f"总局数：{u['games']}　胜率：{wr}%\n"
            f"地主胜：{u['landlord_wins']}　农民胜：{u['farmer_wins']}\n"
            f"最高倍数：x{u['max_mult']}　当前乐豆：{u['beans']}")

    # ==================================================================
    # 群配置
    # ==================================================================
    @filter.command("设置斗地主", alias={"斗地主设置"})
    async def cmd_config(self, event: AstrMessageEvent, content: GreedyStr = None):
        try:
            gid = self._gid(event)
        except ValueError as e:
            yield event.plain_result(str(e))
            return
        if not (await self._is_group_admin(event)):
            yield event.plain_result("只有群主/管理员可以修改斗地主设置～")
            return
        raw = str(content or "").strip()
        conf = self.economy.group_conf(gid)
        if not raw:
            yield event.plain_result(
                "⚙️ 当前设置：\n"
                f"· 模式：{'经典' if conf['mode'] == 'classic' else '癞子'}\n"
                f"· 底分：{conf['base']} 乐豆/分\n"
                f"· 超时：{conf['timeout']}s\n"
                f"· AI 补位：{'允许' if conf['allow_bot'] else '禁止'}\n"
                f"· 入场门槛：{conf['min_beans']} 乐豆\n"
                f"· 签到奖励：{conf['sign_bonus']} 乐豆\n\n"
                "修改示例：\n"
                "设置斗地主 模式 癞子\n设置斗地主 底分 50\n设置斗地主 超时 60\n"
                "设置斗地主 人机 关\n设置斗地主 开关 关")
            return
        parts = raw.replace("　", " ").split()
        if len(parts) < 2:
            yield event.plain_result("用法：设置斗地主 <项> <值>")
            return
        k, v = parts[0], parts[1]
        mapping = {
            "模式": ("mode", {"经典": "classic", "癞子": "leizi"}),
            "底分": ("base", None),
            "超时": ("timeout", None),
            "人机": ("allow_bot", {"开": True, "关": False, "允许": True, "禁止": False}),
            "开关": ("enabled", {"开": True, "关": False}),
            "门槛": ("min_beans", None),
            "签到": ("sign_bonus", None),
        }
        if k not in mapping:
            yield event.plain_result(f"未知设置项「{k}」，发「设置斗地主」查看可用项")
            return
        field, conv = mapping[k]
        try:
            if conv is not None:
                if v not in conv:
                    raise ValueError(f"值应为 {'/'.join(conv)}")
                val = conv[v]
            else:
                val = int(v)
        except ValueError as e:
            yield event.plain_result(f"❌ {e}")
            return
        self.economy.set_group_conf(gid, **{field: val})
        yield event.plain_result(f"✅ 已设置 {k} = {v}")

    @filter.command("斗地主帮助", alias={"斗地主玩法"})
    async def cmd_help(self, event: AstrMessageEvent):
        yield event.plain_result(HELP_TEXT)
