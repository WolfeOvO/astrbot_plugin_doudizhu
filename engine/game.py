"""斗地主对局状态机（叫分 → 抢地主 → 加倍 → 出牌 → 结算）。

纯逻辑，无 I/O；插件层负责消息、定时器与渲染。
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Set, Tuple

from . import cards as C
from . import combos as P

# 阶段常量
PH_BID = "bidding"
PH_GRAB = "grabbing"
PH_DOUBLE = "doubling"
PH_PLAY = "playing"
PH_END = "ended"


@dataclass
class PlayerState:
    uid: str
    name: str
    seat: int
    is_bot: bool = False
    auto: bool = False      # 托管中（人类交给 AI 代打，不影响结算与战绩）
    hand: List[str] = field(default_factory=list)
    role: str = ""          # landlord / farmer
    grabbed: int = 0        # 抢/反抢 次数
    doubled: bool = False   # 是否加倍
    played_any: bool = False  # 是否出过牌（春天判定）
    play_count: int = 0     # 出牌手数
    connected: bool = True  # 掉线/超时状态
    score: int = 0          # 本局结算（乐豆增减）
    is_winner: bool = False


@dataclass
class PlayRecord:
    seat: int
    kind: str            # "play" / "pass"
    combo: Optional[P.Combo] = None
    cards: List[str] = field(default_factory=list)


def _role_txt(p: PlayerState) -> str:
    return "地主" if p.role == "landlord" else "农民"


_MODE_NAMES = {"classic": "经典", "leizi": "癞子", "noshuffle": "不洗牌", "speed": "极速"}


def _noshuffle_deck(rng: random.Random) -> List[str]:
    """不洗牌场近似：抽 5 个点数把 4 张聚拢排列 → 炸弹出现率更高。"""
    deck = C.shuffled_deck(rng)
    ranks = list(C.RANKS)
    rng.shuffle(ranks)
    clusters = set(ranks[:5])
    rest = [c for c in deck if C.card_rank(c) not in clusters]
    cl = [c for c in deck if C.card_rank(c) in clusters]
    rng.shuffle(cl)
    for r in clusters:
        four = [c for c in cl if C.card_rank(c) == r]
        pos = rng.randrange(len(rest) + 1)
        rest[pos:pos] = four
    return rest


class GameError(Exception):
    pass


class GameState:
    """一局斗地主。流程推进全部通过显式方法调用，便于测试与插件接线。"""

    def __init__(self, uids_names: Sequence[Tuple[str, str]], mode: str = "classic",
                 base_per_point: int = 100, rng: Optional[random.Random] = None,
                 bomb_multiplier: bool = True, spring_enabled: bool = True,
                 bots: Optional[Sequence[bool]] = None):
        if len(uids_names) != 3:
            raise GameError("需要恰好 3 名玩家")
        rng = rng or random.Random()
        self.rng = rng
        self.mode = mode                      # classic / leizi / noshuffle / speed
        self.base_per_point = int(base_per_point)  # 每 1 分 = 多少乐豆
        self.bomb_multiplier = bomb_multiplier
        self.spring_enabled = spring_enabled

        self.players: List[PlayerState] = []
        for i, (uid, name) in enumerate(uids_names):
            is_bot = bots[i] if bots is not None else False
            self.players.append(PlayerState(uid=uid, name=name, seat=i, is_bot=is_bot))

        # 癞子
        self.wild_ranks: Set[str] = set()
        if mode == "leizi":
            self.wild_ranks = {rng.choice(C.RANKS)}  # 从 3..2 中选一个点数（不含王）

        # 发牌
        deck = C.shuffled_deck(rng) if mode != "noshuffle" else _noshuffle_deck(rng)
        for i, p in enumerate(self.players):
            p.hand = C.sort_cards(deck[i * 17:(i + 1) * 17])
        self.bottom = C.sort_cards(deck[51:])  # 底面 3 张

        # 流程状态
        self.phase = PH_BID
        self.base_points = 0        # 叫牌分数（地主底分）
        self.multiplier = 1         # 总倍数
        self.bomb_count = 0         # 炸弹/王炸 计数（含加倍）
        self.spring_type = ""       # "" / "spring" / "anti"
        self.first_bidder = rng.randrange(3)
        self._bid_turn = self.first_bidder
        self._bid_turns_done = 0
        self._bid_current = 0       # 当前最高叫分
        self._bid_winner: Optional[int] = None
        self.landlord: Optional[int] = None
        self._grab_turn: Optional[int] = None
        self._grab_queue: List[int] = []
        self._grab_caller: Optional[int] = None
        self._grab_caller_asked = False
        self._grab_any = False
        self._double_queue: List[int] = []
        self.current: Optional[int] = None  # 当前出牌人
        self.last_play: Optional[PlayRecord] = None
        self.pass_count = 0
        self.history: List[PlayRecord] = []
        self.log: List[str] = []
        self.started_at = time.time()
        self.ended_at: Optional[float] = None
        self._bombed_seats: Dict[int, int] = {}

        self.log.append(f"牌局开始（{_MODE_NAMES.get(mode, mode)}场）"
                        f"{'，癞子为 ' + next(iter(self.wild_ranks)) if self.wild_ranks else ''}")
        self.log.append(f"叫牌从 {self.players[self.first_bidder].name} 开始")

    # ------------------------------------------------------------------
    # 叫分
    # ------------------------------------------------------------------
    def bid(self, seat: int, points: int) -> str:
        """points: 0=不叫, 1..3 叫分。返回播报文本，抛 GameError 表示非法。"""
        self._require_phase(PH_BID)
        if seat != self._bid_turn % 3:
            raise GameError(f"还没轮到 {self.players[seat].name} 叫分")
        if points not in (0, 1, 2, 3):
            raise GameError("叫分只能是 0（不叫）/1/2/3")
        if points != 0 and points <= self._bid_current:
            raise GameError(f"叫分必须高于当前 {self._bid_current} 分")
        name = self.players[seat].name
        self._bid_turns_done += 1
        if points == 0:
            self.log.append(f"{name} 不叫")
            msg = f"😶 {name} 不叫"
        else:
            self._bid_current = points
            self._bid_winner = seat
            self.log.append(f"{name} 叫 {points} 分")
            msg = f"🎲 {name} 叫 {points} 分！"
            if points == 3:
                return self._enter_grab(msg)
        # 继续 / 结束叫分
        if self._bid_turns_done >= 3 or self._bid_current == 3:
            if self._bid_winner is None:
                self.phase = "redeal"
                self.log.append("三家都不叫，重新发牌")
                return ""   # 播报由 room._redeal 统一发出，避免重复
            return self._enter_grab(msg)
        self._bid_turn = (self._bid_turn + 1) % 3
        return msg

    @property
    def bid_turn(self) -> int:
        return self._bid_turn % 3

    def _enter_grab(self, prefix: str) -> str:
        """进入抢地主阶段（有界）：先问候选者下家、再问下下家，最后原叫分者有一次"反抢"机会；
        每抢一次 ×2；最后抢的人成为地主，无人抢则叫分者当地主。"""
        winner = self._bid_winner
        assert winner is not None
        self.phase = PH_GRAB
        self._grab_caller = winner
        self._grab_caller_asked = False
        self._grab_any = False
        self._grab_queue = [(winner + 1) % 3, (winner + 2) % 3]
        self._grab_turn = self._grab_queue[0]
        wname = self.players[winner].name
        self.log.append(f"{wname} 成为地主候选人，进入抢地主")
        return prefix + f"\n{wname} 成为地主候选人，其他玩家请抢地主"

    def grab(self, seat: int, do_grab: bool) -> str:
        self._require_phase(PH_GRAB)
        if not self._grab_queue or seat != self._grab_queue[0]:
            raise GameError(f"还没轮到 {self.players[seat].name} 抢地主")
        self._grab_queue.pop(0)
        name = self.players[seat].name
        if do_grab:
            self.players[seat].grabbed += 1
            self._bid_winner = seat          # 地主候选资格转移
            self._grab_any = True
            self.multiplier *= 2
            self.log.append(f"{name} 抢地主（倍数 x2 → {self.multiplier}）")
            msg = f"🔥 {name} 抢地主！倍数 ×2（当前 ×{self.multiplier}）"
        else:
            self.log.append(f"{name} 不抢")
            msg = f"🙅 {name} 不抢"
        if not self._grab_queue:
            if self._grab_any and not self._grab_caller_asked and self._grab_caller is not None \
                    and self._grab_caller != self._bid_winner:
                # 有人抢过 → 原叫分者获得一次反抢机会
                self._grab_caller_asked = True
                self._grab_queue = [self._grab_caller]
            else:
                self._grab_turn = None
                return self._enter_double(msg)
        self._grab_turn = self._grab_queue[0]
        return msg

    @property
    def grab_turn(self) -> Optional[int]:
        return self._grab_turn

    # ------------------------------------------------------------------
    # 加倍
    # ------------------------------------------------------------------
    def _enter_double(self, prefix: str) -> str:
        assert self._bid_winner is not None
        self.base_points = max(self._bid_current, 1)
        self.landlord = self._bid_winner
        self.phase = PH_DOUBLE
        self._double_queue = [self.landlord, (self.landlord + 1) % 3, (self.landlord + 2) % 3]
        lname = self.players[self.landlord].name
        self.log.append(f"{lname} 成为地主，底牌 {C.fmt_cards(self.bottom)}")
        return (prefix + f"\n👑 {lname} 成为地主！底牌：{C.fmt_cards(self.bottom)}"
                f"\n💰 进入加倍阶段：可选「加倍 ×2」「超级加倍 ×4」「不加倍」")

    def double(self, seat: int, factor: int) -> str:
        """factor: 0=不加倍, 2=加倍(×2), 4=超级加倍(×4)。（对齐欢乐斗地主：普通加倍2倍/超级加倍4倍）"""
        self._require_phase(PH_DOUBLE)
        if not self._double_queue or seat != self._double_queue[0]:
            raise GameError(f"还没轮到 {self.players[seat].name} 加倍")
        if factor not in (0, 2, 4):
            raise GameError("加倍只能是 0（不加倍）/ 2（加倍）/ 4（超级加倍）")
        self._double_queue.pop(0)
        name = self.players[seat].name
        if factor:
            self.players[seat].doubled = True
            self.multiplier *= factor
            label = "超级加倍" if factor == 4 else "加倍"
            icon = "💎" if factor == 4 else "💰"
            self.log.append(f"{name} {label}（倍数 x{factor} → {self.multiplier}）")
            msg = f"{icon} {name} {label}！倍数 ×{factor}（当前 ×{self.multiplier}）"
        else:
            self.log.append(f"{name} 不加倍")
            msg = f"😐 {name} 不加倍"
        if not self._double_queue:
            return self._start_play(msg)
        return msg

    @property
    def double_turn(self) -> Optional[int]:
        return self._double_queue[0] if self._double_queue else None

    # ------------------------------------------------------------------
    # 出牌
    # ------------------------------------------------------------------
    def _start_play(self, prefix: str) -> str:
        assert self.landlord is not None
        self.phase = PH_PLAY
        # 底牌归地主，且角色分配
        lp = self.players[self.landlord]
        lp.hand = C.sort_cards(lp.hand + self.bottom)
        for p in self.players:
            p.role = "landlord" if p.seat == self.landlord else "farmer"
        self.current = self.landlord
        self.last_play = None
        self.pass_count = 0
        lname = lp.name
        bottom = C.fmt_cards(self.bottom)
        self.log.append(f"出牌阶段开始，{lname} 先出")
        return (prefix + f"\n🃏 底牌归地主 {lname}：{bottom}\n"
                f"🚀 出牌开始！由 {lname} 先出")

    def _require_phase(self, phase: str):
        if self.phase != phase:
            raise GameError(f"当前阶段不能执行该操作（{self.phase}）")

    def is_legal_play(self, seat: int, cards: Sequence[str]) -> Tuple[Optional[P.Combo], Optional[str]]:
        """校验出牌合法性。返回 (combo, error)。"""
        p = self.players[seat]
        pool = list(p.hand)
        for c in cards:
            if c not in pool:
                return None, f"手里没有 {c}"
            pool.remove(c)
        combo, err = P.choose(list(cards), self.wild_ranks, self._prev_combo())
        return combo, err

    def _prev_combo(self) -> Optional[P.Combo]:
        if self.last_play is None or self.last_play.kind == "pass":
            return None
        return self.last_play.combo

    def play(self, seat: int, cards: Sequence[str]) -> Tuple[P.Combo, str]:
        self._require_phase(PH_PLAY)
        if seat != self.current:
            raise GameError(f"还没轮到 {self.players[seat].name} 出牌")
        if not cards:
            raise GameError("没识别到要出的牌")
        combo, err = self.is_legal_play(seat, cards)
        if err:
            raise GameError(err)
        assert combo is not None
        p = self.players[seat]
        for c in cards:
            p.hand.remove(c)
        p.played_any = True
        p.play_count += 1
        rec = PlayRecord(seat=seat, kind="play", combo=combo, cards=C.sort_cards(cards))
        self.history.append(rec)
        self.last_play = rec
        self.pass_count = 0
        # 炸弹/王炸倍数
        bomb_line = ""
        if combo.kind in (P.BOMB, P.ROCKET):
            self.bomb_count += 1
            self._bombed_seats[seat] = self._bombed_seats.get(seat, 0) + 1
            if self.bomb_multiplier:
                mult = self._bomb_mult(combo)
                self.multiplier *= mult
                tag = "王炸" if combo.kind == P.ROCKET else "炸弹"
                bomb_line = f"\n💥 {tag}加成：倍数 ×{mult}（当前 ×{self.multiplier}）"
        self.log.append(f"{p.name} 出 {combo.text()}")
        role_txt = _role_txt(p)
        # 胜利判定
        if not p.hand:
            self._settle(winner_seat=seat)
            return combo, f"🎉 {p.name}（{role_txt}）出完了最后 {len(cards)} 张牌！"
        self.current = (seat + 1) % 3
        counts = "\n".join(f"{q.name} {len(q.hand)}张" for q in self.players)
        return combo, (f"🃏 {p.name}（{role_txt}）\n"
                       f"出 {combo.text()}{bomb_line}\n\n"
                       f"📊 剩牌：\n{counts}")

    def _bomb_mult(self, combo: P.Combo) -> int:
        if combo.kind == P.ROCKET:
            return 4 if self.mode == "leizi" else 2
        # 炸弹：经典 x2；癞子场 软炸 x2 / 硬炸 x4 / 纯癞子炸 x4
        if self.mode == "leizi":
            return {P.T_SOFT: 2, P.T_HARD: 4, P.T_PURE: 4}.get(combo.tier, 2)
        return 2

    def pass_turn(self, seat: int) -> str:
        self._require_phase(PH_PLAY)
        if seat != self.current:
            raise GameError(f"还没轮到 {self.players[seat].name}")
        if self.last_play is None or self.last_play.kind == "pass":
            raise GameError("你是先手，不能不出")
        p = self.players[seat]
        role_txt = _role_txt(p)
        rec = PlayRecord(seat=seat, kind="pass")
        self.history.append(rec)
        self.pass_count += 1
        self.log.append(f"{p.name} 不出")
        if self.pass_count >= 2:
            # 两家连过 → 上一手出牌者重新领出
            last = next((r for r in reversed(self.history) if r.kind == "play"), None)
            assert last is not None
            self.current = last.seat
            self.last_play = None
            self.pass_count = 0
            self.log.append(f"两家不出，{self.players[last.seat].name} 重新出牌")
            return (f"💨 {p.name}（{role_txt}）不出 → 🔄 两家不出，"
                    f"{self.players[last.seat].name} 重新出牌")
        self.current = (seat + 1) % 3
        return f"💨 {p.name}（{role_txt}）不出"

    # ------------------------------------------------------------------
    # 结算
    # ------------------------------------------------------------------
    def _settle(self, winner_seat: int):
        self.phase = PH_END
        self.ended_at = time.time()
        winner = self.players[winner_seat]
        landlord_p = self.players[self.landlord]  # type: ignore[index]
        landlord_won = winner_seat == self.landlord
        # 春天
        if self.spring_enabled:
            if landlord_won:
                if all(not p.played_any for p in self.players if p.role == "farmer"):
                    self.spring_type = "spring"
                    self.multiplier *= 2
            else:
                if landlord_p.play_count <= 1:
                    self.spring_type = "anti"
                    self.multiplier *= 2
        stake = self.base_points * self.multiplier * self.base_per_point
        for p in self.players:
            if p.role == "landlord":
                p.score = stake * 2 if landlord_won else -stake * 2
            else:
                p.score = -stake if landlord_won else stake
            if p.role == ("landlord" if landlord_won else "farmer"):
                p.is_winner = True
        self.log.append(f"对局结束：{'地主' if landlord_won else '农民'}获胜，"
                        f"基础分 {self.base_points}，倍数 x{self.multiplier}，"
                        f"结算基数 {stake} 乐豆")
        if self.spring_type == "spring":
            self.log.append("春天！倍数翻倍")
        elif self.spring_type == "anti":
            self.log.append("反春天！倍数翻倍")

    def settlement_text(self) -> str:
        if self.phase != PH_END:
            return "对局尚未结束"
        lines = []
        lw = self.players[self.landlord].is_winner if self.landlord is not None else False
        lines.append(f"🏁 本局结束 —— {'👑 地主' if lw else '🌾 农民'}胜利！")
        lines.append(f"📈 底分 {self.base_points} × 总倍数 ×{self.multiplier}"
                     f"（炸弹 {self.bomb_count} 个）")
        if self.spring_type == "spring":
            lines.append("🌸 春天！倍数再翻倍")
        elif self.spring_type == "anti":
            lines.append("🌸 反春天！倍数再翻倍")
        lines.append("——————————————")
        for p in self.players:
            role = _role_txt(p)
            if p.is_bot:
                lines.append(f"· 🤖 {p.name}（{role}）：∞ 乐豆")
            else:
                delta = f"+{p.score}" if p.score >= 0 else str(p.score)
                icon = "🎉" if p.is_winner else "😢"
                lines.append(f"· {icon} {p.name}（{role}）：{delta} 乐豆")
        return "\n".join(lines)

    def score_deltas(self) -> Dict[str, int]:
        return {p.uid: p.score for p in self.players}

    def state_for(self, seat: int) -> dict:
        """给某个玩家视角的状态摘要。"""
        p = self.players[seat]
        others = []
        for q in self.players:
            if q.seat == seat:
                continue
            others.append({
                "name": q.name, "seat": q.seat, "role": q.role,
                "cards": len(q.hand), "is_bot": q.is_bot,
            })
        return {
            "phase": self.phase, "mode": self.mode,
            "hand": list(p.hand), "hand_size": len(p.hand),
            "role": p.role, "current": self.current, "landlord": self.landlord,
            "bottom": list(self.bottom) if p.role == "landlord" else None,
            "last": None if not self.last_play or self.last_play.kind == "pass"
                    else self.last_play.combo.text() if self.last_play.combo else None,
            "last_seat": self.last_play.seat if self.last_play else None,
            "others": others, "multiplier": self.multiplier,
            "wild": next(iter(self.wild_ranks)) if self.wild_ranks else None,
        }
