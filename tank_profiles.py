#!/usr/bin/env python3
"""坦克档案配置，供 publish-new.py / match_runner.py / match_analyzer.py 共用。

密钥从环境变量读取，勿把真实 agtk_ key 提交进仓库。
复制 .env.example 为 .env 后填写。各脚本应在 get_tank_keys 前调用 load_dotenv。
"""

import os
import sys


def _key(env_name):
    """读取环境变量；未设置时返回占位符，便于 dry-run / 文档演示。"""
    return os.environ.get(env_name, "agtk_YOUR_KEY")


def _build_keys():
    """每次调用时读环境变量，确保 load_dotenv 之后生效。"""
    return [
        {"name": "demo-teleport", "key": _key("AGENTANK_KEY_TELEPORT"), "skill": "teleport"},
        {"name": "demo-shield", "key": _key("AGENTANK_KEY_SHIELD"), "skill": "shield"},
        {"name": "demo-boost", "key": _key("AGENTANK_KEY_BOOST"), "skill": "boost"},
        {"name": "demo-overload", "key": _key("AGENTANK_KEY_OVERLOAD"), "skill": "overload"},
        {"name": "demo-stun", "key": _key("AGENTANK_KEY_STUN"), "skill": "stun"},
        {"name": "demo-freeze", "key": _key("AGENTANK_KEY_FREEZE"), "skill": "freeze"},
        {"name": "demo-cloak", "key": _key("AGENTANK_KEY_CLOAK"), "skill": "cloak"},
        {"name": "demo-poison", "key": _key("AGENTANK_KEY_POISON"), "skill": "poison"},
    ]


TANK_PROFILES = {
    "bt": {
        # keys 由 get_tank_keys 动态生成（读 .env）
        "file": "dist/bt-tank-{skill}.js",
        "branch": "main",
        "build_cmd": ["node", "build-new.js", "--all-skills"],
        "submitted_by": "framework",
    },
}
DEFAULT_TANK = "bt"


def get_tank_keys(profile, only=None):
    """获取档案中的 key 列表，支持 --only 过滤。"""
    keys = profile.get("keys") or _build_keys()
    if not keys:
        print("错误: 该坦克档案未配置 keys。请在 .env 中填写 AGENTANK_KEY_*。")
        sys.exit(1)
    if only:
        names = [n.strip() for n in only.split(",")]
        filtered = [k for k in keys if k["name"] in names]
        not_found = set(names) - {k["name"] for k in filtered}
        if not_found:
            all_names = [k["name"] for k in keys]
            print(f"错误: 未找到坦克名: {', '.join(not_found)}  可用: {', '.join(all_names)}")
            sys.exit(1)
        return filtered
    return keys
