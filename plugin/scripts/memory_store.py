"""Pure-local cognitive graph memory engine (v2.0) for Antigravity local-memory plugin.

Features (zero external dependencies — Python stdlib + SQLite FTS5 only):
- Cognitive taxonomy: semantic / episodic / procedural
- Workspace isolation: global + per-workspace scoping
- Hybrid search: SQLite FTS5 BM25 (CJK bigram + Latin tokens) + substring/tag boosts
- Biological dynamics: power-law time decay + recall reinforcement (access_count)
- Knowledge graph: typed directed edges (relates_to, depends_on, derived_from, part_of, supersedes, contradicts)
  with 1-hop graph expansion on recall
- Conflict evolution: supersedes marks older facts inactive while preserving provenance edges
- Session injection dedup cache: prevents PreInvocation spam across tool loops and turns
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import math
import os
import re
import sqlite3
from typing import Any
from urllib.parse import unquote

PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEED_FILE = os.path.join(PLUGIN_ROOT, "seed_memories.json")

VALID_CATEGORIES = ("profile", "project", "preference", "learning", "fact")
VALID_MEMORY_TYPES = ("semantic", "episodic", "procedural")
VALID_STATUSES = ("active", "archived", "superseded")
VALID_RELATIONS = (
    "relates_to",
    "depends_on",
    "derived_from",
    "part_of",
    "supersedes",
    "contradicts",
)

CATEGORY_LABELS = {
    "profile": "个人档案 (Profile)",
    "project": "科研与项目 (Projects)",
    "preference": "工作偏好 (Preferences)",
    "learning": "学习记录 (Learning)",
    "fact": "其他记忆 (Facts)",
}

MEMORY_TYPE_LABELS = {
    "semantic": "语义",
    "episodic": "情景",
    "procedural": "程序",
}

DEFAULT_TYPE_BY_CATEGORY = {
    "profile": "semantic",
    "preference": "semantic",
    "fact": "semantic",
    "project": "semantic",
    "learning": "episodic",
}

STOPWORDS = {
    # Chinese common function/filler words
    "的", "了", "在", "是", "我", "你", "他", "她", "它", "们", "这", "那", "个",
    "和", "与", "或", "就", "都", "也", "很", "吗", "呢", "啊", "吧", "呀", "哦",
    "请", "帮", "给", "把", "被", "让", "对", "为", "以", "上", "下", "中", "里",
    "有", "没", "不", "会", "能", "要", "想", "看", "说", "做", "用", "去", "来",
    "一下", "什么", "怎么", "如何", "可以", "请问", "帮我", "帮忙", "看看", "这个",
    "那个", "现在", "目前", "继续", "好的", "谢谢", "收到", "知道", "明白",
    "项目", "问题", "方面", "情况", "东西", "地方", "感觉", "好看", "这里", "那里",
    "刚才", "之前", "之后", "其实", "觉得", "需要", "希望", "建议", "方案", "设计",
    "改进", "参考", "相关", "系统", "然后", "不过", "还是", "如果", "因为", "所以",
    "但是", "已经", "正在", "一个", "一些", "所有", "比较", "非常", "特别", "真的",
    "确实", "具体", "主要", "基本", "一般", "大概", "可能", "应该", "最好", "查看",
    "分析", "介绍", "解释", "告诉", "功能", "事情", "源码", "源代码", "看到",
    # English stopwords
    "the", "and", "for", "with", "this", "that", "from", "have", "has", "was",
    "were", "are", "you", "your", "can", "could", "would", "should", "will",
    "what", "how", "why", "when", "where", "which", "please", "help", "just",
    "about", "into", "then", "than", "also", "more", "some", "make", "using",
}

TRIVIAL_INPUTS = {
    "ok", "okay", "yes", "no", "hi", "hello", "thanks", "thx", "continue", "go",
    "好", "好的", "行", "嗯", "是的", "对", "谢谢", "继续", "开始", "可以", "没问题",
    "知道了", "明白", "收到", "1", "2", "3", "a", "b", "c",
}

_LATIN_TOKEN_RE = re.compile(r"[a-z0-9_+#.-]{2,}", re.IGNORECASE)
_CJK_RUN_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]+")


def get_memory_dir() -> str:
    """Return the persistent local memory directory, creating it if needed."""
    custom_dir = os.environ.get("ANTIGRAVITY_LOCAL_MEMORY_DIR", "").strip()
    plugin_data = os.environ.get("PLUGIN_DATA", "").strip()
    if custom_dir:
        base_dir = custom_dir
    elif plugin_data:
        base_dir = plugin_data
    else:
        base_dir = os.path.join(PLUGIN_ROOT, "data")
    base_dir = os.path.normpath(base_dir)
    os.makedirs(base_dir, exist_ok=True)
    return base_dir


def get_db_path() -> str:
    return os.path.join(get_memory_dir(), "memory.db")


def get_markdown_path() -> str:
    return os.path.join(get_memory_dir(), "memories.md")


def get_nebula_path() -> str:
    return os.path.join(get_memory_dir(), "nebula.html")


def get_session_cache_path() -> str:
    return os.path.join(get_memory_dir(), "session_cache.json")


def _utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _parse_utc(ts: str | None) -> _dt.datetime | None:
    if not ts:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S UTC", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S"):
        try:
            return _dt.datetime.strptime(ts.strip(), fmt).replace(tzinfo=_dt.timezone.utc)
        except ValueError:
            continue
    return None


def _normalize_category(category: str | None) -> str:
    if not category:
        return "fact"
    c = category.strip().lower()
    return c if c in VALID_CATEGORIES else "fact"


def _normalize_memory_type(memory_type: str | None, category: str = "fact") -> str:
    if memory_type:
        mt = memory_type.strip().lower()
        if mt in VALID_MEMORY_TYPES:
            return mt
    return DEFAULT_TYPE_BY_CATEGORY.get(category, "semantic")


def _normalize_relation(relation: str | None) -> str:
    if not relation:
        return "relates_to"
    r = relation.strip().lower().replace(" ", "_")
    return r if r in VALID_RELATIONS else "relates_to"


def normalize_workspace(raw: str | None) -> str:
    """Normalize a workspace path or scope name to a canonical identifier."""
    if not raw:
        return "global"
    w = raw.strip()
    if not w or w.lower() == "global":
        return "global"
    if w.lower().startswith("file:///"):
        w = unquote(w[8:])
    elif w.lower().startswith("file://"):
        w = unquote(w[7:])
    w = w.replace("\\", "/").rstrip("/")
    # Windows drive path normalization (e.g. C:/Users/...)
    if len(w) >= 2 and w[1] == ":":
        w = w[0].lower() + w[1:]
    return w.lower()


def workspace_display_name(workspace: str) -> str:
    if not workspace or workspace == "global":
        return "全局"
    base = workspace.rstrip("/").split("/")[-1]
    return base or workspace


def _normalize_tags(tags: list[str] | str | None) -> str:
    if not tags:
        return ""
    if isinstance(tags, str):
        parts = re.split(r"[,，;；\s]+", tags)
    else:
        parts = [str(t) for t in tags]
    cleaned: list[str] = []
    seen: set[str] = set()
    for p in parts:
        tok = p.strip().lstrip("#")
        if tok and tok.lower() not in seen:
            seen.add(tok.lower())
            cleaned.append(tok)
    return ",".join(cleaned[:20])


def _clamp_importance(importance: int | None, pinned: bool = False) -> int:
    if importance is None:
        return 5 if pinned else 3
    try:
        val = int(importance)
    except (TypeError, ValueError):
        val = 3
    return max(1, min(5, val))


def _extract_tokens(text: str, *, for_query: bool = False) -> list[str]:
    """Extract Latin/numeric tokens + CJK bigrams (and unigrams for index) for FTS5 BM25."""
    if not text:
        return []
    lowered = text.lower()
    tokens: list[str] = []
    seen: set[str] = set()

    def _add(tok: str) -> None:
        if not tok or tok in seen:
            return
        if for_query and tok in STOPWORDS:
            return
        seen.add(tok)
        tokens.append(tok)

    for m in _LATIN_TOKEN_RE.findall(lowered):
        cleaned = m.strip(".-_")
        if len(cleaned) >= 2:
            _add(cleaned)

    for run in _CJK_RUN_RE.findall(lowered):
        n = len(run)
        if n == 1:
            if not for_query or run not in STOPWORDS:
                _add(run)
            continue
        # Add 2-char overlapping bigrams (primary Chinese BM25 indexing unit)
        for i in range(n - 1):
            bg = run[i : i + 2]
            if for_query and bg in STOPWORDS:
                continue
            _add(bg)
        # Also include 3-char trigrams for higher precision phrases
        if n >= 3:
            for i in range(n - 2):
                _add(run[i : i + 3])
        # Include single chars when indexing so 1-char queries still work if explicitly issued
        if not for_query:
            for ch in run:
                if ch not in STOPWORDS:
                    _add(ch)

    return tokens


def _build_fts_text(
    content: str,
    key: str | None = None,
    tags: str | None = None,
    category: str | None = None,
    memory_type: str | None = None,
    workspace: str | None = None,
) -> str:
    combined = " ".join(
        x for x in (key or "", tags or "", category or "", memory_type or "", workspace or "", content or "") if x
    )
    return " ".join(_extract_tokens(combined, for_query=False))


def _fts_available(conn: sqlite3.Connection) -> bool:
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='memories_fts'"
        ).fetchone()
        return row is not None
    except sqlite3.Error:
        return False


def _sync_fts_row(conn: sqlite3.Connection, row_id: int) -> None:
    if not _fts_available(conn):
        return
    row = conn.execute(
        "SELECT id, key, category, memory_type, workspace, tags, content FROM memories WHERE id = ?",
        (row_id,),
    ).fetchone()
    conn.execute("DELETE FROM memories_fts WHERE memory_id = ?", (row_id,))
    if row is None:
        return
    fts_text = _build_fts_text(
        content=row["content"],
        key=row["key"],
        tags=row["tags"],
        category=row["category"],
        memory_type=row["memory_type"],
        workspace=row["workspace"],
    )
    conn.execute(
        "INSERT INTO memories_fts (memory_id, search_text) VALUES (?, ?)",
        (row_id, fts_text),
    )


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(get_db_path(), timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    _init_and_migrate(conn)
    return conn


def _init_and_migrate(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key TEXT UNIQUE,
            category TEXT NOT NULL DEFAULT 'fact',
            memory_type TEXT NOT NULL DEFAULT 'semantic',
            workspace TEXT NOT NULL DEFAULT 'global',
            tags TEXT NOT NULL DEFAULT '',
            content TEXT NOT NULL,
            pinned INTEGER NOT NULL DEFAULT 0,
            importance INTEGER NOT NULL DEFAULT 3,
            access_count INTEGER NOT NULL DEFAULT 0,
            last_accessed_at TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'active',
            superseded_by INTEGER,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )

    # Seamless v1.0 -> v2.0 column migration
    existing_cols = {
        r["name"] for r in conn.execute("PRAGMA table_info(memories)").fetchall()
    }
    migrations: list[tuple[str, str]] = [
        ("memory_type", "TEXT NOT NULL DEFAULT 'semantic'"),
        ("workspace", "TEXT NOT NULL DEFAULT 'global'"),
        ("tags", "TEXT NOT NULL DEFAULT ''"),
        ("importance", "INTEGER NOT NULL DEFAULT 3"),
        ("access_count", "INTEGER NOT NULL DEFAULT 0"),
        ("last_accessed_at", "TEXT NOT NULL DEFAULT ''"),
        ("status", "TEXT NOT NULL DEFAULT 'active'"),
        ("superseded_by", "INTEGER"),
    ]
    migrated_any = False
    for col, col_def in migrations:
        if col not in existing_cols:
            conn.execute(f"ALTER TABLE memories ADD COLUMN {col} {col_def}")
            migrated_any = True

    if migrated_any:
        # Backfill migrated v1.0 rows sensibly
        conn.execute(
            "UPDATE memories SET importance = 5 WHERE pinned = 1 AND importance = 3"
        )
        conn.execute(
            "UPDATE memories SET last_accessed_at = updated_at WHERE last_accessed_at = ''"
        )
        conn.execute(
            "UPDATE memories SET memory_type = 'semantic' WHERE memory_type = '' OR memory_type IS NULL"
        )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS memory_edges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            src_id INTEGER NOT NULL,
            dst_id INTEGER NOT NULL,
            relation TEXT NOT NULL DEFAULT 'relates_to',
            weight REAL NOT NULL DEFAULT 1.0,
            created_at TEXT NOT NULL,
            UNIQUE(src_id, dst_id, relation),
            FOREIGN KEY(src_id) REFERENCES memories(id) ON DELETE CASCADE,
            FOREIGN KEY(dst_id) REFERENCES memories(id) ON DELETE CASCADE
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_status_ws ON memories(status, workspace)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_edges_src ON memory_edges(src_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_edges_dst ON memory_edges(dst_id)")

    # Create FTS5 virtual table if supported by SQLite build
    try:
        conn.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts
            USING fts5(memory_id UNINDEXED, search_text)
            """
        )
    except sqlite3.Error:
        pass

    conn.commit()
    _seed_if_first_run(conn)

    # Ensure all memories are indexed in FTS5 (covers v1.0 -> v2.0 migration)
    if _fts_available(conn):
        mem_count = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        fts_count = conn.execute("SELECT COUNT(*) FROM memories_fts").fetchone()[0]
        if mem_count != fts_count:
            conn.execute("DELETE FROM memories_fts")
            for row in conn.execute("SELECT id FROM memories").fetchall():
                _sync_fts_row(conn, row["id"])
            conn.commit()
        if migrated_any or not os.path.exists(get_nebula_path()):
            export_markdown(conn)


