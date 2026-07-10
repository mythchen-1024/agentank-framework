#!/usr/bin/env python3
"""
批量对战胜率统计工具

用法:
  python match_runner.py                              # 跑 tank_profiles 全部坦克，各 10 场，赛后自动分析
  python match_runner.py -n 10                        # 每坦克各跑 10 场
  python match_runner.py --only demo-boost,demo-shield -n 15
  python match_runner.py --delay 1.5                  # 每局间隔 1.5 秒（默认 0.5）
  python match_runner.py --save results.json          # 结果保存到 JSON 文件
  python match_runner.py --map grassy_field           # 固定地图（默认 random）
  python match_runner.py --opponent tnk_8lbIwTIeW23JbJ9YF             # 指定对手坦克 ID（反复挑战同一人）
  python match_runner.py --no-analyze                 # 赛后不自动跑 match_analyzer
  python match_runner.py --analyze-serve              # 赛后分析并启动 report_server

坦克列表与 publish / match_analyzer 共用 tank_profiles.py（--tank / --only）。
"""

import argparse
import json
import os
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime

from tank_profiles import DEFAULT_TANK, TANK_PROFILES, get_tank_keys

# Windows 控制台强制 UTF-8
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE_URL = "https://agentank.ai"
DEFAULT_ROUNDS = 20
DEFAULT_DELAY = 0.5
DEFAULT_ANALYZE_DB = os.path.join("analysis", "data.db")
# 瞬时 SSL/断连重试：平台侧偶发掐 TLS，不重试会白白丢掉场次
# battle function failed 偶发可恢复：多给几次退避（实测重试后常能打成 W/L）
API_RETRIES = 5
API_RETRY_BASE_SEC = 1.5


def load_dotenv():
    """加载同目录 .env，供 tank_profiles 读 AGENTANK_KEY_*。"""
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(env_path):
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    os.environ.setdefault(k.strip(), v.strip())


def _read_http_error_body(exc):
    """完整读取 HTTPError 响应体（只读一次；读后挂到 exc._body_text 供后续复用）。"""
    cached = getattr(exc, "_body_text", None)
    if cached is not None:
        return cached
    try:
        raw = exc.read()
        text = raw.decode("utf-8", errors="replace").strip() if raw else ""
    except Exception:
        text = ""
    exc._body_text = text
    return text


def _format_error_body(body_text):
    """尽量把响应体格式化成可读全文，避免看起来像被截断。"""
    if not body_text:
        return ""
    # 平台常返回: battle function failed: 400 Bad Request { json }
    brace = body_text.find("{")
    if brace >= 0:
        prefix = body_text[:brace].strip()
        json_part = body_text[brace:]
        try:
            pretty = json.dumps(json.loads(json_part), ensure_ascii=False, indent=2)
            return f"{prefix}\n{pretty}" if prefix else pretty
        except Exception:
            pass
    try:
        return json.dumps(json.loads(body_text), ensure_ascii=False, indent=2)
    except Exception:
        return body_text


def _is_transient_http_error(exc):
    """网关抖动，或平台 battle 执行瞬时失败（可重试）。"""
    if not isinstance(exc, urllib.error.HTTPError):
        return False
    if exc.code in (502, 503, 504):
        return True
    if exc.code != 400:
        return False
    body = _read_http_error_body(exc).lower()
    # 这类 400 是平台侧 battle 跑挂，不是参数错误；实测可重试成功
    return "battle function failed" in body or "error processing your request" in body


def _is_transient_network_error(exc):
    """判断是否为可重试的网络/SSL/瞬时平台错误。"""
    if isinstance(exc, TimeoutError):
        return True
    if isinstance(exc, urllib.error.URLError) and not isinstance(exc, urllib.error.HTTPError):
        reason = exc.reason
        text = str(reason).lower() if reason is not None else str(exc).lower()
        needles = (
            "unexpected_eof",
            "eof occurred",
            "connection reset",
            "connection aborted",
            "timed out",
            "temporarily unavailable",
            "broken pipe",
            "ssl",
        )
        return any(n in text for n in needles)
    return _is_transient_http_error(exc)


