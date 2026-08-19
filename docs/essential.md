# Jargon Vault — Quick Reference

A short guide for users. You don't need to read any code to use the app.

## Signing in

This is a local, multi-user, allow-list app — not an open public sign-up:

- Only emails on the server's allow-list can register, or sign in with Google for the first time. **Exception:** if you're the very first person to register on a fresh install, you get in regardless of the allow-list, and you automatically become the admin — you'll see a message saying so. Keep that email and password safe, since you'll have full control over the site (registration rules, the allow-list, Google sign-in, other users) from **Settings → Admin**.
- You can use email + password, or (if the server is set up for it) Google sign-in.
- There is no "forgot password" and no confirmation email — remember your password.
- Each user's notes, tags, field templates, and AI settings are separate by default. You can't see anyone else's data unless they choose to share it — see [Sharing](#sharing) below.

## What a note contains

The core fields are fixed; the rest come from a "field template":

| Field | Meaning |
|---|---|
| Name | The title. Required. (Leave it blank and it auto-names to `Untitle(n)` and gets a "no name" tag; add a real name later and the tag is removed automatically.) |
| Description | The full body. What-you-see-is-what-you-get editing (bold, inline code, code blocks, pasted images, highlighting). |
| Tags | Attributes you can pick freely, and later group. |
| Attachments | Any file type. Listed in the editor and in the detail window; click to open. |
| Template fields | Depend on the field template you picked — see the next section. |

## Field templates: add fields without code

Besides the four core fields (name / description / tags / attachments), any other one-line text field (like "alias" or "synonym") is defined by a **field template**. When you create a note, you pick a template first, and the editor shows that template's fields.

