# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

The version number lives in `app/config.py` (`APP_VERSION`) and is shown in
**Settings → About**, so a bug report can always say which version it came from.

## Versioning

**`0.x.y` is Beta. `1.0.0` will be the first stable release.**

- **Beta (`0.x.y`)** — the feature set is complete and in daily use, but the
  shape of things can still change while feedback comes in. New features and
  breaking changes bump the **minor** (`0.9` → `0.10`); bug fixes bump the
  **patch**. This is exactly what SemVer already says about `0.x`, so there is
  no house rule to learn.
- **Every `0.` tag is published as a GitHub pre-release**, enforced in
  `.github/workflows/release.yml` rather than left to whoever cuts the release.
- **`1.0.0` ships when** the on-disk formats are frozen (note frontmatter and
  the v3 export), no entry below carries a *Known issues* section, and the
  response times promised in `SECURITY.md` are ones I can actually keep.
- Versions before `0.9.0` were never published anywhere; they are listed below
  for continuity, not because a `v0.7.0` tag exists to download.

## [Unreleased]

## [0.9.0] - 2026-08-19

Team vaults are gone; public notebooks take their place. The whole
`/api/teams/*` surface and the `?team=` query parameter are removed — anything
built against them breaks. Knowledge hand-over now works by **publishing a
frozen snapshot** instead of co-owning a live vault: the snapshot lives outside
any user directory, survives account deletion, and anyone can import it and
become the new maintainer.

### Added

- **Public notebooks** (`/p/<id>`). Publish your whole vault — or a tag/group
  scope — as a frozen, login-free snapshot: a read-only page listing every
  entry, plus a downloadable v3 export that imports straight into any Jargon
  Vault (`POST /api/import` unchanged). Content never updates behind your back;
  press *Update content* to overwrite the snapshot, take it down anytime.
  Site-wide switch (off by default) under **Admin → Site features**; turning it
  off kills every existing snapshot immediately (files kept, re-enable to
  restore). Published exports are sanitized: tags, bookmarks, review progress,
  version history and template AI prompts never leave the login wall
  (`tests/test_publish_api.py` guards this). Snapshots are included in site
  backups and deliberately excluded from personal exports.
- **Site registration invites.** Invite links are no longer tied to a team:
  a site admin generates a link under **Admin → Who gets in**; holding it lets
  someone register past the whitelist (single-use, 7 days, revocable — same
  defenses as before). The registry moved to `data/invites.json`.

### Changed

- **Version numbers now say what they mean.** This is the first version to be
  published anywhere, and it is Beta, so it is numbered `0.9.0` rather than
  continuing an internal `1.x` line that nobody could download. See the
  Versioning section above for the whole rule. **Settings → About** appends a
  localized *Beta* marker whenever the version starts with `0.`, derived from
  the version string itself rather than from a second flag that could drift.

### Removed

- **Team vaults** (slices 2–4 of the 2026-08 team-vault plan): shared vaults,
  roles, member management, team invites, cross-vault SRS drawing, admin team
  rescue, and the `?team=` parameter on every content endpoint. Evidence-based
  removal — the feature saw zero real use over its whole life. Existing
  `data/teams/` directories are no longer read: export them with a pre-0.9
  version first (team export → personal import), or delete the directory.
  Optimistic locking, atomic writes and the personal-progress store
  (slices 0–1) remain — they stand on their own.

## [0.8.0] - 2026-08-18

### Added

- **Sample data on first run.** Registering a new account now seeds a small set of
  example entries, tags and tag groups into that account's own vault, so a fresh
  install is no longer a blank page. A sticky bar at the top of the page offers to
  delete them in one click and links to the official demo site; it stays until you
  press it. The delete is deliberately narrow — it removes only the entries that
  shipped with the install, never anything you wrote yourself (`DELETE /api/demo`,
  guarded by `tests/test_demo.py`). Set `GLOSSARY_SEED_DEMO=0` to turn seeding off.
  The demo bundle used to be Docker-only; it is now packaged into the desktop build
  as well.

### Changed

- **Built-in field templates are now four**, chosen to be distinct at a glance:
  Jargon (one term), Passage Decoder (one passage), Graph (one figure), Code snippet
  (one snippet). Everything else installs as a plugin.
  - `passage-decoder` was promoted from official plugin to built-in — the only
    plugin-to-built-in move in the project's history. It loses the plugin manifest's
    long-form `intro` page, since built-in templates have no place to carry one.
  - `english-word` and `plant-id` were demoted to official plugins. Existing accounts
    keep their template exactly as it is, including any renaming or field edits; it
    simply becomes editable and uninstallable. No note data is touched.
  - `photo-technique` and `process-sop` packages were retired entirely. Accounts that
    had them keep their copy as an ordinary custom template; it can no longer be
    reinstalled from the catalog.
  - The English display name of the default template changed from "Glossary" to
    "Jargon". Only the display string changed — the seed name stays "Glossary" because
    it is the anchor that detects whether a user renamed the template.

