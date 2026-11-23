"""Pure-stdlib Stdio MCP server (v2.2) for the Antigravity local-memory plugin."""

from __future__ import annotations

import json
import os
import sys
from typing import Any

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import memory_store  # noqa: E402


def _configure_utf8() -> None:
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8")
            except Exception:
                pass


NEBULA_APP_URI = "ui://local-memory/nebula.html"

RESOURCES = [
    {
        "uri": NEBULA_APP_URI,
        "name": "记忆星图 (Local Memory Nebula)",
        "description": "Interactive offline Canvas 2D Star Nebula visualization of all saved local memories and knowledge-graph edges.",
        "mimeType": "text/html;profile=mcp-app",
        "_meta": {
            "ui": {
                "prefersBorder": False,
            }
        },
    }
]

TOOLS = [
    {
        "name": "local_memory_remember",
        "description": (
            "Save or update a persistent local memory across Antigravity conversations (v2.0). "
            "Supports cognitive typing (semantic/episodic/procedural), workspace scoping, tags, "
            "importance (1-5), supersedes conflict evolution, and knowledge-graph links."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "Concise, self-contained memory statement to persist locally.",
                },
                "category": {
                    "type": "string",
                    "enum": ["profile", "project", "preference", "learning", "fact"],
                    "default": "fact",
                    "description": "Topic category.",
                },
                "memory_type": {
                    "type": "string",
                    "enum": ["semantic", "episodic", "procedural"],
                    "description": (
                        "Cognitive layer: 'semantic' (stable facts/profile), "
                        "'episodic' (time-bound events/milestones), "
                        "'procedural' (reusable workflows/rules/lessons)."
                    ),
                },
                "workspace": {
                    "type": "string",
                    "default": "global",
                    "description": "Scope: 'global' (default) or a project directory path / project name.",
                },
                "key": {
                    "type": "string",
                    "description": "Optional unique slug for cleanly updating/replacing an existing memory.",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional domain/topic tags (e.g. ['research', 'python', 'workflow']).",
                },
                "importance": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 5,
                    "description": "Importance score from 1 (minor) to 5 (core). Defaults to 5 if pinned else 3.",
                },
                "pinned": {
                    "type": "boolean",
                    "default": False,
                    "description": "If true, always load in the lean SessionStart Core Profile.",
                },
                "supersedes_id": {
                    "type": "integer",
                    "description": "Optional ID of an outdated/contradicted memory that this new memory replaces while keeping history.",
                },
                "supersedes_key": {
                    "type": "string",
                    "description": "Optional key of an outdated/contradicted memory that this new memory replaces.",
                },
                "links": {
                    "type": "array",
                    "description": "Optional knowledge-graph edges to create from this memory to existing memories.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "target_id": {"type": "integer"},
                            "target_key": {"type": "string"},
                            "relation": {
                                "type": "string",
                                "enum": [
                                    "relates_to",
                                    "depends_on",
                                    "derived_from",
                                    "part_of",
                                    "supersedes",
                                    "contradicts",
                                ],
                                "default": "relates_to",
                            },
                            "weight": {"type": "number", "default": 1.0},
                        },
                    },
                },
            },
            "required": ["content"],
        },
    },
    {
        "name": "local_memory_recall",
        "description": (
            "Search persistent local memories using hybrid FTS5 BM25 + CJK matching, "
            "3-factor scoring (relevance, importance, power-law decay/reinforcement), "
            "and 1-hop knowledge-graph expansion."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query keywords or natural-language phrase (Chinese or English).",
                },
                "category": {
                    "type": "string",
                    "enum": ["profile", "project", "preference", "learning", "fact"],
                    "description": "Optional category filter.",
                },
                "memory_type": {
                    "type": "string",
                    "enum": ["semantic", "episodic", "procedural"],
                    "description": "Optional cognitive layer filter.",
                },
                "workspace": {
                    "type": "string",
                    "description": "Optional active workspace path/name to boost or isolate results.",
                },
                "include_all_workspaces": {
                    "type": "boolean",
                    "default": True,
                    "description": "If false and workspace is provided, restrict to global + matching workspace only.",
                },
                "include_graph": {
                    "type": "boolean",
                    "default": True,
                    "description": "If true, include 1-hop connected knowledge-graph neighbors.",
                },
                "include_inactive": {
                    "type": "boolean",
                    "default": False,
                    "description": "If true, also search archived and superseded historical memories.",
                },
                "limit": {
                    "type": "integer",
                    "default": 10,
                    "description": "Maximum number of results to return.",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "local_memory_list",
        "description": "List saved local memories with importance, decay strength, and access counts.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "enum": ["profile", "project", "preference", "learning", "fact"],
                    "description": "Optional category filter.",
                },
                "memory_type": {
                    "type": "string",
                    "enum": ["semantic", "episodic", "procedural"],
                    "description": "Optional cognitive layer filter.",
                },
                "workspace": {
                    "type": "string",
                    "description": "Optional workspace filter (returns global + that workspace).",
                },
                "status": {
                    "type": "string",
                    "enum": ["active", "archived", "superseded", "all"],
                    "default": "active",
                    "description": "Lifecycle status filter.",
                },
                "limit": {
                    "type": "integer",
                    "default": 30,
                    "description": "Maximum number of items to list.",
                },
            },
        },
    },
    {
        "name": "local_memory_forget",
        "description": "Delete or soft-archive a saved local memory by its numeric id or unique key.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {
                    "type": "integer",
                    "description": "Numeric memory id.",
                },
                "key": {
                    "type": "string",
                    "description": "Unique memory key.",
                },
                "mode": {
                    "type": "string",
                    "enum": ["delete", "archive"],
                    "default": "delete",
                    "description": "'delete' removes permanently; 'archive' keeps in inactive history.",
                },
            },
        },
    },
    {
        "name": "local_memory_link",
        "description": "Create or update a directed knowledge-graph relation between two existing memories.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "src_id": {"type": "integer", "description": "Source memory ID."},
                "src_key": {"type": "string", "description": "Source memory key (if src_id omitted)."},
                "dst_id": {"type": "integer", "description": "Target memory ID."},
                "dst_key": {"type": "string", "description": "Target memory key (if dst_id omitted)."},
                "relation": {
                    "type": "string",
                    "enum": [
                        "relates_to",
                        "depends_on",
                        "derived_from",
                        "part_of",
                        "supersedes",
                        "contradicts",
                    ],
                    "default": "relates_to",
                    "description": "Directed relationship type.",
                },
                "weight": {
                    "type": "number",
                    "default": 1.0,
                    "description": "Edge strength between 0.1 and 2.0.",
                },
            },
        },
    },
    {
        "name": "local_memory_nebula",
        "title": "记忆星图",
        "description": (
            "Open or refresh the interactive Star Nebula visualization (both inline/side-pane/global MCP App "
            "and standalone offline nebula.html) from all saved memories and knowledge-graph edges. "
            "Set open_in_browser=true only when the user explicitly asks for an external browser window."
        ),
        "_meta": {
            "ui": {
                "resourceUri": NEBULA_APP_URI,
                "visibility": ["model", "app"],
            },
            "com.google.antigravity/ui": {
                "entrypoints": [
                    {"type": "aux_pane", "title": "记忆星图"},
                    {"type": "global", "title": "记忆星图"},
                ]
            },
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "output_path": {
                    "type": "string",
                    "description": "Optional custom absolute path to write nebula.html (defaults to plugin data directory).",
                },
                "open_in_browser": {
                    "type": "boolean",
                    "default": False,
                    "description": "If true, open the generated nebula.html directly in the user's default system browser.",
                },
            },
        },
    },
]


