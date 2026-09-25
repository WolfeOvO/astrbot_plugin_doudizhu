"""乐豆经济 + 战绩 + 群配置持久化。

存储: data/plugin_data/astrbot_plugin_doudizhu/economy.json
结构:
{
  "users": {
    "12345": {"beans": 2000, "last_sign": "2026-09-25", "sign_streak": 3,
               "wins": 1, "losses": 2, "games": 3, "landlord_wins": 1,
               "max_mult": 64, "last_relief": "2026-09-20"}
  },
  "groups": {
    "999": {"enabled": true, "mode": "classic", "base": 100, "timeout": 45,
             "allow_bot": true, "min_beans": 100, "sign_bonus": 500, "relief": 500}
  }
}
"""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Dict, Optional

DEFAULT_USER = {
    "name": "", "beans": 2000, "last_sign": "", "sign_streak": 0,
    "wins": 0, "losses": 0, "games": 0, "landlord_wins": 0,
    "farmer_wins": 0, "max_mult": 1, "last_relief": "",
}

DEFAULT_GROUP = {
    "enabled": True,
    "mode": "classic",     # classic / leizi / noshuffle / speed
    "base": 100,           # 每 1 分底分对应乐豆
    "timeout": 45,         # 出牌/叫分超时秒数
    "allow_bot": True,     # 人不够时是否允许 AI 补位（人机模式）
    "min_beans": 100,      # 入场最低乐豆
    "sign_bonus": 500,     # 每日签到基础奖励
    "relief": 500,         # 救济金数额
    "relief_floor": 500,   # 低于此数才能领救济
}


class Economy:
    def __init__(self, data_dir: str):
        self.path = os.path.join(data_dir, "economy.json")
        self._lock = threading.Lock()
        self.data = {"users": {}, "groups": {}}
        self.load()

    def load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                d = json.load(f)
            self.data["users"] = d.get("users", {})
            self.data["groups"] = d.get("groups", {})
        except (FileNotFoundError, json.JSONDecodeError):
            pass

    def save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)

    # ---------------- 用户 ----------------
    def user(self, uid: str) -> Dict[str, Any]:
        u = self.data["users"].get(str(uid))
        if u is None:
            u = dict(DEFAULT_USER)
            self.data["users"][str(uid)] = u
        for k, v in DEFAULT_USER.items():
            u.setdefault(k, v)
        return u

    def beans(self, uid: str) -> int:
        return int(self.user(uid)["beans"])

    def add_beans(self, uid: str, delta: int) -> int:
        with self._lock:
            u = self.user(uid)
            u["beans"] = max(0, int(u["beans"]) + int(delta))
            self.save()
            return u["beans"]

    def settle(self, deltas: Dict[str, int]):
        """一次性结算多人（对局结束时）。"""
        with self._lock:
            for uid, delta in deltas.items():
                u = self.user(uid)
                u["beans"] = max(0, int(u["beans"]) + int(delta))
            self.save()

    def record_game(self, uid: str, won: bool, is_landlord: bool, mult: int,
                    name: str = ""):
        with self._lock:
            u = self.user(uid)
            if name:
                u["name"] = name
            u["games"] += 1
            if won:
                u["wins"] += 1
                if is_landlord:
                    u["landlord_wins"] += 1
                else:
                    u["farmer_wins"] += 1
            else:
                u["losses"] += 1
            u["max_mult"] = max(int(u.get("max_mult", 1)), int(mult))
            self.save()

    def sign_in(self, uid: str, date_str: str, bonus: int) -> Dict[str, Any]:
        """返回 {"already": bool, "beans": int, "streak": int, "reward": int}"""
        with self._lock:
            u = self.user(uid)
            if u["last_sign"] == date_str:
                return {"already": True, "beans": u["beans"], "streak": u["sign_streak"], "reward": 0}
            if _yesterday(date_str) == u.get("last_sign"):
                u["sign_streak"] = int(u["sign_streak"]) + 1
            else:
                u["sign_streak"] = 1
            streak_bonus = min(int(u["sign_streak"]) - 1, 6) * 100  # 连签每天 +100，封顶 +600
            reward = bonus + streak_bonus
            u["beans"] = int(u["beans"]) + reward
            u["last_sign"] = date_str
            self.save()
            return {"already": False, "beans": u["beans"], "streak": u["sign_streak"], "reward": reward}

    def claim_relief(self, uid: str, date_str: str) -> Dict[str, Any]:
        with self._lock:
            u = self.user(uid)
            g = self.data["groups"]
            floor = 500
            reward = 500
            for conf in g.values():
                floor = min(floor, int(conf.get("relief_floor", 500)))
                reward = max(reward, int(conf.get("relief", 500)))
            if u["last_relief"] == date_str:
                return {"ok": False, "reason": "今天已经领过了，明天再来吧", "beans": u["beans"]}
            if int(u["beans"]) >= floor:
                return {"ok": False, "reason": f"乐豆不少于 {floor}，不能领救济～", "beans": u["beans"]}
            u["beans"] = int(u["beans"]) + reward
            u["last_relief"] = date_str
            self.save()
            return {"ok": True, "beans": u["beans"], "reward": reward}

    def leaderboard(self, top: int = 10):
        users = {k: v for k, v in self.data["users"].items()
                 if not str(k).startswith("__bot")}
        ranked = sorted(users.items(), key=lambda kv: -int(kv[1].get("beans", 0)))
        return ranked[:top]

    # ---------------- 群配置 ----------------
    def group_conf(self, gid: str) -> Dict[str, Any]:
        g = self.data["groups"].get(str(gid))
        if g is None:
            g = dict(DEFAULT_GROUP)
            self.data["groups"][str(gid)] = g
        for k, v in DEFAULT_GROUP.items():
            g.setdefault(k, v)
        return g

    def set_group_conf(self, gid: str, **kw):
        with self._lock:
            g = self.group_conf(gid)
            for k, v in kw.items():
                if k in DEFAULT_GROUP:
                    g[k] = v
            self.save()

    # ---------------- 群活跃查询 ----------------
    def active_groups(self):
        return list(self.data["groups"].keys())


def _yesterday(date_str: str) -> str:
    import datetime
    d = datetime.date.fromisoformat(date_str) - datetime.timedelta(days=1)
    return d.isoformat()


def today_str() -> str:
    """UTC+8 日期字符串。"""
    import datetime
    return (datetime.datetime.utcnow() + datetime.timedelta(hours=8)).date().isoformat()
