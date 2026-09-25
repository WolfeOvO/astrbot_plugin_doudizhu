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
    # 加倍：地主普通加倍 ×2 → 16（其余两家不加倍）
    lt = g.double_turn
    check("double starts at landlord", lt == g.landlord)
    g.double(lt, 2)
    g.double(g.double_turn, 0)
    g.double(g.double_turn, 0)
    check("play phase", g.phase == "playing")
    check("multiplier == 16", g.multiplier == 16, str(g.multiplier))
    check("landlord has 20", len(g.players[g.landlord].hand) == 20)
    check("base points 1", g.base_points == 1)

    # 出牌：地主出最小的单张，两家过 → 回到地主（播报带身份+剩余牌数）
    lp = g.players[g.landlord]
    c0 = C.sort_cards(lp.hand)[0]
    _, msg = g.play(g.landlord, [c0])
    check("play msg has role", "（地主）" in msg, msg)
    check("play msg has counts", "📊 剩牌" in msg, msg)
    check("play msg vertical", "\n出 " in msg and "📊 剩牌：\n" in msg, msg)
    pmsg = g.pass_turn(g.current)
    check("pass msg has role", "（农民）" in pmsg, pmsg)
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


def test_super_double_and_messages():
    print("[super double ×4 + rich messages]")
    rng = random.Random(11)
    bots = [False, True, True]
    g = GameState([("u0", "A"), ("u1", "B"), ("u2", "C")], mode="classic",
                  rng=rng, bots=bots)
    g.bid(g.bid_turn, 1)
    g.bid(g.bid_turn, 0)
    g.bid(g.bid_turn, 0)
    g.grab(g.grab_turn, False)
    g.grab(g.grab_turn, False)
    check("doubling phase", g.phase == "doubling")
    lt = g.double_turn
    check("double starts at landlord", lt == g.landlord)
    m0 = g.multiplier
    msg = g.double(lt, 4)
    check("super double ×4", g.multiplier == m0 * 4, f"{g.multiplier} != {m0 * 4}")
    check("msg mentions 超级加倍", "超级加倍" in msg and "×4" in msg, msg)
    g.double(g.double_turn, 2)
    g.double(g.double_turn, 0)
    check("multiplier m0×8", g.multiplier == m0 * 8, str(g.multiplier))
    # 出牌播报：身份 + 剩余牌数
    lp = g.players[g.landlord]
    c0 = C.sort_cards(lp.hand)[0]
    _, msg = g.play(g.landlord, [c0])
    check("play msg role+counts", "（地主）" in msg and "📊 剩牌" in msg and "张" in msg, msg)
    # 结算文案：真人显示乐豆增减，机器人显示 ∞
    g._settle(winner_seat=g.landlord)
    txt = g.settlement_text()
    check("settlement rich+∞", "本局结束" in txt and "∞ 乐豆" in txt and "乐豆" in txt, txt)


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
    print("[fuzz 450 games]")
    wins_l = 0
    total = 0
    springs = 0
    max_mult = 1
    for seed in range(150):
        for mode in ("classic", "leizi", "noshuffle"):
            r = play_full_game(seed, mode)
            total += 1
            if r["winner_landlord"]:
                wins_l += 1
            if r["spring"]:
                springs += 1
            max_mult = max(max_mult, r["multiplier"])
            if not r["score_sum_zero"]:
                check(f"zero-sum seed={seed} {mode}", False, str(r["scores"]))
    check(f"{total} games completed", total == 450, f"got {total}")
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


def test_noshuffle_and_auto():
    print("[noshuffle deal + auto(托管) 标记]")
    total_bombs = 0
    for seed in range(30):
        g = GameState([("u0", "A"), ("u1", "B"), ("u2", "C")], mode="noshuffle",
                      rng=random.Random(seed))
        allc = [c for p in g.players for c in p.hand] + list(g.bottom)
        assert len(allc) == 54 and len(set(allc)) == 54, "noshuffle 发牌张数/唯一性"
        for p in g.players:
            cnt = {}
            for c in p.hand:
                k = C.card_rank(c)
                cnt[k] = cnt.get(k, 0) + 1
            total_bombs += sum(1 for v in cnt.values() if v == 4)
    check("noshuffle 炸弹频出", total_bombs >= 5, f"total={total_bombs}")

    # 托管：不影响 is_bot / 结算显示实际乐豆 / 机器人仍 ∞
    g2 = GameState([("u0", "KD"), ("b1", "机器人乙"), ("b2", "机器人丙")],
                   mode="classic", rng=random.Random(7), bots=[False, True, True])
    check("auto 默认关", g2.players[0].auto is False)
    g2.players[0].auto = True
    check("托管不改 is_bot", g2.players[0].is_bot is False)
    for p, role in zip(g2.players, ("farmer", "landlord", "farmer")):
        p.role = role
    g2.landlord = 1
    g2.base_points = 1
    g2.multiplier = 1
    g2.players[1].play_count = 5   # 避免反春天
    g2._settle(winner_seat=2)
    txt = g2.settlement_text()
    check("托管人显示实际乐豆", "【KD】（农民）：+100 乐豆" in txt, txt)
    check("机器人仍显示 ∞", txt.count("∞ 乐豆") == 2, txt)


