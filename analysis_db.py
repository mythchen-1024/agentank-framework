#!/usr/bin/env python3
"""SQLite 持久化层：对局分析数据的 schema 与 upsert 操作。"""

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS analysis_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at TEXT NOT NULL,
    n_per_tank INTEGER NOT NULL,
    tank_filter TEXT,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS my_tanks (
    tank_name TEXT PRIMARY KEY,
    skill TEXT NOT NULL,
    tank_id INTEGER,
    rank_score INTEGER
);

CREATE TABLE IF NOT EXISTS opponents (
    opponent_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    rank_tier TEXT,
    rank_score INTEGER,
    wins INTEGER,
    losses INTEGER,
    draws INTEGER
);

CREATE TABLE IF NOT EXISTS matches (
    match_url_id TEXT PRIMARY KEY,
    my_tank_name TEXT NOT NULL,
    my_skill TEXT NOT NULL,
    opponent_id INTEGER,
    opponent_name TEXT NOT NULL,
    won INTEGER NOT NULL DEFAULT 0,
    draw INTEGER NOT NULL DEFAULT 0,
    map_id TEXT,
    result_reason TEXT,
    created_at TEXT,
    history_url TEXT,
    last_seen_run_id INTEGER,
    my_code_hash TEXT,
    FOREIGN KEY (opponent_id) REFERENCES opponents(opponent_id)
);

CREATE TABLE IF NOT EXISTS run_matches (
    run_id INTEGER NOT NULL,
    match_url_id TEXT NOT NULL,
    PRIMARY KEY (run_id, match_url_id),
    FOREIGN KEY (run_id) REFERENCES analysis_runs(id),
    FOREIGN KEY (match_url_id) REFERENCES matches(match_url_id)
);

CREATE TABLE IF NOT EXISTS match_stats (
    match_url_id TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('me', 'enemy')),
    frames_total INTEGER,
    shots_fired INTEGER,
    shots_hit INTEGER,
    shots_wall INTEGER,
    moves INTEGER,
    turns INTEGER,
    stars INTEGER,
    skill_used INTEGER,
    crashes INTEGER,
    runtime_ms INTEGER,
    diagnosis TEXT,
    PRIMARY KEY (match_url_id, side),
    FOREIGN KEY (match_url_id) REFERENCES matches(match_url_id)
);

CREATE TABLE IF NOT EXISTS match_tactics (
    match_url_id TEXT PRIMARY KEY,
    my_skill_inferred TEXT,
    enemy_skill TEXT,
    my_first_skill_frame INTEGER,
    enemy_first_skill_frame INTEGER,
    death_frame INTEGER,
    my_win_tactic TEXT,
    my_loss_tactic TEXT,
    enemy_playstyle_tags TEXT,
    inferred_bt_layers TEXT,
    profile_gap_tags TEXT,
    has_events INTEGER NOT NULL DEFAULT 0,
    real_bt_layers TEXT,
    profile_timeline TEXT,
    has_raw INTEGER NOT NULL DEFAULT 0,
    enemy_stars INTEGER,
    star_delta INTEGER,
    my_skill_casts INTEGER,
    dominant_bt_layer TEXT,
    anchor_frame INTEGER,
    critical_events_summary TEXT,
    has_bomb INTEGER,
    my_bomb_placed INTEGER,
    enemy_bomb_placed INTEGER,
    bomb_total INTEGER,
    FOREIGN KEY (match_url_id) REFERENCES matches(match_url_id)
);

-- 负场终局窗口：线上 events 关键帧（实证）
CREATE TABLE IF NOT EXISTS match_key_events (
    match_url_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    frame INTEGER NOT NULL,
    kind TEXT NOT NULL,
    actor TEXT,
    side TEXT,
    detail_json TEXT,
    PRIMARY KEY (match_url_id, seq),
    FOREIGN KEY (match_url_id) REFERENCES matches(match_url_id)
);

-- 负场终局窗口：print 行为日志（BT 轨迹 + PF 画像）
CREATE TABLE IF NOT EXISTS match_behavior_log (
    match_url_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    frame INTEGER NOT NULL,
    kind TEXT NOT NULL,
    text TEXT,
    PRIMARY KEY (match_url_id, seq),
    FOREIGN KEY (match_url_id) REFERENCES matches(match_url_id)
);

-- 保留全量 BT trace（seq 允许多帧多条 print，避免同帧覆盖）
CREATE TABLE IF NOT EXISTS match_bt_trace (
    match_url_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    frame INTEGER NOT NULL,
    layer TEXT,
    action TEXT,
    PRIMARY KEY (match_url_id, seq),
    FOREIGN KEY (match_url_id) REFERENCES matches(match_url_id)
);

