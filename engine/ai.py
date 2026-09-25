"""AI 玩家：叫分评估 + 出牌搜索 + 简单配合逻辑。

设计目标是"稳、不送豆"：
- 不拆炸弹/不浪费癞子（除非必要）；
- 支持出牌时保持牌权意识（对手剩牌少时用大牌压制）;
- 农民不压队友（除非能一把走完）。
"""
from __future__ import annotations

import random
from typing import Dict, List, Optional, Sequence, Set, Tuple

from . import cards as C
from . import combos as P

WILD_PENALTY = 1.0   # 用掉一个癞子的估值惩罚
BOMB_PENALTY = 6.0   # 拆炸弹的估值惩罚


# ---------------------------------------------------------------------------
# 手牌评估（叫分 / 加倍）
# ---------------------------------------------------------------------------

def hand_strength(hand: Sequence[str], wild_ranks: Set[str]) -> float:
    """粗略手牌强度 0~10。用于叫分与加倍决策。"""
    cnt: Dict[str, int] = {}
    wilds = 0
    for c in hand:
        r = C.card_rank(c)
        if r in wild_ranks:
            wilds += 1
        cnt[r] = cnt.get(r, 0) + 1
    s = 0.0
    s += cnt.get(C.JOKER_SMALL, 0) * 1.5
    s += cnt.get(C.JOKER_BIG, 0) * 2.0
    s += cnt.get("2", 0) * 0.8
    s += cnt.get("A", 0) * 0.4
    for r, c in cnt.items():
        if c == 4:
            s += 3.0
        if r in wild_ranks:
            s += c * 0.6
    s += wilds * 0.8
    return min(s, 10.0)


def suggest_bid(hand: Sequence[str], wild_ranks: Set[str], current: int) -> int:
    """叫分建议：0=不叫，返回可叫的最大分值（必须 > current，不满足返回 0）。"""
    s = hand_strength(hand, wild_ranks)
    if s >= 6.0:
        want = 3
    elif s >= 4.5:
        want = 2
    elif s >= 3.0:
        want = 1
    else:
        return 0
    return want if want > current else 0


def suggest_grab(hand: Sequence[str], wild_ranks: Set[str]) -> bool:
    return hand_strength(hand, wild_ranks) >= 5.0


def suggest_double(hand: Sequence[str], wild_ranks: Set[str]) -> int:
    """加倍建议（对齐欢乐斗地主：普通加倍×2 / 超级加倍×4）：0=不加倍, 2=加倍, 4=超级加倍。"""
    s = hand_strength(hand, wild_ranks)
    if s >= 7.5:
        return 4
    if s >= 4.5:
        return 2
    return 0


# ---------------------------------------------------------------------------
# 出牌生成
# ---------------------------------------------------------------------------

def _split(hand: Sequence[str], wild_ranks: Set[str]):
    wilds = [c for c in hand if C.card_rank(c) in wild_ranks]
    groups: Dict[str, List[str]] = {}
    for c in hand:
        if c in wilds:
            continue
        r = C.card_rank(c)
        groups.setdefault(r, []).append(c)
    return groups, wilds


def _take(groups: Dict[str, List[str]], r: str, k: int) -> List[str]:
    return groups.get(r, [])[:k]