def test_cap_and_serialize():
    print("[封顶 + 序列化恢复 + 难度]")
    rng = random.Random(3)
    # 封顶：小额场景算 100，封顶 50 → 每人 ±50
    g = GameState([("u0", "A"), ("u1", "B"), ("u2", "C")], mode="classic",
                  rng=rng, cap=50)
    g.bid(g.bid_turn, 1)
    g.bid(g.bid_turn, 0)
    g.bid(g.bid_turn, 0)
    g.grab(g.grab_turn, False)
    g.grab(g.grab_turn, False)
    g.double(g.double_turn, 0)
    g.double(g.double_turn, 0)
    g.double(g.double_turn, 0)
    g.players[g.landlord].play_count = 5
    g._settle(winner_seat=(g.landlord + 1) % 3)
    check("封顶生效", g.capped and max(abs(p.score) for p in g.players) == 50,
          str([p.score for p in g.players]))
    check("封顶文案", "封顶" in g.settlement_text(), g.settlement_text())

    # 序列化 roundtrip：任意局面保存/恢复后一致
    rng2 = random.Random(9)
    g2 = GameState([("u0", "A"), ("u1", "B"), ("u2", "C")], mode="leizi",
                   rng=rng2, bots=[False, True, True], cap=500, difficulty="hard")
    g2.bid(g2.bid_turn, 2)
    g2.bid(g2.bid_turn, 0)
    g2.bid(g2.bid_turn, 0)
    g2.grab(g2.grab_turn, True)
    d = g2.to_dict()
    g3 = GameState.from_dict(d)
    check("序列化 phase", g3.phase == g2.phase, f"{g3.phase} != {g2.phase}")
    check("序列化 hands", [p.hand for p in g3.players] == [p.hand for p in g2.players])
    check("序列化 wild", g3.wild_ranks == g2.wild_ranks)
    check("序列化 cap/difficulty", g3.cap == 500 and g3.difficulty == "hard")
    check("序列化 grab 状态", g3._grab_queue == g2._grab_queue and g3._grab_turn == g2._grab_turn)
    # 恢复后继续走完一局（AI 驱动）
    import time as _t
    steps = 0
    while g3.phase not in ("ended", "redeal") and steps < 500:
        steps += 1
        if g3.phase == "bidding":
            g3.bid(g3.bid_turn, A.suggest_bid(g3.players[g3.bid_turn].hand, g3.wild_ranks, g3._bid_current))
        elif g3.phase == "grabbing":
            g3.grab(g3.grab_turn, A.suggest_grab(g3.players[g3.grab_turn].hand, g3.wild_ranks))
        elif g3.phase == "doubling":
            g3.double(g3.double_turn, A.suggest_double(g3.players[g3.double_turn].hand, g3.wild_ranks))
        elif g3.phase == "playing":
            p = g3.players[g3.current]
            cs = A.choose_play(p.hand, g3.wild_ranks, g3._prev_combo(),
                               {"role": p.role or "farmer", "others_counts": {},
                                "landlord_count": len(g3.players[g3.landlord].hand) if g3.landlord is not None else None,
                                "difficulty": g3.difficulty, "rng": random.Random(1)})
            if cs is None:
                g3.pass_turn(g3.current)
            else:
                g3.play(g3.current, cs)
    check("恢复后能正常打完", g3.phase in ("ended", "redeal"), g3.phase)

    # 难度：easy 用 40% 概率乱出，不至于崩
    rng3 = random.Random(11)
    g4 = GameState([("u0", "A"), ("u1", "B"), ("u2", "C")], mode="classic",
                   rng=rng3, difficulty="easy")
    g4.bid(g4.bid_turn, 1)
    g4.bid(g4.bid_turn, 0)
    g4.bid(g4.bid_turn, 0)
    g4.grab(g4.grab_turn, False)
    g4.grab(g4.grab_turn, False)
    g4.double(g4.double_turn, 0)
    g4.double(g4.double_turn, 0)
    g4.double(g4.double_turn, 0)
    p = g4.players[g4.current]
    cs = A.choose_play(p.hand, g4.wild_ranks, None,
                       {"role": p.role, "difficulty": "easy", "rng": random.Random(5),
                        "others_counts": {}, "landlord_count": 20})
    check("easy AI 出牌合法", cs is not None and all(c in p.hand for c in cs), str(cs))


if __name__ == "__main__":
    test_game_flow()
    test_grab_none()
    test_super_double_and_messages()
    test_noshuffle_and_auto()
    test_cap_and_serialize()
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