def _seed_if_first_run(conn: sqlite3.Connection) -> None:
    """Seed initial user profile memories once if database is empty and unseeded."""
    marker = os.path.join(get_memory_dir(), ".seeded")
    if os.path.exists(marker) or not os.path.exists(SEED_FILE):
        return
    try:
        count = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        if count == 0:
            with open(SEED_FILE, "r", encoding="utf-8") as f:
                raw_seed = json.load(f)
            if isinstance(raw_seed, dict):
                items = raw_seed.get("memories") or []
                seed_edges = raw_seed.get("edges") or []
            else:
                items = raw_seed if isinstance(raw_seed, list) else []
                seed_edges = []
            now = _utc_now()
            for item in items:
                content = (item.get("content") or "").strip()
                if not content:
                    continue
                cat = _normalize_category(item.get("category"))
                mtype = _normalize_memory_type(item.get("memory_type"), cat)
                pinned = 1 if item.get("pinned") else 0
                imp = _clamp_importance(item.get("importance"), pinned=bool(pinned))
                cur = conn.execute(
                    """
                    INSERT OR IGNORE INTO memories (
                        key, category, memory_type, workspace, tags, content,
                        pinned, importance, access_count, last_accessed_at,
                        status, superseded_by, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, 'active', NULL, ?, ?)
                    """,
                    (
                        item.get("key"),
                        cat,
                        mtype,
                        normalize_workspace(item.get("workspace")),
                        _normalize_tags(item.get("tags")),
                        content,
                        pinned,
                        imp,
                        now,
                        now,
                        now,
                    ),
                )
                if cur.lastrowid:
                    _sync_fts_row(conn, cur.lastrowid)
            for edge in seed_edges:
                if not isinstance(edge, dict):
                    continue
                src_id = _resolve_memory_id(conn, edge.get("src_id"), edge.get("src_key"))
                dst_id = _resolve_memory_id(conn, edge.get("dst_id"), edge.get("dst_key"))
                if src_id and dst_id and src_id != dst_id:
                    _upsert_edge(
                        conn,
                        src_id,
                        dst_id,
                        relation=edge.get("relation", "relates_to"),
                        weight=float(edge.get("weight", 1.0)),
                    )
            conn.commit()
            export_markdown(conn)
        with open(marker, "w", encoding="utf-8") as f:
            f.write(_utc_now() + "\n")
    except Exception:
        pass