def gen_leads(hand: Sequence[str], wild_ranks: Set[str]) -> List[List[str]]:
    """生成所有可主动出的候选牌型（不是穷举，是合理集合）。"""
    groups, wilds = _split(hand, wild_ranks)
    plans: List[List[str]] = []
    nw = len(wilds)

    def add(cs: List[str]):
        if cs:
            plans.append(cs)

    # 单张（含癞子本身可单出）
    for r in groups:
        add(_take(groups, r, 1))
    for w in wilds:
        add([w])

    # 对子 / 三张 / 炸弹
    for r, cs0 in groups.items():
        if r in (C.JOKER_SMALL, C.JOKER_BIG):
            continue
        c = len(cs0)
        for size, k_needed in ((2, 2 - c), (3, 3 - c), (4, 4 - c)):
            if k_needed == 0:
                add(cs0[:size])
            elif k_needed > 0 and nw >= k_needed and c + nw >= size:
                add(cs0[:c] + wilds[:k_needed])
    # 王炸
    if C.JOKER_SMALL in hand and C.JOKER_BIG in hand:
        add([C.JOKER_SMALL, C.JOKER_BIG])

    # 三带一 / 三带二
    triples: List[Tuple[str, List[str]]] = []
    for r, cs0 in groups.items():
        if r in (C.JOKER_SMALL, C.JOKER_BIG):
            continue
        c = len(cs0)
        if c >= 3:
            triples.append((r, cs0[:3]))
        elif c == 2 and nw >= 1:
            triples.append((r, cs0[:2] + wilds[:1]))
    for r, base in triples:
        wings1 = _wings_singles(groups, wilds, set(base), 1, exclude_rank=r)
        for w in wings1[:2]:
            add(base + w)
        wings2 = _wings_pairs(groups, wilds, set(base), 1)
        for w in wings2[:2]:
            add(base + w)

    # 顺子
    for L in range(5, 13):
        for s in range(3, 15 - L + 1):
            run = [C.VALUE_CARD[v] for v in range(s, s + L)]
            got: List[str] = []
            need = 0
            ok = True
            for r in run:
                cs0 = groups.get(r, [])
                if cs0:
                    got.append(cs0[0])
                else:
                    need += 1
            if need > nw or not ok:
                continue
            got = got + wilds[:need]
            add(got)

    # 连对
    for k in range(3, 11):
        for s in range(3, 15 - k + 1):
            run = [C.VALUE_CARD[v] for v in range(s, s + k)]
            got: List[str] = []
            need = 0
            ok = True
            for r in run:
                cs0 = groups.get(r, [])
                if len(cs0) >= 2:
                    got += cs0[:2]
                elif len(cs0) == 1:
                    got += cs0[:1]
                    need += 1
                else:
                    need += 2
            if need > nw:
                continue
            got = got + wilds[:need]
            add(got)

    # 飞机（不带 / 带单 / 带对）
    for k in range(2, 7):
        for s in range(3, 15 - k + 1):
            run = [C.VALUE_CARD[v] for v in range(s, s + k)]
            got: List[str] = []
            need = 0
            ok = True
            for r in run:
                cs0 = groups.get(r, [])
                if len(cs0) >= 3:
                    got += cs0[:3]
                elif len(cs0) == 2:
                    got += cs0[:2]
                    need += 1
                elif len(cs0) == 1:
                    got += cs0[:1]
                    need += 2
                else:
                    need += 3
            if need > nw:
                continue
            got = got + wilds[:need]
            add(got)  # 不带
            rest_after = [c for c in hand if c not in got]
            # 带单
            w1 = _wings_singles(*_split(rest_after, wild_ranks), set(got), k)
            for w in w1[:1]:
                add(got + w)
            # 带对
            w2 = _wings_pairs(*_split(rest_after, wild_ranks), set(got), k)
            for w in w2[:1]:
                add(got + w)

    # 四带二
    for r, cs0 in groups.items():
        if r in (C.JOKER_SMALL, C.JOKER_BIG):
            continue
        c = len(cs0)
        if c >= 4:
            base = cs0[:4]
        elif c >= 3 and nw >= 1:
            base = cs0[:3] + wilds[:1]
        else:
            continue
        rest_after = [c2 for c2 in hand if c2 not in base]
        g2, w2 = _split(rest_after, wild_ranks)
        w1 = _wings_singles(g2, w2, set(base), 2, exclude_rank=r)
        for w in w1[:1]:
            add(base + w)
        wp = _wings_pairs(g2, w2, set(base), 2)
        for w in wp[:1]:
            add(base + w)

    # 去重
    seen = set()
    out = []
    for cs in plans:
        key = tuple(sorted(cs))
        if key in seen:
            continue
        seen.add(key)
        out.append(list(cs))
    return out


