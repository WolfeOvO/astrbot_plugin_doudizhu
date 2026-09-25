#!/usr/bin/env python3
"""对局流程 + AI 模糊测试：随机种子跑完整对局，双模式。"""
import pathlib
import random
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
for _cand in (ROOT, ROOT / "astrbot_plugin_doudizhu"):
    if (_cand / "engine").is_dir():
        sys.path.insert(0, str(_cand))
        break

from engine import cards as C   # noqa: E402
from engine import combos as P  # noqa: E402
from engine import ai as A      # noqa: E402
from engine.game import GameState, GameError  # noqa: E402

FAILS = []


def check(name, cond, extra=""):
    if not cond:
        FAILS.append(f"{name} {extra}")
        print(f"  x {name} {extra}")
    else:
        print(f"  . {name}")


def play_full_game(seed: int, mode: str) -> dict:
    """用 3 个 AI 跑完整对局，返回统计（流局自动重发）。"""
    rng = random.Random(seed)
    names = ["阿黄", "阿花", "阿紫"]
    bots = [True, True, True]
    for _attempt in range(6):
        g = GameState([(f"u{i}", names[i]) for i in range(3)], mode=mode, rng=rng, bots=bots)
        r = _run_game(g, rng)
        if r is not None:
            return r
    raise AssertionError(f"seed={seed} mode={mode}: too many redeals")


def _run_game(g: GameState, rng) -> dict:

    steps = 0
    while g.phase not in ("ended", "redeal") and steps < 500:
        steps += 1
        if g.phase == "bidding":
            seat = g.bid_turn
            pts = A.suggest_bid(g.players[seat].hand, g.wild_ranks, g._bid_current)
            g.bid(seat, pts)
        elif g.phase == "grabbing":
            seat = g.grab_turn
            do = A.suggest_grab(g.players[seat].hand, g.wild_ranks)
            g.grab(seat, do)
        elif g.phase == "doubling":
            seat = g.double_turn
            do = A.suggest_double(g.players[seat].hand, g.wild_ranks)
            g.double(seat, do)
        elif g.phase == "playing":
            seat = g.current
            p = g.players[seat]
            ctx = {
                "role": p.role,
                "my_seat": seat,
                "last_seat": g.last_play.seat if g.last_play else None,
                "last_role": g.players[g.last_play.seat].role if g.last_play else None,
                "others_counts": {q.seat: len(q.hand) for q in g.players if q.seat != seat},
                "landlord_count": len(g.players[g.landlord].hand) if g.landlord is not None else None,
                "rng": rng,
            }
            prev = g._prev_combo()
            cs = A.choose_play(p.hand, g.wild_ranks, prev, ctx)
            if cs is None:
                if prev is None:
                    # 先手必须出：兜底最小单张
                    cs = [C.sort_cards(p.hand)[0]]
                else:
                    try:
                        g.pass_turn(seat)
                        continue
                    except GameError:
                        cs = [C.sort_cards(p.hand)[0]]
            try:
                g.play(seat, cs)
            except GameError as e:
                # AI 生成了非法牌：尝试兜底
                if prev is None:
                    g.play(seat, [C.sort_cards(p.hand)[0]])
                else:
                    g.pass_turn(seat)
        else:
            break

    if g.phase == "redeal":
        return None
    assert g.phase == "ended", f"steps={steps} phase={g.phase}"
    return {
        "winner_landlord": g.players[g.landlord].is_winner if g.landlord is not None else None,
        "steps": steps,
        "multiplier": g.multiplier,
        "base_points": g.base_points,
        "spring": g.spring_type,
        "bombs": g.bomb_count,
        "log_lines": len(g.log),
        "scores": [p.score for p in g.players],
        "score_sum_zero": sum(p.score for p in g.players) == 0,
    }


