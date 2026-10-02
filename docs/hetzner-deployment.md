# Hetzner deployment — server setup, config, and backups

Written 2026-10-02, the day of the migration off Render. This is the
operational reference for the live deployment — what's actually running,
where, and how to recover it if something breaks.

## The box

Atesta's existing Hetzner VPS, `204.168.247.216`, SSH alias
`atesta-paperclip` (configured in `~/.ssh/config`). Shared with
Paperclip (`app.atesta.io`) — this was a deliberate reuse of
already-paid-for infrastructure, not a new server. See
`docs/demo-hosting-runbook.md` (in the `atesta` repo) for the box's
general nginx/Cloudflare-origin-cert conventions this deployment follows.

## Directory layout

```
/opt/vadium-lookup/
├── docker-compose.yml       # compose file lives at this level, not in app/
├── .env                     # DB_PASSWORD + the 5 real secrets, chmod 600
└── app/                     # rsynced from the vadium-lookup repo root
    ├── Dockerfile
    ├── mvp/
    └── requirements.txt
```

The repo's own `docker-compose.yml` (committed at the repo root) uses
`build: .` because within the repo, the Dockerfile sits alongside it.
On the server, the compose file was moved up one level and the build
context changed to `./app` to match this layout — see the comment in
the committed `docker-compose.yml` for this exact note. If redeploying
from a different layout, adjust the build context accordingly.

## Services

Two containers, defined in `docker-compose.yml`:

- **`vadium-lookup`** — the app itself, built from the repo's
  `Dockerfile`, listening on `127.0.0.1:3300` (not exposed beyond
  localhost — nginx is the only thing that reaches it).
- **`postgres`** (Postgres 17) — a dedicated instance for this service,
  deliberately **not shared** with Paperclip's own `paperclip-postgres-1`
  container. Same "additive, isolated" principle already established
  for this box: a public product's data shouldn't share a blast radius
  with the internal board tool's. Data lives in the
  `vadium_postgres_data` named Docker volume.

Redeploying: `rsync` the updated repo into `/opt/vadium-lookup/app/`,
then `docker compose build vadium-lookup && docker compose up -d` from
`/opt/vadium-lookup/`. No CI/CD — this is a manual deploy, same as
Paperclip's own.

## nginx

`/etc/nginx/sites-available/vadium-lookup`, symlinked into
`sites-enabled/`. Follows Paperclip's exact vhost pattern: redirect 80→443,
terminate TLS using the box's existing wildcard Cloudflare Origin
Certificate (`/etc/ssl/cloudflare/atesta.{crt,key}`, valid to 2041,
covers `*.atesta.io`), proxy to `127.0.0.1:3300`. One addition beyond
Paperclip's template: `proxy_buffering off`, required because the MCP
transport streams Server-Sent Events — buffered proxying would hold
chunks instead of streaming them through.

**Important, verified directly**: a Cloudflare Origin Certificate is
only trusted by Cloudflare's own edge, never by a direct client —
connecting straight to the server IP will always fail standard TLS
validation (confirmed: `curl` without `-k` returns nothing against the
raw IP). This is correct, not a bug. Real traffic must flow through
Cloudflare's proxy (DNS record set to "Proxied," orange cloud), which
terminates the real, publicly-trusted certificate at the edge and only
re-encrypts to this origin cert on the Cloudflare-to-origin leg.

## DNS

`vadium-lookup.atesta.io` — Cloudflare zone `atesta.io`
(`d4ae69ee6f4104eb22b5006736219d56`). Moved 2026-10-02 from a CNAME to
`vadium-lookup.onrender.com` (DNS-only) to an **A record** at
`204.168.247.216`, **Proxied** — matching Paperclip's own pattern, and
the opposite proxy setting from the old Render CNAME, which specifically
needed to be unproxied so Render could verify domain ownership. Don't
copy that setting by habit when working on this domain elsewhere.

## Environment variables

Set in `/opt/vadium-lookup/.env` (chmod 600, never committed):

| Variable | Source |
|---|---|
| `DATABASE_URL` | Constructed from `DB_PASSWORD` — see `docker-compose.yml` |
| `DB_PASSWORD` | Generated on the server at migration time (`openssl rand -base64 24`), never transcribed to chat or any local file |
| `BASESCAN_API_KEY` | Moved from Render's env vars, sourced originally from `arc/.env.local` |
| `CDP_API_KEY_ID` / `CDP_API_KEY_SECRET` | Same — moved from Render |
| `ERC8004_AGENT_ID` | Same — moved from Render |
| `X402_PAY_TO_ADDRESS` | Same — moved from Render |

Moving secrets between machines: transfer the file directly
(`scp`/pipe over SSH), never print values to a terminal you're reading
as text. See the 2026-10-02 session transcript for the exact pattern
used — extract only the needed keys locally, `scp` to the server,
merge server-side, delete the uploaded copy.

## Backups — two separate mechanisms, covering different failure modes

**1. Hetzner Cloud Backups** (enabled 2026-10-02, account-level, not
configured by anything in this repo) — automatic, recurring full-disk
snapshots of the *entire VPS*, 7 rotating slots (oldest deleted when a
new one is made and all slots are full), costs 20% of the server's
monthly plan price, flat regardless of how many slots are filled.
Protects against: total disk/hardware failure. Does **not** let you
restore just one service — a restore rolls back the whole box,
Paperclip included, to that snapshot's point in time. Check the
Hetzner Cloud Console's Backup tab for this server for the actual
schedule/last-backup timestamp — not reproduced here since it's an
account setting, not something this repo controls.

**2. Daily `pg_dump` cron** (`/opt/backups/pg-backup.sh`, root crontab
at `/etc/cron.d/pg-backup`, runs 03:00 server time) — dumps **both**
`paperclip-postgres-1` and `vadium-lookup-postgres-1` separately to
compressed `.sql.gz` files in `/opt/backups/postgres/`, 14-day local
retention. Added 2026-10-02 after finding neither database had any
backup at all before this. Protects against: a bad migration, an
accidental `DELETE`, app-level data corruption — the things a
once-a-week full-VM snapshot might not have a recent-enough or
granular-enough restore point for. Restoring: `gunzip -c
<file>.sql.gz | docker exec -i vadium-lookup-postgres-1 psql -U vadium
vadium_lookup`.

**What's still a real gap, not yet closed**: both mechanisms above
write to (or snapshot) the same physical server. A real off-box/offsite
copy of the `pg_dump` output (Hetzner Object Storage or equivalent)
is a legitimate follow-up, not done as of this writing. Hetzner's own
Backups feature is the closer-to-offsite option of the two (it's not
on the same disk as the live data), but it's still the same physical
machine's infrastructure, not a separate provider/region.

## What changed from the Render deployment, for anyone expecting that setup

- No more `render.yaml`-driven native Python buildpack — this is now a
  Docker deployment (`Dockerfile` + `docker-compose.yml`).
- No more ephemeral local SQLite for `report_outcome` — real Postgres,
  confirmed to survive a restart (and, separately, confirmed that the
  *old* Render setup did NOT survive a redeploy — the actual reason
  for this migration, not a preference).
- `render.yaml` is kept in the repo for reference only — it is not live
  and should not be assumed current without checking
  `vadium-lookup.atesta.io`'s DNS first.