def compute_recency_strength(memory: dict[str, Any], now: _dt.datetime | None = None) -> float:
    """Power-law decay + recall reinforcement (inspired by Generative Agents & dsh-memory-nebula)."""
    if memory.get("pinned"):
        return 1.0
    now_dt = now or _dt.datetime.now(_dt.timezone.utc)
    ref_ts = _parse_utc(memory.get("last_accessed_at")) or _parse_utc(memory.get("updated_at"))
    if ref_ts is None:
        return 0.7
    age_days = max(0.0, (now_dt - ref_ts).total_seconds() / 86400.0)
    mtype = memory.get("memory_type") or "semantic"
    base_half_life = 14.0 if mtype == "episodic" else 45.0
    access_count = max(0, int(memory.get("access_count") or 0))
    reinforced_half_life = base_half_life * (1.0 + 0.35 * math.log1p(access_count))
    return 1.0 / (1.0 + (age_days / reinforced_half_life) ** 0.85)


def export_markdown(conn: sqlite3.Connection | None = None) -> str:
    """Export active and archived memories + knowledge graph links to UTF-8 Markdown."""
    owns_conn = conn is None
    if conn is None:
        conn = _connect()
    try:
        rows = conn.execute(
            """
            SELECT *
            FROM memories
            ORDER BY
                CASE status WHEN 'active' THEN 0 WHEN 'archived' THEN 1 ELSE 2 END,
                pinned DESC, importance DESC, updated_at DESC, id DESC
            """
        ).fetchall()
        edges = conn.execute(
            "SELECT src_id, dst_id, relation, weight FROM memory_edges ORDER BY src_id, id"
        ).fetchall()
        edges_by_src: dict[int, list[sqlite3.Row]] = {}
        for e in edges:
            edges_by_src.setdefault(e["src_id"], []).append(e)

        active_rows = [r for r in rows if r["status"] == "active"]
        inactive_rows = [r for r in rows if r["status"] != "active"]

        lines = [
            "# Antigravity 本地认知记忆库 (Local Memory v2.2)",
            "",
            f"> 存储路径：`{get_db_path()}`  ",
            f"> 最后同步：{_utc_now()}  ",
            f"> 活跃记忆：{len(active_rows)} 条 ｜ 归档/演化历史：{len(inactive_rows)} 条 ｜ 图谱关系：{len(edges)} 条",
            "",
        ]

        grouped: dict[str, list[sqlite3.Row]] = {cat: [] for cat in VALID_CATEGORIES}
        for row in active_rows:
            cat = row["category"] if row["category"] in grouped else "fact"
            grouped[cat].append(row)

        for cat in VALID_CATEGORIES:
            items = grouped[cat]
            if not items:
                continue
            lines.append(f"## {CATEGORY_LABELS.get(cat, cat)}")
            lines.append("")
            for item in items:
                pin_badge = "[置顶] " if item["pinned"] else ""
                type_badge = f"[{MEMORY_TYPE_LABELS.get(item['memory_type'], item['memory_type'])}]"
                ws_badge = f"[{workspace_display_name(item['workspace'])}]"
                stars = "★" * int(item["importance"]) + "☆" * (5 - int(item["importance"]))
                key_badge = f" (`{item['key']}`)" if item["key"] else ""
                tag_badge = f" `#{item['tags'].replace(',', ' #')}`" if item["tags"] else ""
                rel_items = edges_by_src.get(item["id"], [])
                rel_str = ""
                if rel_items:
                    rel_parts = [f"{e['relation']} #{e['dst_id']}" for e in rel_items]
                    rel_str = f" 〔关联: {', '.join(rel_parts)}〕"
                lines.append(
                    f"- **#{item['id']}** {pin_badge}{type_badge}{ws_badge} {stars} "
                    f"{item['content']}{key_badge}{tag_badge}{rel_str} "
                    f"— *召回 {item['access_count']} 次 · {item['updated_at']}*"
                )
            lines.append("")

        if inactive_rows:
            lines.append("## 归档与历史演化 (Archived / Superseded)")
            lines.append("")
            for item in inactive_rows:
                status_tag = (
                    f"已被 #{item['superseded_by']} 替代"
                    if item["status"] == "superseded" and item["superseded_by"]
                    else item["status"]
                )
                lines.append(
                    f"- ~~**#{item['id']}** [{status_tag}] {item['content']}~~ — *{item['updated_at']}*"
                )
            lines.append("")

        md_text = "\n".join(lines).rstrip() + "\n"
        with open(get_markdown_path(), "w", encoding="utf-8") as f:
            f.write(md_text)
        try:
            import nebula_export

            nebula_export.export_nebula(conn, get_nebula_path(), compute_recency_strength)
        except Exception:
            pass
        return get_markdown_path()
    finally:
        if owns_conn:
            conn.close()


