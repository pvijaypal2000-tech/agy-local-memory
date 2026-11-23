"""SessionStart hook (v2.0) for Antigravity local-memory plugin.

Injects a lean Core Profile + current workspace summary + memory inventory,
and records injected memory IDs in the session cache so PreInvocation won't repeat them.
"""

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


def _read_stdin_json() -> dict[str, Any]:
    try:
        if sys.stdin.isatty():
            return {}
        raw = sys.stdin.read().strip()
        if not raw:
            return {}
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def main() -> int:
    _configure_utf8()
    try:
        event = _read_stdin_json()
        conv_id = event.get("conversationId")
        ws_paths = event.get("workspacePaths") or []
        active_ws = ws_paths[0] if isinstance(ws_paths, list) and ws_paths else event.get("cwd")

        context, injected_ids = memory_store.format_session_start_context(workspace=active_ws)
        if not context:
            print("{}")
            return 0

        memory_store.record_session_injection(conv_id, injected_ids)
        payload = {
            "injectSteps": [
                {
                    "systemMessage": {
                        "systemMessage": context,
                    }
                }
            ]
        }
        print(json.dumps(payload, ensure_ascii=False))
        return 0
    except Exception:
        # Fail-open so memory hook issues never block a conversation turn
        print("{}")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
