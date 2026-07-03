# ASFA scripts

## `asfa_mcp.py` — ASFA MCP server (stdio)

Exposes ASFA as MCP tools so Claude Desktop / Claude Code can drive it in
natural language. Talks to the `database` layer directly (no Flask/auth needed).

**SDK:** the official `mcp` package (stable 1.28.x — `mcp>=1.2,<2`, already in
`requirements.txt`). Uses the high-level `FastMCP` API over stdio.

### Tools
Read: `get_mission_status`, `get_gym_summary(days=7)`, `get_scout_pipeline`,
`get_scent_recommendation`, `get_body_comp_latest`,
`search_audit_log(query)`, `list_agents`.
Write (token-guarded): `log_gym_set(exercise, weight, reps, rpe, token)`,
`add_scout_job(title, company, url, token)`, `move_scout_job(job_id, stage, token)`.

### Auth
Write tools require a `token` argument matching `ASFA_MCP_TOKEN` (which defaults
to `APP_PASSWORD`). If neither is set in the server's environment, **write tools
are disabled** and only read tools work. Read tools are open (stdio is local).

### Run manually
```bash
python scripts/asfa_mcp.py        # serves over stdio
ASFA_DB_PATH=/path/to.db python scripts/asfa_mcp.py   # target a specific DB
```

### Claude Desktop
Already registered in `~/Library/Application Support/Claude/claude_desktop_config.json`
as the `asfa` server (read tools work immediately). To enable the write tools,
set the token in that entry's `env`:
```json
"asfa": {
  "command": ".../.venv/bin/python",
  "args": [".../scripts/asfa_mcp.py"],
  "env": { "ASFA_MCP_TOKEN": "<your APP_PASSWORD>" }
}
```
Then restart Claude Desktop. Leaving `ASFA_MCP_TOKEN` empty keeps writes off.