def generate_nebula(output_path: str | None = None) -> dict[str, Any]:
    """Generate/refresh the standalone offline Star Nebula HTML visualization."""
    import nebula_export

    conn = _connect()
    try:
        target = output_path or get_nebula_path()
        return nebula_export.export_nebula(conn, target, compute_recency_strength)
    finally:
        conn.close()


def render_nebula_html_live(embed_mcp_bridge: bool = True) -> tuple[str, dict[str, Any]]:
    """Render live Star Nebula HTML directly in memory for the Antigravity MCP App view."""
    import nebula_export

    conn = _connect()
    try:
        return nebula_export.render_nebula_html(
            conn, compute_recency_strength, embed_mcp_bridge=embed_mcp_bridge
        )
    finally:
        conn.close()


def _resolve_memory_id(
    conn: sqlite3.Connection,
    memory_id: int | None = None,
    key: str | None = None,
) -> int | None:
    if memory_id is not None:
        row = conn.execute("SELECT id FROM memories WHERE id = ?", (int(memory_id),)).fetchone()
        return row["id"] if row else None
    if key and key.strip():
        row = conn.execute("SELECT id FROM memories WHERE key = ?", (key.strip(),)).fetchone()
        return row["id"] if row else None
    return None


def _upsert_edge(
    conn: sqlite3.Connection,
    src_id: int,
    dst_id: int,
    relation: str = "relates_to",
    weight: float = 1.0,
) -> dict[str, Any]:
    if src_id == dst_id:
        raise ValueError("Cannot link a memory to itself.")
    norm_rel = _normalize_relation(relation)
    w = max(0.1, min(2.0, float(weight)))
    now = _utc_now()
    conn.execute(
        """
        INSERT INTO memory_edges (src_id, dst_id, relation, weight, created_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(src_id, dst_id, relation) DO UPDATE SET weight = excluded.weight
        """,
        (src_id, dst_id, norm_rel, w, now),
    )
    return {"src_id": src_id, "dst_id": dst_id, "relation": norm_rel, "weight": w}


def link_memories(
    src_id: int | None = None,
    src_key: str | None = None,
    dst_id: int | None = None,
    dst_key: str | None = None,
    relation: str = "relates_to",
    weight: float = 1.0,
) -> dict[str, Any]:
    """Create or update a directed knowledge-graph edge between two memories."""
    conn = _connect()
    try:
        resolved_src = _resolve_memory_id(conn, src_id, src_key)
        resolved_dst = _resolve_memory_id(conn, dst_id, dst_key)
        if resolved_src is None:
            raise ValueError("Source memory not found.")
        if resolved_dst is None:
            raise ValueError("Destination memory not found.")
        edge = _upsert_edge(conn, resolved_src, resolved_dst, relation=relation, weight=weight)
        conn.commit()
        export_markdown(conn)
        return {"action": "linked", "edge": edge}
    finally:
        conn.close()