CREATE INDEX IF NOT EXISTS idx_matches_my_skill ON matches(my_skill);
CREATE INDEX IF NOT EXISTS idx_matches_opponent ON matches(opponent_name);
CREATE INDEX IF NOT EXISTS idx_run_matches_run ON run_matches(run_id);
"""


class AnalysisDB:
    """对局分析 SQLite 访问封装。"""

    def __init__(self, db_path):
        self.db_path = os.path.abspath(db_path)
        os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
        self._init_schema()

    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_schema(self):
        with self._connect() as conn:
            conn.executescript(SCHEMA_SQL)
            self._migrate_schema(conn)

    def _migrate_schema(self, conn):
        """对已有 DB 追加新列，避免旧库直接报错。"""
        cols = {row[1] for row in conn.execute("PRAGMA table_info(match_tactics)").fetchall()}
        additions = [
            ("my_loss_tactic", "TEXT"),
            ("inferred_bt_layers", "TEXT"),
            ("profile_gap_tags", "TEXT"),
            # raw 遥测解析新列：真实 BT 层级 / 画像时间线 / 是否已解析 raw
            ("real_bt_layers", "TEXT"),
            ("profile_timeline", "TEXT"),
            ("has_raw", "INTEGER NOT NULL DEFAULT 0"),
            ("enemy_stars", "INTEGER"),
            ("star_delta", "INTEGER"),
            ("my_skill_casts", "INTEGER"),
            ("dominant_bt_layer", "TEXT"),
            ("anchor_frame", "INTEGER"),
            ("critical_events_summary", "TEXT"),
            # raw records 炸弹统计：NULL=无 raw 未知，0=无炸弹，1=有炸弹
            ("has_bomb", "INTEGER"),
            ("my_bomb_placed", "INTEGER"),
            ("enemy_bomb_placed", "INTEGER"),
            ("bomb_total", "INTEGER"),
        ]
        for name, col_type in additions:
            if name not in cols:
                conn.execute(f"ALTER TABLE match_tactics ADD COLUMN {name} {col_type}")
        # matches 表版本维度：codeHash 用于区分发版前后对局
        m_cols = {row[1] for row in conn.execute("PRAGMA table_info(matches)").fetchall()}
        if "my_code_hash" not in m_cols:
            conn.execute("ALTER TABLE matches ADD COLUMN my_code_hash TEXT")
        # 索引在迁移后创建，避免旧库 CREATE TABLE IF NOT EXISTS 跳过新列时建索引失败
        m_cols = {row[1] for row in conn.execute("PRAGMA table_info(matches)").fetchall()}
        if "my_code_hash" in m_cols:
            conn.execute("CREATE INDEX IF NOT EXISTS idx_matches_code_hash ON matches(my_code_hash)")
        # 新表：旧库通过 CREATE IF NOT EXISTS 补建
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS match_key_events (
                match_url_id TEXT NOT NULL,
                seq INTEGER NOT NULL,
                frame INTEGER NOT NULL,
                kind TEXT NOT NULL,
                actor TEXT,
                side TEXT,
                detail_json TEXT,
                PRIMARY KEY (match_url_id, seq),
                FOREIGN KEY (match_url_id) REFERENCES matches(match_url_id)
            );
            CREATE TABLE IF NOT EXISTS match_behavior_log (
                match_url_id TEXT NOT NULL,
                seq INTEGER NOT NULL,
                frame INTEGER NOT NULL,
                kind TEXT NOT NULL,
                text TEXT,
                PRIMARY KEY (match_url_id, seq),
                FOREIGN KEY (match_url_id) REFERENCES matches(match_url_id)
            );
        """)
        # match_bt_trace 旧 schema 为 (match_url_id, frame)，需迁移为带 seq
        bt_cols = {row[1] for row in conn.execute("PRAGMA table_info(match_bt_trace)").fetchall()}
        if bt_cols and "seq" not in bt_cols:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS match_bt_trace_new (
                    match_url_id TEXT NOT NULL,
                    seq INTEGER NOT NULL,
                    frame INTEGER NOT NULL,
                    layer TEXT,
                    action TEXT,
                    PRIMARY KEY (match_url_id, seq),
                    FOREIGN KEY (match_url_id) REFERENCES matches(match_url_id)
                );
                INSERT INTO match_bt_trace_new (match_url_id, seq, frame, layer, action)
                SELECT match_url_id, frame, frame, layer, action FROM match_bt_trace;
                DROP TABLE match_bt_trace;
                ALTER TABLE match_bt_trace_new RENAME TO match_bt_trace;
            """)

    def reset(self):
        """DROP 全部表后重建空库（配合 --reset-db 清库重采）。"""
        with self._connect() as conn:
            conn.executescript("""
                DROP TABLE IF EXISTS run_matches;
                DROP TABLE IF EXISTS match_stats;
                DROP TABLE IF EXISTS match_tactics;
                DROP TABLE IF EXISTS match_bt_trace;
                DROP TABLE IF EXISTS match_key_events;
                DROP TABLE IF EXISTS match_behavior_log;
                DROP TABLE IF EXISTS matches;
                DROP TABLE IF EXISTS opponents;
                DROP TABLE IF EXISTS my_tanks;
                DROP TABLE IF EXISTS analysis_runs;
            """)
            conn.executescript(SCHEMA_SQL)

    @contextmanager
    def connection(self):
        conn = self._connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def create_run(self, n_per_tank, tank_filter=None, notes=None):
        """新建一次分析 run，返回 run_id。"""
        run_at = datetime.now(timezone.utc).isoformat()
        with self.connection() as conn:
            cur = conn.execute(
                "INSERT INTO analysis_runs (run_at, n_per_tank, tank_filter, notes) VALUES (?, ?, ?, ?)",
                (run_at, n_per_tank, tank_filter, notes),
            )
            return cur.lastrowid

    def upsert_my_tank(self, tank_name, skill, tank_id=None, rank_score=None):
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO my_tanks (tank_name, skill, tank_id, rank_score)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(tank_name) DO UPDATE SET
                    skill = excluded.skill,
                    tank_id = COALESCE(excluded.tank_id, my_tanks.tank_id),
                    rank_score = COALESCE(excluded.rank_score, my_tanks.rank_score)
                """,
                (tank_name, skill, tank_id, rank_score),
            )

    def upsert_opponent(self, opponent_id, name, rank_tier=None, rank_score=None,
                        wins=None, losses=None, draws=None):
        """对手信息 upsert；opponent_id 可为 None（未知 ID 时用 name hash 占位）。"""
        if opponent_id is None:
            opponent_id = abs(hash(name)) % (10 ** 9)
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO opponents (opponent_id, name, rank_tier, rank_score, wins, losses, draws)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(opponent_id) DO UPDATE SET
                    name = excluded.name,
                    rank_tier = COALESCE(excluded.rank_tier, opponents.rank_tier),
                    rank_score = COALESCE(excluded.rank_score, opponents.rank_score),
                    wins = COALESCE(excluded.wins, opponents.wins),
                    losses = COALESCE(excluded.losses, opponents.losses),
                    draws = COALESCE(excluded.draws, opponents.draws)
                """,
                (opponent_id, name, rank_tier, rank_score, wins, losses, draws),
            )
        return opponent_id

    def save_match(self, run_id, record):
        """
        保存单场对局及 stats/tactics。
        record 为 match_analyzer 解析后的 dict。
        """
        match_url_id = record["match_url_id"]
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO matches (
                    match_url_id, my_tank_name, my_skill, opponent_id, opponent_name,
                    won, draw, map_id, result_reason, created_at, history_url,
                    last_seen_run_id, my_code_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(match_url_id) DO UPDATE SET
                    my_tank_name = excluded.my_tank_name,
                    my_skill = excluded.my_skill,
                    opponent_id = excluded.opponent_id,
                    opponent_name = excluded.opponent_name,
                    won = excluded.won,
                    draw = excluded.draw,
                    map_id = excluded.map_id,
                    result_reason = excluded.result_reason,
                    created_at = excluded.created_at,
                    history_url = excluded.history_url,
                    last_seen_run_id = excluded.last_seen_run_id,
                    my_code_hash = COALESCE(excluded.my_code_hash, matches.my_code_hash)
                """,
                (
                    match_url_id,
                    record["my_tank_name"],
                    record["my_skill"],
                    record.get("opponent_id"),
                    record["opponent_name"],
                    1 if record["won"] else 0,
                    1 if record.get("draw") else 0,
                    record.get("map_id"),
                    record.get("result_reason"),
                    record.get("created_at"),
                    record.get("history_url"),
                    run_id,
                    record.get("my_code_hash"),
                ),
            )
            conn.execute(
                "INSERT OR IGNORE INTO run_matches (run_id, match_url_id) VALUES (?, ?)",
                (run_id, match_url_id),
            )
            for side in ("me", "enemy"):
                stats = record.get("stats", {}).get(side)
                if not stats:
                    continue
                conn.execute(
                    """
                    INSERT INTO match_stats (
                        match_url_id, side, frames_total, shots_fired, shots_hit, shots_wall,
                        moves, turns, stars, skill_used, crashes, runtime_ms, diagnosis
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(match_url_id, side) DO UPDATE SET
                        frames_total = excluded.frames_total,
                        shots_fired = excluded.shots_fired,
                        shots_hit = excluded.shots_hit,
                        shots_wall = excluded.shots_wall,
                        moves = excluded.moves,
                        turns = excluded.turns,
                        stars = excluded.stars,
                        skill_used = excluded.skill_used,
                        crashes = excluded.crashes,
                        runtime_ms = excluded.runtime_ms,
                        diagnosis = excluded.diagnosis
                    """,
                    (
                        match_url_id, side,
                        stats.get("frames_total"),
                        stats.get("shots_fired"),
                        stats.get("shots_hit"),
                        stats.get("shots_wall"),
                        stats.get("moves"),
                        stats.get("turns"),
                        stats.get("stars"),
                        stats.get("skill_used"),
                        stats.get("crashes"),
                        stats.get("runtime_ms"),
                        stats.get("diagnosis"),
                    ),
                )
            tactics = record.get("tactics") or {}
            conn.execute(
                """
                INSERT INTO match_tactics (
                    match_url_id, my_skill_inferred, enemy_skill,
                    my_first_skill_frame, enemy_first_skill_frame, death_frame,
                    my_win_tactic, my_loss_tactic, enemy_playstyle_tags,
                    inferred_bt_layers, profile_gap_tags, has_events,
                    real_bt_layers, profile_timeline, has_raw,
                    enemy_stars, star_delta, my_skill_casts,
                    dominant_bt_layer, anchor_frame, critical_events_summary,
                    has_bomb, my_bomb_placed, enemy_bomb_placed, bomb_total
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(match_url_id) DO UPDATE SET
                    my_skill_inferred = excluded.my_skill_inferred,
                    enemy_skill = excluded.enemy_skill,
                    my_first_skill_frame = excluded.my_first_skill_frame,
                    enemy_first_skill_frame = excluded.enemy_first_skill_frame,
                    death_frame = excluded.death_frame,
                    my_win_tactic = excluded.my_win_tactic,
                    my_loss_tactic = excluded.my_loss_tactic,
                    enemy_playstyle_tags = excluded.enemy_playstyle_tags,
                    inferred_bt_layers = excluded.inferred_bt_layers,
                    profile_gap_tags = excluded.profile_gap_tags,
                    has_events = excluded.has_events,
                    real_bt_layers = COALESCE(excluded.real_bt_layers, match_tactics.real_bt_layers),
                    profile_timeline = COALESCE(excluded.profile_timeline, match_tactics.profile_timeline),
                    has_raw = MAX(excluded.has_raw, match_tactics.has_raw),
                    enemy_stars = excluded.enemy_stars,
                    star_delta = excluded.star_delta,
                    my_skill_casts = excluded.my_skill_casts,
                    dominant_bt_layer = COALESCE(excluded.dominant_bt_layer, match_tactics.dominant_bt_layer),
                    anchor_frame = excluded.anchor_frame,
                    critical_events_summary = excluded.critical_events_summary,
                    has_bomb = COALESCE(excluded.has_bomb, match_tactics.has_bomb),
                    my_bomb_placed = COALESCE(excluded.my_bomb_placed, match_tactics.my_bomb_placed),
                    enemy_bomb_placed = COALESCE(excluded.enemy_bomb_placed, match_tactics.enemy_bomb_placed),
                    bomb_total = COALESCE(excluded.bomb_total, match_tactics.bomb_total)
                """,
                (
                    match_url_id,
                    tactics.get("my_skill_inferred"),
                    tactics.get("enemy_skill"),
                    tactics.get("my_first_skill_frame"),
                    tactics.get("enemy_first_skill_frame"),
                    tactics.get("death_frame"),
                    tactics.get("my_win_tactic"),
                    tactics.get("my_loss_tactic"),
                    json.dumps(tactics.get("enemy_playstyle_tags") or [], ensure_ascii=False),
                    json.dumps(tactics.get("inferred_bt_layers") or [], ensure_ascii=False),
                    json.dumps(tactics.get("profile_gap_tags") or [], ensure_ascii=False),
                    1 if tactics.get("has_events") else 0,
                    json.dumps(tactics["real_bt_layers"], ensure_ascii=False)
                    if tactics.get("real_bt_layers") is not None else None,
                    json.dumps(tactics["profile_timeline"], ensure_ascii=False)
                    if tactics.get("profile_timeline") is not None else None,
                    1 if tactics.get("has_raw") else 0,
                    tactics.get("enemy_stars"),
                    tactics.get("star_delta"),
                    tactics.get("my_skill_casts"),
                    tactics.get("dominant_bt_layer"),
                    tactics.get("anchor_frame"),
                    json.dumps(tactics.get("critical_events_summary") or [], ensure_ascii=False),
                    tactics.get("has_bomb"),
                    tactics.get("my_bomb_placed"),
                    tactics.get("enemy_bomb_placed"),
                    tactics.get("bomb_total"),
                ),
            )
            # 负场关键 events / print 行为日志（终局窗口）
            key_events = tactics.get("key_events")
            if key_events is not None:
                conn.execute("DELETE FROM match_key_events WHERE match_url_id = ?", (match_url_id,))
                conn.executemany(
                    """INSERT INTO match_key_events
                       (match_url_id, seq, frame, kind, actor, side, detail_json)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    [
                        (
                            match_url_id, ev["seq"], ev["frame"], ev["kind"],
                            ev.get("actor"), ev.get("side"),
                            json.dumps(ev.get("detail") or {}, ensure_ascii=False),
                        )
                        for ev in key_events
                    ],
                )
            behavior_log = tactics.get("behavior_log")
            if behavior_log is not None:
                conn.execute("DELETE FROM match_behavior_log WHERE match_url_id = ?", (match_url_id,))
                conn.executemany(
                    """INSERT INTO match_behavior_log
                       (match_url_id, seq, frame, kind, text)
                       VALUES (?, ?, ?, ?, ?)""",
                    [
                        (match_url_id, row["seq"], row["frame"], row["kind"], row.get("text"))
                        for row in behavior_log
                    ],
                )
            # 全量 BT trace（seq 递增，支持同帧多条）
            bt_trace = record.get("bt_trace")
            if bt_trace:
                conn.execute(
                    "DELETE FROM match_bt_trace WHERE match_url_id = ?", (match_url_id,)
                )
                conn.executemany(
                    "INSERT INTO match_bt_trace (match_url_id, seq, frame, layer, action)"
                    " VALUES (?, ?, ?, ?, ?)",
                    [
                        (match_url_id, i, t["frame"], t.get("layer"), t.get("action"))
                        for i, t in enumerate(bt_trace)
                    ],
                )

    def match_exists(self, match_url_id):
        with self.connection() as conn:
            row = conn.execute(
                "SELECT 1 FROM matches WHERE match_url_id = ?", (match_url_id,)
            ).fetchone()
            return row is not None

    # ── 查询（供 report_server 使用）──

    def list_runs(self):
        with self.connection() as conn:
            rows = conn.execute(
                """
                SELECT r.id, r.run_at, r.n_per_tank, r.tank_filter,
                       COUNT(rm.match_url_id) AS match_count,
                       SUM(CASE WHEN m.won = 1 THEN 1 ELSE 0 END) AS wins,
                       SUM(CASE WHEN m.won = 0 AND m.draw = 0 THEN 1 ELSE 0 END) AS losses,
                       SUM(CASE WHEN m.draw = 1 THEN 1 ELSE 0 END) AS draws
                FROM analysis_runs r
                LEFT JOIN run_matches rm ON rm.run_id = r.id
                LEFT JOIN matches m ON m.match_url_id = rm.match_url_id
                GROUP BY r.id
                ORDER BY r.id DESC
                """
            ).fetchall()
            return [dict(r) for r in rows]

    def delete_run(self, run_id):
        """
        删除指定 analysis run 及其 run_matches 关联。
        若某对局不再被任何 run 引用，则一并清理 match_stats / match_tactics / matches。
        """
        with self.connection() as conn:
            row = conn.execute(
                "SELECT id FROM analysis_runs WHERE id = ?", (run_id,)
            ).fetchone()
            if not row:
                return None

            match_count = conn.execute(
                "SELECT COUNT(*) FROM run_matches WHERE run_id = ?", (run_id,)
            ).fetchone()[0]

            conn.execute("DELETE FROM run_matches WHERE run_id = ?", (run_id,))
            conn.execute("DELETE FROM analysis_runs WHERE id = ?", (run_id,))

            orphan_rows = conn.execute(
                """
                SELECT m.match_url_id FROM matches m
                LEFT JOIN run_matches rm ON rm.match_url_id = m.match_url_id
                WHERE rm.run_id IS NULL
                """
            ).fetchall()
            orphan_ids = [r[0] for r in orphan_rows]
            orphans_removed = 0
            if orphan_ids:
                placeholders = ",".join("?" * len(orphan_ids))
                conn.execute(
                    f"DELETE FROM match_stats WHERE match_url_id IN ({placeholders})",
                    orphan_ids,
                )
                conn.execute(
                    f"DELETE FROM match_tactics WHERE match_url_id IN ({placeholders})",
                    orphan_ids,
                )
                conn.execute(
                    f"DELETE FROM match_bt_trace WHERE match_url_id IN ({placeholders})",
                    orphan_ids,
                )
                conn.execute(
                    f"DELETE FROM match_key_events WHERE match_url_id IN ({placeholders})",
                    orphan_ids,
                )
                conn.execute(
                    f"DELETE FROM match_behavior_log WHERE match_url_id IN ({placeholders})",
                    orphan_ids,
                )
                cur = conn.execute(
                    f"DELETE FROM matches WHERE match_url_id IN ({placeholders})",
                    orphan_ids,
                )
                orphans_removed = cur.rowcount

            return {
                "run_id": run_id,
                "matches_unlinked": match_count,
                "orphaned_matches_removed": orphans_removed,
            }

    def _run_match_filter(self, run_id, code_hash=None, min_rank_score=None):
        """
        run 内对局过滤基础 SQL。
        - code_hash: 只看指定版本（my_code_hash 前缀匹配，便于用短 hash 查询）
        - min_rank_score: 排除弱对手局；rank_score 未知的对手默认放行
          （只有难打对手会被 enrichment，未知不代表弱，排除会砍掉大部分样本）
        """
        sql = """
            FROM run_matches rm
            JOIN matches m ON m.match_url_id = rm.match_url_id
            LEFT JOIN match_tactics t ON t.match_url_id = m.match_url_id
            LEFT JOIN opponents o ON o.opponent_id = m.opponent_id
            WHERE rm.run_id = ?
            """
        params = [run_id]
        if code_hash:
            sql += " AND m.my_code_hash LIKE ?\n"
            params.append(code_hash + "%")
        if min_rank_score is not None:
            sql += " AND (o.rank_score IS NULL OR o.rank_score >= ?)\n"
            params.append(min_rank_score)
        return sql, tuple(params)

    def _insert_json_each(self, base_sql, json_column):
        """json_each 必须出现在 WHERE 之前，否则 SQLite 语法错误。"""
        needle = "WHERE rm.run_id = ?"
        insert = f", json_each({json_column}) AS j\n                {needle}"
        return base_sql.replace(needle, insert)

    def run_code_hashes(self, run_id):
        """run 内出现过的我方代码版本列表（版本选择器数据源）。"""
        base, params = self._run_match_filter(run_id)
        with self.connection() as conn:
            rows = conn.execute(
                f"""
                SELECT COALESCE(m.my_code_hash, 'unknown') AS code_hash,
                       COUNT(*) AS total,
                       SUM(CASE WHEN m.won = 1 THEN 1 ELSE 0 END) AS wins,
                       SUM(CASE WHEN m.won = 0 AND m.draw = 0 THEN 1 ELSE 0 END) AS losses,
                       MIN(m.created_at) AS first_seen,
                       MAX(m.created_at) AS last_seen
                {base}
                GROUP BY COALESCE(m.my_code_hash, 'unknown')
                ORDER BY last_seen DESC
                """,
                params,
            ).fetchall()
            return [dict(r) for r in rows]

    def run_overview(self, run_id, code_hash=None, min_rank_score=None):
        base, params = self._run_match_filter(run_id, code_hash, min_rank_score)
        with self.connection() as conn:
            overall = conn.execute(
                f"""
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN m.won = 1 THEN 1 ELSE 0 END) AS wins,
                       SUM(CASE WHEN m.won = 0 AND m.draw = 0 THEN 1 ELSE 0 END) AS losses,
                       SUM(CASE WHEN m.draw = 1 THEN 1 ELSE 0 END) AS draws
                {base}
                """,
                params,
            ).fetchone()
            by_skill = conn.execute(
                f"""
                SELECT m.my_skill,
                       COUNT(*) AS total,
                       SUM(CASE WHEN m.won = 1 THEN 1 ELSE 0 END) AS wins,
                       SUM(CASE WHEN m.won = 0 AND m.draw = 0 THEN 1 ELSE 0 END) AS losses
                {base}
                GROUP BY m.my_skill
                ORDER BY m.my_skill
                """,
                params,
            ).fetchall()
            return {
                "overall": dict(overall),
                "by_skill": [dict(r) for r in by_skill],
            }

    def run_skill_matrix(self, run_id, code_hash=None, min_rank_score=None):
        base, params = self._run_match_filter(run_id, code_hash, min_rank_score)
        with self.connection() as conn:
            rows = conn.execute(
                f"""
                SELECT m.my_skill,
                       COALESCE(t.enemy_skill, 'unknown') AS enemy_skill,
                       COUNT(*) AS total,
                       SUM(CASE WHEN m.won = 1 THEN 1 ELSE 0 END) AS wins,
                       SUM(CASE WHEN m.won = 0 AND m.draw = 0 THEN 1 ELSE 0 END) AS losses
                {base}
                GROUP BY m.my_skill, COALESCE(t.enemy_skill, 'unknown')
                ORDER BY m.my_skill, enemy_skill
                """,
                params,
            ).fetchall()
            return [dict(r) for r in rows]

    def run_opponents(self, run_id, min_samples=1, sort="worst", page=1, page_size=20,
                      code_hash=None, min_rank_score=None):
        base, params = self._run_match_filter(run_id, code_hash, min_rank_score)
        order = "win_rate ASC, total DESC" if sort == "worst" else "total DESC"
        offset = (page - 1) * page_size
        with self.connection() as conn:
            rows = conn.execute(
                f"""
                SELECT m.opponent_name,
                       m.opponent_id,
                       o.rank_tier,
                       o.rank_score,
                       COUNT(*) AS total,
                       SUM(CASE WHEN m.won = 1 THEN 1 ELSE 0 END) AS wins,
                       SUM(CASE WHEN m.won = 0 AND m.draw = 0 THEN 1 ELSE 0 END) AS losses,
                       ROUND(100.0 * SUM(CASE WHEN m.won = 1 THEN 1 ELSE 0 END) / COUNT(*), 1) AS win_rate
                {base}
                GROUP BY m.opponent_name, m.opponent_id, o.rank_tier, o.rank_score
                HAVING COUNT(*) >= ?
                ORDER BY {order}
                LIMIT ? OFFSET ?
                """,
                params + (min_samples, page_size, offset),
            ).fetchall()
            total_row = conn.execute(
                f"""
                SELECT COUNT(*) FROM (
                    SELECT m.opponent_name
                    {base}
                    GROUP BY m.opponent_name
                    HAVING COUNT(*) >= ?
                )
                """,
                params + (min_samples,),
            ).fetchone()
            return {
                "items": [dict(r) for r in rows],
                "total": total_row[0] if total_row else 0,
                "page": page,
                "page_size": page_size,
            }

    def run_behavior(self, run_id, my_skill=None, result=None, code_hash=None, min_rank_score=None):
        """我方行为统计，可按 skill / 胜负 / 版本 / 对手强度过滤。"""
        base, params = self._run_match_filter(run_id, code_hash, min_rank_score)
        extra = []
        bind = list(params)
        if my_skill:
            extra.append("AND m.my_skill = ?")
            bind.append(my_skill)
        if result == "W":
            extra.append("AND m.won = 1")
        elif result == "L":
            extra.append("AND m.won = 0 AND m.draw = 0")
        clause = " ".join(extra)
        # 复用 _run_match_filter 基础 SQL，再拼上 me 侧 stats join
        from_sql = base.replace(
            "WHERE rm.run_id = ?",
            "JOIN match_stats s ON s.match_url_id = m.match_url_id AND s.side = 'me'\n"
            "            WHERE rm.run_id = ?",
        )
        with self.connection() as conn:
            rows = conn.execute(
                f"""
                SELECT m.my_skill,
                       CASE WHEN m.won = 1 THEN 'W' WHEN m.draw = 1 THEN 'D' ELSE 'L' END AS result,
                       COUNT(*) AS n,
                       ROUND(AVG(s.shots_fired), 2) AS avg_shots_fired,
                       ROUND(AVG(s.shots_hit), 2) AS avg_shots_hit,
                       ROUND(AVG(s.shots_wall), 2) AS avg_shots_wall,
                       ROUND(AVG(s.moves), 2) AS avg_moves,
                       ROUND(AVG(s.turns), 2) AS avg_turns,
                       ROUND(AVG(s.stars), 2) AS avg_stars,
                       ROUND(AVG(s.skill_used), 2) AS avg_skill_used,
                       ROUND(AVG(s.frames_total), 2) AS avg_frames,
                       ROUND(AVG(t.death_frame), 2) AS avg_death_frame
                {from_sql}
                {clause}
                GROUP BY m.my_skill, result
                ORDER BY m.my_skill, result
                """,
                bind,
            ).fetchall()
            return [dict(r) for r in rows]

    def run_enemy_profiles(self, run_id, result_filter=None, code_hash=None, min_rank_score=None):
        base, params = self._run_match_filter(run_id, code_hash, min_rank_score)
        extra = ""
        bind = list(params)
        if result_filter == "W":
            extra = "AND m.won = 1"
        elif result_filter == "L":
            extra = "AND m.won = 0 AND m.draw = 0"
        from_sql = base.replace(
            "WHERE rm.run_id = ?",
            "JOIN match_stats es ON es.match_url_id = m.match_url_id AND es.side = 'enemy'\n"
            "            WHERE rm.run_id = ?",
        )
        with self.connection() as conn:
            rows = conn.execute(
                f"""
                SELECT COALESCE(t.enemy_skill, 'unknown') AS enemy_skill,
                       COUNT(*) AS n,
                       ROUND(AVG(es.shots_fired), 2) AS avg_shots,
                       ROUND(AVG(es.moves), 2) AS avg_moves,
                       ROUND(AVG(es.turns), 2) AS avg_turns,
                       ROUND(AVG(es.stars), 2) AS avg_stars,
                       ROUND(AVG(es.skill_used), 2) AS avg_skill_used,
                       ROUND(100.0 * SUM(es.shots_hit) / NULLIF(SUM(es.shots_fired), 0), 1) AS hit_rate
                {from_sql}
                {extra}
                GROUP BY COALESCE(t.enemy_skill, 'unknown')
                ORDER BY n DESC
                """,
                bind,
            ).fetchall()
            return [dict(r) for r in rows]

    def run_win_patterns(self, run_id, code_hash=None, min_rank_score=None):
        base, params = self._run_match_filter(run_id, code_hash, min_rank_score)
        with self.connection() as conn:
            rows = conn.execute(
                f"""
                SELECT COALESCE(t.my_win_tactic, 'unknown') AS tactic,
                       m.my_skill,
                       COUNT(*) AS n
                {base}
                AND m.won = 1
                GROUP BY COALESCE(t.my_win_tactic, 'unknown'), m.my_skill
                ORDER BY n DESC
                """,
                params,
            ).fetchall()
            return [dict(r) for r in rows]

    def run_loss_patterns(self, run_id, code_hash=None, min_rank_score=None):
        """Phase 1：负场战术分布，镜像 run_win_patterns。"""
        base, params = self._run_match_filter(run_id, code_hash, min_rank_score)
        with self.connection() as conn:
            rows = conn.execute(
                f"""
                SELECT COALESCE(t.my_loss_tactic, 'unknown') AS tactic,
                       m.my_skill,
                       COUNT(*) AS n
                {base}
                AND m.won = 0 AND m.draw = 0
                GROUP BY COALESCE(t.my_loss_tactic, 'unknown'), m.my_skill
                ORDER BY n DESC
                """,
                params,
            ).fetchall()
            return [dict(r) for r in rows]

    def run_cross_matrix(self, run_id, result="L", code_hash=None, min_rank_score=None):
        """Phase 2：敌 skill/tag × 战术交叉矩阵。"""
        base, params = self._run_match_filter(run_id, code_hash, min_rank_score)
        tactic_col = "my_win_tactic" if result == "W" else "my_loss_tactic"
        result_clause = "AND m.won = 1" if result == "W" else "AND m.won = 0 AND m.draw = 0"
        from_sql = self._insert_json_each(base, "t.enemy_playstyle_tags")
        with self.connection() as conn:
            rows = conn.execute(
                f"""
                SELECT m.my_skill,
                       COALESCE(t.enemy_skill, 'unknown') AS enemy_skill,
                       j.value AS enemy_tag,
                       COALESCE(t.{tactic_col}, 'unknown') AS tactic,
                       COUNT(*) AS n
                {from_sql}
                {result_clause}
                AND t.enemy_playstyle_tags IS NOT NULL
                AND t.enemy_playstyle_tags != '[]'
                GROUP BY m.my_skill, COALESCE(t.enemy_skill, 'unknown'), j.value,
                         COALESCE(t.{tactic_col}, 'unknown')
                ORDER BY n DESC
                """,
                params,
            ).fetchall()
            return {
                "result": result,
                "cells": [dict(r) for r in rows],
            }

    def run_diagnosis_stats(self, run_id, result="L", code_hash=None, min_rank_score=None):
        """Phase 3：按 diagnosis × enemy_skill 聚合。"""
        base, params = self._run_match_filter(run_id, code_hash, min_rank_score)
        extra = "AND m.won = 1" if result == "W" else "AND m.won = 0 AND m.draw = 0"
        from_sql = base.replace(
            "WHERE rm.run_id = ?",
            "JOIN match_stats s ON s.match_url_id = m.match_url_id AND s.side = 'me'\n"
            "            WHERE rm.run_id = ?",
        )
        with self.connection() as conn:
            rows = conn.execute(
                f"""
                SELECT COALESCE(s.diagnosis, 'unknown') AS diagnosis,
                       COALESCE(t.enemy_skill, 'unknown') AS enemy_skill,
                       COUNT(*) AS n
                {from_sql}
                {extra}
                GROUP BY COALESCE(s.diagnosis, 'unknown'), COALESCE(t.enemy_skill, 'unknown')
                ORDER BY n DESC
                """,
                params,
            ).fetchall()
            return {
                "result": result,
                "items": [dict(r) for r in rows],
            }

    def run_bt_layer_losses(self, run_id, code_hash=None, min_rank_score=None):
        """
        Phase 4：负场按 BT 层级 × my_skill 计数。
        raw 遥测解析出的 real_bt_layers 优先（source='real'），
        无 raw 数据的对局回退启发式 inferred_bt_layers（source='inferred'）。
        """
        base, params = self._run_match_filter(run_id, code_hash, min_rank_score)
        real_sql = self._insert_json_each(base, "t.real_bt_layers")
        inferred_sql = self._insert_json_each(base, "t.inferred_bt_layers")
        with self.connection() as conn:
            rows = conn.execute(
                f"""
                SELECT m.my_skill, j.value AS layer, COUNT(*) AS n, 'real' AS source
                {real_sql}
                AND m.won = 0 AND m.draw = 0
                AND t.real_bt_layers IS NOT NULL
                AND t.real_bt_layers != '[]'
                GROUP BY m.my_skill, j.value
                UNION ALL
                SELECT m.my_skill, j.value AS layer, COUNT(*) AS n, 'inferred' AS source
                {inferred_sql}
                AND m.won = 0 AND m.draw = 0
                AND (t.real_bt_layers IS NULL OR t.real_bt_layers = '[]')
                AND t.inferred_bt_layers IS NOT NULL
                AND t.inferred_bt_layers != '[]'
                GROUP BY m.my_skill, j.value
                ORDER BY n DESC
                """,
                params + params,
            ).fetchall()
            return [dict(r) for r in rows]

    def list_matches(self, run_id, my_skill=None, opponent=None, won=None, page=1, page_size=30,
                     code_hash=None, min_rank_score=None,
                     enemy_skill=None, result_reason=None, loss_tactic=None,
                     gap_tag=None, match_id=None, has_bomb=None):
        base, params = self._run_match_filter(run_id, code_hash, min_rank_score)
        extra = []
        bind = list(params)
        if my_skill:
            extra.append("AND m.my_skill = ?")
            bind.append(my_skill)
        if opponent:
            extra.append("AND m.opponent_name LIKE ?")
            bind.append(f"%{opponent}%")
        if won is not None:
            extra.append("AND m.won = ?")
            bind.append(1 if won else 0)
        # 多维补充筛选：敌技能 / 终局原因 / 负场战术 / Gap 标签 / 对局 id
        if enemy_skill:
            extra.append("AND t.enemy_skill = ?")
            bind.append(enemy_skill)
        if result_reason:
            extra.append("AND m.result_reason = ?")
            bind.append(result_reason)
        if loss_tactic:
            extra.append("AND t.my_loss_tactic = ?")
            bind.append(loss_tactic)
        if gap_tag:
            # profile_gap_tags 存 JSON 数组字符串，模糊匹配标签片段
            extra.append("AND t.profile_gap_tags LIKE ?")
            bind.append(f"%{gap_tag}%")
        if match_id:
            extra.append("AND m.match_url_id LIKE ?")
            bind.append(f"%{match_id}%")
        if has_bomb == "1":
            extra.append("AND t.has_bomb = 1")
        elif has_bomb == "0":
            extra.append("AND t.has_bomb = 0")
        elif has_bomb == "unknown":
            extra.append("AND t.has_bomb IS NULL")
        clause = " ".join(extra)
        offset = (page - 1) * page_size
        from_sql = base.replace(
            "WHERE rm.run_id = ?",
            "JOIN match_stats sm ON sm.match_url_id = m.match_url_id AND sm.side = 'me'\n"
            "            LEFT JOIN match_stats se ON se.match_url_id = m.match_url_id AND se.side = 'enemy'\n"
            "            WHERE rm.run_id = ?",
        )
        with self.connection() as conn:
            rows = conn.execute(
                f"""
                SELECT m.match_url_id, m.my_tank_name, m.my_skill, m.opponent_name,
                       m.won, m.draw, m.map_id, m.result_reason, m.created_at, m.history_url,
                       m.my_code_hash,
                       t.enemy_skill, t.my_win_tactic, t.my_loss_tactic,
                       t.inferred_bt_layers, t.profile_gap_tags, t.has_events,
                       t.real_bt_layers, t.has_raw, t.dominant_bt_layer,
                       t.enemy_stars, t.star_delta, t.my_skill_casts,
                       t.critical_events_summary, t.anchor_frame,
                       t.has_bomb, t.my_bomb_placed, t.enemy_bomb_placed, t.bomb_total,
                       sm.stars AS me_stars, se.stars AS enemy_stars_stat,
                       sm.diagnosis,
                       CASE WHEN t.critical_events_summary IS NOT NULL
                            AND t.critical_events_summary != '[]' THEN 1 ELSE 0 END AS has_timeline
                {from_sql}
                {clause}
                ORDER BY m.created_at DESC
                LIMIT ? OFFSET ?
                """,
                bind + [page_size, offset],
            ).fetchall()
            return [dict(r) for r in rows]

    def list_run_matches_for_reanalyze(self, run_id):
        """返回 run 内对局基础信息，供 --reanalyze-run 从 cache 重解析。"""
        with self.connection() as conn:
            rows = conn.execute(
                """
                SELECT m.match_url_id, m.my_tank_name, m.my_skill, m.opponent_name,
                       m.opponent_id, m.won, m.draw, m.map_id, m.result_reason,
                       m.created_at, m.history_url, m.my_code_hash
                FROM run_matches rm
                JOIN matches m ON m.match_url_id = rm.match_url_id
                WHERE rm.run_id = ?
                ORDER BY m.created_at
                """,
                (run_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def list_matches_needing_bomb_backfill(self, limit=None, run_id=None):
        """
        返回 my_bomb_placed 仍为 NULL 的对局，供 --backfill-bombs 补拉 raw。
        run_id：限定某次 analysis run；limit：按 created_at 倒序取最近 N 场。
        """
        sql = """
                SELECT m.match_url_id, m.my_tank_name, m.won, m.draw, m.created_at
                FROM matches m
                JOIN match_tactics t ON t.match_url_id = m.match_url_id
                """
        params = []
        if run_id is not None:
            sql += """
                JOIN run_matches rm ON rm.match_url_id = m.match_url_id
                WHERE t.my_bomb_placed IS NULL AND rm.run_id = ?
                """
            params.append(run_id)
        else:
            sql += " WHERE t.my_bomb_placed IS NULL"
        sql += " ORDER BY m.created_at DESC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        with self.connection() as conn:
            rows = conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]

    def update_bomb_stats(self, match_url_id, stats):
        """仅更新炸弹统计列，不重写整行 tactics。"""
        with self.connection() as conn:
            conn.execute(
                """
                UPDATE match_tactics SET
                    has_bomb = ?,
                    my_bomb_placed = ?,
                    enemy_bomb_placed = ?,
                    bomb_total = ?
                WHERE match_url_id = ?
                """,
                (
                    stats.get("has_bomb"),
                    stats.get("my_bomb_placed"),
                    stats.get("enemy_bomb_placed"),
                    stats.get("bomb_total"),
                    match_url_id,
                ),
            )

    def run_result_reason_stats(self, run_id, code_hash=None, min_rank_score=None):
        """负场终局原因分布（star / crashed / runTime 等）。"""
        base, params = self._run_match_filter(run_id, code_hash, min_rank_score)
        with self.connection() as conn:
            rows = conn.execute(
                f"""
                SELECT COALESCE(m.result_reason, 'unknown') AS reason, COUNT(*) AS n
                {base}
                AND m.won = 0 AND m.draw = 0
                GROUP BY COALESCE(m.result_reason, 'unknown')
                ORDER BY n DESC
                """,
                params,
            ).fetchall()
            return [dict(r) for r in rows]

    def get_match_anchor(self, match_url_id):
        """返回对局锚点帧（anchor_frame 优先，缺失回退 death_frame），供地图查看器定位。"""
        with self.connection() as conn:
            row = conn.execute(
                """
                SELECT anchor_frame, death_frame
                FROM match_tactics
                WHERE match_url_id = ?
                """,
                (match_url_id,),
            ).fetchone()
        if not row:
            return None
        return row["anchor_frame"] if row["anchor_frame"] is not None else row["death_frame"]

    def get_match_timeline(self, match_url_id):
        """合并负场 key events + print 行为日志，供弹窗时序图。"""
        with self.connection() as conn:
            meta = conn.execute(
                """
                SELECT m.match_url_id, m.result_reason, m.history_url, m.won, m.draw,
                       t.my_loss_tactic, t.death_frame, t.anchor_frame,
                       t.real_bt_layers, t.dominant_bt_layer, t.enemy_stars, t.star_delta
                FROM matches m
                LEFT JOIN match_tactics t ON t.match_url_id = m.match_url_id
                WHERE m.match_url_id = ?
                """,
                (match_url_id,),
            ).fetchone()
            if not meta:
                return None
            meta = dict(meta)
            key_rows = conn.execute(
                """
                SELECT seq, frame, kind, actor, side, detail_json
                FROM match_key_events
                WHERE match_url_id = ?
                ORDER BY frame, seq
                """,
                (match_url_id,),
            ).fetchall()
            log_rows = conn.execute(
                """
                SELECT seq, frame, kind, text
                FROM match_behavior_log
                WHERE match_url_id = ?
                ORDER BY frame, seq
                """,
                (match_url_id,),
            ).fetchall()
        items = []
        kind_labels = {
            "star_spawned": "星星出现",
            "star_collected": "吃星",
            "fire": "开火",
            "shot_wall": "打墙",
            "shot_hit": "命中",
            "crashed": "撞毁",
            "skill_cast": "施放技能",
            "skill_applied": "技能生效",
        }
        for r in key_rows:
            detail = json.loads(r["detail_json"] or "{}")
            skill = detail.get("skill")
            label = kind_labels.get(r["kind"], r["kind"])
            if skill:
                label = f"{label}({skill})"
            side = r["side"] or ""
            who = "我" if side == "me" else ("敌" if side == "enemy" else "")
            items.append({
                "frame": r["frame"],
                "lane": "event",
                "kind": r["kind"],
                "label": f"{who}{label}".strip(),
                "detail": detail,
            })
        for r in log_rows:
            lane = "profile" if r["kind"] == "pf" else "behavior"
            items.append({
                "frame": r["frame"],
                "lane": lane,
                "kind": r["kind"],
                "label": r["text"] or "",
                "detail": {},
            })
        items.sort(key=lambda x: (x["frame"], x["lane"]))
        anchor = meta.get("anchor_frame") or meta.get("death_frame")
        return {
            "match_url_id": match_url_id,
            "anchor_frame": anchor,
            "window": 30,
            "history_url": meta.get("history_url"),
            "result_reason": meta.get("result_reason"),
            "my_loss_tactic": meta.get("my_loss_tactic"),
            "death_frame": meta.get("death_frame"),
            "real_bt_layers": json.loads(meta["real_bt_layers"]) if meta.get("real_bt_layers") else [],
            "dominant_bt_layer": meta.get("dominant_bt_layer"),
            "enemy_stars": meta.get("enemy_stars"),
            "star_delta": meta.get("star_delta"),
            "items": items,
        }

    def run_version_compare(self, run_id, hash_a, hash_b, min_rank_score=None):
        """
        两版 codeHash 按敌方技能聚合胜率，回答「发版后 vs 某敌技能有没有变强」。
        hash_a/hash_b 支持短前缀（LIKE 前缀匹配）。
        """
        mat_a = self.run_skill_matrix(run_id, code_hash=hash_a, min_rank_score=min_rank_score)
        mat_b = self.run_skill_matrix(run_id, code_hash=hash_b, min_rank_score=min_rank_score)

        def agg_by_enemy(rows):
            out = {}
            for r in rows:
                es = r["enemy_skill"]
                if es not in out:
                    out[es] = {"total": 0, "wins": 0, "losses": 0}
                out[es]["total"] += r["total"] or 0
                out[es]["wins"] += r["wins"] or 0
                out[es]["losses"] += r["losses"] or 0
            return out

        a_map = agg_by_enemy(mat_a)
        b_map = agg_by_enemy(mat_b)
        enemy_skills = sorted(set(a_map.keys()) | set(b_map.keys()))
        rows = []
        for es in enemy_skills:
            a = a_map.get(es, {"total": 0, "wins": 0, "losses": 0})
            b = b_map.get(es, {"total": 0, "wins": 0, "losses": 0})
            a_wr = (a["wins"] / a["total"] * 100) if a["total"] else None
            b_wr = (b["wins"] / b["total"] * 100) if b["total"] else None
            rows.append({
                "enemy_skill": es,
                "a_total": a["total"],
                "a_wins": a["wins"],
                "a_wr": round(a_wr, 1) if a_wr is not None else None,
                "b_total": b["total"],
                "b_wins": b["wins"],
                "b_wr": round(b_wr, 1) if b_wr is not None else None,
                "delta_wr": round(b_wr - a_wr, 1) if a_wr is not None and b_wr is not None else None,
            })
        return {"hash_a": hash_a, "hash_b": hash_b, "rows": rows}

    def run_profile_gaps(self, run_id, code_hash=None, min_rank_score=None):
        """Phase 3 gap 标签聚合（负场），附带 suggest_params 处方。"""
        base, params = self._run_match_filter(run_id, code_hash, min_rank_score)
        from_sql = self._insert_json_each(base, "t.profile_gap_tags")
        with self.connection() as conn:
            rows = conn.execute(
                f"""
                SELECT j.value AS gap_tag, COUNT(*) AS n
                {from_sql}
                AND m.won = 0 AND m.draw = 0
                AND t.profile_gap_tags IS NOT NULL
                AND t.profile_gap_tags != '[]'
                GROUP BY j.value
                ORDER BY n DESC
                """,
                params,
            ).fetchall()
        # 从配置文件读取 gap 处方（suggest_params），避免循环 import match_analyzer
        gap_rules = {}
        expectations_path = os.path.join(os.path.dirname(__file__), "bt_profile_expectations.json")
        if os.path.isfile(expectations_path):
            with open(expectations_path, encoding="utf-8") as f:
                gap_rules = json.load(f).get("gap_rules", {})
        out = []
        for r in rows:
            rule = gap_rules.get(r["gap_tag"], {})
            out.append({
                "gap_tag": r["gap_tag"],
                "n": r["n"],
                "suggest_params": rule.get("suggest_params"),
            })
        return out
