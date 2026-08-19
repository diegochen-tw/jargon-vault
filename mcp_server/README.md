# Jargon Vault MCP Server

Lets [MCP](https://modelcontextprotocol.io/) clients like Claude Code / Claude Desktop drive a *running* Jargon Vault through standard tool calls — create/search/update/delete notes, manage tags and groups, field templates, plugins, AI generation, and import/export.

This folder is a **standalone helper**, not part of the main Jargon Vault app (`app/`). It only calls Jargon Vault's existing `/api/*` endpoints over HTTP (the same thing the frontend's `static/js/api.js` does); it never touches the filesystem or SQLite directly, so the app's "files are the source of truth" and "write file, then update index" guarantees are unaffected. **Jargon Vault must already be running** before you use it (`python main.py`).

## Install

```powershell
cd mcp_server
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Set up login

Copy `.env.example` to `.env` and fill in an account you registered in Jargon Vault:

```
JARGON_BASE_URL=http://127.0.0.1:8787
JARGON_EMAIL=you@example.com
JARGON_PASSWORD=your-password
```

Instead of email + password, you can also sign in through the browser and copy the `gv_session` cookie value into `JARGON_SESSION_COOKIE` (DevTools → Application/Storage → Cookies). `.env` is already in `.gitignore` and won't be committed.

> These variables are read from the **process that starts the MCP server** (they are not auto-loaded from the `.env` file). So when registering with Claude Code below, pass them via `--env` or the `env` field in `.mcp.json`, or set them yourself before starting (e.g. `$env:JARGON_EMAIL=...`).

## Test on its own

```powershell
mcp dev server.py
```

This opens the MCP Inspector web page, where you can call each tool one by one to confirm it can reach Jargon Vault.

## Register with Claude Code

The repo root already has a [`.mcp.json`](../.mcp.json) (project-scope config, shared by the team, safe to commit). It uses a relative path to this folder's venv and the `${JARGON_EMAIL}` / `${JARGON_PASSWORD}` env-var expansion syntax — **the credentials are not written into this file**. Instead, set them in the shell/system environment that starts Claude Code, for example:

```powershell
# This sets the variables for the environment that launches VS Code / Claude Code,
# which is a different thing from mcp_server/.env (only read by `mcp dev`).
[Environment]::SetEnvironmentVariable("JARGON_EMAIL", "you@example.com", "User")
[Environment]::SetEnvironmentVariable("JARGON_PASSWORD", "your-password", "User")
```

Restart VS Code (so the new env vars take effect). Claude Code detects the `.mcp.json` at the repo root, asks for approval the first time, and after you approve you can ask it to operate Jargon Vault in the conversation (e.g. "after analyzing this code, save the terms it uses as Jargon Vault notes").

If you'd rather not use system env vars, you can replace `${JARGON_EMAIL}` / `${JARGON_PASSWORD}` in `.mcp.json` with literal string values — but then that file contains secrets, so add it to `.gitignore` before filling it in, and never commit a plaintext password.

You can also register a user-level (not project-specific) MCP server via the CLI:

```powershell
claude mcp add jargon-vault --env JARGON_EMAIL=you@example.com --env JARGON_PASSWORD=your-password -- C:/path/to/jargon-vault/mcp_server/.venv/Scripts/python.exe C:/path/to/jargon-vault/mcp_server/server.py
```

## Tools it provides

| Category | Tools |
|---|---|
| Account | `whoami` |
| Notes | `search_notes`, `get_note`, `create_note`, `create_notes_bulk`, `update_note`, `delete_note`, `restore_note_version`, `delete_note_assets`, `upload_attachment`, `upload_image` |
| Tags / groups | `list_tags`, `rename_tag`, `delete_tag`, `set_tag_group`, `rename_tag_group`, `dissolve_tag_group`, `dissolve_all_tag_groups` |
| Field templates | `list_templates`, `create_template`, `update_template`, `delete_template` |
| Import / export | `export_notes`, `import_notes` |
| Plugins | `list_plugins`, `install_plugin`, `uninstall_plugin`, `update_plugin_config` |
| AI (local Ollama) | `get_ai_settings`, `update_ai_settings` (site admin only), `ai_generate` |

To bulk-insert analysis results, prefer `create_notes_bulk` (one call, written one at a time, a single failure doesn't affect the others). Use `create_note` / `upload_attachment` etc. for a single note or when you need fine control (custom id, attachments).

## Format of the `description` field

A note's `description` is Jargon Vault's own minimal markdown (not standard markdown). It supports:

- `**bold**`
- `` `inline code` ``
- triple-backtick fenced code blocks (with an optional language, e.g. ` ```python `)
- `![alt](image-url)`
- `{{color:text}}` highlighting
- plain `\n` line breaks between paragraphs

## Known limits

- Google OAuth sign-in is not covered (it needs browser interaction; the MCP server supports only email/password or an existing session cookie).
- `ai_generate` needs the Jargon Vault machine to have AI enabled and a local Ollama running, or it returns an error.
- It operates as a single user (the one you signed in as) and can't reach into other users' data — an extension of Jargon Vault's own "full user isolation" design.