def remember(
    content: str,
    category: str = "fact",
    key: str | None = None,
    pinned: bool = False,
    memory_type: str | None = None,
    workspace: str | None = None,
    tags: list[str] | str | None = None,
    importance: int | None = None,
    supersedes_id: int | None = None,
    supersedes_key: str | None = None,
    links: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Insert or update a memory with cognitive typing, workspace scoping, conflict evolution, and graph links."""
    text = (content or "").strip()
    if not text:
        raise ValueError("Memory content cannot be empty.")

    norm_cat = _normalize_category(category)
    norm_type = _normalize_memory_type(memory_type, norm_cat)
    norm_ws = normalize_workspace(workspace)
    norm_tags = _normalize_tags(tags)
    norm_key = key.strip() if key and key.strip() else None
    pin_val = 1 if pinned else 0
    imp_val = _clamp_importance(importance, pinned=bool(pin_val))
    now = _utc_now()

    conn = _connect()
    try:
        # Handle explicit supersedes before insert so new memory can inherit the key cleanly
        superseded_old_id = _resolve_memory_id(conn, supersedes_id, supersedes_key)
        if superseded_old_id is not None:
            old_row = conn.execute("SELECT id, key FROM memories WHERE id = ?", (superseded_old_id,)).fetchone()
            if old_row:
                archived_key = f"{old_row['key']}__superseded_{old_row['id']}" if old_row["key"] else None
                if norm_key is None and old_row["key"]:
                    norm_key = old_row["key"]
                conn.execute(
                    """
                    UPDATE memories
                    SET key = ?, pinned = 0, status = 'superseded', updated_at = ?
                    WHERE id = ?
                    """,
                    (archived_key, now, old_row["id"]),
                )

        action = "created"
        if norm_key:
            existing = conn.execute(
                "SELECT id FROM memories WHERE key = ? AND status = 'active'", (norm_key,)
            ).fetchone()
            if existing:
                conn.execute(
                    """
                    UPDATE memories
                    SET category = ?, memory_type = ?, workspace = ?, tags = ?,
                        content = ?, pinned = ?, importance = ?, last_accessed_at = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        norm_cat,
                        norm_type,
                        norm_ws,
                        norm_tags,
                        text,
                        pin_val,
                        imp_val,
                        now,
                        now,
                        existing["id"],
                    ),
                )
                row_id = existing["id"]
                action = "updated"
            else:
                cur = conn.execute(
                    """
                    INSERT INTO memories (
                        key, category, memory_type, workspace, tags, content,
                        pinned, importance, access_count, last_accessed_at,
                        status, superseded_by, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, 'active', NULL, ?, ?)
                    """,
                    (
                        norm_key,
                        norm_cat,
                        norm_type,
                        norm_ws,
                        norm_tags,
                        text,
                        pin_val,
                        imp_val,
                        now,
                        now,
                        now,
                    ),
                )
                row_id = cur.lastrowid
        else:
            existing_exact = conn.execute(
                "SELECT id FROM memories WHERE content = ? AND workspace = ? AND status = 'active'",
                (text, norm_ws),
            ).fetchone()
            if existing_exact:
                conn.execute(
                    """
                    UPDATE memories
                    SET category = ?, memory_type = ?, tags = CASE WHEN ? != '' THEN ? ELSE tags END,
                        pinned = MAX(pinned, ?), importance = MAX(importance, ?),
                        last_accessed_at = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        norm_cat,
                        norm_type,
                        norm_tags,
                        norm_tags,
                        pin_val,
                        imp_val,
                        now,
                        now,
                        existing_exact["id"],
                    ),
                )
                row_id = existing_exact["id"]
                action = "deduplicated"
            else:
                cur = conn.execute(
                    """
                    INSERT INTO memories (
                        key, category, memory_type, workspace, tags, content,
                        pinned, importance, access_count, last_accessed_at,
                        status, superseded_by, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, 'active', NULL, ?, ?)
                    """,
                    (
                        None,
                        norm_cat,
                        norm_type,
                        norm_ws,
                        norm_tags,
                        text,
                        pin_val,
                        imp_val,
                        now,
                        now,
                        now,
                    ),
                )
                row_id = cur.lastrowid

        created_edges: list[dict[str, Any]] = []
        if superseded_old_id is not None and superseded_old_id != row_id:
            conn.execute(
                "UPDATE memories SET superseded_by = ? WHERE id = ?",
                (row_id, superseded_old_id),
            )
            created_edges.append(
                _upsert_edge(conn, row_id, superseded_old_id, relation="supersedes", weight=1.0)
            )
            _sync_fts_row(conn, superseded_old_id)
            action = "superseded"

        if links:
            for link in links:
                if not isinstance(link, dict):
                    continue
                target_id = _resolve_memory_id(conn, link.get("target_id"), link.get("target_key"))
                if target_id and target_id != row_id:
                    created_edges.append(
                        _upsert_edge(
                            conn,
                            row_id,
                            target_id,
                            relation=link.get("relation", "relates_to"),
                            weight=link.get("weight", 1.0),
                        )
                    )

        _sync_fts_row(conn, row_id)
        conn.commit()
        md_path = export_markdown(conn)
        row = conn.execute("SELECT * FROM memories WHERE id = ?", (row_id,)).fetchone()
        return {
            "action": action,
            "memory": dict(row) if row else {"id": row_id},
            "edges_created": created_edges,
            "markdown_path": md_path,
        }
    finally:
        conn.close()


def _workspace_matches(mem_ws: str, target_ws: str | None, include_all: bool) -> bool:
    if include_all or not target_ws or target_ws == "global":
        return include_all or mem_ws == "global"
    if mem_ws == "global" or mem_ws == target_ws:
        return True
    # Also allow matching by folder basename if user tagged workspace concisely
    return workspace_display_name(mem_ws) == workspace_display_name(target_ws)


def recall(
    query: str,
    category: str | None = None,
    memory_type: str | None = None,
    workspace: str | None = None,
    include_all_workspaces: bool = True,
    include_graph: bool = True,
    include_inactive: bool = False,
    limit: int = 10,
    min_relevance: float = 0.0,
    min_token_hits: int = 1,
    exclude_ids: set[int] | None = None,
    reinforce: bool = True,
) -> list[dict[str, Any]]:
    """Hybrid BM25 + CJK bigram + 3-factor scoring (Relevance * Importance * Decay) + 1-hop graph expansion."""
    q = (query or "").strip()
    norm_ws = normalize_workspace(workspace) if workspace else None
    excluded = exclude_ids or set()

    if not q:
        return list_memories(
            category=category,
            memory_type=memory_type,
            workspace=workspace if not include_all_workspaces else None,
            status="all" if include_inactive else "active",
            limit=limit,
        )

    conn = _connect()
    try:
        # 1. Fetch candidate pool filtered by status/category/type/workspace
        sql = "SELECT * FROM memories WHERE 1=1"
        params: list[Any] = []
        if not include_inactive:
            sql += " AND status = 'active'"
        if category:
            sql += " AND category = ?"
            params.append(_normalize_category(category))
        if memory_type:
            sql += " AND memory_type = ?"
            params.append(_normalize_memory_type(memory_type))

        rows = conn.execute(sql, params).fetchall()
        candidates: dict[int, dict[str, Any]] = {}
        for r in rows:
            d = dict(r)
            if not _workspace_matches(d["workspace"], norm_ws, include_all_workspaces):
                continue
            candidates[d["id"]] = d

        if not candidates:
            return []

        # 2. FTS5 BM25 retrieval (gracefully degrades if FTS5 unavailable)
        query_tokens = _extract_tokens(q, for_query=True)
        # Prioritize longer / non-single-char tokens for FTS query precision
        fts_tokens = [t for t in query_tokens if len(t) >= 2][:24] or query_tokens[:16]
        bm25_scores: dict[int, float] = {}
        if fts_tokens and _fts_available(conn):
            fts_expr = " OR ".join(f'"{t.replace(chr(34), "")}"' for t in fts_tokens if t)
            if fts_expr:
                try:
                    fts_rows = conn.execute(
                        "SELECT memory_id, bm25(memories_fts) AS rank FROM memories_fts WHERE memories_fts MATCH ? LIMIT 80",
                        (fts_expr,),
                    ).fetchall()
                    # SQLite bm25() returns more negative values for stronger matches
                    for fr in fts_rows:
                        mid = int(fr["memory_id"])
                        if mid in candidates:
                            raw_strength = abs(float(fr["rank"]))
                            # Map to (0, 1] smoothly
                            bm25_scores[mid] = raw_strength / (1.0 + raw_strength)
                except sqlite3.Error:
                    pass

        # 3. Compute 3-factor scores: Relevance + Importance + Recency/Decay + Workspace boost
        now_dt = _dt.datetime.now(_dt.timezone.utc)
        q_lower = q.lower()
        scored_direct: list[tuple[float, float, dict[str, Any]]] = []

        for mid, mem in candidates.items():
            if mid in excluded:
                continue
            haystack = f"{mem.get('key') or ''} {mem.get('tags') or ''} {mem.get('content') or ''}".lower()
            tags_lower = (mem.get("tags") or "").lower()

            rel = bm25_scores.get(mid, 0.0)
            exact_phrase_hit = len(q_lower) >= 2 and q_lower in haystack
            if exact_phrase_hit:
                rel = min(1.0, rel + 0.55)
            # Token/tag hit ratio bonus
            token_hits = 0
            tag_hits = 0
            key_hit = bool(mem.get("key") and mem["key"].lower() in q_lower)
            if fts_tokens:
                token_hits = sum(1 for t in fts_tokens if t in haystack)
                if token_hits > 0:
                    ratio = token_hits / max(1, min(len(fts_tokens), 6))
                    rel = min(1.0, max(rel, 0.25 + 0.55 * min(1.0, ratio)))
                tag_hits = sum(1 for t in fts_tokens if t in tags_lower)
                if tag_hits > 0:
                    rel = min(1.0, rel + 0.20)

            if min_token_hits > 1 and not exact_phrase_hit and not key_hit and tag_hits == 0:
                if token_hits < min_token_hits:
                    continue

            if rel <= 0.0 or rel < min_relevance:
                continue

            imp_score = int(mem.get("importance") or 3) / 5.0
            rec_score = compute_recency_strength(mem, now=now_dt)
            ws_boost = (
                0.15
                if (norm_ws and norm_ws != "global" and mem.get("workspace") == norm_ws)
                else 0.0
            )
            pin_boost = 0.08 if mem.get("pinned") else 0.0
            total_score = 0.55 * rel + 0.25 * imp_score + 0.20 * rec_score + ws_boost + pin_boost

            item = dict(mem)
            item["score"] = round(total_score, 4)
            item["relevance"] = round(rel, 4)
            item["recency_strength"] = round(rec_score, 4)
            item["match_source"] = "direct"
            scored_direct.append((total_score, rel, item))

        scored_direct.sort(key=lambda x: (x[0], x[1], x[2]["id"]), reverse=True)
        cap = max(1, min(limit, 50))
        direct_results = [x[2] for x in scored_direct[:cap]]

        # 4. 1-hop Knowledge Graph Expansion
        final_results: list[dict[str, Any]] = list(direct_results)
        if include_graph and direct_results:
            direct_ids = {m["id"] for m in direct_results}
            graph_candidates: dict[int, tuple[float, list[str]]] = {}
            top_seeds = direct_results[: min(4, len(direct_results))]

            for seed in top_seeds:
                seed_id = seed["id"]
                seed_score = float(seed["score"])
                edge_rows = conn.execute(
                    """
                    SELECT src_id, dst_id, relation, weight
                    FROM memory_edges
                    WHERE src_id = ? OR dst_id = ?
                    """,
                    (seed_id, seed_id),
                ).fetchall()
                for e in edge_rows:
                    neighbor_id = e["dst_id"] if e["src_id"] == seed_id else e["src_id"]
                    if neighbor_id in direct_ids or neighbor_id in excluded:
                        continue
                    if neighbor_id not in candidates:
                        continue
                    if e["relation"] == "supersedes" and not include_inactive:
                        continue
                    prop_score = seed_score * 0.55 * float(e["weight"])
                    direction = (
                        f"#{seed_id} --{e['relation']}--> #{neighbor_id}"
                        if e["src_id"] == seed_id
                        else f"#{neighbor_id} --{e['relation']}--> #{seed_id}"
                    )
                    if neighbor_id not in graph_candidates:
                        graph_candidates[neighbor_id] = (prop_score, [direction])
                    else:
                        prev_score, prev_paths = graph_candidates[neighbor_id]
                        graph_candidates[neighbor_id] = (
                            max(prev_score, prop_score),
                            prev_paths + [direction],
                        )

            expanded = sorted(graph_candidates.items(), key=lambda kv: kv[1][0], reverse=True)
            max_extra = max(1, min(3, cap - len(final_results) if len(final_results) < cap else 2))
            for nid, (gscore, paths) in expanded[:max_extra]:
                nmem = dict(candidates[nid])
                nmem["score"] = round(gscore, 4)
                nmem["recency_strength"] = round(compute_recency_strength(nmem, now=now_dt), 4)
                nmem["match_source"] = "graph_1hop"
                nmem["via_relations"] = paths
                final_results.append(nmem)

            final_results = final_results[:cap]

        # 5. Recall reinforcement (update access_count + last_accessed_at for direct hits)
        if reinforce and direct_results:
            now_str = _utc_now()
            hit_ids = [m["id"] for m in direct_results]
            conn.executemany(
                "UPDATE memories SET access_count = access_count + 1, last_accessed_at = ? WHERE id = ?",
                [(now_str, mid) for mid in hit_ids],
            )
            conn.commit()
            for m in final_results:
                if m["id"] in hit_ids:
                    m["access_count"] = int(m.get("access_count") or 0) + 1
                    m["last_accessed_at"] = now_str

        return final_results
    finally:
        conn.close()


def list_memories(
    category: str | None = None,
    memory_type: str | None = None,
    workspace: str | None = None,
    status: str = "active",
    limit: int = 30,
) -> list[dict[str, Any]]:
    """List saved memories ordered by pinned status, importance, and recency."""
    conn = _connect()
    try:
        sql = "SELECT * FROM memories WHERE 1=1"
        params: list[Any] = []
        if status and status.lower() != "all":
            st = status.strip().lower()
            if st in VALID_STATUSES:
                sql += " AND status = ?"
                params.append(st)
        if category:
            sql += " AND category = ?"
            params.append(_normalize_category(category))
        if memory_type:
            sql += " AND memory_type = ?"
            params.append(_normalize_memory_type(memory_type))
        if workspace:
            norm_ws = normalize_workspace(workspace)
            sql += " AND (workspace = ? OR workspace = 'global')"
            params.append(norm_ws)
        sql += " ORDER BY pinned DESC, importance DESC, updated_at DESC, id DESC LIMIT ?"
        params.append(max(1, min(limit, 100)))
        rows = conn.execute(sql, params).fetchall()
        now_dt = _dt.datetime.now(_dt.timezone.utc)
        out: list[dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            d["recency_strength"] = round(compute_recency_strength(d, now=now_dt), 4)
            out.append(d)
        return out
    finally:
        conn.close()


def forget(
    memory_id: int | None = None,
    key: str | None = None,
    mode: str = "delete",
) -> dict[str, Any]:
    """Delete or soft-archive a memory by numeric id or unique key."""
    if memory_id is None and not (key and key.strip()):
        raise ValueError("Provide either memory_id or key to forget a memory.")

    conn = _connect()
    try:
        target_id = _resolve_memory_id(conn, memory_id, key)
        if target_id is None:
            return {"action": mode, "affected": 0, "deleted": 0}

        if (mode or "delete").strip().lower() == "archive":
            now = _utc_now()
            cur = conn.execute(
                "UPDATE memories SET status = 'archived', pinned = 0, updated_at = ? WHERE id = ?",
                (now, target_id),
            )
            conn.commit()
            export_markdown(conn)
            return {"action": "archived", "affected": cur.rowcount, "deleted": 0}

        conn.execute("UPDATE memories SET superseded_by = NULL WHERE superseded_by = ?", (target_id,))
        cur = conn.execute("DELETE FROM memories WHERE id = ?", (target_id,))
        if _fts_available(conn):
            conn.execute("DELETE FROM memories_fts WHERE memory_id = ?", (target_id,))
        conn.commit()
        export_markdown(conn)
        return {"action": "deleted", "affected": cur.rowcount, "deleted": cur.rowcount}
    finally:
        conn.close()


# ---- Session Injection Deduplication Cache ----


def _load_session_cache() -> dict[str, Any]:
    path = get_session_cache_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_session_cache(cache: dict[str, Any]) -> None:
    # Keep at most 30 most recent conversation entries to prevent unbounded growth
    if len(cache) > 30:
        ordered = sorted(
            cache.items(),
            key=lambda kv: str((kv[1] or {}).get("updated_at", "")),
            reverse=True,
        )
        cache = dict(ordered[:30])
    try:
        with open(get_session_cache_path(), "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def record_session_injection(
    conversation_id: str | None,
    injected_ids: list[int],
    input_hash: str | None = None,
) -> None:
    if not conversation_id:
        return
    cache = _load_session_cache()
    entry = cache.get(conversation_id) or {"injected_ids": [], "last_input_hash": ""}
    merged = sorted(set(int(i) for i in entry.get("injected_ids", [])) | set(int(i) for i in injected_ids))
    entry["injected_ids"] = merged
    if input_hash is not None:
        entry["last_input_hash"] = input_hash
    entry["updated_at"] = _utc_now()
    cache[conversation_id] = entry
    _save_session_cache(cache)


def get_session_state(conversation_id: str | None) -> tuple[set[int], str]:
    if not conversation_id:
        return set(), ""
    cache = _load_session_cache()
    entry = cache.get(conversation_id) or {}
    return set(int(i) for i in entry.get("injected_ids", [])), str(entry.get("last_input_hash", ""))


def hash_user_input(user_input: str, workspace: str) -> str:
    raw = f"{workspace}::{user_input.strip()}".encode("utf-8", errors="ignore")
    return hashlib.sha256(raw).hexdigest()[:16]


def is_trivial_input(user_input: str) -> bool:
    cleaned = re.sub(r"[\s\.,!?;:。，！？；：~～]+", "", (user_input or "").strip().lower())
    if not cleaned or len(cleaned) < 3:
        return True
    if cleaned in TRIVIAL_INPUTS:
        return True
    tokens = _extract_tokens(user_input, for_query=True)
    return len(tokens) == 0


# ---- Hook Formatters ----


def format_session_start_context(
    workspace: str | None = None,
    max_chars: int = 1800,
) -> tuple[str, list[int]]:
    """Build a lean, high-signal SessionStart digest (Core Profile + current workspace + index)."""
    norm_ws = normalize_workspace(workspace) if workspace else "global"
    conn = _connect()
    try:
        all_active = [
            dict(r)
            for r in conn.execute(
                """
                SELECT * FROM memories
                WHERE status = 'active'
                ORDER BY pinned DESC, importance DESC, updated_at DESC, id DESC
                """
            ).fetchall()
        ]
        if not all_active:
            return "", []

        # 1. Core pinned memories (global or matching workspace)
        core = [
            m
            for m in all_active
            if m["pinned"] and _workspace_matches(m["workspace"], norm_ws, include_all=False)
        ]
        # 2. Top workspace-specific non-pinned memories (up to 4) when inside a project
        ws_specific: list[dict[str, Any]] = []
        if norm_ws != "global":
            ws_specific = [
                m
                for m in all_active
                if not m["pinned"]
                and m["workspace"] != "global"
                and _workspace_matches(m["workspace"], norm_ws, include_all=False)
            ][:4]

        selected = core[:12] + ws_specific
        injected_ids: list[int] = []

        # Inventory summary (Anthropic progressive-disclosure index)
        type_counts = {"semantic": 0, "episodic": 0, "procedural": 0}
        for m in all_active:
            mt = m.get("memory_type") or "semantic"
            type_counts[mt] = type_counts.get(mt, 0) + 1

        lines = [
            f"以下是用户的本地核心档案（Local Memory v2.2，纯本地存储于 {get_memory_dir()}）：",
            "",
        ]
        total = sum(len(x) for x in lines)
        for m in selected:
            t_label = MEMORY_TYPE_LABELS.get(m["memory_type"], m["memory_type"])
            ws_label = f"/{workspace_display_name(m['workspace'])}" if m["workspace"] != "global" else ""
            key_tag = f" ({m['key']})" if m["key"] else ""
            entry = f"- #{m['id']} [{t_label}{ws_label}] {m['content']}{key_tag}"
            if total + len(entry) + 1 > max_chars:
                break
            lines.append(entry)
            injected_ids.append(int(m["id"]))
            total += len(entry) + 1

        remaining = len(all_active) - len(injected_ids)
        lines.append("")
        lines.append(
            f"记忆库索引：共 {len(all_active)} 条活跃记忆"
            f"（语义 {type_counts['semantic']} / 情景 {type_counts['episodic']} / 程序 {type_counts['procedural']}）。"
            + (
                f"其余 {remaining} 条将由 PreInvocation 按话题自动召回，也可随时调用 local_memory_recall 深入检索。"
                if remaining > 0
                else "发现新的长期偏好、科研进展或可复用经验时，请主动调用 local_memory_remember 保存。"
            )
        )
        return "\n".join(lines), injected_ids
    finally:
        conn.close()


def format_pre_invocation_context(
    user_input: str,
    workspace: str | None = None,
    conversation_id: str | None = None,
    max_items: int = 4,
    max_chars: int = 1200,
) -> tuple[str, list[int]]:
    """JIT turn-level recall with dedup cache, relevance gate, and 1-hop graph expansion."""
    if is_trivial_input(user_input):
        return "", []

    norm_ws = normalize_workspace(workspace) if workspace else "global"
    injected_before, last_hash = get_session_state(conversation_id)
    cur_hash = hash_user_input(user_input, norm_ws)

    # Skip repeated PreInvocation hooks within the same user turn's tool-call loop
    if conversation_id and cur_hash == last_hash:
        return "", []

    q_tokens = _extract_tokens(user_input, for_query=True)
    multi_tokens = [t for t in q_tokens if len(t) >= 2]
    req_hits = 2 if len(multi_tokens) >= 3 else 1

    hits = recall(
        query=user_input,
        workspace=norm_ws if norm_ws != "global" else None,
        include_all_workspaces=(norm_ws == "global"),
        include_graph=True,
        include_inactive=False,
        limit=max_items,
        min_relevance=0.45,
        min_token_hits=req_hits,
        exclude_ids=injected_before,
        reinforce=True,
    )

    if not hits:
        record_session_injection(conversation_id, [], input_hash=cur_hash)
        return "", []

    lines = ["本轮话题按需召回的关联记忆（Local Memory JIT Recall）："]
    total = len(lines[0])
    newly_injected: list[int] = []
    for m in hits:
        t_label = MEMORY_TYPE_LABELS.get(m["memory_type"], m["memory_type"])
        via = ""
        if m.get("match_source") == "graph_1hop" and m.get("via_relations"):
            via = f" 〔图谱关联: {m['via_relations'][0]}〕"
        entry = f"- #{m['id']} [{t_label}] {m['content']}{via}"
        if total + len(entry) + 1 > max_chars:
            break
        lines.append(entry)
        newly_injected.append(int(m["id"]))
        total += len(entry) + 1

    record_session_injection(conversation_id, newly_injected, input_hash=cur_hash)
    if not newly_injected:
        return "", []
    return "\n".join(lines), newly_injected
