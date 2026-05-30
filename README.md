# docker-backup-r2

Periodically uploads a Docker container's local backups to a Cloudflare R2
bucket and prunes them with **GFS (grandfather-father-son) retention**.

It's designed for the common case where a container writes timestamped backup
files into a host directory and keeps only the last few on disk. This tool
ships those files off to R2 (your long-term archive) and decides what to keep
there — without ever deleting something just because the source is offline.

## How it works

Each cycle is **stateless and idempotent** — it reconciles against the live R2
listing, so a restarted container or a long-idle source both converge
correctly:

1. **Discover** local files in `BACKUP_DIR` matching `BACKUP_GLOB`.
2. **List** existing objects in the R2 bucket under `R2_PREFIX`.
3. **Upload** any local file missing from R2 (or whose size differs — catches
   interrupted uploads). Uploads are *additive*: a file disappearing locally
   never causes an R2 deletion.
4. **Prune** R2 down to the GFS-selected set.

Because there's no local state database, nothing breaks if the container is
recreated or the source app is down for weeks.

## Retention (GFS)

Retention uses the same well-understood model as `restic`/`borg`: each `KEEP_*`
rule independently selects backups, and a backup survives if **any** rule keeps
it (the union).

| Variable      | Keeps                                                        |
|---------------|-------------------------------------------------------------|
| `KEEP_LAST`   | The N most recent backups, regardless of age (**the floor**) |
| `KEEP_DAILY`  | The most recent backup from each of the last N days with one |
| `KEEP_WEEKLY` | The most recent backup from each of the last N ISO weeks     |
| `KEEP_MONTHLY`| The most recent backup from each of the last N months        |
| `KEEP_YEARLY` | The most recent backup from each of the last N years         |

The periodic rules keep the latest backup *from periods that actually contain a
backup*, so an intermittent source never triggers wrongful deletion, and
`KEEP_LAST` guarantees a minimum count is always retained.

**Example** — "keep the last 5, plus one a day for a week, plus one a week for a
month":

```
KEEP_LAST=5
KEEP_DAILY=7
KEEP_WEEKLY=4
```

## Configuration

All configuration is via environment variables — see [.env.example](.env.example)
for the full annotated list. The essentials:

| Variable                 | Required | Description                                   |
|--------------------------|----------|-----------------------------------------------|
| `R2_ACCOUNT_ID`          | yes      | Cloudflare account id (the endpoint subdomain) |
| `R2_ACCESS_KEY_ID`       | yes      | R2 API token access key                       |
| `R2_SECRET_ACCESS_KEY`   | yes      | R2 API token secret                           |
| `R2_BUCKET`              | yes      | Target bucket name                            |
| `R2_PREFIX`              | no       | Key prefix inside the bucket (default `""`)   |
| `BACKUP_DIR`             | no       | Source dir inside container (default `/backups`) |
| `BACKUP_GLOB`            | no       | Match pattern (default `*`)                   |
| `BACKUP_TIMESTAMP_REGEX` | no*      | Regex extracting the timestamp from filenames |
| `BACKUP_TIMESTAMP_FORMAT`| no       | Explicit `strptime` format for the capture    |
| `KEEP_LAST` / `KEEP_DAILY` / `KEEP_WEEKLY` / `KEEP_MONTHLY` / `KEEP_YEARLY` | one+ | Retention rules |
| `RUN_ONCE`               | no       | `true` = one cycle then exit (default `false`) |
| `INTERVAL_SECONDS`       | no       | Loop interval (default `3600`)                |
| `DRY_RUN`                | no       | `true` = log actions without changing R2      |
| `LOG_LEVEL`              | no       | `INFO` (default), `DEBUG`, etc.               |

\* Strongly recommended whenever any periodic (`DAILY`/`WEEKLY`/…) rule is used,
so the time buckets are based on the real backup time rather than upload time.

## Running with Docker Compose (recommended)

The container schedules itself internally (loop mode), so it fits cleanly into a
docker-compose + Portainer setup — one service, `restart: unless-stopped`, logs
straight to stdout.

```bash
cp .env.example .env       # then edit credentials, paths, retention
docker compose up -d --build
docker compose logs -f     # or view in Portainer
```

Point `BACKUP_DIR_HOST` in `.env` at the host directory your app writes backups
to; it's mounted **read-only** at `/backups`.

> **Scheduling note:** set `INTERVAL_SECONDS` comfortably *shorter* than the time
> it takes your source to churn through its on-disk backups. If the source keeps
> 5 files and writes one per hour (~5h of history), an interval of several hours
> risks missing a backup that was created and rotated out between runs. Each
> cycle logs `local=… already-in-R2=… uploaded=… pruned=…` so you can monitor it.

## One-shot mode (host cron / manual)

The same image runs a single cycle and exits with `RUN_ONCE=true`:

```bash
docker compose run --rm -e RUN_ONCE=true backup-r2
```

Handy for host cron/systemd timers, or for a `DRY_RUN=true RUN_ONCE=true` check
before going live.

## Publishing the image

[.github/workflows/ci.yml](.github/workflows/ci.yml) runs the test suite on every
push and pull request, and — only for pushes to `main` and `v*` tags — builds and
pushes a multi-arch (amd64 + arm64) image to GitHub Container Registry using the
built-in `GITHUB_TOKEN` (no PAT needed). The publish step waits on tests passing.

Resulting image: `ghcr.io/antoinecordelle/docker-backup-r2`, tagged `latest`
(default branch), `sha-<commit>`, and the semver version for `vX.Y.Z` tags.

The package is **private by default** — to pull it without authenticating, open
it under the repo's *Packages*, then *Package settings → Change visibility →
Public*. Then switch [docker-compose.yml](docker-compose.yml) to the `image:`
line instead of `build:`.

## Development

```bash
uv run --extra dev pytest        # run the test suite (uses moto to mock R2)
uv run python -m backup_r2       # run locally against real R2 (needs env vars)
```

Tests cover timestamp parsing, the GFS bucketing (including sparse/gappy
timestamps that simulate downtime), upload idempotency, source-rotation safety,
dry-run, and the prefix safety guard that prevents pruning unrelated objects.
