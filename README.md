# Jargon Vault

**A tiny, fast, local-first web app for jotting down the jargon, acronyms, and cryptic codes you bump into at work — and slowly turning them into actual knowledge.**

[![Release](https://img.shields.io/github/v/release/diegochen-tw/jargon-vault?include_prereleases&sort=semver)](https://github.com/diegochen-tw/jargon-vault/releases)
[![CI](https://github.com/diegochen-tw/jargon-vault/actions/workflows/ci.yml/badge.svg)](https://github.com/diegochen-tw/jargon-vault/actions/workflows/ci.yml)
[![Container](https://img.shields.io/badge/ghcr.io-jargon--vault-2496ED?logo=docker&logoColor=white)](https://github.com/diegochen-tw/jargon-vault/pkgs/container/jargon-vault)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

> **Status: Beta (`0.9.x`).** The feature set is complete and I use it every day —
> what Beta means here is that the shape of things can still change while feedback
> comes in, so a `0.x` upgrade may ask something of you. `1.0.0` will be the first
> stable release; the bar it has to clear is written down in
> [CHANGELOG.md](CHANGELOG.md#versioning).

<!-- TODO: add a short demo GIF here (type-to-search → save a term → local AI fills in a definition). -->

## Why I built this

Every job has its own secret language. Part numbers that look like `cSSFI123`. Three-letter acronyms nobody ever stops to explain. Internal codenames, industry slang, that one error string you end up googling *every single time*. Someone drops it in a meeting, you nod along, and a week later it's gone from your head.

I wanted somewhere to dump these the second I heard them — one term, one note, ten seconds, done. Not a document. Not a wiki.

Notion's lovely, but it's built for big pages, not tiny facts. A wiki is even heavier — structure, hierarchy, ceremony — which is a lot of overhead just to remember "what does this acronym mean?" I wanted the opposite: the **smallest possible unit of knowledge**, saved with basically zero friction.

So the whole thing is built around one idea: **write it down now, organize it later.** Fire terms in as fast as you can type. Later on, once patterns start showing up, tag and group them into something tidy — at your own pace, whenever it actually makes sense. Works just as nicely for one person as it does for a small team: everyone keeps their own vault, and when you want to hand knowledge over you publish a frozen, login-free snapshot the other person can read — or import and take over.

And it all runs on your own machine. Your notes are plain Markdown files on your disk. The AI (if you want it) is a local model. Nothing ever leaves your computer.

## What's in the box

- **Search as you type** — start typing, results narrow instantly. It matches substrings too, so `SFI` finds `cSSFI123`.
- **Your data is just files** — every note is one Markdown file with a bit of YAML on top. Read them, edit them, back them up, throw them in git. No lock-in, ever.
- **Capture now, organize later** — save a term in seconds; add tags and groups whenever you feel like it.
- **Tags and tag groups, not rigid folders** — a note can have lots of tags; tags can live in groups. No forced hierarchy.
- **Custom fields, no code** — the built-in fields are just name / description / tags / attachments. Anything else (aliases, pronunciation, source…) comes from editable "field templates". Four ship built in — Jargon, Passage Decoder, Graph, Code snippet — and anything beyond those installs as a plugin. Zero programming either way.
- **Optional local AI** — let a model on your own machine draft a definition, suggest tags, or chew through a whole article and turn the unfamiliar terms into notes. Works with Ollama or any OpenAI-compatible server (LM Studio, llama.cpp, vLLM). Off by default, and nothing ever touches the cloud.
- **Semantic search** — find a term by what it means, not just by the characters in it. Uses a local embedding model, so it keeps the same privacy promise: point it at an address on your own machine and nothing leaves it.
- **Multi-user, isolated by default** — everyone gets their own notes, tags, templates, and settings; nobody can edit, or even see, anyone else's vault.
- **Public notebooks** — publish your whole vault, or just the tags/groups you pick, as a frozen snapshot at its own link. No account needed to read it, and it ships with a downloadable export so whoever you handed it to can import the lot and become the new maintainer. Content never changes behind your back — you press *Update* — and the whole feature is off until an admin turns it on. Tags, bookmarks, review progress and version history stay behind the login wall.
- **Public link for a single term** — hand someone a link to one note and they can open it without an account, which is what you actually want in the middle of explaining jargon. Off by default, expires by itself after 48 hours, revocable one at a time or all at once, and served with `noindex` so it never lands in a search engine.
- **Import / export** — JSON (keeps the full structure), CSV (for spreadsheet people), or ZIP (carries images and attachments along). Exporting is as supported as importing; leaving is not punished.
- **Spaced-repetition review** — a plain Leitner round of 5–50 cards (10 by default): see the term, recall it, flip, grade yourself. Deliberately no due counter, no streak, no guilt for skipping a week.
- **Duplicate detection and merge** — finds the same term written twice under a different spelling or alias and folds them into one. Conflicting values are reported back to you rather than silently picked; the absorbed note goes to the recycle bin.
- **Hard to lose a note** — a 30-day recycle bin, per-note version history, optimistic locking so two tabs can't silently overwrite each other, and whole-site backups on a schedule. All on by default.
- **12 UI languages**, light and dark themes, and an interface language that follows your account rather than the device you happen to be on.
- **Optional MCP server** — exposes search, create, update and export as standard tools, so an agent can drive the vault through the same API the app uses.
- **Plays nice on phones** — responsive layout, adjustable text size, and code notes that stay readable one-handed.

## Quick start

```bash
pip install -r requirements.txt
python main.py
```

Open <http://127.0.0.1:8787>, click **"還沒有帳號?註冊" (No account yet? Register)**, and sign up with any email — no environment variables, no terminal wrangling required. **The very first account ever created on a fresh install automatically becomes the admin**, whatever the email allow-list says, so there's no chicken-and-egg problem: nobody has to open PowerShell just to get past the door. You'll get a message on screen telling you that you're now the admin — keep that email and password safe, since it's the one with full control over the site. From there, head to **Settings → Admin** to decide how everyone else gets in: open registration to anyone, or add specific emails to the allow-list.

That first-account exception only applies once, when the user list is empty. Every registration after that (including a second account on the same install) is gated by the allow-list/registration-mode rules below, same as always — so if you *do* want to lock things down from the start, set `ALLOWED_EMAILS` before that first run:

```bash
# Optional: skip the "open to whoever registers first" window entirely by
# pre-listing who's allowed, right from the very first start.
ALLOWED_EMAILS=you@example.com,friend@example.com python main.py
```

It also inherits any leftover data from before the app went multi-user, if there's any lying around.

### Common settings (environment variables)

| Variable | Required | What it does |
|---|---|---|
| `ALLOWED_EMAILS` | No | Comma-separated allow-list of emails that may register. **Seeds** the allow-list on first start (managed afterward in Settings → Admin) and also stays live as a supplement / break-glass list. Not needed just to get the first (admin) account going — see [Quick start](#quick-start) — but useful if you want to restrict who can register from the very first run. |
| `ADMIN_EMAILS` | No | Comma-separated admin rescue **and transfer** list — see [Becoming an admin](#becoming-an-admin) below. Handy if you ever lock yourself out, or if you deployed before the admin feature existed. |
| `GLOSSARY_PORT` | No | Change the port (default `8787`). |
| `GLOSSARY_HOST` | No | Bind address (default `127.0.0.1`; Docker sets `0.0.0.0`). |
| `GLOSSARY_DATA_DIR` | No | Override the data folder (default `<repo>/data`). Handy for testing with throwaway data. |
| `SESSION_SECRET` | No | Key used to sign session cookies. Leave it unset and one gets generated and saved to `data/.session_secret`. |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | No | Only needed for Google sign-in. **Seeds** the OAuth config on first start (managed afterward in Settings → Admin). Grab them from the Google Cloud Console. |
| `PUBLIC_BASE_URL` | Depends | Required when you're behind a reverse proxy **and** using Google sign-in, e.g. `https://jargon.example.com` (no trailing `/`). More below. |

> **Note:** registration mode, the email allow-list, and Google OAuth all live in `data/site_settings.json` and are managed from **Settings → Admin**. The environment variables above only *seed* that file on the very first start; after that the admin UI is the source of truth. (One exception: `ALLOWED_EMAILS` stays a live supplement to the stored allow-list.)

```bash
GLOSSARY_PORT=8899 python main.py          # change port
GLOSSARY_DATA_DIR=/tmp/gv python main.py   # isolated data folder (for testing)
```

**About `PUBLIC_BASE_URL`:** behind a reverse proxy (Cloudflare Tunnel, nginx, whatever), the app receives a plain internal HTTP request, so it often guesses the wrong scheme/host when it builds Google's OAuth `redirect_uri`. That mismatch is what triggers the dreaded `redirect_uri_mismatch`. Set `PUBLIC_BASE_URL` to your real public URL and the app just uses it verbatim instead of guessing.

## Becoming an admin

Admins get **Settings → Admin**, where you control registration mode, the email allow-list, Google OAuth, and the user list (promote/demote/delete). How you *get* to be one depends on your situation:

- **Fresh install (running `python main.py`):** the very first account you register is automatically the admin. Nothing else to do.
- **Docker:** the seeded demo account (`demo@example.com` by default) is already an admin — sign in and head to Settings → Admin. See the [Docker section](#run-with-docker-fastest-way-to-try-it).
- **Already have an admin, want another one:** the existing admin just opens Settings → Admin → the user list and hits **Promote** next to anyone.

### "I deployed this before the admin feature existed and nobody is an admin"

This is the awkward one. The admin mechanism was added *after* some people already had Jargon Vault running — typically with everyone signing in via Google OAuth — so there's no admin account anywhere, and no way to reach the admin UI to make one. Chicken, meet egg.

That's exactly what `ADMIN_EMAILS` is for. Set it to the email of an **existing** account and restart:

```bash
# plain
ADMIN_EMAILS=you@example.com python main.py

# Docker: uncomment/set ADMIN_EMAILS in .env.docker, then
docker compose up -d
```

Two things happen:

1. **Rescue (instant):** anyone whose email is in `ADMIN_EMAILS` is treated as an admin right away, regardless of what's stored — so you can't get permanently locked out.
2. **Transfer (on startup):** the matching account is also **permanently written** into the store as an admin. So once you've restarted once, you can **delete `ADMIN_EMAILS` again** and that account stays an admin for good. No need to keep the env var hanging around.

So the whole "move my existing OAuth account to admin" dance is: add your email to `ADMIN_EMAILS` → restart once → (optionally) remove it. Done.

## Run with Docker (fastest way to try it)

### Pull the published image — no clone, no build

```bash
mkdir jargon-vault && cd jargon-vault
curl -O https://raw.githubusercontent.com/diegochen-tw/jargon-vault/main/docker-compose.ghcr.yml
curl -o .env.docker https://raw.githubusercontent.com/diegochen-tw/jargon-vault/main/.env.docker.example
docker compose -f docker-compose.ghcr.yml up -d
```

Multi-arch images (`linux/amd64` + `linux/arm64`) are published to
`ghcr.io/diegochen-tw/jargon-vault` on every release. For anything beyond trying it out,
pin a version instead of riding `latest`:

```bash
GV_VERSION=0.9.0 docker compose -f docker-compose.ghcr.yml up -d
```

### Or build from source

Already cloned the repo? Up in seconds, with sample data to poke at:

```bash
cp .env.docker.example .env.docker   # required — Compose reads it for settings
docker compose up -d --build
```

Either way: open <http://localhost:8787> and sign in with the demo account:

- **Email:** `demo@example.com`
- **Password:** `demo1234`

You'll land on a small pile of sample notes (concepts, code snippets, a decoded passage, an annotated figure) with tag groups already set up — enough to get the vibe. Any account you register yourself gets the same sample notes seeded into its own vault, with a bar at the top of the page to delete them in one click; set `GLOSSARY_SEED_DEMO=0` if you'd rather new accounts start empty. The demo account is an **admin**, so you can also poke around **Settings → Admin** to control registration, the allow-list, Google OAuth, and users.

### From "just trying it" to "actually using it"

You keep the **same demo account** — just wipe the sample data and start clean. One command nukes the whole data volume and rebuilds:

```bash
# Windows (PowerShell)
scripts/reset.ps1

# Linux / macOS
sh scripts/reset.sh
```

Want a **blank** instance instead of the sample notes? Copy `.env.docker.example` to `.env.docker`, set `DEMO_SEED=blank` (and, honestly, change `DEMO_PASSWORD` while you're at it), then run the reset script. The demo login still works — it's just empty and ready for your own terms.

`DEMO_SEED` decides what a fresh start seeds: `sample` (demo account + sample notes, the default), `blank` (demo account only, no notes), or `off` (seed nothing — register your own account via `ALLOWED_EMAILS`). All the Docker settings live in `.env.docker` — copy it from `.env.docker.example` before your first `up` (Compose insists the file exists).

All your data lives in the `gv-data` Docker volume, so it survives `docker compose down` / `up`. Only the reset script (which runs `docker compose down -v`) wipes it.

## Deploy on a Synology NAS (Container Manager)

Same image, same compose syntax — the compose files already use the short volume/`env_file` syntax so they play nicely with Synology's older Compose version. Needs **DSM 7.2+** with the **Container Manager** package installed (Package Center → search "Container Manager").

**Shortcut:** a NAS is exactly where you don't want to build a container image (it's a slow CPU and, on ARM models, an emulated build). Put just **two files** in the folder — `docker-compose.ghcr.yml` (renamed to `docker-compose.yml` so Container Manager picks it up) and `.env.docker` — and it pulls the published image instead. That skips the clone and the build entirely; the rest of the steps below are unchanged.

**1. Get the project files onto the NAS.** Easiest with SSH (Control Panel → Terminal & SNMP → enable SSH service):

```bash
ssh your-user@your-nas-ip
cd /volume1/docker            # or wherever you keep this kind of thing
git clone https://github.com/diegochen-tw/jargon-vault.git jargon-vault
cd jargon-vault
cp .env.docker.example .env.docker
vi .env.docker                 # fill in ALLOWED_EMAILS at minimum
```

No SSH? Download the **source zip from the [latest release](https://github.com/diegochen-tw/jargon-vault/releases/latest)**, upload it with **File Station** into a shared folder (e.g. `docker/jargon-vault`), extract it there, then duplicate `.env.docker.example` as `.env.docker` and edit it with File Station's built-in text editor. (Use the release zip rather than zipping a working copy from your own machine — a working copy can contain your `.env` files, local run scripts, and the whole `data/` directory.)

**2. Create the project in Container Manager:**

- **Container Manager → Project → Create**
- **Project name:** `jargon-vault`
- **Path:** the folder from step 1 (the one containing `docker-compose.yml` *and* `.env.docker`)
- Container Manager auto-detects `docker-compose.yml` — leave it on "Create docker-compose.yml" pointed at the existing file, hit **Next**, then **Done** to build and start it

**3. Open it up.** `http://<nas-ip>:8787` — sign in the same way as any other deployment (register the first account via `ALLOWED_EMAILS`, or sign in with Google if you set up OAuth). All data lands in the `gv-data` volume, visible under **Container Manager → Volume**.

**Changing env vars later** (e.g. the `ADMIN_EMAILS` transfer trick from [Becoming an admin](#becoming-an-admin) above): edit `.env.docker` again (SSH or File Station), then in Container Manager select the project and **Stop** it, then **Start** it again — a plain restart reuses the old environment, but stop-then-start recreates the container and actually picks up the new file.

**Port already taken?** DSM itself doesn't usually sit on 8787, but if something else on your NAS does, add `HOST_PORT=9000` (or whatever) to `.env.docker` before creating the project.

## Where your data lives

One folder per user, under `data/users/<user id>/`:

- `notes/*.md` — one file per note. Edit them in any editor, put them in git, whatever you like.
- `notes/assets/<id>/` — images pasted into a note's description, plus attachments.
- `tags.json` / `templates.json` / `plugins.json` — your tags, field templates, and installed plugins. These are the real source of truth and are **not** committed to git (they're private). (AI connection settings are *not* here: they are site-wide and live in `data/site_settings.json`, managed by the site administrator.)
- `progress.json` — your bookmarks and review progress. Despite the name this is a **source of truth**, not a cache: it is no longer written into the `.md` files, so deleting it loses the data for good.
- `index.db` — the search index. Delete it whenever; it's rebuilt on the next startup.
- `vectors.db` — semantic-search embeddings. Expensive to rebuild but never load-bearing; the app never touches it on startup.

## How it works (the short version)

- **No folders, just tag groups.** The old "category" idea is gone. A note has tags; tags can belong to a group. In the sidebar, clicking a group is an OR filter (any tag in that group matches); clicking tags is an AND filter (must match all of them).
- **Fields come from templates.** The only built-in fields are name / description / tags / attachments. Everything else is defined by editable field templates, so you can add a one-line field (like "source") from the UI without touching code.
- **User isolation by default, sharing by explicit opt-in.** Every user's notes, tags, templates, and search index live in their own `data/users/<id>/` folder, and nothing is ever read across that line — with exactly two registered exceptions, both opt-in and both revocable: a public link is a ticket valid for exactly one note, and a public notebook is a *frozen copy* computed at publish time into `data/published/`, never a live view into anyone's folder. (AI connection settings are site-wide, not per user.)

## Tests

```bash
pip install -r requirements-dev.txt
pytest                                        # run everything
pytest --cov=app --cov-report=term-missing    # with coverage
```

Running everything takes well over an hour on a laptop, so grab a coffee or run a subset (`pytest -k search`). CI splits the suite into four parallel shards, plus a fast job that checks the 12 UI translations and the version metadata up front.

## Tech

FastAPI backend + vanilla JavaScript frontend — no build step, no framework, no ORM. Files are the source of truth; SQLite FTS5 (trigram) is a throwaway search index that's rebuilt on startup.

## Docs

- [docs/essential.md](docs/essential.md) — everything you can do, no code required (for users).
- [mcp_server/README.md](mcp_server/README.md) — optional MCP server, so tools like Claude Code can drive Jargon Vault for you.
- [CHANGELOG.md](CHANGELOG.md) — what changed in each release, plus the versioning rules.
- The code itself. There is no build step and no framework, so `app/` and `static/js/` read top to bottom; each module's header comment says what it may and may not do.

A handful of those header comments point at `CLAUDE.md`, a working document I keep
outside this repo. You are not missing a rule you need: the comment always states the
constraint itself, and the file it names only records why I landed there. Nothing in
`app/` reads it at runtime.

## Security

Found a vulnerability? **Please don't open a public issue** — that's an exploit notice for every instance that hasn't updated yet. Email <diego.taoyuan@gmail.com> or use GitHub's private security advisories. See [SECURITY.md](SECURITY.md) for scope, supported versions, and response times.

Not sure which version you're on? **Settings → About** shows it.

## License

[MIT](LICENSE) — do whatever you like: use it, fork it, self-host it, ship it inside a company. Just keep the copyright notice around. PRs welcome.
