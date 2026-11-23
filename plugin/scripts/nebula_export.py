"""Zero-dependency standalone HTML5 Canvas 2D Star Nebula exporter for local-memory."""

from __future__ import annotations

import datetime as _dt
import json
import os
import sqlite3
from typing import Any

PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE_PATH = os.path.join(PLUGIN_ROOT, "templates", "nebula_template.html")
BRIDGE_PATH = os.path.join(PLUGIN_ROOT, "templates", "mcp_app_bridge.min.js")
PLACEHOLDER = "/*__NEBULA_DATA__*/ null"


def _safe_inline_json(obj: Any) -> str:
    raw = json.dumps(obj, ensure_ascii=False)
    return (
        raw.replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def render_nebula_html(
    conn: sqlite3.Connection,
    recency_fn: Any,
    title: str = "Antigravity · 记忆星图",
    embed_mcp_bridge: bool = False,
) -> tuple[str, dict[str, Any]]:
    """Build the self-contained Star Nebula HTML document in memory."""
    if not os.path.exists(TEMPLATE_PATH):
        raise FileNotFoundError(f"Nebula template not found: {TEMPLATE_PATH}")

    rows = conn.execute(
        """
        SELECT id, key, category, memory_type, workspace, tags, content,
               pinned, importance, access_count, last_accessed_at,
               status, superseded_by, created_at, updated_at
        FROM memories
        ORDER BY id ASC
        """
    ).fetchall()
    edge_rows = conn.execute(
        "SELECT id, src_id, dst_id, relation, weight, created_at FROM memory_edges ORDER BY id ASC"
    ).fetchall()

    now_dt = _dt.datetime.now(_dt.timezone.utc)
    memories_payload: list[dict[str, Any]] = []
    workspaces: set[str] = {"global"}
    active_count = 0

    for r in rows:
        d = dict(r)
        if d.get("status", "active") == "active":
            active_count += 1
        ws = d.get("workspace") or "global"
        workspaces.add(ws)
        raw_tags = d.get("tags") or ""
        tag_list = [t.strip() for t in raw_tags.split(",") if t.strip()]
        memories_payload.append(
            {
                "id": int(d["id"]),
                "key": d.get("key"),
                "category": d.get("category") or "fact",
                "memory_type": d.get("memory_type") or "semantic",
                "workspace": ws,
                "tags": tag_list,
                "content": d.get("content") or "",
                "pinned": bool(d.get("pinned")),
                "importance": int(d.get("importance") or 3),
                "access_count": int(d.get("access_count") or 0),
                "recency_strength": round(float(recency_fn(d, now=now_dt)), 4),
                "status": d.get("status") or "active",
                "superseded_by": d.get("superseded_by"),
                "created_at": d.get("created_at") or "",
                "updated_at": d.get("updated_at") or "",
            }
        )

    edges_payload = [
        {
            "id": int(e["id"]),
            "src_id": int(e["src_id"]),
            "dst_id": int(e["dst_id"]),
            "relation": e["relation"] or "relates_to",
            "weight": float(e["weight"] or 1.0),
            "created_at": e["created_at"] or "",
        }
        for e in edge_rows
    ]

    payload = {
        "title": title,
        "generatedAt": now_dt.strftime("%Y-%m-%d %H:%M:%S UTC"),
        "memories": memories_payload,
        "edges": edges_payload,
        "workspaces": sorted(workspaces),
    }

    with open(TEMPLATE_PATH, "r", encoding="utf-8") as f:
        tpl = f.read()

    if PLACEHOLDER not in tpl:
        raise ValueError("Nebula template missing data placeholder.")

    html = tpl.replace(PLACEHOLDER, _safe_inline_json(payload), 1)

    if embed_mcp_bridge and os.path.exists(BRIDGE_PATH):
        with open(BRIDGE_PATH, "r", encoding="utf-8") as bf:
            bridge_js = bf.read().replace("</script>", "<\\/script>")
        html = html.replace("</body>", f"<script>{bridge_js}</script>\n</body>", 1)

    stats = {
        "memory_count": len(memories_payload),
        "active_count": active_count,
        "edge_count": len(edges_payload),
        "generated_at": payload["generatedAt"],
    }
    return html, stats


def export_nebula(
    conn: sqlite3.Connection,
    output_path: str,
    recency_fn: Any,
    title: str = "Antigravity · 记忆星图",
) -> dict[str, Any]:
    """Render all memories and knowledge-graph edges into a single offline nebula.html file."""
    html, stats = render_nebula_html(conn, recency_fn, title=title, embed_mcp_bridge=False)
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    return {
        "nebula_path": output_path,
        "memory_count": stats["memory_count"],
        "active_count": stats["active_count"],
        "edge_count": stats["edge_count"],
    }