def _urlopen_json(req, retries=API_RETRIES, retry_counter=None):
    """带退避重试的 urlopen+JSON 解析，专治 SSL EOF / 断连 / battle 瞬时失败。

    retry_counter: 可选 dict，成功恢复时累加 recovered / battle_failed 计数，
    供 run_tank 汇总区分「瞬时失败已恢复」与真正 ERROR。
    """
    last_exc = None
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if attempt > 0 and isinstance(retry_counter, dict):
                    retry_counter["recovered"] = retry_counter.get("recovered", 0) + 1
                return data
        except Exception as e:
            last_exc = e
            if attempt >= retries or not _is_transient_network_error(e):
                raise
            wait = API_RETRY_BASE_SEC * (2 ** attempt)
            hint = e
            is_battle = False
            if isinstance(e, urllib.error.HTTPError):
                body = _read_http_error_body(e)
                hint = f"HTTP {e.code}: {body[:160]}" if body else e
                low = (body or "").lower()
                is_battle = "battle function failed" in low or "error processing your request" in low
            if isinstance(retry_counter, dict):
                retry_counter["attempts"] = retry_counter.get("attempts", 0) + 1
                if is_battle:
                    retry_counter["battle_failed"] = retry_counter.get("battle_failed", 0) + 1
            # battle function failed 也走此重试（平台瞬时跑挂）；打满仍失败才上抛 ERROR
            kind = "battle瞬时失败" if is_battle else "网络抖动"
            print(f"    {kind}重试 {attempt + 1}/{retries}，{wait:.1f}s 后重试: {hint}")
            time.sleep(wait)
    raise last_exc


def api_post(path, tank_key, body=None, retry_counter=None):
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {tank_key}",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json, text/plain, */*",
    }
    data = json.dumps(body).encode("utf-8") if body else None
    req = urllib.request.Request(BASE_URL + path, data=data, headers=headers, method="POST")
    return _urlopen_json(req, retry_counter=retry_counter)