Three templates are built in (you can't delete them, but you can edit their fields and AI instructions):

- **Jargon (Default)** — alias, synonym, and "polysemy" (same word, different meanings). For general term notes.
- **English word** — translation and pronunciation.
- **Code snippet** — a set of fields for code snippets.

Want your own field (say, a "source" field)? Go to **Settings → Field templates**, add or edit a template, and add a field definition (key / label / placeholder). No code needed. Deleting a custom template does **not** hurt notes that already use it — their data is kept, and the app just rebuilds the display from the fields the note already has.

## Tags vs tag groups

The old "category" idea is gone; it's replaced by **tag groups**:

- A note can have many tags.
- A tag can optionally sit under a **group** (for example, put `SFC` and `work order` under a "Manufacturing" group). A tag belongs to one group at most (or no group).
- The left sidebar is a two-level tree, group → tag:
  - **Click a group** = OR filter (any note with any tag in that group shows up).
  - **Click tags** = AND filter (pick several tags; results must match all of them).
- You manage tags and their groups in **Settings → Tags**.

## Keyboard shortcuts

| Key | What it does |
|---|---|
| `/` | Jump to the search box (when the cursor isn't already in a text field). |
| type | Search live, after a short pause. |
| `Enter` (search box, no results) | Turn what you typed into a new note. |
| `Ctrl+Enter` (in the editor) | Save. |
| `Esc` (in the editor) | Cancel editing (any images/attachments uploaded but not yet saved are cleaned up). |
| `Esc` (search box) | Clear the search. |
| `Esc` (detail window / settings / an open dropdown) | Close the current layer. |

The rule for `Esc` is simple: wherever you are, it cancels or closes the current layer — you never have to hunt for a "cancel" button or an X.

## Cards, and the edit/delete toolbar

Every card in the list is read-only by default — clicking it opens the detail window (full description, attachment list); it does not jump straight into editing. To edit or delete:

- On desktop, hover over a card and a small toolbar appears below it (like Google Keep).
- On touch devices, the toolbar shows directly under the card content — no hover needed.

The toolbar has **Edit** (open the editor) and **Delete** (removes it, with a confirmation dialog first).

Keyboard: `Tab` into a card, `Enter` to open the detail window; `Tab` onto a toolbar button also reveals the toolbar, so you can trigger edit/delete from the keyboard.

## How filters stack

Search text, tags, tag groups, and the field-template filter (the "Categories" section in the sidebar) all apply at the same time, combined with **AND**. For example: a keyword + two tags + one group node returns notes that match all of it (within a group it's OR — having any tag in that group counts as matching).

## The description field: what you see is what you get

The editor's Description field shows the formatted result directly, not a mess of raw symbols. The supported syntax is deliberately minimal:

- **Bold** and `inline code` — use the toolbar buttons, or just type `**text**` and `` `text` ``.
- Fenced code blocks (start with ```` ```lang ````, end with ``` ``` ```) — indentation is kept, colored by language, with a copy button in the detail window.
- Paste an image (like a screenshot) with `Ctrl+V` — it uploads and shows up in place.
- Highlighting (`{{color:text}}`, pick a color) — mark important bits.
- Line breaks.

Text pasted from elsewhere (a web page, Word) is always treated as plain text and normalized first (consistent line breaks, extra blank lines removed) — it will not bring in colors, fonts, or other formatting.

To keep the list scrolling fast, **a long description on a card is cut off**. Click the card to open the detail window and read the full version (with images and attachments); close it with `Esc` or by clicking outside.

## Attachments

Besides pasting images into the description, you can also upload any type of file in the editor (not just images). Attachments are listed in the editor (you can remove them), and after saving they appear at the bottom of the detail window — click to open or download in a new tab.

## Sharing

Two separate features, both off until an admin turns them on in **Settings → Admin**.

**The shared library** — for a team on one box. In **Settings → Shared library** you tick which of *your* tags to publish. Notes carrying those tags then show up in everyone else's search results in a separate "From the shared library" section, **read-only**: nobody can edit, delete or merge your notes, and your files never move. Untick a tag and it disappears from the shared library immediately. Your own notes never appear in your own shared-library section (they're already in your normal results). An admin can force a tag back down; that only hides it — your publishing settings are left exactly as you set them, and everything comes back if they undo it.

Two deliberate limits: the shared library only takes a **keyword** (your sidebar's tags and groups are *yours*, and someone else's "MES" isn't necessarily your "MES"), and it shows a single page — if there are more hits it says so and asks you to narrow the search rather than pretending to paginate across everyone's vaults.

**A public link for one note** — click 🔗 in the detail window's toolbar to mint a link that anyone can open **without an account**, which is what you usually want mid-conversation while explaining a piece of jargon. The page shows the name, description, template fields and image attachments — not your tags, and not the note's edit history. Regenerating or revoking kills the old link instantly, and the page is served with `noindex` so it never ends up in a search engine. Delete the note and the link stops working; restore it from the trash and the link works again.

## Semantic search: find it by meaning

Keyword search only matches literal text. Semantic search covers the other case: you remember there's *a term about mistake-proofing when loading material* but not what it's called, and the note itself never uses those words.

To turn it on:

1. **Settings → AI generation** — set an **embedding model** (e.g. `nomic-embed-text` or `bge-m3`) and save. Leave it blank and semantic search stays off.
2. **Settings → Semantic search** — press **Build / update index**. Progress is shown as it goes; you can close the window and come back.
3. Type a query, then press the **Semantic** toggle in the stats row.

Results are a blend of the keyword hits and the closest matches by meaning, so an exact code like `cSSFI123` still comes up first even if nothing is semantically near it.

Three things worth knowing:

- **The index does not update itself.** Adding or editing an entry never waits on the model — saving must not get slower or fail just because the model service is down. So the settings page tells you how many entries are pending, and you press the button when it suits you.
- **Change the embedding model and the whole index is rebuilt.** Vectors from two different models aren't comparable, so mixing them would quietly produce nonsense ranking rather than an error.
- **There is no second page**, and semantic search does not reach the shared library. Both are deliberate — see the developer guide.

## AI generation and the model service

You need a local (or LAN) model service, enabled in **Settings → AI generation** along with the address and model name. Two API formats are supported:

- **Ollama native** — [Ollama](https://ollama.com/), the default. Slightly faster, because this mode can switch off a reasoning model's thinking (which this app throws away anyway).
- **OpenAI-compatible** — LM Studio, llama.cpp's `llama-server`, vLLM, and Ollama's own `/v1` layer. The address has to include `/v1` (e.g. `http://127.0.0.1:1234/v1`). An API key field is there if your service wants one; local services usually don't.

Either way nothing leaves the address you typed. Then you can use:

- **Generate a whole note from a template** — the editor shows a different input depending on the template's input mode: most templates (like Jargon, English word) put a 🤖 icon next to the name field, and you just enter the name; the "Code snippet" template gives you a box to paste content into. Generation follows that template's own AI instructions (editable in **Settings → Field templates**) and produces the name, description, template fields, and suggested tags.
- **Suggest tags** — the 🏷️ icon next to the tags field looks at what you've filled in so far and suggests up to three keyword tags. Works when creating or editing; results are merged with your existing tags (no duplicates).
- **Rewrite one field** — for the name, description, or any template field, ask the AI to write a better version based on everything currently in the note (not just that field's old value).

## Plugin: Article → Keywords

After you install "Article → Keywords" in **Settings → Plugins**, the template dropdown for a new note gains an extra entry:

1. Paste in a whole article.
2. In the preview, select the terms you don't understand and add them to a to-do list.
3. The AI runs once per term, writes an explanation based on the article's context, shows progress, and saves each one as a "Jargon (Default)" note (any tags you picked apply to every one).

You can customize this plugin's AI instructions in **Settings → Plugins**. Uninstalling keeps your custom instructions, so reinstalling restores them.

## The settings window

Click **Settings** in the top-right corner. The left menu has these tabs:

### Import / Export

- **Export JSON** — keeps the full structure (tags, template fields, attachment metadata). Good for backups or for reading with a program.
- **Export CSV** — flattened into a table (tags joined into one column with `;`, template fields flattened into columns, no attachments). Good for Excel / Google Sheets.
- You can export everything, or only selected tags / tag groups.
- **Import…** — load a `.json` or `.csv` you exported earlier. A note whose `id` already exists is overwritten; otherwise it's created as new. Good for restoring a backup or moving data to another machine. Importing only restores attachment info (like the filename), **not** the file itself (move the whole `data/` folder for that).

### Tags

Lists every tag with its note count and group. You can:

- **Rename** — rename a tag everywhere, across all notes that use it.
- **Delete** — remove a tag from all notes (the tag disappears from the list too). Can't be undone.
- **Group / ungroup** — put selected tags into a group, or take them out.

### Field templates

Add / edit / delete field templates and set each one's fields and AI instructions (see "Field templates" and "AI generation" above). Built-in templates can't be deleted.

### AI generation

Turn AI on or off, pick the API format (Ollama native or OpenAI-compatible), and set the service address, optional API key, generation model, and embedding model.

### Semantic search

Index status (built / total / pending), **Build / update index**, and **Clear index**. See "Semantic search" above.

### Plugins

Install / uninstall plugins and adjust their settings (the only plugin right now is "Article → Keywords").

### Shared library

Tick which of your tags to publish to the shared library, and optionally set the name other people see next to your notes (leave it blank and they see the account part of your email, never the full address). If an admin hasn't enabled the shared library, this tab just says so. See [Sharing](#sharing) above.

### Admin (admins only)

Registration mode, the email allow-list, Google sign-in, the user list — plus the two sharing switches (shared library and public links, both off by default) and **Force takedown**, which pulls one person's tag out of the shared library without deleting or changing any of their data.

Export always reflects the *current* data in the selected scope — it is not affected by whatever search or filter you have on screen.