def _wings_singles(groups, wilds, used: Set[str], k: int, exclude_rank: Optional[str] = None) -> List[List[str]]:
    """从剩余牌里挑 k 张单牌做翅膀（优先小组、避免拆大对）。"""
    picks: List[Tuple[int, str]] = []
    for r, cs0 in groups.items():
        if exclude_rank is not None and r == exclude_rank:
            continue
        if any(c in used for c in cs0):
            continue
        # 估值：单牌优先，小牌优先
        val = C.CARD_VALUE[r]
        picks.append((val + (5 if len(cs0) == 1 else 0) + (0 if len(cs0) == 1 else 3 * len(cs0)), r))
    picks.sort()
    out: List[List[str]] = []
    if len(picks) >= k:
        chosen = [r for _, r in picks[:k]]
        cs = []
        for r in chosen:
            cs.append(groups[r][0])
        out.append(cs)
    return out


def _wings_pairs(groups, wilds, used: Set[str], k: int, exclude_rank: Optional[str] = None) -> List[List[str]]:
    """从剩余牌里挑 k 对做翅膀。"""
    pairs: List[Tuple[int, str]] = []
    for r, cs0 in groups.items():
        if exclude_rank is not None and r == exclude_rank:
            continue
        if r in (C.JOKER_SMALL, C.JOKER_BIG):
            continue
        avail = [c for c in cs0 if c not in used]
        if len(avail) >= 2:
            pairs.append((C.CARD_VALUE[r], r))
    pairs.sort()
    out: List[List[str]] = []
    if len(pairs) >= k:
        cs = []
        for _, r in pairs[:k]:
            cs += groups[r][:2]
        out.append(cs)
    return out


def gen_responses(hand: Sequence[str], wild_ranks: Set[str], prev: P.Combo) -> List[List[str]]:
    """生成能压过 prev 的候选（含炸弹与王炸）。"""
    plans: List[List[str]] = []
    for cs in gen_leads(hand, wild_ranks):
        combo, err = P.choose(cs, wild_ranks, prev)
        if combo is not None and err is None:
            plans.append(cs)
    return plans


# ---------------------------------------------------------------------------
# 出牌决策
# ---------------------------------------------------------------------------

