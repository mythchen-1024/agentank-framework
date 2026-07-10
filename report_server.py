#!/usr/bin/env python3
"""本地 HTTP 服务：REST API 读 SQLite + 静态仪表盘。"""

import argparse
import json
import mimetypes
import os
import re
import signal
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from analysis_db import AnalysisDB
from replay_loader import load_raw_from_cache

STATIC_DIR = os.path.join(os.path.dirname(__file__), "analysis", "static")


def json_response(handler, data, status=200):
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Access-Control-Allow-Origin", "http://127.0.0.1")
    handler.end_headers()
    handler.wfile.write(body)


def parse_int(val, default):
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


def parse_optional_int(val):
    """解析可选整数查询参数；空串/缺失返回 None。"""
    if val is None or str(val).strip() == "":
        return None
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


def filter_kwargs(qs):
    """从查询串提取 code_hash / min_rank_score，供各聚合端点共用。"""
    code_hash = qs.get("code_hash", [None])[0] or None
    min_rank_score = parse_optional_int(qs.get("min_rank_score", [None])[0])
    return {"code_hash": code_hash, "min_rank_score": min_rank_score}


class ReportHandler(BaseHTTPRequestHandler):
    db = None

    def log_message(self, fmt, *args):
        if os.environ.get("REPORT_SERVER_QUIET") != "1":
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        qs = parse_qs(parsed.query)

        if path.startswith("/api/"):
            self._handle_api(path, qs)
            return

        if path == "/" or path == "/index.html":
            self._serve_file(os.path.join(STATIC_DIR, "index.html"))
            return

        if path.startswith("/static/"):
            rel = path[len("/static/"):]
            safe = os.path.normpath(rel).lstrip(os.sep)
            if ".." in safe:
                self.send_error(403)
                return
            self._serve_file(os.path.join(STATIC_DIR, safe))
            return

        self.send_error(404)

    def do_DELETE(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        if not path.startswith("/api/"):
            self.send_error(404)
            return
        try:
            self._dispatch_delete(path)
        except Exception as e:
            sys.stderr.write(f"API DELETE error {path}: {e}\n")
            json_response(self, {"error": str(e)}, 500)

    def _dispatch_delete(self, path):
        m = re.match(r"^/api/runs/(\d+)$", path)
        if not m:
            json_response(self, {"error": "not found", "path": path}, 404)
            return
        run_id = int(m.group(1))
        result = self.db.delete_run(run_id)
        if result is None:
            json_response(self, {"error": "run not found", "run_id": run_id}, 404)
            return
        json_response(self, result)

    def _handle_api(self, path, qs):
        db = self.db
        try:
            self._dispatch_api(path, qs, db)
        except Exception as e:
            sys.stderr.write(f"API error {path}: {e}\n")
            json_response(self, {"error": str(e)}, 500)

    def _dispatch_api(self, path, qs, db):
        fk = filter_kwargs(qs)

        m = re.match(r"^/api/runs/(\d+)/overview$", path)
        if m:
            json_response(self, db.run_overview(int(m.group(1)), **fk))
            return

        m = re.match(r"^/api/runs/(\d+)/skill-matrix$", path)
        if m:
            json_response(self, db.run_skill_matrix(int(m.group(1)), **fk))
            return

        m = re.match(r"^/api/runs/(\d+)/code-hashes$", path)
        if m:
            json_response(self, db.run_code_hashes(int(m.group(1))))
            return

        m = re.match(r"^/api/runs/(\d+)/version-compare$", path)
        if m:
            run_id = int(m.group(1))
            hash_a = qs.get("hash_a", [None])[0]
            hash_b = qs.get("hash_b", [None])[0]
            if not hash_a or not hash_b:
                json_response(self, {"error": "hash_a and hash_b required"}, 400)
                return
            json_response(self, db.run_version_compare(
                run_id, hash_a, hash_b, min_rank_score=fk["min_rank_score"]
            ))
            return

        m = re.match(r"^/api/runs/(\d+)/opponents$", path)
        if m:
            run_id = int(m.group(1))
            data = db.run_opponents(
                run_id,
                min_samples=parse_int(qs.get("min_samples", ["1"])[0], 1),
                sort=qs.get("sort", ["worst"])[0],
                page=parse_int(qs.get("page", ["1"])[0], 1),
                page_size=parse_int(qs.get("page_size", ["20"])[0], 20),
                **fk,
            )
            json_response(self, data)
            return

        m = re.match(r"^/api/runs/(\d+)/behavior$", path)
        if m:
            json_response(self, db.run_behavior(
                int(m.group(1)),
                my_skill=qs.get("my_skill", [None])[0],
                result=qs.get("result", [None])[0],
                **fk,
            ))
            return

        m = re.match(r"^/api/runs/(\d+)/enemy-profiles$", path)
        if m:
            json_response(self, db.run_enemy_profiles(
                int(m.group(1)),
                result_filter=qs.get("result", [None])[0],
                **fk,
            ))
            return

        m = re.match(r"^/api/runs/(\d+)/win-patterns$", path)
        if m:
            json_response(self, db.run_win_patterns(int(m.group(1)), **fk))
            return

        m = re.match(r"^/api/runs/(\d+)/loss-patterns$", path)
        if m:
            json_response(self, db.run_loss_patterns(int(m.group(1)), **fk))
            return

        m = re.match(r"^/api/runs/(\d+)/cross-matrix$", path)
        if m:
            result = qs.get("result", ["L"])[0]
            if result not in ("W", "L"):
                result = "L"
            json_response(self, db.run_cross_matrix(int(m.group(1)), result=result, **fk))
            return

        m = re.match(r"^/api/runs/(\d+)/diagnosis$", path)
        if m:
            result = qs.get("result", ["L"])[0]
            if result not in ("W", "L"):
                result = "L"
            json_response(self, db.run_diagnosis_stats(int(m.group(1)), result=result, **fk))
            return

        m = re.match(r"^/api/runs/(\d+)/bt-layer-losses$", path)
        if m:
            json_response(self, db.run_bt_layer_losses(int(m.group(1)), **fk))
            return

        m = re.match(r"^/api/runs/(\d+)/profile-gaps$", path)
        if m:
            json_response(self, db.run_profile_gaps(int(m.group(1)), **fk))
            return

        m = re.match(r"^/api/runs/(\d+)/result-reasons$", path)
        if m:
            json_response(self, db.run_result_reason_stats(int(m.group(1)), **fk))
            return

        m = re.match(r"^/api/matches/([^/]+)/timeline$", path)
        if m:
            match_id = m.group(1)
            data = db.get_match_timeline(match_id)
            if data is None:
                json_response(self, {"error": "match not found", "match_url_id": match_id}, 404)
                return
            json_response(self, data)
            return

        # 地图帧回放：只读本地 raw 缓存，缓存缺失时提示 reanalyze（不在线拉平台 API）
        m = re.match(r"^/api/matches/([^/]+)/replay-raw$", path)
        if m:
            match_id = m.group(1)
            data = load_raw_from_cache(match_id)
            if data is None:
                json_response(self, {
                    "error": "no_raw_cache",
                    "match_url_id": match_id,
                    "hint": "本地无 raw 缓存。对该批次执行 python match_analyzer.py --reanalyze-run <run_id> 或重新采集负场后重试",
                }, 404)
                return
            data["anchor_frame"] = db.get_match_anchor(match_id)
            json_response(self, data)
            return

        if path == "/api/runs":
            json_response(self, db.list_runs())
            return

        m = re.match(r"^/api/runs/(\d+)/matches$", path)
        if m:
            merged = {k: v for k, v in qs.items()}
            merged.setdefault("run_id", [m.group(1)])
            self._api_list_matches(merged, db)
            return

        if path == "/api/matches":
            self._api_list_matches(qs, db)
            return

        if path == "/api/health":
            json_response(self, {
                "ok": True,
                "version": 7,
                "matches_api": [
                    "/api/matches", "/api/runs/{id}/matches",
                    "/api/matches/{id}/timeline", "/api/matches/{id}/replay-raw",
                ],
                "tactics_api": [
                    "/api/runs/{id}/loss-patterns",
                    "/api/runs/{id}/cross-matrix",
                    "/api/runs/{id}/diagnosis",
                    "/api/runs/{id}/bt-layer-losses",
                    "/api/runs/{id}/profile-gaps",
                    "/api/runs/{id}/result-reasons",
                ],
                "version_api": [
                    "/api/runs/{id}/code-hashes",
                    "/api/runs/{id}/version-compare?hash_a=&hash_b=",
                ],
                "filters": ["code_hash", "min_rank_score"],
                "match_filters": [
                    "my_skill", "won", "opponent",
                    "enemy_skill", "result_reason", "loss_tactic", "gap_tag", "match_id",
                    "has_bomb",
                ],
                "delete_run": "DELETE /api/runs/{id}",
            })
            return

        json_response(self, {"error": "not found", "path": path}, 404)

    def _api_list_matches(self, qs, db):
        run_id = parse_int(qs.get("run_id", [None])[0], None)
        if run_id is None:
            json_response(self, {"error": "run_id required"}, 400)
            return
        fk = filter_kwargs(qs)
        won_raw = qs.get("won", [None])[0]
        won = None
        # 显式解析胜负筛选：won=1 胜场，won=0 负场，不传则全部
        if won_raw is not None and str(won_raw) != "":
            won = str(won_raw).lower() in ("1", "true")
        data = db.list_matches(
            run_id,
            my_skill=qs.get("my_skill", [None])[0],
            opponent=qs.get("opponent", [None])[0],
            won=won,
            page=parse_int(qs.get("page", ["1"])[0], 1),
            page_size=parse_int(qs.get("page_size", ["30"])[0], 30),
            # 补充筛选维度（对局明细多维搜索）
            enemy_skill=qs.get("enemy_skill", [None])[0] or None,
            result_reason=qs.get("result_reason", [None])[0] or None,
            loss_tactic=qs.get("loss_tactic", [None])[0] or None,
            gap_tag=qs.get("gap_tag", [None])[0] or None,
            match_id=qs.get("match_id", [None])[0] or None,
            has_bomb=qs.get("has_bomb", [None])[0] or None,
            **fk,
        )
        json_response(self, data)

    def _serve_file(self, filepath):
        if not os.path.isfile(filepath):
            self.send_error(404)
            return
        mime, _ = mimetypes.guess_type(filepath)
        with open(filepath, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", mime or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        # 开发阶段避免浏览器缓存旧版 app.js
        if filepath.endswith((".js", ".html", ".css")):
            self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)


def _pids_listening_on(port, host="127.0.0.1"):
    """查询当前占用端口的 LISTENING 进程 PID（用于启动前清理旧 report_server）。"""
    pids = set()
    if sys.platform == "win32":
        try:
            out = subprocess.check_output(
                ["netstat", "-ano"], text=True, errors="replace", creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except (subprocess.CalledProcessError, FileNotFoundError):
            return pids
        # 匹配 127.0.0.1:port 与 0.0.0.0:port（IDE 多次启动常会叠多个实例）
        hosts = {host}
        if host in ("127.0.0.1", "localhost"):
            hosts.add("0.0.0.0")
        for line in out.splitlines():
            if "LISTENING" not in line.upper():
                continue
            for h in hosts:
                m = re.search(rf"TCP\s+{re.escape(h)}:{port}\s+\S+\s+LISTENING\s+(\d+)", line, re.I)
                if m:
                    pids.add(int(m.group(1)))
    else:
        try:
            out = subprocess.check_output(
                ["lsof", "-i", f"TCP:{port}", "-sTCP:LISTEN", "-t"], text=True, errors="replace",
            )
            for line in out.splitlines():
                if line.strip().isdigit():
                    pids.add(int(line.strip()))
        except (FileNotFoundError, subprocess.CalledProcessError):
            pass
    pids.discard(0)
    return pids


def kill_stale_listeners(port, host="127.0.0.1", exclude_pid=None):
    """
    启动前结束同端口的旧监听进程，避免 IDE 重复运行叠多个 v5 实例导致红条与 API 错乱。
    默认排除当前进程 PID；返回已结束的 PID 列表。
    """
    exclude_pid = exclude_pid if exclude_pid is not None else os.getpid()
    targets = _pids_listening_on(port, host) - {exclude_pid}
    killed = []
    for pid in sorted(targets):
        try:
            if sys.platform == "win32":
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/F"],
                    check=True,
                    capture_output=True,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
            else:
                os.kill(pid, signal.SIGTERM)
            killed.append(pid)
        except (ProcessLookupError, subprocess.CalledProcessError, PermissionError, OSError) as e:
            sys.stderr.write(f"无法结束旧实例 PID {pid}: {e}\n")
    if killed:
        print(f"已结束占用 {host}:{port} 的旧 report_server 进程: {killed}")
        time.sleep(0.4)
    return killed


def serve(db_path, port=8765, host="127.0.0.1", kill_stale=True):
    if kill_stale:
        kill_stale_listeners(port, host)
    db = AnalysisDB(db_path)
    ReportHandler.db = db
    server = ThreadingHTTPServer((host, port), ReportHandler)
    print(f"report_server 监听 http://{host}:{port}/  DB={db_path}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
        server.shutdown()


def main():
    parser = argparse.ArgumentParser(description="对局分析仪表盘 HTTP 服务")
    parser.add_argument("--db", default=os.path.join("analysis", "data.db"))
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument(
        "--no-kill-stale", action="store_true",
        help="启动时不自动结束同端口的旧 report_server 实例",
    )
    args = parser.parse_args()
    serve(args.db, args.port, args.host, kill_stale=not args.no_kill_stale)


if __name__ == "__main__":
    main()
