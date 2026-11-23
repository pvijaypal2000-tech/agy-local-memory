# Local Cross-Session Cognitive Memory Rules (v2.2)

You have a pure-local cognitive graph memory engine (`local-memory` v2.2). All data stays strictly on the user's computer (`memory.db`, `memories.md`, and `nebula.html` inside `PLUGIN_DATA` or the plugin's `data` directory).

Follow these habits automatically across conversations so memory feels seamless, high-signal, and bloat-free:

1. **Choose the right cognitive layer (`memory_type`) when calling `local_memory_remember`**:
   - `semantic`: Stable personal profile, long-term preferences, and durable facts (e.g., domain, preferred tools, language).
   - `episodic`: Time-bound milestones, experiments, or session outcomes.
   - `procedural`: Reusable workflows, checklists, and lessons learned.
2. **Scope memories properly (`workspace`)**:
   - Use `workspace: "global"` for personal background, general preferences, and cross-project habits.
   - Pass the active project directory path or project name in `workspace` for project-specific details so separate projects never pollute each other.
3. **Keep `SessionStart` lean & use JIT recall**:
   - Set `pinned: true` ONLY for core profile/preference/rule items that belong in every new session (aim for <= 10 pinned items).
   - Add concise `tags` and `importance` (1–5) so `PreInvocation` JIT recall and `local_memory_recall` surface the right details on demand.
   - Use `links` or `local_memory_link` (`relates_to`, `depends_on`, `derived_from`, `part_of`) when a new memory builds on an existing one.
4. **Evolve instead of duplicating (`supersedes`)**:
   - Use a stable `key` for routine updates.
   - When a decision or method genuinely changes, pass `supersedes_id` or `supersedes_key` to `local_memory_remember` so the old fact is preserved in `superseded` history with a `supersedes` edge rather than causing contradictions.
   - When the user asks to forget something, use `local_memory_forget` (`mode: "archive"` for soft-archiving or `"delete"` for permanent removal).
5. **Recall & visualize on demand**:
   - `SessionStart` injects the lean Core Profile and `PreInvocation` automatically injects top turn-relevant JIT memories. Call `local_memory_recall` or `local_memory_list` when you need deeper history.
   - When the user asks to view or open their Star Nebula ("打开我的记忆星图"), call `local_memory_nebula()` so it opens in Antigravity's interactive MCP App view (pass `open_in_browser=true` when the user explicitly requests an external browser window).
6. **Privacy guardrail**:
   - Never store raw passwords, API keys, government ID numbers, phone numbers, patient identifiers, or sensitive confidential records in long-term memory; store only safe preferences, methods, and de-identified summaries.
