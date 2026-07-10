#!/usr/bin/env python3
"""
对局分析工具：拉取最近 n 场真实对战，解析后写入 SQLite。

用法:
  python match_analyzer.py -n 50
  python match_analyzer.py -n 100 --only demo-shield,demo-overload
  python match_analyzer.py --match-id mat_xxx          # 按对局 id 单次拉取入库
  python match_analyzer.py -m https://agentank.ai/history/mat_xxx
  python match_analyzer.py --match-id mat_xxx --only demo-shield  # 指定我方视角
  python match_analyzer.py --db analysis/data.db --serve
  python match_analyzer.py --no-fetch --serve
  python match_analyzer.py --reset-db          # 删除 analysis/data.db 并重建空库
  python match_analyzer.py --reset-db --cache  # 同时删除 .cache/matches/

Profile gap 推断读 bt_profile_expectations.json（骨架默认为空；示例见
bt_profile_expectations.example.json）。
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime

from analysis_db import AnalysisDB
from tank_profiles import DEFAULT_TANK, TANK_PROFILES, get_tank_keys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE_URL = "https://agentank.ai"
DEFAULT_N = 5
DEFAULT_DELAY = 0.3
DEFAULT_DB = os.path.join("analysis", "data.db")
DEFAULT_CACHE = os.path.join(".cache", "matches")
PROFILE_EXPECTATIONS_PATH = os.path.join(os.path.dirname(__file__), "bt_profile_expectations.json")
# 平台对局 id：mat_ + 字母数字；也兼容 history / api URL
MATCH_ID_RE = re.compile(r"(mat_[A-Za-z0-9]+)")


def load_profile_expectations():
    """加载 Profile 期望配置，供负场 gap 标签推断。"""
    if not os.path.exists(PROFILE_EXPECTATIONS_PATH):
        return {}
    with open(PROFILE_EXPECTATIONS_PATH, encoding="utf-8") as f:
        return json.load(f)


PROFILE_EXPECTATIONS = None


def get_profile_expectations():
    global PROFILE_EXPECTATIONS
    # 每次读取最新 gap_rules（reanalyze 时配置文件可能已更新）
    PROFILE_EXPECTATIONS = load_profile_expectations()
    return PROFILE_EXPECTATIONS


def load_dotenv():
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(env_path):
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    os.environ.setdefault(k.strip(), v.strip())


def api_request(method, path, tank_key=None, body=None, retries=1):
    url = BASE_URL + path
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {tank_key}",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json, text/plain, */*",
    }
    data = json.dumps(body).encode("utf-8") if body else None
    last_err = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, data=data, headers=headers, method=method)
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.HTTPError, urllib.error.URLError) as e:
            last_err = e
            if attempt < retries:
                time.sleep(1)
    raise last_err


def fetch_cached(path_suffix, cache_dir, no_cache, fetch_fn):
    """磁盘缓存 JSON，避免重复请求同一 match。"""
    os.makedirs(cache_dir, exist_ok=True)
    safe_name = path_suffix.replace("/", "_").replace("?", "_")
    cache_path = os.path.join(cache_dir, safe_name + ".json")
    if not no_cache and os.path.exists(cache_path):
        with open(cache_path, encoding="utf-8") as f:
            return json.load(f)
    data = fetch_fn()
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    return data


def fetch_tank_context(tank_key):
    return api_request("GET", "/api/agent/tank", tank_key=tank_key)


def fetch_match_list(tank_key, limit, offset):
    data = api_request("GET", f"/api/agent/tank/matches?limit={limit}&offset={offset}", tank_key=tank_key)
    if isinstance(data, list):
        return data, False
    return data.get("matches", []), data.get("hasMore", False)


def fetch_agent_json(match_url_id, view=None, tank_key=None, cache_dir=DEFAULT_CACHE, no_cache=False, delay=0):
    suffix = f"matches_{match_url_id}" + (f"_{view}" if view else "")
    path = f"/api/matches/{match_url_id}/agent.json"
    if view:
        path += f"?view={view}"

    def _fetch():
        if delay:
            time.sleep(delay)
        return api_request("GET", path, tank_key=tank_key)

    return fetch_cached(suffix, cache_dir, no_cache, _fetch)


def fetch_opponent_info(tank_key, query, delay=0):
    if delay:
        time.sleep(delay)
    data = api_request("GET", f"/api/agent/opponents?q={urllib.request.quote(query)}&limit=12", tank_key=tank_key)
    return data.get("opponents", [])


def normalize_match_id(raw):
    """接受 mat_xxx 或 history/API URL，提取 match_url_id；无效则返回 None。"""
    if not raw:
        return None
    m = MATCH_ID_RE.search(str(raw).strip())
    return m.group(1) if m else None


def participant_names(summary):
    """从 agent.json summary 取出双方坦克名。"""
    participants = summary.get("participants", {})
    names = [
        participants.get("challenger", {}).get("tankName"),
        participants.get("defender", {}).get("tankName"),
    ]
    return [n for n in names if n]


def resolve_entry_for_match(summary, tank_keys, only=None):
    """
    按对局双方匹配 profile 中的我方坦克。
    --only 可强制指定视角（镜像局或名字歧义时）；匹配失败抛 ValueError。
    """
    names = participant_names(summary)
    if not names:
        raise ValueError("对局 summary 缺少 participants，无法识别双方坦克")
    by_name = {e["name"]: e for e in tank_keys}

    if only:
        preferred = [n.strip() for n in only.split(",") if n.strip()]
        for pref in preferred:
            if pref in names and pref in by_name:
                return by_name[pref]
        raise ValueError(
            f"--only 指定的坦克不在对局中: {', '.join(preferred)}；对局双方: {', '.join(names)}"
        )

    matched = [by_name[n] for n in names if n in by_name]
    if len(matched) == 1:
        return matched[0]
    if len(matched) > 1:
        # 镜像局：优先 challenger，保证视角稳定可复现
        ch_name = (summary.get("participants") or {}).get("challenger", {}).get("tankName")
        if ch_name in by_name:
            return by_name[ch_name]
        return matched[0]

    # 不在 profile 时：无自动兜底，避免误绑错误 Bearer key
    raise ValueError(
        f"无法识别我方坦克；对局双方: {', '.join(names)}；请用 --only 指定 profile 中的名字"
    )


def base_from_summary(summary, match_url_id, my_tank_name, my_skill):
    """无 matches 列表条目时，从 agent.json summary 拼出 ingest 所需 base。"""
    participants = summary.get("participants", {})
    ch = participants.get("challenger", {})
    de = participants.get("defender", {})
    match_info = summary.get("match", {}) or {}
    if ch.get("tankName") == my_tank_name:
        enemy_name = de.get("tankName", "?")
        enemy_id = de.get("tankId")
    else:
        enemy_name = ch.get("tankName", "?")
        enemy_id = ch.get("tankId")
    result_reason = match_info.get("resultReason")
    draw = result_reason == "tie"
    winner = match_info.get("winnerTankName")
    won = bool(winner == my_tank_name) and not draw
    return {
        "match_url_id": match_url_id,
        "won": won,
        "draw": draw,
        "opponent_name": enemy_name or "?",
        "opponent_id": enemy_id,
        "map_id": match_info.get("mapId"),
        "result_reason": result_reason,
        "created_at": match_info.get("createdAt") or summary.get("createdAt"),
        "history_url": f"{BASE_URL}/history/{match_url_id}",
        "my_tank_name": my_tank_name,
        "my_skill": my_skill,
    }


def normalize_list_match(raw, my_tank_id, my_tank_name):
    """从 matches 列表条目提取基础字段。"""
    url_id = raw.get("matchUrlId") or raw.get("urlId") or ""
    won = raw.get("won")
    if won is None:
        winner_id = raw.get("winnerTankId")
        if winner_id is not None and my_tank_id is not None:
            won = winner_id == my_tank_id
        else:
            won = False
    draw = raw.get("draw") or raw.get("resultReason") == "tie"
    opponent_name = raw.get("opponentName")
    opponent_id = raw.get("opponentTankId")
    if not opponent_name:
        ch_id = raw.get("challengerTankId")
        de_id = raw.get("defenderTankId")
        if ch_id == my_tank_id:
            opponent_name = raw.get("defenderTankName", "?")
            opponent_id = opponent_id or de_id
        else:
            opponent_name = raw.get("challengerTankName", "?")
            opponent_id = opponent_id or ch_id
    return {
        "match_url_id": url_id,
        "won": bool(won) and not draw,
        "draw": bool(draw),
        "opponent_name": opponent_name or "?",
        "opponent_id": opponent_id,
        "map_id": raw.get("mapId"),
        "result_reason": raw.get("resultReason"),
        "created_at": raw.get("createdAt"),
        "history_url": f"{BASE_URL}/history/{url_id}" if url_id else None,
        "my_tank_name": my_tank_name,
    }


def _stats_from_summary(tank_stats, frames_total):
    if not tank_stats:
        return {}
    return {
        "frames_total": frames_total,
        "shots_fired": tank_stats.get("shotsFired"),
        "shots_hit": tank_stats.get("shotsHit"),
        "shots_wall": tank_stats.get("shotsWall"),
        "moves": tank_stats.get("moves"),
        "turns": tank_stats.get("turns"),
        "stars": tank_stats.get("stars"),
        "skill_used": tank_stats.get("skillUsed"),
        "crashes": tank_stats.get("crashes"),
        "runtime_ms": tank_stats.get("runtimeMs"),
        "diagnosis": tank_stats.get("diagnosis"),
    }


def parse_events(events, my_name, enemy_name, won, draw=False, result_reason=None, me_stats=None):
    """从 events 流推断技能、打法标签、胜/负场战术、BT 层级与 Profile gap。"""
    if not events:
        return {"has_events": False}

    me_stats = me_stats or {}
    my_skill = None
    enemy_skill = None
    my_first_skill = None
    enemy_first_skill = None
    death_frame = None
    my_stars = 0
    my_fires = 0
    my_moves = 0
    my_turns = 0
    enemy_fires = 0
    enemy_moves = 0
    enemy_turns = 0
    enemy_star_collect_frame = None
    first_star_frame = None
    my_star_collect_frame = None
    enemy_stars = 0
    my_skill_casts = 0
    my_shots_hit_ev = 0
    my_shots_wall_ev = 0
    my_shots_fired_ev = 0

    for ev in events:
        frame = ev.get("frame", 0)
        tank = ev.get("tank", "")
        event = ev.get("event", "")

        if event == "skill_cast" or (event == "skill_applied" and ev.get("action") == "applied"):
            skill = ev.get("skill")
            if tank == my_name and my_skill is None:
                my_skill = skill
            if tank == enemy_name and enemy_skill is None:
                enemy_skill = skill

        if event == "skill_cast":
            if tank == my_name:
                my_skill_casts += 1
                if my_first_skill is None:
                    my_first_skill = frame
            if tank == enemy_name and enemy_first_skill is None:
                enemy_first_skill = frame

        if event == "crashed" and ev.get("tank") == my_name:
            death_frame = frame

        if event == "star_spawned" and first_star_frame is None:
            first_star_frame = frame

        if event == "star_collected":
            if ev.get("tank") == my_name:
                my_stars += 1
                if my_star_collect_frame is None:
                    my_star_collect_frame = frame
            elif ev.get("tank") == enemy_name:
                enemy_stars += 1
                if enemy_star_collect_frame is None:
                    enemy_star_collect_frame = frame

        if tank == my_name:
            if event == "fire":
                my_shots_fired_ev += 1
            elif event == "shot_hit":
                my_shots_hit_ev += 1
            elif event == "shot_wall":
                my_shots_wall_ev += 1

        if event == "fire" and tank == my_name:
            my_fires += 1
        if event == "fire" and tank == enemy_name:
            enemy_fires += 1
        if event == "move" and tank == my_name:
            my_moves += 1
        if event == "turn" and tank == my_name:
            my_turns += 1
        if event == "move" and tank == enemy_name:
            enemy_moves += 1
        if event == "turn" and tank == enemy_name:
            enemy_turns += 1

    total_frames = max((ev.get("frame", 0) for ev in events), default=1) or 1

    # 敌方打法标签：starRusher 改为 enemy_star_rush，基于敌方抢星行为
    tags = []
    if enemy_fires >= 2 and enemy_fires / total_frames > 0.08:
        tags.append("aggressive")
    if enemy_moves <= 2 and enemy_turns >= 3:
        tags.append("static")
    if enemy_moves >= 8 and enemy_fires <= 1:
        tags.append("defensive")
    if enemy_star_collect_frame is not None and first_star_frame is not None:
        if enemy_star_collect_frame - first_star_frame <= 20:
            tags.append("enemy_star_rush")

    my_win_tactic = None
    if won:
        if my_star_collect_frame is not None and my_star_collect_frame <= 25:
            my_win_tactic = "early_star"
        elif my_first_skill is not None and my_first_skill <= 5:
            my_win_tactic = "skill_first"
        elif my_fires >= 2 and my_moves <= 5:
            my_win_tactic = "point_blank"
        elif death_frame and death_frame >= total_frames * 0.7:
            my_win_tactic = "line_control"
        else:
            my_win_tactic = "fast_kill"

    my_loss_tactic = None
    inferred_bt_layers = []
    profile_gap_tags = []

    if not won and not draw:
        diagnosis = (me_stats.get("diagnosis") or "").lower()
        # events 直算优先，summary 作 fallback（离线 reanalyze 不依赖 summary）
        shots_fired = my_shots_fired_ev or me_stats.get("shots_fired") or 0
        shots_hit = my_shots_hit_ev or me_stats.get("shots_hit") or 0
        shots_wall = my_shots_wall_ev or me_stats.get("shots_wall") or 0
        moves_stat = me_stats.get("moves") or my_moves
        stars_stat = me_stats.get("stars") if me_stats.get("stars") is not None else my_stars
        enemy_stars_stat = me_stats.get("enemy_stars") if me_stats.get("enemy_stars") is not None else enemy_stars
        early_death = death_frame is not None and death_frame <= total_frames * 0.35
        aim_fail = (shots_wall >= 2 and shots_fired >= 2) or (shots_fired >= 1 and shots_hit == 0)

        # Phase 1：负场战术（result_reason / diagnosis 优先于 aim_fail 启发式）
        if result_reason == "star" and stars_stat < enemy_stars_stat:
            my_loss_tactic = "star_race"
        elif "never used" in diagnosis or my_skill_casts == 0:
            my_loss_tactic = "skill_unused"
        elif my_star_collect_frame is not None and early_death:
            my_loss_tactic = "star_greed"
        elif "lost the star race" in diagnosis or "lost star race" in diagnosis:
            my_loss_tactic = "star_race"
        elif result_reason == "crashed" and early_death and not aim_fail and shots_fired <= 1:
            my_loss_tactic = "early_crash"
        elif aim_fail:
            my_loss_tactic = "aim_fail"
        elif my_first_skill is not None and early_death and my_first_skill <= total_frames * 0.2:
            my_loss_tactic = "skill_bad_timing"
        elif moves_stat <= 2 or "did not move" in diagnosis:
            my_loss_tactic = "static_target"
        elif my_fires >= 2 and my_moves <= 5:
            my_loss_tactic = "over_aggressive"
        elif "aggressive" in tags and early_death:
            my_loss_tactic = "dodge_fail"
        elif result_reason == "runTime" or (
            (me_stats.get("frames_total") or total_frames) >= 80 and stars_stat <= 1
        ):
            my_loss_tactic = "outscaled"
        else:
            my_loss_tactic = "unknown"

        # Phase 4：行为树层级归因（可多选）
        if my_loss_tactic in ("star_greed", "star_race", "static_target", "dodge_fail", "early_crash"):
            inferred_bt_layers.append("hard_survival")
        if my_loss_tactic == "over_aggressive" and early_death:
            inferred_bt_layers.append("soft_survival")
        if my_loss_tactic in ("star_greed", "star_race"):
            inferred_bt_layers.append("objective")
        if my_loss_tactic in ("over_aggressive", "aim_fail"):
            inferred_bt_layers.append("attack")
        if my_loss_tactic in ("static_target", "early_crash") or (my_turns >= 5 and my_moves <= 3):
            inferred_bt_layers.append("movement")
        if my_loss_tactic in ("skill_bad_timing", "skill_unused"):
            inferred_bt_layers.append("commit")
        inferred_bt_layers = list(dict.fromkeys(inferred_bt_layers))

        # Profile gap：规则来自 bt_profile_expectations.json（骨架默认为空）
        expectations = get_profile_expectations()
        gap_rules = expectations.get("gap_rules", {})
        enemy_sk = enemy_skill or "unknown"
        for gap_key, rule in gap_rules.items():
            loss_ok = my_loss_tactic in rule.get("when_loss_tactic", [])
            skill_ok = not rule.get("enemy_skills") or enemy_sk in rule["enemy_skills"]
            tag_ok = not rule.get("enemy_tags") or any(t in tags for t in rule["enemy_tags"])
            if loss_ok and skill_ok and tag_ok:
                profile_gap_tags.append(gap_key)

    anchor_frame = death_frame if death_frame is not None else total_frames
    star_delta = my_stars - enemy_stars

    return {
        "has_events": True,
        "my_skill_inferred": my_skill,
        "enemy_skill": enemy_skill or "unknown",
        "my_first_skill_frame": my_first_skill,
        "enemy_first_skill_frame": enemy_first_skill,
        "death_frame": death_frame,
        "anchor_frame": anchor_frame,
        "total_frames": total_frames,
        "my_stars": my_stars,
        "enemy_stars": enemy_stars,
        "star_delta": star_delta,
        "my_skill_casts": my_skill_casts,
        "my_win_tactic": my_win_tactic,
        "my_loss_tactic": my_loss_tactic,
        "enemy_playstyle_tags": tags,
        "inferred_bt_layers": inferred_bt_layers,
        "profile_gap_tags": profile_gap_tags,
    }


# ── raw 遥测解析 ──────────────────────────────────────────
# 坦克 BT_DEBUG 每帧 print 的调试行会被平台记入 raw 回放 logs，
# 格式契约（见 entry.js [5] 调试输出）：
#   轨迹行: f{n} {内层节点>...>根层子树}:{动作名}   （trace 自内向外，最后一段是根层）
#   画像行: PF f{n} {敌技能}|{playstyle}|{attackAggr}|{starAggr}|{standoff}
#   开局行: f5 我:{mySkill} vs 敌:{profile.name} | {trace}  （敌侧为中文流派名，见 PROFILE_NAME_ZH_TO_SKILL）
# 据此可还原每帧真实决策层级，并从 PF/开局行推断敌方技能（events 未施放时的 fallback）。

TRACE_LINE_RE = re.compile(r"^f(\d+)\s+(.+):([\w-]+)$")
PF_LINE_RE = re.compile(r"^PF\s+f(\d+)\s+(.+)$")
# 开局第 5 帧 matchup 行：我:{mySkill} vs 敌:{profile.name}（敌侧为中文流派名）
OPENING_MATCHUP_RE = re.compile(r"^f5\s+我:(\w+)\s+vs\s+敌:([^|]+?)\s*\|")

VALID_ENEMY_SKILLS = frozenset({
    "boost", "cloak", "freeze", "overload", "poison", "shield", "stun", "teleport",
})
# 开局行「敌:{中文流派名}」→ skillType。骨架默认空：只认 PF 行里的英文 skillType。
# 若你的 enemy-profiler 用中文 name，在此自行映射，例如 "护盾流": "shield"。
PROFILE_NAME_ZH_TO_SKILL = {}

# 根层子树名 → 归因桶（与框架 tree-factory ROOT 层对齐；未知名默认归 commit）
BT_LAYER_BUCKETS = {
    "hard-survival": "hard_survival",
    "soft-survival": "soft_survival",
    "attack": "attack",
    "skill-attack": "attack",
    "bomb-attack": "bomb_attack",
    "objective": "objective",
    "skill-objective": "objective",
    "movement": "movement",
    "cc-check": "cc",
}

REAL_LAYER_TAIL_FRAMES = 15  # 归因窗口：锚点帧前 N 帧（与 timeline 窗口对齐）
TIMELINE_WINDOW = 30  # 负场关键 event / print 日志入库窗口（锚点前 N 帧）
KEY_EVENT_KINDS = frozenset({
    "star_spawned", "star_collected", "fire", "shot_wall", "shot_hit",
    "crashed", "skill_cast", "skill_applied",
})


def _decode_log_data(data):
    """raw logs 的 data 是 JSON 转义过的字符串（自带引号），先解一层。"""
    if isinstance(data, str) and data.startswith('"'):
        try:
            return json.loads(data)
        except (ValueError, TypeError):
            return data.strip('"')
    return data


def _normalize_enemy_skill(skill):
    """校验并规范化敌方技能 slug，非法值返回 None。"""
    if not skill:
        return None
    sk = str(skill).strip().lower()
    return sk if sk in VALID_ENEMY_SKILLS else None


def _enemy_skill_from_pf_payload(payload):
    """PF 行 payload 首段即 profile.skillType（entry.js 契约）。"""
    if not payload:
        return None
    return _normalize_enemy_skill(payload.split("|", 1)[0])


def _enemy_skill_from_profile_timeline(profile_timeline):
    """profile_timeline 存的是 f{n} {payload}，与 PF 行 payload 同格式。"""
    for line in profile_timeline or []:
        m = re.match(r"^f\d+\s+(.+)$", line)
        if m:
            sk = _enemy_skill_from_pf_payload(m.group(1))
            if sk:
                return sk
    return None


def extract_enemy_skill_from_raw_logs(raw_data, my_name):
    """
    从 raw 回放我方 print 日志推断敌方技能（开局即知，不依赖 skill_cast event）。
    优先级：PF 画像行 skillType > 开局 f5 matchup 行 profile.name 映射。
    返回 (skill_slug, source) 或 (None, None)。
    """
    rd = raw_data.get("replayData") or {}
    names = rd.get("names") or []
    players = ((rd.get("replay") or {}).get("meta") or {}).get("players") or []
    if my_name not in names:
        return None, None
    idx = names.index(my_name)
    if idx >= len(players):
        return None, None
    logs = players[idx].get("logs") or []
    if not logs:
        return None, None

    pf_skill = None
    opening_skill = None
    for lg in logs:
        line = _decode_log_data(lg.get("data", ""))
        if not isinstance(line, str):
            continue
        m = PF_LINE_RE.match(line)
        if m and pf_skill is None:
            pf_skill = _enemy_skill_from_pf_payload(m.group(2))
            if pf_skill:
                continue
        m = OPENING_MATCHUP_RE.match(line)
        if m and opening_skill is None:
            opening_skill = PROFILE_NAME_ZH_TO_SKILL.get(m.group(2).strip())

    if pf_skill:
        return pf_skill, "raw_pf"
    if opening_skill:
        return opening_skill, "raw_opening"
    return None, None


def _recompute_profile_gap_tags(tactics):
    """enemy_skill 从 raw 补全后，负场 gap 标签需按新技能重算。"""
    loss_tactic = tactics.get("my_loss_tactic")
    if not loss_tactic:
        return
    expectations = get_profile_expectations()
    gap_rules = expectations.get("gap_rules", {})
    tags = tactics.get("enemy_playstyle_tags") or []
    enemy_sk = tactics.get("enemy_skill") or "unknown"
    profile_gap_tags = []
    for gap_key, rule in gap_rules.items():
        loss_ok = loss_tactic in rule.get("when_loss_tactic", [])
        skill_ok = not rule.get("enemy_skills") or enemy_sk in rule["enemy_skills"]
        tag_ok = not rule.get("enemy_tags") or any(t in tags for t in rule["enemy_tags"])
        if loss_ok and skill_ok and tag_ok:
            profile_gap_tags.append(gap_key)
    tactics["profile_gap_tags"] = profile_gap_tags


def apply_enemy_skill_from_raw(tactics, raw_data, my_name, parsed_raw=None):
    """
    events 未识别敌方技能时，用 raw 调试日志补全（PF / 开局 matchup）。
    events 已有结果时保留，不覆盖。
    """
    if tactics.get("enemy_skill") and tactics["enemy_skill"] != "unknown":
        return False
    skill, source = extract_enemy_skill_from_raw_logs(raw_data, my_name)
    if not skill and parsed_raw:
        # parse_raw_logs 已扫过 PF，复用 timeline 避免重复遍历
        skill = _enemy_skill_from_profile_timeline(parsed_raw.get("profile_timeline"))
        source = "raw_pf" if skill else None
    if not skill:
        return False
    old = tactics.get("enemy_skill")
    tactics["enemy_skill"] = skill
    if source:
        tactics["enemy_skill_source"] = source
    if old != skill and tactics.get("my_loss_tactic"):
        _recompute_profile_gap_tags(tactics)
    return True


def _event_side(actor, my_name, enemy_name):
    if actor == my_name:
        return "me"
    if actor == enemy_name:
        return "enemy"
    return "other"


def extract_bomb_stats_from_raw(raw_data, my_name):
    """
    从 raw records 统计放雷次数，口径与 replay-viewer.js 一致（placed/created，不计 exploded）。
    无 records 时返回 None；有 raw 但无炸弹时 has_bomb=0。
    """
    rd = raw_data.get("replayData") or {}
    records = (rd.get("replay") or {}).get("records")
    if not isinstance(records, list):
        return None

    names = rd.get("names") or []
    my_idx = names.index(my_name) if my_name in names else 0

    my_n = enemy_n = 0
    for frame in records:
        for ev in frame or []:
            if ev.get("type") != "bomb":
                continue
            if ev.get("action") not in ("placed", "created"):
                continue
            if ev.get("by") == my_idx:
                my_n += 1
            else:
                enemy_n += 1

    total = my_n + enemy_n
    return {
        "has_bomb": 1 if total > 0 else 0,
        "my_bomb_placed": my_n,
        "enemy_bomb_placed": enemy_n,
        "bomb_total": total,
    }


def extract_key_events(events, my_name, enemy_name, anchor_frame, window=TIMELINE_WINDOW):
    """从 events 流提取终局窗口内的关键战术 event（负场复盘用）。"""
    if not events or anchor_frame is None:
        return []
    lo = max(0, anchor_frame - window)
    hi = anchor_frame + 2
    out = []
    seq = 0
    for ev in events:
        frame = ev.get("frame", 0)
        if frame < lo or frame > hi:
            continue
        kind = ev.get("event", "")
        if kind not in KEY_EVENT_KINDS:
            continue
        if kind == "skill_applied" and ev.get("action") != "applied":
            continue
        actor = ev.get("tank", "")
        detail = {}
        if kind == "star_spawned":
            detail["at"] = ev.get("at")
        if kind in ("skill_cast", "skill_applied"):
            detail["skill"] = ev.get("skill")
        if kind in ("fire", "shot_wall", "shot_hit"):
            detail["direction"] = ev.get("direction")
        out.append({
            "seq": seq,
            "frame": frame,
            "kind": kind,
            "actor": actor,
            "side": _event_side(actor, my_name, enemy_name),
            "detail": detail,
        })
        seq += 1
    return out


def build_critical_events_summary(key_events, max_items=4):
    """生成列表列用的 2~4 条短摘要。"""
    if not key_events:
        return []
    kind_zh = {
        "star_spawned": "★出",
        "star_collected": "★吃",
        "fire": "🔫",
        "shot_wall": "🧱",
        "shot_hit": "💥",
        "crashed": "💀",
        "skill_cast": "⚡",
        "skill_applied": "✨",
    }
    side_prefix = {"me": "我", "enemy": "敌", "other": ""}
    summaries = []
    for ev in key_events:
        if len(summaries) >= max_items:
            break
        icon = kind_zh.get(ev["kind"], ev["kind"])
        who = side_prefix.get(ev.get("side"), "")
        skill = (ev.get("detail") or {}).get("skill")
        extra = f"{skill}" if skill else ""
        summaries.append(f"f{ev['frame']}{icon}{who}{extra}")
    return summaries


def slice_behavior_log(bt_trace, profile_timeline, anchor_frame, window=TIMELINE_WINDOW):
    """截取终局窗口内的 print 轨迹与 PF 画像行。"""
    if anchor_frame is None:
        return []
    lo = max(0, anchor_frame - window)
    hi = anchor_frame + 2
    out = []
    seq = 0
    for t in bt_trace or []:
        frame = t.get("frame", 0)
        if lo <= frame <= hi:
            out.append({
                "seq": seq,
                "frame": frame,
                "kind": "bt",
                "text": f"{t.get('layer', '?')}/{t.get('action', '?')}",
            })
            seq += 1
    for line in profile_timeline or []:
        m = re.match(r"^f(\d+)\s+(.+)$", line)
        if not m:
            continue
        frame = int(m.group(1))
        if lo <= frame <= hi:
            out.append({
                "seq": seq,
                "frame": frame,
                "kind": "pf",
                "text": line,
            })
            seq += 1
    out.sort(key=lambda x: (x["frame"], x["seq"]))
    for i, row in enumerate(out):
        row["seq"] = i
    return out


def _bt_layers_from_trace(trace_slice):
    """从 trace 切片按近因去重 + 主导层归因。"""
    from collections import Counter
    if not trace_slice:
        return [], None
    layer_counts = Counter(t["layer"] for t in trace_slice if t.get("layer") != "cc")
    dominant = layer_counts.most_common(1)[0][0] if layer_counts else None
    real_layers = []
    for t in reversed(trace_slice):
        layer = t.get("layer")
        if layer and layer not in real_layers and layer != "cc":
            real_layers.append(layer)
    return real_layers, dominant


def parse_raw_logs(raw_data, my_name, anchor_frame=None):
    """
    从 raw 回放解析我方 print 遥测。
    返回 {"bt_trace", "real_bt_layers", "profile_timeline", "has_raw"}；
    无法定位我方 logs（旧版产物未开 BT_DEBUG 等）时返回 None。
    """
    rd = raw_data.get("replayData") or {}
    names = rd.get("names") or []
    players = ((rd.get("replay") or {}).get("meta") or {}).get("players") or []
    if my_name not in names:
        return None
    idx = names.index(my_name)
    if idx >= len(players):
        return None
    logs = players[idx].get("logs") or []
    if not logs:
        return None

    bt_trace = []
    profile_timeline = []
    for lg in logs:
        line = _decode_log_data(lg.get("data", ""))
        if not isinstance(line, str):
            continue
        m = PF_LINE_RE.match(line)
        if m:
            profile_timeline.append(f"f{m.group(1)} {m.group(2)}")
            continue
        # 跳过已下线的 IDP 验证埋点行（格式类似 f{n} IDP我:{hex}，会被轨迹正则误匹配）
        if "IDP" in line:
            continue
        # 开局第 5 帧格式为 "我:x vs 敌:y | {trace}"，取分隔符后半段
        if " | " in line:
            line = line.rsplit(" | ", 1)[-1]
        m = TRACE_LINE_RE.match(line)
        if not m:
            continue
        frame = int(m.group(1))
        path = m.group(2)
        action = m.group(3)
        # 8 位 hex 多为误匹配的会话 id，不是 BT 动作名
        if re.match(r"^[a-f0-9]{8}$", action):
            continue
        # trace 自内向外记录，最后一段是根层子树名
        root_node = path.split(">")[-1] if ">" in path else path
        layer = BT_LAYER_BUCKETS.get(root_node, "commit")
        bt_trace.append({"frame": frame, "layer": layer, "action": action})

    if not bt_trace:
        return None

    # 归因：锚点帧前 N 帧内的层占用（与 timeline 窗口一致，非「最后 N 条 print」）
    if anchor_frame is not None:
        lo = max(0, anchor_frame - REAL_LAYER_TAIL_FRAMES)
        trace_slice = [t for t in bt_trace if lo <= t["frame"] <= anchor_frame + 1]
    else:
        trace_slice = bt_trace[-REAL_LAYER_TAIL_FRAMES:]
    real_layers, dominant_layer = _bt_layers_from_trace(trace_slice)

    return {
        "bt_trace": bt_trace,
        "real_bt_layers": real_layers,
        "dominant_bt_layer": dominant_layer,
        "profile_timeline": profile_timeline,
        "has_raw": True,
        "enemy_skill_from_log": _enemy_skill_from_profile_timeline(profile_timeline),
    }


def parse_agent_summary(summary_data, base, my_tank_name):
    """合并 agent.json summary 到 record。"""
    participants = summary_data.get("participants", {})
    ch = participants.get("challenger", {})
    de = participants.get("defender", {})
    my_name = my_tank_name
    if ch.get("tankName") == my_tank_name:
        enemy_name = de.get("tankName", base["opponent_name"])
        enemy_id = de.get("tankId")
        my_code_hash = ch.get("codeHash")
    else:
        enemy_name = ch.get("tankName", base["opponent_name"])
        enemy_id = ch.get("tankId")
        my_code_hash = de.get("codeHash")
    # 版本维度：codeHash 区分发版前后的对局，回答"这次发版有没有变强"
    base["my_code_hash"] = my_code_hash

    match_info = summary_data.get("match", {})
    if base.get("result_reason") is None:
        base["result_reason"] = match_info.get("resultReason")
    if base.get("map_id") is None:
        base["map_id"] = match_info.get("mapId")

    winner_name = match_info.get("winnerTankName")
    if winner_name:
        base["won"] = winner_name == my_name
        base["draw"] = match_info.get("resultReason") == "tie"

    summ = summary_data.get("summary", {})
    frames_total = summ.get("framesTotal")
    tanks = summ.get("tanks", {})
    me_stats = _stats_from_summary(tanks.get(my_name), frames_total)
    en_stats = _stats_from_summary(tanks.get(enemy_name), frames_total)

    base["opponent_name"] = enemy_name
    base["opponent_id"] = enemy_id or base.get("opponent_id")
    base["stats"] = {"me": me_stats, "enemy": en_stats}
    return base, my_name, enemy_name


def apply_raw_telemetry(record, tactics, raw_data, my_name, is_loss, raw_all=False):
    """
    处理 raw 遥测响应。
    胜负/平局均统计炸弹（records 轻量扫描）；BT 复盘、behavior_log 仍限负场或 --raw-all，
    避免胜场拉 raw 后做全量 print 解析与入库。
    """
    bomb_stats = extract_bomb_stats_from_raw(raw_data, my_name)
    if bomb_stats:
        tactics.update(bomb_stats)

    parsed = None
    if raw_all or is_loss:
        anchor = (tactics or {}).get("anchor_frame")
        parsed = parse_raw_logs(raw_data, my_name, anchor_frame=anchor)
        if parsed:
            record["bt_trace"] = parsed["bt_trace"]
            tactics["real_bt_layers"] = parsed["real_bt_layers"]
            tactics["dominant_bt_layer"] = parsed.get("dominant_bt_layer")
            tactics["profile_timeline"] = parsed["profile_timeline"]
            tactics["has_raw"] = True
            if is_loss and anchor is not None:
                tactics["behavior_log"] = slice_behavior_log(
                    parsed["bt_trace"],
                    parsed["profile_timeline"],
                    anchor,
                )
    # PF / 开局 matchup 补全敌方技能（不依赖 parse_raw_logs 成败）
    apply_enemy_skill_from_raw(tactics, raw_data, my_name, parsed_raw=parsed)


def ingest_one_match(base, tank_key, args, db, run_id, index=None, total=None):
    """
    拉取单场 summary/events/raw，解析后入库。
    批量采集与 --match-id 单次拉取共用，避免两套解析逻辑分叉。
    """
    url_id = base["match_url_id"]
    name = base["my_tank_name"]
    prefix = f"  [{index}/{total}] " if index is not None and total is not None else "  "
    print(f"{prefix}{url_id} vs {base.get('opponent_name', '?')}", end="")

    summary = fetch_agent_json(
        url_id, tank_key=tank_key, cache_dir=args.cache_dir,
        no_cache=args.no_cache, delay=args.delay,
    )
    record, my_name, enemy_name = parse_agent_summary(summary, base, name)

    need_events = not args.summary_only and (args.deep or record["won"] or (not record["won"] and not record["draw"]))
    tactics = {"has_events": False}
    if need_events:
        try:
            ev_data = fetch_agent_json(
                url_id, view="events", tank_key=tank_key,
                cache_dir=args.cache_dir, no_cache=args.no_cache, delay=args.delay,
            )
            tactics = parse_events(
                ev_data.get("events", []),
                my_name,
                enemy_name,
                record["won"],
                draw=record.get("draw", False),
                result_reason=record.get("result_reason"),
                me_stats={
                    **(record.get("stats", {}).get("me") or {}),
                    "enemy_stars": (record.get("stats", {}).get("enemy") or {}).get("stars"),
                },
            )
            # 负场：提取终局窗口关键 events 摘要（完整明细入库在 raw 解析后）
            if not record["won"] and not record.get("draw"):
                anchor = tactics.get("anchor_frame")
                key_ev = extract_key_events(
                    ev_data.get("events", []), my_name, enemy_name, anchor,
                )
                tactics["key_events"] = key_ev
                tactics["critical_events_summary"] = build_critical_events_summary(key_ev)
        except Exception as e:
            print(f" events失败:{e}", end="")

    # raw：胜负均拉 view=raw 统计炸弹；BT 复盘仍限负场（--raw-all 时胜场也全量解析）
    is_loss = not record["won"] and not record.get("draw")
    need_raw_bomb = not args.summary_only and not args.no_raw
    if need_raw_bomb:
        try:
            raw_data = fetch_agent_json(
                url_id, view="raw", tank_key=tank_key,
                cache_dir=args.cache_dir, no_cache=args.no_cache, delay=args.delay,
            )
            apply_raw_telemetry(
                record, tactics, raw_data, my_name, is_loss, raw_all=args.raw_all,
            )
        except Exception as e:
            print(f" raw失败:{e}", end="")

    record["tactics"] = tactics
    record["opponent_name"] = enemy_name
    opp_id = db.upsert_opponent(record.get("opponent_id"), record["opponent_name"])
    record["opponent_id"] = opp_id
    db.save_match(run_id, record)
    tag = "W" if record["won"] else ("D" if record["draw"] else "L")
    print(f"  [{tag}]")
    return record


def collect_matches_for_tank(entry, n, tank_key, args, db, run_id):
    """单坦克采集 n 场并入库。"""
    name = entry["name"]
    skill = entry.get("skill", "unknown")
    print(f"\n{'='*50}")
    print(f"  采集 [{name}] skill={skill}  目标 {n} 场")
    print(f"{'='*50}")

    ctx = fetch_tank_context(tank_key)
    tank = ctx.get("tank", {})
    my_tank_id = tank.get("id")
    skill = tank.get("skillType") or skill
    db.upsert_my_tank(name, skill, my_tank_id, tank.get("rankScore"))

    collected = []
    offset = 0
    while len(collected) < n:
        batch, has_more = fetch_match_list(tank_key, min(50, n - len(collected) + 10), offset)
        if not batch:
            break
        for raw in batch:
            if len(collected) >= n:
                break
            url_id = raw.get("matchUrlId") or raw.get("urlId")
            if not url_id:
                continue
            base = normalize_list_match(raw, my_tank_id, name)
            base["my_skill"] = skill
            collected.append(base)
        offset += len(batch)
        if not has_more:
            break
        time.sleep(args.delay)

    for i, base in enumerate(collected):
        ingest_one_match(base, tank_key, args, db, run_id, index=i + 1, total=len(collected))

    return collected


def collect_match_by_id(match_id_raw, tank_keys, args, db, run_id):
    """
    按对局 id（或 history URL）单次拉取并入库。
    先拉公开 summary 识别双方，再匹配 profile 确定我方视角与 Bearer key。
    """
    url_id = normalize_match_id(match_id_raw)
    if not url_id:
        raise ValueError(f"无效对局 id: {match_id_raw!r}（期望 mat_xxx 或含该 id 的 URL）")

    print(f"\n{'='*50}")
    print(f"  单场拉取 {url_id}")
    print(f"{'='*50}")

    probe_key = tank_keys[0]["key"] if tank_keys else None
    summary = fetch_agent_json(
        url_id, tank_key=probe_key, cache_dir=args.cache_dir,
        no_cache=args.no_cache, delay=args.delay,
    )
    entry = resolve_entry_for_match(summary, tank_keys, only=getattr(args, "only", None))
    tank_key = entry.get("key") or probe_key
    name = entry["name"]
    skill = entry.get("skill", "unknown")

    my_tank_id = None
    if tank_key:
        try:
            ctx = fetch_tank_context(tank_key)
            tank = ctx.get("tank", {})
            # 仅当 context 坦克名与视角一致时采信 skill/id，避免镜像局用错 key
            if tank.get("name") == name or tank.get("tankName") == name or not tank.get("name"):
                my_tank_id = tank.get("id")
                skill = tank.get("skillType") or skill
            else:
                # key 对应另一辆坦克时仍用 entry.skill；id 留空
                skill = entry.get("skill") or skill
        except Exception as e:
            print(f"  tank context 失败（继续用公开数据）: {e}")

    db.upsert_my_tank(name, skill, my_tank_id)
    print(f"  视角: [{name}] skill={skill}")

    base = base_from_summary(summary, url_id, name, skill)
    # summary 已写入 cache，ingest 再拉会命中磁盘缓存
    record = ingest_one_match(base, tank_key, args, db, run_id, index=1, total=1)
    return [record]


def reanalyze_run(db, run_id, tank_keys, args):
    """从本地 cache 重解析 run 内对局，不重新请求平台 API。"""
    matches = db.list_run_matches_for_reanalyze(run_id)
    if not matches:
        print(f"Run #{run_id} 无对局")
        return 0
    tank_key = tank_keys[0]["key"]
    updated = 0
    print(f"\n重解析 Run #{run_id}，共 {len(matches)} 场（仅用 cache）")
    for i, m in enumerate(matches):
        url_id = m["match_url_id"]
        my_name = m["my_tank_name"]
        print(f"  [{i+1}/{len(matches)}] {url_id}", end="")
        try:
            summary = fetch_agent_json(
                url_id, tank_key=tank_key, cache_dir=args.cache_dir,
                no_cache=False, delay=0,
            )
            base = {
                "match_url_id": url_id,
                "my_tank_name": my_name,
                "my_skill": m["my_skill"],
                "opponent_name": m["opponent_name"],
                "opponent_id": m.get("opponent_id"),
                "won": bool(m["won"]),
                "draw": bool(m["draw"]),
                "map_id": m.get("map_id"),
                "result_reason": m.get("result_reason"),
                "created_at": m.get("created_at"),
                "history_url": m.get("history_url"),
                "my_code_hash": m.get("my_code_hash"),
            }
            record, my_name, enemy_name = parse_agent_summary(summary, base, my_name)

            tactics = {"has_events": False}
            is_loss = not record["won"] and not record.get("draw")
            try:
                ev_data = fetch_agent_json(
                    url_id, view="events", tank_key=tank_key,
                    cache_dir=args.cache_dir, no_cache=False, delay=0,
                )
                tactics = parse_events(
                    ev_data.get("events", []),
                    my_name,
                    enemy_name,
                    record["won"],
                    draw=record.get("draw", False),
                    result_reason=record.get("result_reason"),
                    me_stats={
                        **(record.get("stats", {}).get("me") or {}),
                        "enemy_stars": (record.get("stats", {}).get("enemy") or {}).get("stars"),
                    },
                )
                if is_loss:
                    anchor = tactics.get("anchor_frame")
                    key_ev = extract_key_events(
                        ev_data.get("events", []), my_name, enemy_name, anchor,
                    )
                    tactics["key_events"] = key_ev
                    tactics["critical_events_summary"] = build_critical_events_summary(key_ev)
            except Exception as e:
                print(f" events:{e}", end="")

            if not args.no_raw:
                try:
                    raw_data = fetch_agent_json(
                        url_id, view="raw", tank_key=tank_key,
                        cache_dir=args.cache_dir, no_cache=False, delay=0,
                    )
                    apply_raw_telemetry(
                        record, tactics, raw_data, my_name, is_loss, raw_all=args.raw_all,
                    )
                except Exception as e:
                    print(f" raw:{e}", end="")

            record["tactics"] = tactics
            db.save_match(run_id, record)
            updated += 1
            print(" OK")
        except Exception as e:
            print(f" FAIL:{e}")
    return updated


def backfill_bomb_stats(db, tank_keys, args):
    """
    对 DB 内 my_bomb_placed 仍为 NULL 的对局补拉 raw 并只更新炸弹列。
    按 match_url_id 去重，避免多 run 重复请求平台。
    """
    if args.no_raw:
        print("错误: --backfill-bombs 与 --no-raw 冲突")
        return 0
    matches = db.list_matches_needing_bomb_backfill(
        limit=getattr(args, "backfill_limit", None),
        run_id=getattr(args, "backfill_run", None),
    )
    if not matches:
        print("无需回填：指定范围内对局已有炸弹统计")
        return 0
    tank_key = tank_keys[0]["key"]
    ok = fail = skip = 0
    scope = ""
    if getattr(args, "backfill_run", None) is not None:
        scope = f" run#{args.backfill_run}"
    elif getattr(args, "backfill_limit", None) is not None:
        scope = f" 最近{args.backfill_limit}场"
    print(f"\n炸弹统计回填{scope}：共 {len(matches)} 场")
    for i, m in enumerate(matches):
        url_id = m["match_url_id"]
        my_name = m["my_tank_name"]
        tag = "W" if m["won"] else ("D" if m["draw"] else "L")
        print(f"  [{i+1}/{len(matches)}] {url_id} [{tag}]", end="")
        try:
            raw_data = fetch_agent_json(
                url_id, view="raw", tank_key=tank_key,
                cache_dir=args.cache_dir, no_cache=args.no_cache, delay=args.delay,
            )
            stats = extract_bomb_stats_from_raw(raw_data, my_name)
            if not stats:
                print(" 跳过(无 records)")
                skip += 1
                continue
            db.update_bomb_stats(url_id, stats)
            ok += 1
            print(f" 我{stats['my_bomb_placed']}/敌{stats['enemy_bomb_placed']}")
        except Exception as e:
            print(f" FAIL:{e}")
            fail += 1
    print(f"\n回填完成: 成功 {ok}  跳过 {skip}  失败 {fail}")
    return ok


def enrich_opponents(tank_keys, db, run_id, args):
    """对 run 内负场+低胜率对手 enrichment。"""
    opps = db.run_opponents(run_id, min_samples=1, sort="worst", page=1, page_size=30)
    names = {item["opponent_name"] for item in opps["items"]}
    tank_key = tank_keys[0]["key"]
    for name in names:
        try:
            results = fetch_opponent_info(tank_key, name, delay=args.delay)
            if results:
                o = results[0]
                db.upsert_opponent(
                    o.get("id"), o.get("name", name),
                    o.get("rankTier"), o.get("rankScore"),
                    o.get("wins"), o.get("losses"), o.get("draws"),
                )
        except Exception:
            pass


def print_console_summary(db, run_id, min_rank_score=None):
    """控制台简要中文摘要。"""
    overview = db.run_overview(run_id, min_rank_score=min_rank_score)
    ov = overview["overall"]
    total = ov.get("total") or 0
    wins = ov.get("wins") or 0
    losses = ov.get("losses") or 0
    wr = wins / total * 100 if total else 0

    print(f"\n{'='*50}")
    print("  分析摘要" + (f"（对手 rank_score >= {min_rank_score}）" if min_rank_score else ""))
    print(f"{'='*50}")
    print(f"  总场次: {total}  胜: {wins}  负: {losses}  胜率: {wr:.1f}%")

    # 版本维度：多 codeHash 时按版本给出胜率，回答"发版有没有变强"
    hashes = db.run_code_hashes(run_id)
    if len(hashes) > 1:
        print("\n  按代码版本:")
        for row in hashes:
            t = row["total"] or 1
            w = row["wins"] or 0
            short = (row["code_hash"] or "unknown")[:12]
            print(f"    {short:<14} {w}/{t}  WR:{w/t*100:.1f}%  ({(row['first_seen'] or '')[:10]}~{(row['last_seen'] or '')[:10]})")

    print("\n  按技能:")
    for row in overview["by_skill"]:
        t = row["total"] or 1
        w = row["wins"] or 0
        print(f"    {row['my_skill']:<12} {w}/{t}  WR:{w/t*100:.1f}%")

    matrix = db.run_skill_matrix(run_id, min_rank_score=min_rank_score)
    if matrix:
        print("\n  技能矩阵 (我方×敌方):")
        for row in matrix:
            t = row["total"] or 1
            w = row["wins"] or 0
            print(f"    {row['my_skill']} vs {row['enemy_skill']:<10} {w}/{t}  WR:{w/t*100:.1f}%")

    opps = db.run_opponents(run_id, min_samples=2, sort="worst", page=1, page_size=5,
                            min_rank_score=min_rank_score)
    if opps["items"]:
        print("\n  难打对手 Top5:")
        for o in opps["items"]:
            print(f"    {o['opponent_name']:<24} {o['wins']}/{o['total']}  WR:{o['win_rate']:.1f}%")

    losses = db.run_loss_patterns(run_id, min_rank_score=min_rank_score)
    if losses:
        grouped = {}
        for row in losses:
            grouped[row["tactic"]] = grouped.get(row["tactic"], 0) + row["n"]
        top = sorted(grouped.items(), key=lambda x: -x[1])[:5]
        print("\n  负场战术 Top5:")
        for tactic, n in top:
            print(f"    {tactic:<18} {n}场")

    # 真实 BT 层级归因（raw 遥测），区分实证与启发式来源
    bt_rows = db.run_bt_layer_losses(run_id, min_rank_score=min_rank_score)
    real_rows = [r for r in bt_rows if r.get("source") == "real"]
    if real_rows:
        grouped = {}
        for row in real_rows:
            grouped[row["layer"]] = grouped.get(row["layer"], 0) + row["n"]
        top = sorted(grouped.items(), key=lambda x: -x[1])[:5]
        print("\n  负场终局 BT 层占用 Top5（raw 实证）:")
        for layer, n in top:
            print(f"    {layer:<18} {n}场")


def make_analysis_args(**overrides):
    """构建分析参数（供 match_runner 赛后自动调用，避免重复解析 CLI）。"""
    defaults = {
        "limit": DEFAULT_N,
        "only": None,
        "tank": DEFAULT_TANK,
        "db": DEFAULT_DB,
        "cache_dir": DEFAULT_CACHE,
        "delay": DEFAULT_DELAY,
        "no_cache": False,
        "summary_only": False,
        "raw_all": False,
        "no_raw": False,
        "min_rank": None,
        "deep": False,
        "serve": False,
        "port": 8765,
        "match_id": None,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def run_analysis(args, tank_keys=None):
    """
    采集最近对局并分析入库（CLI 与 match_runner 共用）。
    tank_keys: 可选，直接传入已跑完的坦克列表（含 name/key/skill），跳过 profile 过滤。
    返回新创建的 run_id。
    """
    load_dotenv()
    db = AnalysisDB(args.db)
    if tank_keys is None:
        profile = TANK_PROFILES[args.tank]
        tank_keys = get_tank_keys(profile, only=args.only)

    run_id = db.create_run(args.limit, tank_filter=args.only)
    print(f"Run #{run_id}  DB: {args.db}")
    all_collected = []
    for entry in tank_keys:
        matches = collect_matches_for_tank(entry, args.limit, entry["key"], args, db, run_id)
        all_collected.extend(matches)
    enrich_opponents(tank_keys, db, run_id, args)
    print_console_summary(db, run_id, min_rank_score=args.min_rank)

    if getattr(args, "serve", False):
        from report_server import serve
        print(f"\n启动仪表盘: http://127.0.0.1:{args.port}")
        serve(args.db, args.port)

    return run_id


def run_analysis_by_match_id(args, tank_keys=None):
    """
    按对局 id 单次拉取入库。
    tank_keys 默认取完整 profile（不按 --only 过滤），以便匹配对局双方；
    --only 只用于指定我方视角。
    """
    load_dotenv()
    db = AnalysisDB(args.db)
    if tank_keys is None:
        profile = TANK_PROFILES[args.tank]
        tank_keys = get_tank_keys(profile, only=None)

    mid = normalize_match_id(args.match_id)
    run_id = db.create_run(1, tank_filter=args.only, notes=f"match-id:{mid}")
    print(f"Run #{run_id}  DB: {args.db}  match={mid}")
    try:
        collect_match_by_id(args.match_id, tank_keys, args, db, run_id)
    except ValueError as e:
        print(f"错误: {e}")
        sys.exit(1)
    enrich_opponents(tank_keys, db, run_id, args)
    print_console_summary(db, run_id, min_rank_score=args.min_rank)

    if getattr(args, "serve", False):
        from report_server import serve
        print(f"\n启动仪表盘: http://127.0.0.1:{args.port}")
        serve(args.db, args.port)

    return run_id


def main():
    load_dotenv()
    parser = argparse.ArgumentParser(description="对局分析工具（SQLite 入库）")
    parser.add_argument("-n", "--limit", type=int, default=DEFAULT_N, help=f"每坦克采集场数 (默认 {DEFAULT_N})")
    parser.add_argument("--only", "-o", default=None, help="只分析指定坦克名，逗号分隔")
    parser.add_argument("--tank", "-t", default=DEFAULT_TANK, choices=list(TANK_PROFILES.keys()))
    parser.add_argument("--db", default=DEFAULT_DB, help=f"SQLite 路径 (默认 {DEFAULT_DB})")
    parser.add_argument("--cache-dir", default=DEFAULT_CACHE)
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY)
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--summary-only", action="store_true", help="不拉 events")
    parser.add_argument("--raw-all", action="store_true",
                        help="胜场也做完整 raw/BT 复盘（默认胜场仅拉 raw 统计炸弹）")
    parser.add_argument("--no-raw", action="store_true", help="不拉 raw 遥测")
    parser.add_argument("--min-rank", type=int, default=None,
                        help="控制台摘要只统计 rank_score >= 此值的对手（未知强度对手保留）")
    parser.add_argument("--reset-db", action="store_true", help="清空 DB 并重建 schema")
    parser.add_argument("--cache", dest="clear_cache", action="store_true",
                        help="与 --reset-db 联用：同时删除缓存目录")
    parser.add_argument("--deep", action="store_true", help="平局也拉 events")
    parser.add_argument("--no-fetch", action="store_true", help="跳过 API 采集")
    parser.add_argument("--match-id", "-m", default=None,
                        help="按对局 id 单次拉取（mat_xxx 或 history URL），与 -n 批量互斥")
    parser.add_argument("--reanalyze-run", type=int, default=None,
                        help="从 cache 重解析指定 run_id 的对局（不重新打平台）")
    parser.add_argument("--backfill-bombs", action="store_true",
                        help="对 DB 内缺炸弹统计的对局补拉 raw 并回填（仅更新炸弹列）")
    parser.add_argument("--backfill-limit", type=int, default=None,
                        help="与 --backfill-bombs 联用：只处理最近 N 场（按 created_at 倒序）")
    parser.add_argument("--backfill-run", type=int, default=None,
                        help="与 --backfill-bombs 联用：只处理指定 run_id 内的对局")
    parser.add_argument("--serve", action="store_true", help="完成后启动 report_server")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    if args.reset_db:
        if args.clear_cache and os.path.isdir(args.cache_dir):
            import shutil
            shutil.rmtree(args.cache_dir)
            print(f"已删除缓存: {args.cache_dir}")
        # 用 DROP TABLE 重建，避免 Windows 上 DB 被 report_server 占用时删文件失败
        db = AnalysisDB(args.db)
        db.reset()
        print(f"DB 已重建（空库）: {args.db}")
        if args.no_fetch and not args.match_id:
            sys.exit(0)

    db = AnalysisDB(args.db)
    profile = TANK_PROFILES[args.tank]
    tank_keys = get_tank_keys(profile, only=args.only)

    if args.reanalyze_run is not None:
        n = reanalyze_run(db, args.reanalyze_run, tank_keys, args)
        print_console_summary(db, args.reanalyze_run, min_rank_score=args.min_rank)
        print(f"\n重解析完成: {n} 场")
        if args.serve:
            from report_server import serve
            print(f"\n启动仪表盘: http://127.0.0.1:{args.port}")
            serve(args.db, args.port)
        sys.exit(0)

    if args.backfill_bombs:
        backfill_bomb_stats(db, tank_keys, args)
        sys.exit(0)

    if args.match_id:
        # 单场：用完整 profile 匹配双方；--only 只约束视角，不在此过滤 keys
        full_keys = get_tank_keys(profile, only=None)
        run_analysis_by_match_id(args, tank_keys=full_keys)
        sys.exit(0)

    if not args.no_fetch:
        run_id = run_analysis(args, tank_keys=tank_keys)
    else:
        runs = db.list_runs()
        if not runs:
            print("无历史 run，请先执行采集。")
            sys.exit(1)
        run_id = runs[0]["id"]
        print(f"跳过采集，使用最近 run #{run_id}")
        if args.serve:
            from report_server import serve
            print(f"\n启动仪表盘: http://127.0.0.1:{args.port}")
            serve(args.db, args.port)


if __name__ == "__main__":
    main()