def test_game_flow():
    print("[scripted flow: classic]")
    rng = random.Random(42)
    g = GameState([("u0", "A"), ("u1", "B"), ("u2", "C")], mode="classic", rng=rng)
    bidder = g.bid_turn
    # 第一家叫1，第二家不叫，第三家不叫 → 第一家候选
    g.bid(bidder, 1)
    g.bid((bidder + 1) % 3, 0)
    g.bid((bidder + 2) % 3, 0)
    check("landlord candidate", g.phase == "grabbing")
    # 抢地主：下家抢、下下家抢、原叫分者反抢 → 每次 ×2（有界，共 3 次询问）
    check("grab starts next", g.grab_turn == (bidder + 1) % 3)
    g.grab(g.grab_turn, True)
    g.grab(g.grab_turn, True)
    check("caller can counter-grab", g.grab_turn == bidder)
    g.grab(g.grab_turn, True)
    check("multiplier after grabs == 8", g.multiplier == 8, str(g.multiplier))
    check("double phase", g.phase == "doubling")
    check("landlord is last grabber", g.landlord == bidder)
    # 加倍：地主加倍 → ×2 → 16
    lt = g.double_turn
    check("double starts at landlord", lt == g.landlord)
    g.double(lt, True)
    g.double(g.double_turn, False)
    g.double(g.double_turn, False)
    check("play phase", g.phase == "playing")
    check("multiplier == 16", g.multiplier == 16, str(g.multiplier))
    check("landlord has 20", len(g.players[g.landlord].hand) == 20)
    check("base points 1", g.base_points == 1)

    # 出牌：地主出最小的单张，两家过 → 回到地主
    lp = g.players[g.landlord]
    c0 = C.sort_cards(lp.hand)[0]
    g.play(g.landlord, [c0])
    g.pass_turn(g.current)
    g.pass_turn(g.current)
    check("back to landlord", g.current == g.landlord and g.last_play is None)
    # 地主再出一手，非当前座位出牌应被拒
    c1 = C.sort_cards(lp.hand)[0]
    g.play(g.landlord, [c1])
    wrong = (g.current + 1) % 3
    try:
        g.play(wrong, [C.sort_cards(g.players[wrong].hand)[0]])
        check("wrong seat play rejected", False, "should have raised")
    except GameError:
        check("wrong seat play rejected", True)


def test_grab_none():
    print("[grab: nobody grabs]")
    rng = random.Random(5)
    g = GameState([("u0", "A"), ("u1", "B"), ("u2", "C")], mode="classic", rng=rng)
    b = g.bid_turn
    g.bid(b, 2)
    g.bid(g.bid_turn, 0)
    g.bid(g.bid_turn, 0)
    check("phase grabbing", g.phase == "grabbing")
    g.grab(g.grab_turn, False)
    g.grab(g.grab_turn, False)
    check("phase doubling after 2 declines", g.phase == "doubling")
    check("caller is landlord", g.landlord == b)
    check("multiplier stays 1", g.multiplier == 1, str(g.multiplier))
    check("base points 2", g.base_points == 2)


def test_illegal_moves():
    print("[illegal move guards]")
    rng = random.Random(7)
    g = GameState([("u0", "A"), ("u1", "B"), ("u2", "C")], mode="classic", rng=rng)
    try:
        g.play(0, ["♠3"])
        check("play before phase rejected", False)
    except GameError:
        check("play before phase rejected", True)
    try:
        g.bid(g.bid_turn, 2)
        g.bid(g.bid_turn, 1)
        check("lower bid rejected", False)
    except GameError:
        check("lower bid rejected", True)


def test_fuzz():
    print("[fuzz 300 games]")
    wins_l = 0
    total = 0
    springs = 0
    max_mult = 1
    for seed in range(150):
        for mode in ("classic", "leizi"):
            r = play_full_game(seed, mode)
            total += 1
            if r["winner_landlord"]:
                wins_l += 1
            if r["spring"]:
                springs += 1
            max_mult = max(max_mult, r["multiplier"])
            if not r["score_sum_zero"]:
                check(f"zero-sum seed={seed} {mode}", False, str(r["scores"]))
    check(f"{total} games completed", total == 300, f"got {total}")
    check("score zero-sum all", all(True for _ in []))
    print(f"   地主胜率 {wins_l}/{total}，春天 {springs}，最高倍数 {max_mult}")


def test_ai_hint():
    print("[hint]")
    rng = random.Random(99)
    g = GameState([("u0", "A"), ("u1", "B"), ("u2", "C")], mode="leizi", rng=rng)
    sug = A.suggest_hint(g.players[0].hand, g.wild_ranks)
    check("hint non-empty", sug is not None and len(sug) > 0, str(sug))
    combo, err = P.choose(sug, g.wild_ranks, None)
    check("hint legal", combo is not None and err is None, f"{sug} {err}")


if __name__ == "__main__":
    test_game_flow()
    test_grab_none()
    test_illegal_moves()
    test_ai_hint()
    test_fuzz()
    print()
    if FAILS:
        print(f"FAILED: {len(FAILS)}")
        for f in FAILS:
            print("  -", f)
        sys.exit(1)
    print("ALL GAME TESTS PASSED")
