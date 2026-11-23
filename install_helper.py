"""Cross-platform installer for Antigravity Local Memory v2.2 (Windows / macOS / Linux)."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile


def _print(msg: str) -> None:
    print(msg, flush=True)


def _check_python() -> None:
    if sys.version_info < (3, 10):
        raise SystemExit(
            f"[ERROR] 需要 Python 3.10 及以上版本，当前版本为 {sys.version.split()[0]}。\n"
            "请先从 https://www.python.org/downloads/ 安装新版 Python 后重试。"
        )
    try:
        import sqlite3  # noqa: F401
    except Exception as exc:
        raise SystemExit(f"[ERROR] 当前 Python 缺少 sqlite3 模块：{exc}")


def _write_configs(target_dir: str, py_exe: str) -> None:
    # Quote executable path if it contains spaces for shell command execution in hooks.json
    quoted_py = f'"{py_exe}"' if " " in py_exe else py_exe
    hooks_cfg = {
        "local-memory-lifecycle": {
            "SessionStart": [
                {
                    "type": "command",
                    "command": f"{quoted_py} scripts/session_start.py",
                    "timeout": 10,
                }
            ],
            "PreInvocation": [
                {
                    "type": "command",
                    "command": f"{quoted_py} scripts/pre_invocation.py",
                    "timeout": 8,
                }
            ],
        }
    }
    mcp_cfg = {
        "mcpServers": {
            "local-memory": {
                "command": py_exe,
                "args": [os.path.join(target_dir, "scripts", "mcp_server.py")],
                "env": {
                    "PYTHONIOENCODING": "utf-8",
                    "PYTHONUTF8": "1",
                },
            }
        }
    }
    with open(os.path.join(target_dir, "hooks.json"), "w", encoding="utf-8") as f:
        json.dump(hooks_cfg, f, ensure_ascii=False, indent=2)
        f.write("\n")
    with open(os.path.join(target_dir, "mcp_config.json"), "w", encoding="utf-8") as f:
        json.dump(mcp_cfg, f, ensure_ascii=False, indent=2)
        f.write("\n")


def _self_test(target_dir: str, py_exe: str) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        env = os.environ.copy()
        env["ANTIGRAVITY_LOCAL_MEMORY_DIR"] = tmp
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"

        # 1. Test SessionStart hook (seeds 3 welcome stars + 2 edges + builds nebula.html)
        p_start = subprocess.run(
            [py_exe, os.path.join(target_dir, "scripts", "session_start.py")],
            cwd=target_dir,
            input=json.dumps({"conversationId": "install-check", "hookEventName": "SessionStart"}),
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            check=True,
        )
        start_data = json.loads(p_start.stdout)
        assert "Local Memory" in start_data["injectSteps"][0]["systemMessage"]["systemMessage"]

        # 2. Test PreInvocation hook
        p_pre = subprocess.run(
            [py_exe, os.path.join(target_dir, "scripts", "pre_invocation.py")],
            cwd=target_dir,
            input=json.dumps(
                {
                    "conversationId": "install-check-2",
                    "lastUserInput": "记忆星图怎么查看程序记忆？",
                    "hookEventName": "PreInvocation",
                },
                ensure_ascii=False,
            ),
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            check=True,
        )
        json.loads(p_pre.stdout)

        # 3. Test MCP server (6 tools + local_memory_nebula + MCP App UI resource)
        reqs = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "local_memory_nebula", "arguments": {}}},
            {"jsonrpc": "2.0", "id": 4, "method": "resources/list", "params": {}},
            {"jsonrpc": "2.0", "id": 5, "method": "resources/read", "params": {"uri": "ui://local-memory/nebula.html"}},
        ]
        p_mcp = subprocess.run(
            [py_exe, os.path.join(target_dir, "scripts", "mcp_server.py")],
            input="\n".join(json.dumps(r, ensure_ascii=False) for r in reqs) + "\n",
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            check=True,
        )
        resps = [json.loads(line) for line in p_mcp.stdout.strip().splitlines()]
        assert len(resps[1]["result"]["tools"]) == 6
        neb = json.loads(resps[2]["result"]["content"][0]["text"])
        assert neb["memory_count"] == 3 and neb["edge_count"] == 2
        assert os.path.exists(neb["nebula_path"])
        resources = resps[3]["result"]["resources"]
        assert any(r["uri"] == "ui://local-memory/nebula.html" for r in resources)
        app_html = resps[4]["result"]["contents"][0]["text"]
        assert "__MCP_APP__" in app_html and "welcome_semantic" in app_html


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    parser = argparse.ArgumentParser(description="Install Antigravity Local Memory v2.2 plugin.")
    parser.add_argument(
        "--target",
        default=os.path.join(os.path.expanduser("~"), ".gemini", "config", "plugins", "local-memory"),
        help="Target plugin installation directory.",
    )
    args = parser.parse_args()

    _check_python()
    bundle_root = os.path.dirname(os.path.abspath(__file__))
    src_plugin = os.path.join(bundle_root, "plugin")
    if not os.path.isdir(src_plugin):
        raise SystemExit(f"[ERROR] 未找到插件源目录：{src_plugin}")

    target_dir = os.path.abspath(os.path.expanduser(args.target))
    py_exe = os.path.abspath(sys.executable)

    _print("====================================================")
    _print("  Antigravity Local Memory v2.2 一键安装程序")
    _print("====================================================")
    _print(f"检测到 Python：{py_exe} ({sys.version.split()[0]})")
    _print(f"目标安装目录：{target_dir}")

    os.makedirs(target_dir, exist_ok=True)
    # Copy plugin files while preserving any existing user data/ directory
    for entry in os.listdir(src_plugin):
        src_path = os.path.join(src_plugin, entry)
        dst_path = os.path.join(target_dir, entry)
        if os.path.isdir(src_path):
            if os.path.exists(dst_path):
                shutil.rmtree(dst_path)
            shutil.copytree(src_path, dst_path)
        else:
            shutil.copy2(src_path, dst_path)

    _write_configs(target_dir, py_exe)
    _print("[1/2] 插件文件与本机专属配置写入完成，正在运行自检...")
    _self_test(target_dir, py_exe)
    _print("[2/2] 自动化自检全部通过（钩子 + 6 个 MCP 工具 + MCP App 星图 + 3 颗入门星辰）！")
    _print("")
    _print("接下来只需 2 步即可开始体验：")
    _print("  1. 完全退出并重启一次 Antigravity 桌面端（或 IDE）。")
    _print("  2. 新开一个对话，直接对它说：")
    _print("       「打开我的记忆星图」")
    _print("     即可在 Antigravity 内置侧边栏或浏览器中漫游你的记忆星云图谱！")
    _print("  3. 看完入门示例后，想清空体验星辰只需说：")
    _print("       「清空这 3 条入门示例记忆」")
    _print("====================================================")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
