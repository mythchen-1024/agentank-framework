#!/usr/bin/env python3
"""从本地 raw 缓存读取回放帧数据，供 report_server 地图帧查看器使用。

只读 .cache/matches/matches_{id}_raw.json（match_analyzer 拉取时写入），
不主动请求平台 API；缓存缺失时由调用方提示用户 reanalyze。
"""

import json
import os
import re

# 与 match_analyzer.DEFAULT_CACHE 保持一致（相对 report_server 启动目录）
DEFAULT_CACHE = os.path.join(".cache", "matches")

_MATCH_ID_RE = re.compile(r"^mat_[A-Za-z0-9]+$")


def _decode_log_text(data):
    """raw logs 的 data 是 JSON 转义过的字符串（自带引号），先解一层（同 match_analyzer）。"""
    if isinstance(data, str) and data.startswith('"'):
        try:
            val = json.loads(data)
            return val if isinstance(val, str) else data
        except (ValueError, TypeError):
            return data.strip('"')
    return data if isinstance(data, str) else None


def load_raw_from_cache(match_url_id, cache_dir=DEFAULT_CACHE):
    """
    读取 raw 缓存并提取地图 + 每帧事件记录。
    返回精简 dict（供前端逐帧重建），缓存不存在或结构异常时返回 None。
    """
    # 防路径穿越：match_url_id 只允许 mat_ 前缀的字母数字
    if not _MATCH_ID_RE.match(match_url_id or ""):
        return None
    path = os.path.join(cache_dir, f"matches_{match_url_id}_raw.json")
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None

    rd = raw.get("replayData") or {}
    replay = rd.get("replay") or {}
    records = replay.get("records")
    map_obj = rd.get("map") or {}
    grid = map_obj.get("map")
    if not isinstance(records, list) or not isinstance(grid, list) or not grid:
        return None

    # meta.players[i].tank 是每个玩家的初始坦克状态（id/position/direction），
    # 前端用它做 objectId → 玩家索引的权威映射（events 里的 by 字段在 crashed 等事件上是击杀者，不可靠）
    meta_players = (replay.get("meta") or {}).get("players") or []
    player_tanks = [p.get("tank") for p in meta_players]

    # 全程 print 日志：DB 的 timeline 只存终局窗口，这里补全供弹窗从 f0 展示
    logs = []
    for i, p in enumerate(meta_players):
        for lg in p.get("logs") or []:
            text = _decode_log_text(lg.get("data", ""))
            if text:
                logs.append({"frame": lg.get("frame", 0), "player": i, "text": text})
    logs.sort(key=lambda x: x["frame"])

    return {
        "match_url_id": match_url_id,
        "source": "cache",
        "names": rd.get("names") or [],
        "map": {
            "grid": grid,
            "name": map_obj.get("name"),
            "spawn": map_obj.get("players") or [],
        },
        "player_tanks": player_tanks,
        "logs": logs,
        "records": records,
        "total_frames": len(records),
    }
