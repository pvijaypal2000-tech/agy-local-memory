---
name: memory-management
description: Manage, inspect, curate, link, and visualize the user's pure-local cross-session cognitive memory engine (v2.2 SQLite FTS5 + knowledge graph + interactive Star Nebula MCP App & offline HTML).
---

# Local Memory Management (v2.2)

Use the `local-memory` MCP tools (`local_memory_remember`, `local_memory_recall`, `local_memory_list`, `local_memory_forget`, `local_memory_link`, `local_memory_nebula`) to manage persistent user memory across conversations:

- **Star Nebula visualization**: Every write auto-updates `nebula.html` in the plugin data directory. Call `local_memory_nebula()` whenever the user asks to view/open their Star Nebula to render the interactive MCP App view inside Antigravity (or pass `open_in_browser=true` to launch the standalone offline `nebula.html` in the system browser).
- **Cognitive layers**: Classify entries as `semantic` (stable facts), `episodic` (dated milestones/events), or `procedural` (reusable workflows/checklists).
- **Workspace scoping**: Keep personal traits in `workspace: "global"` and project specifics under their workspace path so projects stay isolated.
- **Knowledge graph**: Connect related memories via `links` on `local_memory_remember` or `local_memory_link` (`relates_to`, `depends_on`, `derived_from`, `part_of`, `supersedes`, `contradicts`). Recall automatically expands 1-hop active neighbors.
- **Conflict evolution**: When a newer decision replaces an older one, pass `supersedes_id` or `supersedes_key` to keep provenance without contradicting active context.
- **Inspect & curate**: Call `local_memory_list` (supports `status: "active" | "archived" | "superseded" | "all"`) or inspect the auto-synced `memories.md` inside `PLUGIN_DATA` (or the plugin's `data/` directory).
- **Privacy**: Keep stored memories concise and free of secrets, passwords, API keys, or sensitive personal identifiers.