### Fixed

- **Tag similarity check is now actually localized.** Its 21 strings existed in all
  12 language blocks but 10 of them held a verbatim copy of the Simplified Chinese
  text — including `en`, so the fallback chain could never rescue it. Every language
  is now translated. Two backend errors reachable from that screen (`AI generation
  not enabled`, `no tags to analyze`) returned Chinese text that the frontend alerted
  raw, overriding the localized message; they now return machine codes that the
  frontend maps to translated strings, and model errors append their technical detail
  instead of replacing the message.

### Known issues

- A few tag-manager endpoints outside the similarity screen (`app/tags.py`,
  `app/routers/taxonomy.py`) still return Chinese `detail` strings.

## [0.7.0] - 2026-08-04

The first version with the complete feature set, used daily but never published.
This entry describes the feature set as of 0.7.0 rather than replaying the commit log.

### Added

- **Notes are files.** One Markdown file per term (YAML frontmatter + body) under
  `data/users/<id>/notes/`. Open them in any editor, sync them however you like.
  SQLite FTS5 is a throwaway search index, rebuilt from scratch on every start —
  delete it any time and it comes back.
- **Search as you type**, with substring matching and filters for tags, tag groups,
  templates, and dates. Every filter, sort, and page is pushed down to SQL; results
  page in 50 at a time from a "load more" cursor.
- **Tags and tag groups** instead of folders. The sidebar tree is groups → tags;
  clicking a group is an OR filter, clicking tags is an AND filter. Each tag keeps
  the timestamp of the first time you used it.
- **Field templates.** The only built-in fields are name / description / tags /
  attachments — everything else (alias, synonymy, English term, domain, source…) is
  a user-editable one-line field, no code required. Ships with five templates:
  jargon, English word, code snippet, SOP, and plant ID. Individual fields can be
  switched off without losing the values already stored in them.
- **`[[wiki links]]` and backlinks.** Links resolve by name, not by id, so you can
  link to a term you have not written yet and it connects itself once you do.
- **Duplicate detection and merge** — exact, normalized, and alias-level matching.
  Merging never silently drops a field value: conflicts are reported back to you,
  and the merged-away note goes to the trash, not oblivion.
- **Trash** with a 30-day retention window, plus per-note version history.
- **Spaced-repetition review.** A fixed 20-card round, Leitner boxes, and
  deliberately no due counter, no streak, and no accumulating debt — this is for
  jargon you met at work, not an exam.
- **Optional local AI.** Draft a definition, suggest tags, or turn a whole article
  into notes. Works with Ollama or any OpenAI-compatible server (LM Studio,
  llama.cpp, vLLM). Off by default, nothing leaves your machine, and the write path
  never calls a model — saving a note can never be slowed down or broken by it.
- **Semantic search.** Local embeddings in a per-user vector store, fused with
  keyword results by Reciprocal Rank Fusion. Filters are applied *before* vector
  comparison, never after. Index updates are triggered by you, not by every save.
- **Multi-user with full data isolation.** Email + password or Google sign-in,
  registration modes (open / allow-list / closed), and an admin console for site
  settings and users.
- **Login protection**, on by default: independent per-IP and per-email
  sliding-window lockout, with an admin "unlock everyone" rescue button.
- **Opt-in shared library.** Publish selected tags and other signed-in users get a
  read-only, cross-user view of just those notes. Your files never move or get
  copied. Admins can force a takedown without touching the owner's own settings.
- **Public share links** — one revocable, `noindex` link per note, readable without
  an account. Site-wide off by default.
- **Whole-site backup and restore** (admin only), automatic every 7 days, keeping
  the last 10 archives, with zip-slip protection and a pre-restore snapshot.
- **Import / export** as JSON, CSV, or ZIP (with image and attachment files), with
  selective export by tag or group. Field templates and tag groups travel with the
  export, since neither can be rebuilt from the notes alone.
- **Plugins** — installable extras; ships with "article → keywords".
- **12 UI languages**, light and dark themes, adjustable text size, an image
  lightbox, a three-colour highlighter, code blocks with syntax highlighting, and
  client-side image compression (with EXIF/GPS stripping) before upload.
- **Optional MCP server**, so agent tools can read and write the vault for you.
- **Docker images** for `linux/amd64` and `linux/arm64` on
  `ghcr.io/diegochen-tw/jargon-vault`, plus a documented Synology NAS deployment.

[Unreleased]: https://github.com/diegochen-tw/jargon-vault/compare/v0.9.0...HEAD
[0.9.0]: https://github.com/diegochen-tw/jargon-vault/releases/tag/v0.9.0
