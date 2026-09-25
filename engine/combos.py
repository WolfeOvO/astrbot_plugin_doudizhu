"""斗地主牌型识别、比较与癞子（万能牌）支持。

牌型:
  单张 single / 对子 pair / 三张 triple / 三带一 triple1 / 三带二 triple2
  顺子 straight (>=5 张, 3..A) / 连对 straight2 (>=3 对, 3..A)
  飞机 plane (>=2 个连续三张) / 飞机带单 plane1 / 飞机带对 plane2
  四带二(单) four2 / 四带两对 four4 / 炸弹 bomb / 王炸 rocket

癞子规则（对齐欢乐斗地主「癞子场」官方规则）:
  - 癞子可代替除大小王以外的任意点数牌；绝不可代替大小王；
  - 癞子单独出时按癞子自身的点数计算（77 单出即一对 7）；
  - 炸弹分四级: 王炸(4) > 纯癞子炸弹(3) > 硬炸弹(2) > 软炸弹(1)；
    硬炸弹=四张同点非癞子牌；软炸弹=癞子与非癞子牌搭配而成的四张；
    纯癞子炸弹=四张癞子牌（同一点数四张全为癞子）；
  - 带牌翅膀不能使用"三张/四张"的主体点数（防歧义，指定严格模式）；
  - 飞机带对 / 四带两对：对子须为不同点数（四张同点不可拆成两对）；
  - 带单翅膀允许同点数重复（如飞机带单 444555+77 合法）。
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Set, Tuple

from .cards import (CARD_VALUE, JOKER_BIG, JOKER_SMALL, card_rank, card_suit,
                    card_value, fmt_cards, is_joker, sort_cards)

_JOKER_MIN = CARD_VALUE[JOKER_SMALL]  # 16：癞子不可代替大小王

# ==== 牌型常量 ====
ROCKET = "rocket"
BOMB = "bomb"
SINGLE = "single"
PAIR = "pair"
TRIPLE = "triple"
TRIPLE_ONE = "triple1"
TRIPLE_PAIR = "triple2"
STRAIGHT = "straight"
STRAIGHT_PAIR = "straight2"
PLANE = "plane"
PLANE_ONE = "plane1"
PLANE_PAIR = "plane2"
FOUR_TWO_SINGLE = "four2"
FOUR_TWO_PAIR = "four4"

# ==== 炸弹等级（癞子场）====
T_NONE = 0     # 非炸弹
T_SOFT = 1     # 软炸弹（癞子 + 非癞子）
T_HARD = 2     # 硬炸弹（四张同点非癞子）
T_PURE = 3     # 纯癞子炸弹（四张癞子牌）
T_ROCKET = 4   # 王炸

KIND_NAME = {
    ROCKET: "王炸", BOMB: "炸弹", SINGLE: "单张", PAIR: "对子", TRIPLE: "三张",
    TRIPLE_ONE: "三带一", TRIPLE_PAIR: "三带二",
    STRAIGHT: "顺子", STRAIGHT_PAIR: "连对", PLANE: "飞机",
    PLANE_ONE: "飞机带单", PLANE_PAIR: "飞机带对",
    FOUR_TWO_SINGLE: "四带二", FOUR_TWO_PAIR: "四带两对",
}

# 炸弹等级中文（用于播报）
TIER_NAME = {T_SOFT: "软炸", T_HARD: "硬炸", T_PURE: "纯癞子炸", T_ROCKET: "王炸"}

# 用于"选择解释"的优先级（越大越强）
KIND_PRIO = {
    ROCKET: 16, BOMB: 15, PLANE_PAIR: 14, PLANE_ONE: 13, PLANE: 12,
    STRAIGHT_PAIR: 11, STRAIGHT: 10, FOUR_TWO_PAIR: 9, FOUR_TWO_SINGLE: 8,
    TRIPLE_PAIR: 7, TRIPLE_ONE: 6, TRIPLE: 5, PAIR: 4, SINGLE: 3,
}


@dataclass
class Combo:
    kind: str
    main: int            # 比较主值（链型=最大值；其余=主体点数）
    length: int = 1      # 顺子=张数 / 连对=对数 / 飞机=组数 / 其他=1
    cards: List[str] = field(default_factory=list)
    wild_used: int = 0   # 该解释用到的癞子数（0=硬牌）
    tier: int = T_NONE   # 炸弹等级（仅 kind=BOMB/ROCKET 有意义）

    def describe(self) -> str:
        n = KIND_NAME.get(self.kind, self.kind)
        if self.kind == ROCKET:
            return n
        if self.kind == BOMB:
            return f"{TIER_NAME.get(self.tier, '炸弹')}"
        if self.kind == STRAIGHT:
            return f"{n}（{self.length}张）"
        if self.kind == STRAIGHT_PAIR:
            return f"{n}（{self.length}对）"
        if self.kind in (PLANE, PLANE_ONE, PLANE_PAIR):
            return f"{n}（{self.length}组）"
        return n

    def text(self, with_cards: bool = True) -> str:
        base = self.describe()
        if with_cards and self.cards:
            return f"{base}：{fmt_cards(self.cards)}"
        return base


def beats(a: Optional[Combo], b: Optional[Combo]) -> bool:
    """a 能否压过 b。b=None 表示自由出牌（恒 True）。"""
    if a is None:
        return False
    if b is None:
        return True
    if a.kind == ROCKET:
        return True
    if b.kind == ROCKET:
        return False
    if a.kind == BOMB and b.kind == BOMB:
        if a.tier != b.tier:
            return a.tier > b.tier
        return a.main > b.main
    if a.kind == BOMB:
        return True
    if b.kind == BOMB:
        return False
    if a.kind != b.kind or a.length != b.length:
        return False
    return a.main > b.main


def bomb_tier(kind: str, main: int, wild_used: int, lezi_value: Optional[int]) -> int:
    """推导炸弹等级。lezi_value=癞子本点数（无癞子玩法传 None）。"""
    if kind == ROCKET:
        return T_ROCKET
    if kind != BOMB:
        return T_NONE
    if wild_used == 0:
        if lezi_value is not None and main == lezi_value:
            return T_PURE
        return T_HARD
    return T_SOFT


# ---------------------------------------------------------------------------
# 经典（无癞子）识别
# ---------------------------------------------------------------------------

def detect(cards: Sequence[str]) -> Optional[Combo]:
    cs = list(cards)
    n = len(cs)
    if n == 0:
        return None
    vals = sorted(card_value(c) for c in cs)
    cnt = Counter(vals)
    counts = sorted(cnt.values(), reverse=True)

    if n == 2 and set(cs) == set((JOKER_SMALL, JOKER_BIG)):
        return Combo(ROCKET, 17, 1, cs, 0, T_ROCKET)
    if n == 1:
        return Combo(SINGLE, vals[0], 1, cs)
    if n == 2 and counts == [2] and len(cnt) == 1:
        return Combo(PAIR, next(iter(cnt)), 1, cs)
    if n == 3 and counts == [3]:
        return Combo(TRIPLE, next(iter(cnt)), 1, cs)
    if n == 4:
        if counts == [4]:
            return Combo(BOMB, next(iter(cnt)), 1, cs, 0, T_HARD)
        if counts == [3, 1]:
            tri = next(v for v, c in cnt.items() if c == 3)
            return Combo(TRIPLE_ONE, tri, 1, cs)
    if n == 5 and counts == [3, 2]:
        tri = next(v for v, c in cnt.items() if c == 3)
        return Combo(TRIPLE_PAIR, tri, 1, cs)

    # 顺子
    if 5 <= n <= 12 and len(cnt) == n and all(v <= 14 for v in vals) \
            and vals == list(range(vals[0], vals[0] + n)):
        return Combo(STRAIGHT, vals[-1], n, cs)

    # 连对
    if 6 <= n <= 20 and n % 2 == 0:
        k = n // 2
        u = sorted(cnt)
        if k >= 3 and len(u) == k and all(c == 2 for c in cnt.values()) \
                and u[-1] <= 14 and u == list(range(u[0], u[0] + k)):
            return Combo(STRAIGHT_PAIR, u[-1], k, cs)

    # 飞机（不带）
    if n >= 6 and n % 3 == 0:
        k = n // 3
        u = sorted(cnt)
        if k >= 2 and len(u) == k and all(c == 3 for c in cnt.values()) \
                and u[-1] <= 14 and u == list(range(u[0], u[0] + k)):
            return Combo(PLANE, u[-1], k, cs)

    if n >= 8:
        for k, wings_per in ((n // 4, 1) if n % 4 == 0 else (0, 0),
                             (n // 5, 2) if n % 5 == 0 else (0, 0)):
            if not k or k < 2:
                continue
            res = _find_plane_with_wings(cnt, k, wings_per, n)
            if res is not None:
                kind = PLANE_ONE if wings_per == 1 else PLANE_PAIR
                return Combo(kind, res, k, cs)

    # 四带二
    if n in (6, 8):
        for q in sorted(v for v, c in cnt.items() if c == 4):
            wings = n - 4
            wcv = Counter(card_value(c) for c in cs if card_value(c) != q)
            if wings == 2:
                if len(wcv) == 2 and sum(wcv.values()) == 2 and all(c == 1 for c in wcv.values()):
                    return Combo(FOUR_TWO_SINGLE, q, 1, cs)
                if len(wcv) == 1 and next(iter(wcv.values())) == 2:
                    return Combo(FOUR_TWO_SINGLE, q, 1, cs)
            if wings == 4:
                if len(wcv) == 2 and all(c == 2 for c in wcv.values()):
                    return Combo(FOUR_TWO_PAIR, q, 1, cs)
    return None


def _find_plane_with_wings(cnt: Counter, k: int, wings_per: int, n: int) -> Optional[int]:
    """在经典模式下查找 k 个连续三张（带翅膀），返回飞机最大值(末尾)，找不到返回 None。"""
    u = sorted(v for v, c in cnt.items() if c >= 3 and v <= 14)
    if len(u) < k:
        return None
    for start in range(3, 14 - k + 2):
        run = list(range(start, start + k))
        if not all(cnt.get(v, 0) >= 3 for v in run):
            continue
        for v in run:
            if cnt[v] != 3:
                break
        else:
            rest = Counter()
            for v, c in cnt.items():
                if v in run:
                    continue
                rest[v] = c
            if wings_per == 1:
                if sum(rest.values()) == k:
                    return run[-1]
            else:
                if sum(rest.values()) == 2 * k and all(c == 2 for c in rest.values()):
                    return run[-1]
    return None


# ---------------------------------------------------------------------------
# 癞子（万能牌）解释
# ---------------------------------------------------------------------------

def _lezi_value(wild_ranks: Optional[Set[str]]) -> Optional[int]:
    if not wild_ranks:
        return None
    for r in wild_ranks:
        if r in ("小王", "大王"):
            continue
        return CARD_VALUE[r]
    return None


def _checks(cnt: Counter, w: int, n: int, lezi_value: Optional[int]) -> List[Tuple[str, int, int, int]]:
    """返回可能的 (kind, main, length, wild_used) 候选；cnt=非癞子牌计数, w=剩余癞子数。"""
    res: List[Tuple[str, int, int, int]] = []
    vals = sorted(cnt)
    lv = lezi_value if lezi_value is not None else 3

    # 单张 / 对子 / 三张 / 炸弹（同点数一组）
    def one_group(size: int, kind: str):
        if len(cnt) == 1:
            v, c = next(iter(cnt.items()))
            # 癞子不可代替大小王：王只有 1 张，永远凑不出对/三/炸
            if v >= _JOKER_MIN and c < size:
                return
            if c <= size and c + w == size:
                if size == 4:
                    res.append((BOMB, v, 1, w))
                elif size == 3:
                    res.append((TRIPLE, v, 1, w))
                elif size == 2:
                    res.append((PAIR, v, 1, w))
                elif size == 1 and w == 0:
                    res.append((SINGLE, v, 1, 0))
        elif len(cnt) == 0 and w == size:
            # 全癞子组合：只能算作癞子本身的点数（不可当作王）
            if size == 4:
                res.append((BOMB, lv, 1, w))
            elif size == 3:
                res.append((TRIPLE, lv, 1, w))
            elif size == 2:
                res.append((PAIR, lv, 1, w))
            elif size == 1:
                res.append((SINGLE, lv, 1, w))

    if n == 1:
        one_group(1, SINGLE)
    if n == 2:
        one_group(2, PAIR)
    if n == 3:
        one_group(3, TRIPLE)
    if n == 4:
        one_group(4, BOMB)
        # 三带一
        for v in vals:
            if v >= _JOKER_MIN:
                continue
            f1 = 3 - cnt[v]
            if f1 < 0 or f1 > w:
                continue
            others = sum(c for x, c in cnt.items() if x != v)
            f2 = w - f1
            if others + f2 == 1:
                res.append((TRIPLE_ONE, v, 1, w))
    if n == 5:
        # 三带二
        for v in vals:
            if v >= _JOKER_MIN:
                continue
            f1 = 3 - cnt[v]
            if f1 < 0 or f1 > w:
                continue
            f2 = w - f1
            others = {x: c for x, c in cnt.items() if x != v}
            total_others = sum(others.values())
            if total_others + f2 != 2:
                continue
            if f2 == 0:
                ok = len(others) == 1 and next(iter(others.values())) == 2
            elif f2 == 1:
                ok = total_others == 1 and all(x < _JOKER_MIN for x in others)
            else:
                ok = total_others == 0
            if ok:
                res.append((TRIPLE_PAIR, v, 1, w))

    # 顺子
    if 5 <= n <= 12:
        for s in range(3, 15):
            e = s + n - 1
            if e > 14:
                break
            if any(x < s or x > e for x in cnt):
                continue
            if any(c > 1 for c in cnt.values()):
                continue
            if n - sum(cnt.values()) == w:
                res.append((STRAIGHT, e, n, w))

    # 连对
    if 6 <= n <= 20 and n % 2 == 0:
        k = n // 2
        for s in range(3, 15):
            e = s + k - 1
            if e > 14:
                break
            if any(x < s or x > e for x in cnt):
                continue
            if any(c > 2 for c in cnt.values()):
                continue
            if 2 * k - sum(cnt.values()) == w:
                res.append((STRAIGHT_PAIR, e, k, w))

    # 飞机（不带）
    if n >= 6 and n % 3 == 0:
        k = n // 3
        if 2 <= k <= 6:
            for s in range(3, 15):
                e = s + k - 1
                if e > 14:
                    break
                if any(x < s or x > e for x in cnt):
                    continue
                if any(c > 3 for c in cnt.values()):
                    continue
                if 3 * k - sum(cnt.values()) == w:
                    res.append((PLANE, e, k, w))

    # 飞机带单 / 飞机带对（严格：翅膀不能使用主体点数）
    if n >= 8:
        if n % 4 == 0 and n // 4 >= 2:
            k = n // 4
            _plane_wings(cnt, w, k, 1, res, lezi_value)
        if n % 5 == 0 and n // 5 >= 2:
            k = n // 5
            _plane_wings(cnt, w, k, 2, res, lezi_value)

    # 四带二
    if n in (6, 8):
        for q in vals:
            if q >= _JOKER_MIN:
                continue
            f1 = 4 - cnt[q]
            if f1 < 0 or f1 > w:
                continue
            f2 = w - f1
            others = {x: c for x, c in cnt.items() if x != q}
            total_others = sum(others.values())
            if n == 6:
                if total_others + f2 == 2:
                    if f2 == 0:
                        if len(others) in (1, 2) and sum(others.values()) == 2:
                            res.append((FOUR_TWO_SINGLE, q, 1, w))
                    elif f2 == 1:
                        if total_others == 1:
                            res.append((FOUR_TWO_SINGLE, q, 1, w))
                    else:
                        if total_others == 0:
                            res.append((FOUR_TWO_SINGLE, q, 1, w))
            if n == 8:
                if total_others + f2 == 4:
                    if f2 == 0:
                        if len(others) == 2 and all(c == 2 for c in others.values()):
                            res.append((FOUR_TWO_PAIR, q, 1, w))
                    else:
                        # 翅膀用癞子补对：统计奇数组
                        pairs = 0
                        need_fill = 0
                        ok = True
                        for x, c in others.items():
                            if c > 2 or x >= _JOKER_MIN:
                                ok = False
                                break
                            pairs += c // 2
                            if c % 2:
                                pairs += 1
                                need_fill += 1
                        if ok and pairs == 2 and need_fill == f2:
                            res.append((FOUR_TWO_PAIR, q, 1, w))
    return res


def _plane_wings(cnt: Counter, w: int, k: int, wings_per: int, res, lezi_value):
    """飞机带翅膀的候选取。wings_per=1(单) / 2(对)。"""
    for s in range(3, 15):
        e = s + k - 1
        if e > 14:
            break
        run = list(range(s, e + 1))
        if any(x < s or x > e for x in cnt):
            continue
        if any(cnt.get(v, 0) > 3 for v in run):
            continue
        f_trip = sum(3 - cnt.get(v, 0) for v in run)
        if f_trip > w:
            continue
        wing_nat = {x: c for x, c in cnt.items() if x not in run}
        f_wing = w - f_trip
        if wings_per == 1:
            total_wing = sum(wing_nat.values()) + f_wing
            if total_wing == k and all(c <= 4 for c in wing_nat.values()):
                res.append((PLANE_ONE, e, k, w))
        else:
            pairs = 0
            need_fill = 0
            ok = True
            for x, c in wing_nat.items():
                if c > 2 or x >= _JOKER_MIN:
                    ok = False
                    break
                pairs += c // 2
                if c % 2:
                    pairs += 1
                    need_fill += 1
            if ok and pairs == k and need_fill == f_wing:
                res.append((PLANE_PAIR, e, k, w))


def evaluate(cards: Sequence[str], wild_ranks: Optional[Set[str]] = None) -> List[Combo]:
    """返回一组牌的全部合法解释（去重，优先保留少用癞子的解释）。"""
    cs = list(cards)
    if not cs:
        return []
    wild_ranks = set(wild_ranks or ())
    wilds = [c for c in cs if card_rank(c) in wild_ranks]
    if not wilds:
        combo = detect(cs)
        return [combo] if combo else []

    lv = _lezi_value(wild_ranks)
    anchors = [c for c in cs if c not in wilds]
    results: Dict[Tuple[str, int, int], Combo] = {}

    def add(kind: str, main: int, length: int, wild_used: int, combo_cards: List[str]):
        key = (kind, main, length)
        tier = bomb_tier(kind, main, wild_used, lv)
        prev = results.get(key)
        if prev is None or wild_used < prev.wild_used:
            results[key] = Combo(kind, main, length, combo_cards, wild_used, tier)

    nr = len(wilds)
    for mask in range(1 << nr):
        nat_cards = anchors + [wilds[i] for i in range(nr) if (mask >> i) & 1]
        w = nr - bin(mask).count("1")
        cnt = Counter(card_value(c) for c in nat_cards)
        n = len(nat_cards) + w
        for kind, main, length, wild_used in _checks(cnt, w, n, lv):
            add(kind, main, length, wild_used, cs)
        # 王炸（必须两张真王且无癞子剩）
        if w == 0 and set(nat_cards) == set((JOKER_SMALL, JOKER_BIG)):
            add(ROCKET, 17, 1, 0, cs)
    return list(results.values())


def _key(c: Combo) -> Tuple[int, int, int, int, int]:
    return (KIND_PRIO[c.kind], c.tier, c.main, c.length, -c.wild_used)


def choose(cards: Sequence[str], wild_ranks: Optional[Set[str]] = None,
           prev: Optional[Combo] = None) -> Tuple[Optional[Combo], Optional[str]]:
    """为一次出牌选择解释。prev 非空时必须能压过。

    返回 (combo, error)：combo=None 且 error=None 表示需要"不要"（管不上，理论上不会出现）。
    """
    interps = evaluate(cards, wild_ranks)
    if not interps:
        return None, "这不是合法牌型"
    if prev is None:
        return max(interps, key=_key), None
    winners = [c for c in interps if beats(c, prev)]
    if not winners:
        return None, f"管不上，需要大过（{prev.describe()}）"
    return max(winners, key=_key), None