def choose_play(hand: Sequence[str], wild_ranks: Set[str], prev: Optional[P.Combo],
                ctx: Optional[dict] = None) -> Optional[List[str]]:
    """选择一手牌；返回 None 表示不出（跟牌时）。手牌仅剩必出时总会返回。"""
    ctx = ctx or {}
    role = ctx.get("role", "farmer")
    last_role = ctx.get("last_role")
    my_seat = ctx.get("my_seat")
    last_seat = ctx.get("last_seat")
    counts = ctx.get("others_counts") or {}
    landlord_count = ctx.get("landlord_count")
    rng = ctx.get("rng") or random.Random()

    groups, wilds = _split(hand, wild_ranks)
    nw = len(wilds)

    # 一手走完：优先直接赢
    if prev is None:
        if len(hand) <= 12:  # find a winning subset
            leads = gen_leads(hand, wild_ranks)
            for cs in leads:
                if len(cs) == len(hand):
                    combo, err = P.choose(cs, wild_ranks, None)
                    if combo is not None and err is None:
                        return cs
        leads = gen_leads(hand, wild_ranks)
        if not leads:
            return None
        scored = []
        for cs in leads:
            combo, err = P.choose(cs, wild_ranks, None)
            if combo is None or err:
                continue
            keep = [c for c in hand if c not in cs]
            leftover = _leftover_singles(keep, wild_ranks)
            is_bomb = combo.kind in (P.BOMB, P.ROCKET)
            score = (
                (100 if is_bomb else 0)                       # 不轻易首出炸弹
                + leftover * 2                                 # 留牌散牌越少越好
                - len(cs) * 0.3                                # 出得多一点好
                + combo.main * 0.06                            # 小牌先走
                + combo.wild_used * WILD_PENALTY               # 省癞子
            )
            scored.append((score, cs))
        if not scored:
            # 兜底：出最小单张
            smallest = C.sort_cards(hand)[0]
            return [smallest]
        scored.sort(key=lambda t: t[0])
        return scored[0][1]

    # 跟牌
    # 农民不压队友（除非自己一把走完）
    if role == "farmer" and last_role == "farmer":
        finish = [cs for cs in gen_responses(hand, wild_ranks, prev) if len(cs) == len(hand)]
        if finish:
            return finish[0]
        return None

    resps = gen_responses(hand, wild_ranks, prev)
    if not resps:
        return None
    # 一手走完直接出
    finish = [cs for cs in resps if len(cs) == len(hand)]
    if finish:
        return finish[0]

    # 地主/农民通用：避免最后两张都是大牌时乱炸，先按小压原则
    normal, bombs = [], []
    for cs in resps:
        combo, err = P.choose(cs, wild_ranks, prev)
        if combo is None or err:
            continue
        if combo.kind in (P.BOMB, P.ROCKET):
            bombs.append((cs, combo))
        else:
            normal.append((cs, combo))
    # 对家剩余牌数少 → 压制优先（用较大牌）
    opp_low = False
    if role == "farmers_side":
        opp_low = False
    if role == "landlord":
        opp_low = any(v <= 2 for v in counts.values())
    else:
        opp_low = landlord_count is not None and landlord_count <= 2

    if normal:
        scored = []
        for cs, combo in normal:
            keep = [c for c in hand if c not in cs]
            leftover = _leftover_singles(keep, wild_ranks)
            score = (
                combo.main * 1.0
                + combo.wild_used * WILD_PENALTY
                + leftover * 0.8
            )
            if opp_low:
                score -= combo.main * 0.5   # 对家要跑：尽量大
            scored.append((score, cs, combo))
        scored.sort(key=lambda t: t[0])
        best = scored[0]
        # 明显亏牌（例如拆掉对子纯亏）时，宁可 pass —— 仅当自己是农民且要不起大牌时
        if opp_low and best[2].main < 14 and bombs:
            # 对家快赢，普通牌压不住大牌，考虑炸弹
            bomb_card = _best_bomb(bombs, hand, wild_ranks)
            if bomb_card is not None:
                return bomb_card[0]
        return best[1]

    if bombs:
        # 无普通牌可压：牌局紧要（对家剩 <=2 或自己 <=5 张）才炸
        bomb_card = _best_bomb(bombs, hand, wild_ranks)
        urgent = opp_low or len(hand) <= 5
        if urgent and bomb_card is not None:
            return bomb_card[0]
    return None


def _best_bomb(bombs, hand, wild_ranks):
    if not bombs:
        return None
    def key(t):
        cs, combo = t
        return (combo.tier, combo.main, combo.wild_used)
    return sorted(bombs, key=key)[0]


def _leftover_singles(hand: Sequence[str], wild_ranks: Set[str]) -> int:
    """粗估剩下的"难出"小组数：单张 + 对子 + 三张 各算不同。"""
    groups, wilds = _split(hand, wild_ranks)
    n = 0
    for r, cs in groups.items():
        c = len(cs)
        if c == 1:
            n += 1
        elif c == 2:
            n += 1
        elif c == 3:
            n += 1
        elif c == 4:
            n += 0
    if C.JOKER_SMALL in hand and C.JOKER_BIG in hand:
        n -= 1
    return max(n, 1 if hand else 0)


def suggest_hint(hand: Sequence[str], wild_ranks: Set[str], prev: Optional[P.Combo] = None) -> Optional[List[str]]:
    """给人类玩家的提示：返回一个有意义的候选。"""
    if prev is None:
        leads = gen_leads(hand, wild_ranks)
        if not leads:
            return None
        scored = []
        for cs in leads:
            combo, err = P.choose(cs, wild_ranks, None)
            if combo is None or err:
                continue
            scored.append((combo.main, -len(cs), cs))
        scored.sort(key=lambda t: (t[0], t[1]))
        return scored[0][2] if scored else None
    resps = gen_responses(hand, wild_ranks, prev)
    if not resps:
        return None
    scored = []
    for cs in resps:
        combo, err = P.choose(cs, wild_ranks, prev)
        if combo is None or err:
            continue
        scored.append((combo.main, combo.wild_used, cs))
    scored.sort(key=lambda t: (t[0], t[1]))
    return scored[0][2] if scored else None
