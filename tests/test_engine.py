#!/usr/bin/env python3
"""引擎单元测试（宿主直接可跑：python3 tests/test_engine.py）"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
for _cand in (ROOT, ROOT / "astrbot_plugin_doudizhu"):
    if (_cand / "engine").is_dir():
        sys.path.insert(0, str(_cand))
        break

from engine import cards as C          # noqa: E402
from engine import combos as P         # noqa: E402

FAILS = []


def check(name, cond, extra=""):
    if not cond:
        FAILS.append(f"{name} {extra}")
        print(f"  x {name} {extra}")
    else:
        print(f"  . {name}")


def test_deck():
    print("[deck]")
    deck = C.new_deck()
    check("deck 54 unique", len(deck) == 54 and len(set(deck)) == 54)
    check("fmt order", C.fmt_cards(["♥3", "♠3"]) == "♠3 ♥3")


def test_detect_classic():
    print("[classic detect]")
    cases = [
        (["♠3"], P.SINGLE, 3, 1),
        (["♠3", "♥3"], P.PAIR, 3, 1),
        (["♠3", "♥3", "♦3"], P.TRIPLE, 3, 1),
        (["♠3", "♥3", "♦3", "♣4"], P.TRIPLE_ONE, 3, 1),
        (["♠3", "♥3", "♦3", "♣4", "♠4"], P.TRIPLE_PAIR, 3, 1),
        (["♠3", "♥3", "♦3", "♣3"], P.BOMB, 3, 1),
        (["小王", "大王"], P.ROCKET, 17, 1),
        (["♠3", "♠4", "♠5", "♠6", "♠7"], P.STRAIGHT, 7, 5),
        (["♠10", "♠J", "♠Q", "♠K", "♠A"], P.STRAIGHT, 14, 5),
        (["♠3", "♥3", "♠4", "♥4", "♠5", "♥5"], P.STRAIGHT_PAIR, 5, 3),
        (["♠3", "♥3", "♦3", "♠4", "♥4", "♦4"], P.PLANE, 4, 2),
        (["♠3", "♥3", "♦3", "♠4", "♥4", "♦4", "♠5", "♥6"], P.PLANE_ONE, 4, 2),
        (["♠3", "♥3", "♦3", "♠4", "♥4", "♦4", "♠5", "♥5", "♠6", "♥6"], P.PLANE_PAIR, 4, 2),
        (["♠3", "♥3", "♦3", "♣3", "♠4", "♥4"], P.FOUR_TWO_SINGLE, 3, 1),
        (["♠3", "♥3", "♦3", "♣3", "♠4", "♥4", "♠5", "♥5"], P.FOUR_TWO_PAIR, 3, 1),
    ]
    for cs, kind, main, ln in cases:
        combo = P.detect(cs)
        ok = combo is not None and combo.kind == kind and combo.main == main and combo.length == ln
        check(f"detect {' '.join(cs)}", ok, f"-> {combo}")
    neg = [["♠3", "♠4", "♠5", "♠6"], ["♠3", "♥4"], ["♠3", "♥3", "♦4"], ["♠3", "♥3", "♦4", "♣4"]]
    for cs in neg:
        check(f"neg {' '.join(cs)}", P.detect(cs) is None, f"-> {P.detect(cs)}")


def test_beats():
    print("[beats]")
    s3 = P.detect(["♠3"])
    s4 = P.detect(["♠4"])
    check("4>3", P.beats(s4, s3) and not P.beats(s3, s4))
    p3 = P.detect(["♠3", "♥3"])
    p4 = P.detect(["♠4", "♥4"])
    check("pair4>pair3", P.beats(p4, p3))
    check("pair3 !> single4", not P.beats(p3, s4))
    bomb = P.detect(["♠3", "♥3", "♦3", "♣3"])
    check("bomb>pair", P.beats(bomb, p4))
    rocket = P.detect(["小王", "大王"])
    check("rocket>bomb", P.beats(rocket, bomb) and not P.beats(bomb, rocket))
    st = P.detect(["♠3", "♠4", "♠5", "♠6", "♠7"])
    st2 = P.detect(["♥4", "♥5", "♥6", "♥7", "♥8"])
    st6 = P.detect(["♠4", "♠5", "♠6", "♠7", "♠8", "♠9"])
    check("straight+1 wins", P.beats(st2, st))
    check("diff len no", not P.beats(st6, st))
    check("free play", P.beats(st, None))


def test_parse():
    print("[parse]")
    check("34567", C.tokenize("34567") == [(None, '3'), (None, '4'), (None, '5'), (None, '6'), (None, '7')])
    check("10JQKA", C.tokenize("10JQKA") == [(None, '10'), (None, 'J'), (None, 'Q'), (None, 'K'), (None, 'A')])
    check("王炸", C.tokenize("王炸") == [(None, '小王'), (None, '大王')])
    check("♠3♥4", C.tokenize("♠3♥4") == [('♠', '3'), ('♥', '4')])
    check("s3 h4", C.tokenize("s3 h4") == [('♠', '3'), ('♥', '4')])
    hand = ["♠3", "♥3", "♦3", "♣4", "小王"]
    got, err = C.resolve("333", hand)
    check("resolve 333", err is None and got == ["♠3", "♥3", "♦3"], f"{got} {err}")
    got, err = C.resolve("小王", hand)
    check("resolve 小王", got == ["小王"])
    got, err = C.resolve("♥4", hand)
    check("resolve ♥4 missing", got is None and bool(err))
    got, err = C.resolve("5", hand)
    check("resolve 5 missing", got is None and bool(err))


def test_laizi():
    print("[leizi]")
    w = {"7"}
    interps = P.evaluate(["♠3", "♥3", "♦3", "♠7"], w)
    kinds = {(c.kind, c.main) for c in interps}
    check("3+wild -> soft bomb", (P.BOMB, 3) in kinds, str(kinds))
    interps = P.evaluate(["♠7", "♠8", "♠9", "♠10", "♠J"], w)
    kinds = {(c.kind, c.main, c.length) for c in interps}
    check("wild straight 11", (P.STRAIGHT, 11, 5) in kinds, str(kinds))
    check("wild straight 12", (P.STRAIGHT, 12, 5) in kinds, str(kinds))
    interps = P.evaluate(["♠5", "♥5", "♠7", "♥7"], w)
    kinds = {(c.kind, c.main) for c in interps}
    check("2+2wild -> bomb5", (P.BOMB, 5) in kinds, str(kinds))
    interps = P.evaluate(["小王", "♠7"], w)
    check("no pair-joker fill", not any(c.kind == P.PAIR and c.main == 16 for c in interps), str(interps))
    interps = P.evaluate(["♠7", "♥7"], w)
    check("pair of leizi natural",
          len(interps) == 1 and interps[0].kind == P.PAIR and interps[0].main == 7 and interps[0].wild_used == 0,
          str(interps))
    prev = P.detect(["♠6", "♠7", "♠8", "♠9", "♠10"])
    combo, err = P.choose(["♠7", "♠8", "♠9", "♠10", "♠J"], w, prev=prev)
    check("choose beats straight", err is None and combo is not None and P.beats(combo, prev), f"{combo} {err}")
    interps = P.evaluate(["♠3", "♥3", "♠4", "♥4", "♠7"], w)
    kinds = {(c.kind, c.main) for c in interps}
    check("wild triple_pair", (P.TRIPLE_PAIR, 3) in kinds, str(kinds))


def test_bomb_tiers():
    print("[bomb tiers]")
    w = {"7"}
    soft = [c for c in P.evaluate(["♠3", "♥3", "♦3", "♠7"], w) if c.kind == P.BOMB]
    check("soft tier", soft and soft[0].tier == P.T_SOFT, str(soft))
    hard = P.detect(["♠3", "♥3", "♦3", "♣3"])
    check("hard tier", hard.tier == P.T_HARD)
    pure = [c for c in P.evaluate(["♠7", "♥7", "♦7", "♣7"], w) if c.kind == P.BOMB]
    check("pure tier", pure and pure[0].tier == P.T_PURE and pure[0].wild_used == 0, str(pure))
    rocket = P.detect(["小王", "大王"])
    check("rocket tier", rocket.tier == P.T_ROCKET)
    check("pure > hard", bool(pure) and P.beats(pure[0], hard))
    check("rocket > pure", bool(pure) and P.beats(rocket, pure[0]))
    check("hard > soft", bool(soft) and P.beats(hard, soft[0]))
    check("soft !> hard", bool(soft) and not P.beats(soft[0], hard))




def test_wings_strict():
    print("[wings strict / jokers]")
    w = {"7"}
    interps = P.evaluate(["♠3", "♥3", "♦3", "♠4", "♥4", "♦4", "♠5", "♥5", "♦5", "♠7"], w)
    check("plane-pair rejects 3+1 same-rank wings", not any(c.kind == P.PLANE_PAIR for c in interps),
          str([c.text(False) for c in interps]))
    interps = P.evaluate(["♠4", "♥4", "♦4", "♣4", "♠5", "♥5", "♦5", "♠7"], w)
    check("four2-pair rejects 3+1 same-rank wings", not any(c.kind == P.FOUR_TWO_PAIR for c in interps),
          str([c.text(False) for c in interps]))
    interps = P.evaluate(["♠3", "♥3", "♦3", "小王", "♠7"], w)
    check("triple-pair rejects joker+wild", not any(c.kind == P.TRIPLE_PAIR for c in interps),
          str([c.text(False) for c in interps]))
    interps = P.evaluate(["♠3", "♥3", "♦3", "♠5", "♠7"], w)
    check("triple-pair allows 5+wild -> 55", any(c.kind == P.TRIPLE_PAIR for c in interps),
          str([c.text(False) for c in interps]))


if __name__ == "__main__":
    test_deck()
    test_detect_classic()
    test_beats()
    test_parse()
    test_laizi()
    test_bomb_tiers()
    test_wings_strict()
    print()
    if FAILS:
        print(f"FAILED: {len(FAILS)}")
        for f in FAILS:
            print("  -", f)
        sys.exit(1)
    print("ALL TESTS PASSED")
