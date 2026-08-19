# Security Policy

Jargon Vault is a self-hosted, single-process app maintained by one person in his
spare time. Security reports are taken seriously anyway — please read the scope
below so we both spend our time on the things that actually matter.

## Reporting a vulnerability

**Please do not open a public GitHub issue for a security problem.** A public issue
is a working exploit notice for every unpatched instance out there.

Use either of these instead:

- **Email:** <diego.taoyuan@gmail.com> — put `[jargon-vault security]` in the subject.
- **GitHub private security advisory:** repository → **Security** → **Report a vulnerability**.

Helpful to include: the affected version (**Settings → About** shows it, and
`GET /api/auth/me` returns it), how you deployed it (bare `python main.py`, Docker,
behind a reverse proxy), and the smallest reproduction you have. Proof-of-concept
code is welcome; please do not test against instances you do not own.

## Response times

Best-effort, from one maintainer probably in a different timezone than you:

| Stage | Target |
|---|---|
| Acknowledgement of your report | within 5 business days |
| Initial assessment (accepted / not a vuln / need more info) | within 10 business days |
| Fix released for accepted high-severity issues | within 30 days |

You will be credited in the release notes unless you ask not to be.

## Supported versions

| Version | Supported |
|---|---|
| 0.9.x | ✅ Yes |
| < 0.9 | ❌ No — never published, please upgrade |

Only the latest patch release of a supported minor line receives fixes.

Jargon Vault is in Beta (`0.x`), so exactly one minor line is supported at a
time: the newest one. When `0.10.0` lands, `0.9.x` stops receiving fixes the
same day. Longer support windows start at `1.0.0` — see the Versioning section
of [CHANGELOG.md](CHANGELOG.md).

## In scope

The parts of this app that make a real security promise:

- **Authentication and sessions** — signed httpOnly session cookies (`itsdangerous`,
  not JWT) and bcrypt password hashing.
- **Login rate limiting** — independent per-IP and per-email lockout, on by default.
  Including its ordering guarantees: the lockout is checked *before* the password is
  verified, a lockout rejects even the correct password, and a dummy bcrypt comparison
  runs for unknown accounts so response timing does not leak which emails are registered.
- **Cross-user boundaries.** User data is isolated by default; the only two deliberate
  exceptions are the opt-in shared library and public share links. Anything that lets
  one user read, write, or infer another user's notes, tags, assets, or review progress
  is in scope — concretely: the eight-gate check in `resolve_shared_note()`, the
  six-gate check in `resolve_share_token()`, the `visible_tags()` computation, asset
  access that must clear full note authorization before touching a file, and share-link
  nonces that must be random rather than derived from a note id.
- **Path traversal** — note and user ids, backup filenames, and zip-slip protection on
  backup restore and ZIP import.
- **Admin-only surfaces** — site settings, the user list, and whole-site backup,
  download, upload, and restore. Any way for a non-admin to reach these, or for the
  Google `client_secret` to come back out of the API in plaintext, is in scope.
- **Cross-user content rendering** — content authored by one user and rendered in
  another user's browser (shared-library cards and detail views, public share pages).
- **Dependency vulnerabilities**, if you can describe a practical path through this app.

## Out of scope

These are known, deliberate properties of a self-hosted single-process app, not
vulnerabilities. Reports about them will be closed as "by design":

- **No email infrastructure**, therefore no verification emails and no password reset.
  Account recovery is the operator's job (`ADMIN_EMAILS` is the documented break-glass).
- **Rate-limit state lives in process memory** and is cleared on restart. Running the
  app with multiple workers multiplies the effective threshold — that configuration is
  not supported.
- **TLS, HSTS, WAF, and network exposure are the operator's responsibility.** The app
  serves plain HTTP and expects a reverse proxy in front of it if you put it on the
  internet. Misconfiguring `trust_forwarded_for` in either direction is documented in
  the admin UI and is an operational mistake, not a code defect.
- **Anything requiring existing shell or filesystem access** to the host or the `data/`
  directory. That directory holds every user's notes and the session-signing key by design.
- **User-supplied AI endpoint addresses.** The AI connection settings deliberately let
  you point the app at any HTTP address — that is the entire feature, and it is off by default.
- **Self-inflicted content** — what you can make your own browser render inside your own
  note. Cross-*user* rendering is in scope (see above); cross-*self* is not.
- **Resource exhaustion by an authenticated, allow-listed user.** This is not a
  multi-tenant SaaS; people who can sign in are people you invited.
- Missing security headers, missing SPF/DMARC, outdated-but-unreachable dependencies,
  and automated-scanner output with no demonstrated impact.

## Safe harbour

Testing against your own instance, in good faith, following this policy, will never
be met with a legal complaint from this project.
