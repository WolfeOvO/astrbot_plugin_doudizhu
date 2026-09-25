"""斗地主核心牌模型。

牌面字符串表示:
  普通牌: "<花色><点数>"   如 "♠3"、"♥10"、"♦K"、"♣A"
  王:    "小王" / "大王"

大小顺序: 3 < 4 < 5 < 6 < 7 < 8 < 9 < 10 < J < Q < K < A < 2 < 小王 < 大王
"""
from __future__ import annotations

import random
from typing import Iterable, List, Optional, Sequence, Tuple

SUITS: Tuple[str, ...] = ("♠", "♥", "♦", "♣")
RANKS: Tuple[str, ...] = ("3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A", "2")
JOKER_SMALL = "小王"
JOKER_BIG = "大王"
JOKERS: Tuple[str, str] = (JOKER_SMALL, JOKER_BIG)

CARD_VALUE = {r: i + 3 for i, r in enumerate(RANKS)}  # 3..15
CARD_VALUE[JOKER_SMALL] = 16
CARD_VALUE[JOKER_BIG] = 17
VALUE_CARD = {v: r for r, v in CARD_VALUE.items()}

_SUIT_ORDER = {s: i for i, s in enumerate(SUITS)}


def is_joker(card: str) -> bool:
    return card in JOKERS


def card_suit(card: str) -> Optional[str]:
    return None if is_joker(card) else card[0]


def card_rank(card: str) -> str:
    return card if is_joker(card) else card[1:]


def card_value(card: str) -> int:
    return CARD_VALUE[card_rank(card)]


def new_deck() -> List[str]:
    """返回一副新牌（54 张，未洗）。"""
    return [s + r for s in SUITS for r in RANKS] + [JOKER_SMALL, JOKER_BIG]


def shuffled_deck(rng: Optional[random.Random] = None) -> List[str]:
    deck = new_deck()
    (rng or random).shuffle(deck)
    return deck


def sort_cards(cards: Iterable[str], desc: bool = False) -> List[str]:
    return sorted(
        cards,
        key=lambda c: (card_value(c), _SUIT_ORDER.get(card_suit(c) or "", 9)),
        reverse=desc,
    )


def fmt_cards(cards: Sequence[str], sep: str = " ") -> str:
    if not cards:
        return "（无）"
    return sep.join(sort_cards(cards))


# ---------------------------------------------------------------------------
# 玩家输入解析
# ---------------------------------------------------------------------------

_SUIT_ALIASES = {
    "♠": "♠", "♤": "♠", "s": "♠", "S": "♠",
    "♥": "♥", "♡": "♥", "h": "♥", "H": "♥",
    "♦": "♦", "d": "♦", "D": "♦",
    "♣": "♣", "c": "♣", "C": "♣",
}
_CHINESE_SUITS = {"黑桃": "♠", "红桃": "♥", "方块": "♦", "梅花": "♣"}
_PRE_REPLACE = (("王炸", "wW"), ("双王", "wW"), ("火箭", "wW"), ("俩王", "wW"))
_STRIP_WORDS = ("三连对", "连对", "顺子", "飞机带对", "飞机带单", "飞机",
                "三带二", "三带一", "四带两对", "四带二")
_STRIP_CHARS = (" ", "\t", ",", "，", "、", "+", "＋", "；", ";", "-", "_")


def _read_rank(s: str, i: int) -> Tuple[Optional[str], int]:
    ch = s[i]
    if ch == "t":
        return "10", 1
    if ch in "3456789":
        return ch, 1
    if ch in "Jj":
        return "J", 1
    if ch in "Qq":
        return "Q", 1
    if ch in "Kk":
        return "K", 1
    if ch in "Aa":
        return "A", 1
    if ch == "2":
        return "2", 1
    if ch == "小":
        return JOKER_SMALL, 1
    if ch in ("大", "王"):
        return JOKER_BIG, 1
    if ch == "w":
        return JOKER_SMALL, 1
    if ch == "W":
        return JOKER_BIG, 1
    return None, 1


def tokenize(text: str) -> List[Tuple[Optional[str], str]]:
    """把玩家输入解析成 [(花色|None, 点数), ...] 序列。

    支持: "34567"、"3 4 5 6 7"、"♠3♥4"、"s3 h4"、"小王 大王"、"10JQKA2"、
          "黑桃3"、"王炸"/"双王"、"对3"/"一对3"/"两3"（对子）、"三个4"/"三张4"、
          "四个5"（炸弹）、"三连对"/"飞机带单"等描述词会被忽略。
    """
    s = str(text or "")
    for k, v in _PRE_REPLACE:
        s = s.replace(k, v)
    for w in _STRIP_WORDS:
        s = s.replace(w, "")
    s = s.replace("10", "t").replace("T", "t")
    for junk in _STRIP_CHARS:
        s = s.replace(junk, "")

    out: List[Tuple[Optional[str], str]] = []
    i, n = 0, len(s)
    while i < n:
        two = s[i:i + 2]
        if two in _CHINESE_SUITS:
            i += 2
            if i < n:
                r, adv = _read_rank(s, i)
                if r:
                    out.append((_CHINESE_SUITS[two], r))
                    i += adv
                    continue
            continue
        ch = s[i]
        if ch == "小" and s[i + 1:i + 2] == "王":
            out.append((None, JOKER_SMALL))
            i += 2
            continue
        if ch == "大" and s[i + 1:i + 2] == "王":
            out.append((None, JOKER_BIG))
            i += 2
            continue
        # 数量词前缀：对3 / 一对3 / 两3 / 两个4 / 三个4 / 三张4 / 四个5
        mc, j = 0, None
        if two == "一对":
            mc, j = 2, i + 2
        elif ch == "对":
            mc, j = 2, i + 1
        elif ch == "两":
            mc, j = 2, i + 1
            if s[j:j + 1] == "个":
                j += 1
        elif ch in ("三", "四") and s[i + 1:i + 2] in ("个", "张"):
            mc, j = (3 if ch == "三" else 4), i + 2
        if j is not None and j < n:
            r, adv = _read_rank(s, j)
            if r:
                out.extend([(None, r)] * mc)
                i = j + adv
                continue
        if ch in _SUIT_ALIASES:
            suit = _SUIT_ALIASES[ch]
            i += 1
            if i < n:
                r, adv = _read_rank(s, i)
                if r:
                    out.append((suit, r))
                    i += adv
                    continue
            continue
        r, adv = _read_rank(s, i)
        if r:
            out.append((None, r))
            i += adv
            continue
        i += 1
    return out


def resolve(text: str, hand: Sequence[str]) -> Tuple[Optional[List[str]], Optional[str]]:
    """把玩家输入解析为手中确切的牌。

    返回 (cards, error)。花色缺省时自动从手中挑选（优先按现有顺序）。
    """
    tokens = tokenize(text)
    if not tokens:
        return None, "没识别到牌～例：3 4 5 6 7 或 ♠3 ♥4 或 小王 大王"
    pool = list(hand)
    picked: List[str] = []
    for suit, rank in tokens:
        candidates = [
            c for c in pool
            if card_rank(c) == rank and (suit is None or card_suit(c) == suit)
        ]
        if not candidates:
            feat = f"{suit}{rank}" if suit else rank
            return None, f"手里没有 {feat}"
        c = candidates[0]
        pool.remove(c)
        picked.append(c)
    return sort_cards(picked), None