def api_get(path, tank_key, retry_counter=None):
    headers = {
        "Authorization": f"Bearer {tank_key}",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json, text/plain, */*",
    }
    req = urllib.request.Request(BASE_URL + path, headers=headers, method="GET")
    return _urlopen_json(req, retry_counter=retry_counter)


def resolve_opponent_id(tank_key, identifier):
    """将对手标识（数字ID 或 tnk_字符串 或 坦克名）解析为数字 ID。"""
    # 已经是纯数字
    if identifier.isdigit():
        return int(identifier)

    # 从最近对局里按 tnk_ 或名字匹配
    matches = api_get("/api/agent/tank/matches?limit=50&offset=0", tank_key)
    for m in matches if isinstance(matches, list) else matches.get("matches", []):
        # 检查对手字段
        for field_id, field_name in [("defenderTankId", "defenderTankName"),
                                     ("challengerTankId", "challengerTankName")]:
            tid = m.get(field_id)
            tname = m.get(field_name, "")
            turl_id = m.get("defenderTankUrlId", "") or m.get("challengerTankUrlId", "")
            if str(tid) == identifier or turl_id == identifier or tname == identifier:
                return int(tid)

    print(f"  警告: 无法解析对手 '{identifier}'，尝试直接当数字使用")
    return int(identifier)


def run_challenge(tank_key, map_id, my_tank_id_ref, opponent_id=None, retry_counter=None):
    """发起排名挑战，返回 (tag, opponent, url_id)"""
    body = {"mapId": map_id}
    if opponent_id:
        body["opponentTankId"] = opponent_id
    else:
        body["randomOpponent"] = True

    result = api_post("/api/agent/tank/challenge", tank_key, body, retry_counter=retry_counter)
    url_id = result.get("urlId") or result.get("matchUrlId", "")
    winner = result.get("winnerTankId")
    opponent = result.get("defenderTankName", "?")
    challenger_id = result.get("challengerTankId")

    if my_tank_id_ref[0] is None and challenger_id:
        my_tank_id_ref[0] = challenger_id

    my_id = my_tank_id_ref[0]
    if winner == my_id:
        tag = "W"
    elif winner and winner != my_id:
        tag = "L"
    else:
        tag = "D"

    return tag, opponent, url_id


def run_simulate(tank_key, bot_name, retry_counter=None):
    """发起模拟对战，返回 (tag, bot_name, url_id)"""
    result = api_post(
        "/api/agent/tank/simulate", tank_key, {"opponentBot": bot_name},
        retry_counter=retry_counter,
    )
    winner = result.get("winner")
    url_id = result.get("urlId") or result.get("matchUrlId", "")

    if winner == "me":
        tag = "W"
    elif winner in ("opponent", "training-bot"):
        tag = "L"
    elif winner == "tie":
        tag = "D"
    else:
        tag = "D"

    return tag, bot_name, url_id


def run_tank(tank_key, tank_name, rounds, mode, map_id, bot_name, delay, opponent_id=None):
    """对单个坦克跑 rounds 场比赛，返回详细记录列表和统计。"""
    print(f"\n{'='*56}")
    opponent_hint = f"  对手:{opponent_id}" if opponent_id else ""
    print(f"  {tank_name}  ({tank_key[:16]}...)  模式:{mode}  场次:{rounds}{opponent_hint}")
    print(f"{'='*56}")

    wins = losses = draws = errors = 0
    records = []
    my_tank_id_ref = [None]
    # 瞬时失败统计：recovered=重试后打成 W/L；battle_failed=触发过 battle 400
    retry_stats = {"attempts": 0, "recovered": 0, "battle_failed": 0}

    # 解析对手 ID（支持数字/tnk_字符串/坦克名）
    resolved_opponent = None
    if opponent_id:
        try:
            resolved_opponent = resolve_opponent_id(tank_key, opponent_id)
            print(f"  对手数字 ID: {resolved_opponent}")
        except Exception as e:
            print(f"  解析对手 ID 失败: {e}，将直接使用原值")
            resolved_opponent = int(opponent_id) if opponent_id.isdigit() else None

    for i in range(rounds):
        ts = datetime.now().strftime("%H:%M:%S")
        try:
            if mode == "challenge":
                tag, opponent, url_id = run_challenge(
                    tank_key, map_id, my_tank_id_ref, resolved_opponent,
                    retry_counter=retry_stats,
                )
            else:
                tag, opponent, url_id = run_simulate(
                    tank_key, bot_name, retry_counter=retry_stats,
                )

            if tag == "W":
                wins += 1
            elif tag == "L":
                losses += 1
            else:
                draws += 1

            total = wins + losses + draws
            wr = wins / total * 100 if total else 0
            match_url = f"https://agentank.ai/history/{url_id}" if url_id else ""
            print(f"  [{i+1:>3}/{rounds}] {tag}  vs {opponent:<24}  W:{wins} L:{losses} D:{draws}  WR:{wr:5.1f}%  {ts}")

            records.append({
                "index": i + 1,
                "result": tag,
                "opponent": opponent,
                "url": match_url,
                "time": ts,
            })

        except urllib.error.HTTPError as e:
            errors += 1
            # 完整保留响应体并格式化，便于排查（平台原文往往就只有一句模糊 error）
            body_text = _read_http_error_body(e)
            pretty = _format_error_body(body_text)
            err_msg = f"HTTP {e.code}: {pretty}" if pretty else f"HTTP {e.code}: {e.reason}"
            # 多行错误缩进打印，避免和进度行糊在一起、也避免看起来像被截断
            first, *rest = err_msg.splitlines() or [err_msg]
            print(f"  [{i+1:>3}/{rounds}] ERROR {first}")
            for line in rest:
                print(f"           {line}")
            records.append({"index": i + 1, "result": "ERROR", "error": err_msg, "time": ts})

        except Exception as e:
            errors += 1
            err_msg = f"{type(e).__name__}: {e}"
            print(f"  [{i+1:>3}/{rounds}] ERROR: {err_msg}")
            records.append({"index": i + 1, "result": "ERROR", "error": err_msg, "time": ts})

        time.sleep(delay)

    total = wins + losses + draws
    wr = wins / total * 100 if total else 0

    print(f"\n  ── {tank_name} 汇总 ──")
    print(f"  有效场次: {total}  错误: {errors}")
    print(f"  胜 {wins}  负 {losses}  平 {draws}  胜率: {wr:.1f}%")
    if retry_stats.get("attempts"):
        print(
            f"  瞬时重试: {retry_stats['attempts']} 次"
            f"（battle失败 {retry_stats.get('battle_failed', 0)}，"
            f"重试后恢复 {retry_stats.get('recovered', 0)}）"
        )

    loss_records = [r for r in records if r.get("result") == "L" and r.get("url")]
    if loss_records:
        print(f"  负场回放:")
        for r in loss_records:
            print(f"    [{r['index']:>3}] vs {r['opponent']}  {r['url']}")

    # 汇总时再打一遍完整错误，避免长 JSON 混在进度行里不好回看
    error_records = [r for r in records if r.get("result") == "ERROR"]
    if error_records:
        print(f"  错误详情:")
        for r in error_records:
            print(f"    [{r['index']:>3}] {r.get('error', '')}")

    return {
        "tank_key": tank_key,
        "tank_name": tank_name,
        "wins": wins,
        "losses": losses,
        "draws": draws,
        "errors": errors,
        "win_rate": round(wr, 2),
        "records": records,
    }


def resolve_tanks(args):
    """从 tank_profiles 或 -k 原始 key 解析要跑的坦克列表。"""
    if args.keys:
        # -k 仍支持裸 key；若能匹配 profile 则带上 name/skill 供赛后分析
        profile_keys = {k["key"]: k for k in get_tank_keys(TANK_PROFILES[args.tank])}
        tanks = []
        for raw in args.keys:
            if raw in profile_keys:
                tanks.append(profile_keys[raw])
            else:
                tanks.append({"key": raw, "name": raw[:20] + "..."})
        return tanks
    profile = TANK_PROFILES[args.tank]
    return get_tank_keys(profile, only=args.only)


def run_post_match_analysis(args, tanks):
    """全部对局结束后，拉取刚打过的场次并写入 analysis DB。"""
    from match_analyzer import make_analysis_args, run_analysis

    print(f"\n{'='*56}")
    print("  对局结束，自动运行 match_analyzer 入库...")
    print(f"{'='*56}")
    analysis_args = make_analysis_args(
        # +5适配我们对战时其他人打我的情况
        limit=args.rounds+5,
        only=args.only,
        tank=args.tank,
        db=args.analyze_db,
        serve=args.analyze_serve,
    )
    run_id = run_analysis(analysis_args, tank_keys=tanks)
    print(f"\n分析完成 Run #{run_id}  DB={args.analyze_db}")
    if not args.analyze_serve:
        print(f"启动仪表盘: python report_server.py --db {args.analyze_db}")


def print_summary(all_stats):
    print(f"\n{'='*56}")
    print("  全部坦克汇总")
    print(f"{'='*56}")
    print(f"  {'坦克名':<24} {'场次':>4}  {'胜':>4}  {'负':>4}  {'平':>4}  {'胜率':>7}")
    print(f"  {'-'*52}")
    for s in all_stats:
        total = s["wins"] + s["losses"] + s["draws"]
        print(f"  {s['tank_name']:<24} {total:>4}  {s['wins']:>4}  {s['losses']:>4}  {s['draws']:>4}  {s['win_rate']:>6.1f}%")
    print(f"{'='*56}")


def main():
    load_dotenv()
    parser = argparse.ArgumentParser(description="AgenTank 批量对战胜率统计")
    parser.add_argument("-k", "--keys", nargs="+", metavar="KEY",
                        help="指定一个或多个坦克 API key（不填则使用 tank_profiles）")
    parser.add_argument("--tank", "-t", default=DEFAULT_TANK, choices=list(TANK_PROFILES.keys()),
                        help=f"坦克档案（默认 {DEFAULT_TANK}，与 publish/match_analyzer 共用）")
    parser.add_argument("--only", "-o", default=None,
                        help="只跑指定坦克名，逗号分隔（如 demo-boost,demo-shield）")
    parser.add_argument("-n", "--rounds", type=int, default=DEFAULT_ROUNDS,
                        help=f"每坦克比赛场次（默认: {DEFAULT_ROUNDS}）")
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY,
                        help=f"每局间隔秒数（默认: {DEFAULT_DELAY}）")
    parser.add_argument("--mode", choices=["challenge", "simulate"], default="challenge",
                        help="比赛模式: challenge=排名对战(默认)  simulate=机器人模拟")
    parser.add_argument("--map", default="random",
                        help="地图 ID（challenge 模式，默认: random）")
    parser.add_argument("--bot", default="azure-hunter",
                        help="机器人名（simulate 模式，默认: azure-hunter）")
    parser.add_argument("--opponent", metavar="TANK_ID",
                        help="指定对手（数字ID / tnk_字符串 / 坦克名，会自动从对局历史解析）")
    parser.add_argument("--save", metavar="FILE",
                        help="将详细结果保存为 JSON 文件（如 results.json）")
    parser.add_argument("--no-analyze", action="store_true",
                        help="全部对局结束后不自动运行 match_analyzer")
    parser.add_argument("--analyze-db", default=DEFAULT_ANALYZE_DB,
                        help=f"赛后分析写入的 SQLite 路径（默认: {DEFAULT_ANALYZE_DB}）")
    parser.add_argument("--analyze-serve", action="store_true",
                        help="赛后分析完成后启动 report_server 仪表盘")
    args = parser.parse_args()

    tanks = resolve_tanks(args)
    if not tanks:
        print("错误: 没有可用的坦克 key，请在 tank_profiles.py 配置或使用 -k 传入。")
        sys.exit(1)

    print(f"档案: {args.tank}  共 {len(tanks)} 个坦克，每坦克 {args.rounds} 场，模式: {args.mode}")

    all_stats = []
    for t in tanks:
        stats = run_tank(
            tank_key=t["key"],
            tank_name=t["name"],
            rounds=args.rounds,
            mode=args.mode,
            map_id=args.map,
            bot_name=args.bot,
            delay=args.delay,
            opponent_id=args.opponent,
        )
        all_stats.append(stats)

    print_summary(all_stats)

    if args.save:
        output = {
            "generated_at": datetime.now().isoformat(),
            "rounds": args.rounds,
            "mode": args.mode,
            "tanks": all_stats,
        }
        with open(args.save, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
        print(f"\n详细结果已保存: {args.save}")

    if not args.no_analyze:
        run_post_match_analysis(args, tanks)


if __name__ == "__main__":
    main()