def _write_message(msg: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _handle_tool_call(name: str, args: dict[str, Any]) -> dict[str, Any]:
    if name == "local_memory_remember":
        res = memory_store.remember(
            content=args.get("content", ""),
            category=args.get("category", "fact"),
            key=args.get("key"),
            pinned=bool(args.get("pinned", False)),
            memory_type=args.get("memory_type"),
            workspace=args.get("workspace"),
            tags=args.get("tags"),
            importance=args.get("importance"),
            supersedes_id=args.get("supersedes_id"),
            supersedes_key=args.get("supersedes_key"),
            links=args.get("links"),
        )
        return {
            "content": [{"type": "text", "text": json.dumps(res, ensure_ascii=False, indent=2)}],
            "structuredContent": res,
            "isError": False,
        }

    if name == "local_memory_recall":
        items = memory_store.recall(
            query=args.get("query", ""),
            category=args.get("category"),
            memory_type=args.get("memory_type"),
            workspace=args.get("workspace"),
            include_all_workspaces=bool(args.get("include_all_workspaces", True)),
            include_graph=bool(args.get("include_graph", True)),
            include_inactive=bool(args.get("include_inactive", False)),
            limit=int(args.get("limit", 10)),
        )
        return {
            "content": [{"type": "text", "text": json.dumps(items, ensure_ascii=False, indent=2)}],
            "isError": False,
        }

    if name == "local_memory_list":
        items = memory_store.list_memories(
            category=args.get("category"),
            memory_type=args.get("memory_type"),
            workspace=args.get("workspace"),
            status=args.get("status", "active"),
            limit=int(args.get("limit", 30)),
        )
        return {
            "content": [{"type": "text", "text": json.dumps(items, ensure_ascii=False, indent=2)}],
            "isError": False,
        }

    if name == "local_memory_forget":
        res = memory_store.forget(
            memory_id=args.get("id"),
            key=args.get("key"),
            mode=args.get("mode", "delete"),
        )
        return {
            "content": [{"type": "text", "text": json.dumps(res, ensure_ascii=False, indent=2)}],
            "structuredContent": res,
            "isError": False,
        }

    if name == "local_memory_link":
        res = memory_store.link_memories(
            src_id=args.get("src_id"),
            src_key=args.get("src_key"),
            dst_id=args.get("dst_id"),
            dst_key=args.get("dst_key"),
            relation=args.get("relation", "relates_to"),
            weight=float(args.get("weight", 1.0)),
        )
        return {
            "content": [{"type": "text", "text": json.dumps(res, ensure_ascii=False, indent=2)}],
            "structuredContent": res,
            "isError": False,
        }

    if name == "local_memory_nebula":
        res = memory_store.generate_nebula(output_path=args.get("output_path"))
        if bool(args.get("open_in_browser", False)):
            try:
                neb_path = os.path.normpath(res["nebula_path"])
                if hasattr(os, "startfile"):
                    os.startfile(neb_path)
                else:
                    import webbrowser

                    webbrowser.open("file:///" + neb_path.replace("\\", "/"))
                res["opened_in_browser"] = True
            except Exception as exc:
                res["opened_in_browser"] = False
                res["open_error"] = str(exc)
        return {
            "content": [{"type": "text", "text": json.dumps(res, ensure_ascii=False, indent=2)}],
            "structuredContent": res,
            "isError": False,
        }

    return {
        "content": [{"type": "text", "text": f"Unknown tool: {name}"}],
        "isError": True,
    }


def _handle_request(req: dict[str, Any]) -> dict[str, Any] | None:
    method = req.get("method", "")
    req_id = req.get("id")
    params = req.get("params") or {}

    if req_id is None:
        return None

    if method == "initialize":
        proto = params.get("protocolVersion") or "2024-11-05"
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": proto,
                "capabilities": {"tools": {}, "resources": {}},
                "serverInfo": {"name": "local-memory", "version": "2.2.0"},
            },
        }

    if method == "ping":
        return {"jsonrpc": "2.0", "id": req_id, "result": {}}

    if method == "resources/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"resources": RESOURCES}}

    if method == "resources/read":
        uri = str(params.get("uri") or "")
        if uri == NEBULA_APP_URI:
            try:
                html, _ = memory_store.render_nebula_html_live(embed_mcp_bridge=True)
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "contents": [
                            {
                                "uri": NEBULA_APP_URI,
                                "mimeType": "text/html;profile=mcp-app",
                                "text": html,
                                "_meta": {"ui": {"prefersBorder": False}},
                            }
                        ]
                    },
                }
            except Exception as exc:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32603, "message": f"Failed to render nebula UI: {exc}"},
                }
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32602, "message": f"Unknown resource URI: {uri}"},
        }

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": TOOLS}}

    if method == "tools/call":
        tool_name = params.get("name", "")
        tool_args = params.get("arguments") or {}
        try:
            result = _handle_tool_call(tool_name, tool_args)
        except Exception as exc:
            result = {
                "content": [{"type": "text", "text": f"Error: {exc}"}],
                "isError": True,
            }
        return {"jsonrpc": "2.0", "id": req_id, "result": result}

    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


def main() -> int:
    _configure_utf8()
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError as exc:
            _write_message(
                {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": f"Parse error: {exc}"},
                }
            )
            continue

        resp = _handle_request(req)
        if resp is not None:
            _write_message(resp)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
