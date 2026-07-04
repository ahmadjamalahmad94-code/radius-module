# Docker image size — why `hoberadius:latest` hit 10.7GB and the fix

**Date:** 2026-07-04 · **Observed:** `hoberadius:latest` = **10.7GB** (vs
freeradius 363MB, nginx 93MB). `docker system prune` reclaimed 15.29GB of
build cache but the image stayed huge and re-grew after each deploy.

## Root cause

1. **Build context = repo root** (`deploy/docker-compose.yml` →
   `context: ..`) and the Dockerfile did **`COPY . /app`**.
2. The old `.dockerignore` was a short **denylist** (`instance/*.db`,
   `backups/`, `logs/`…). On the production server the repo dir accumulates
   untracked multi-GB files a denylist can never keep up with — anything under
   `instance/` that isn't a top-level `*.db` (WAL checkpoints, wizard uploads),
   customer **migration dumps** (`*.sql`, `*.sql.gz`, `migration_samples/`),
   stray archives, etc. All of it was baked into every image.
3. Each rebuild left the previous ~10GB image **dangling** (`<none>`), plus
   BuildKit cached the giant context layers → disk grew with every deploy
   until a manual prune.

## Fix (3 layers of defence)

1. **`.dockerignore` is now an allowlist (deny-by-default).** `*` excludes
   everything; only the runtime set is re-included:
   `app/`, `translations/`, `tools/`, `deploy/`, `wsgi.py`,
   `requirements.txt`, `babel.cfg`. Followed by re-denies for caches,
   `*.db*`, `*.sql` (except `app/radius/db/migrations/*.sql` — required at
   startup), archives, logs, and secret files. Anything new that appears on
   the server stays out of the context automatically.
2. **Dockerfile copies explicit paths** instead of `COPY . /app` — second
   line of defence with identical runtime layout (`/app/app`,
   `/app/translations`, `/app/tools`, `/app/deploy`, `/app/wsgi.py`).
3. **`deploy.sh upgrade` now prunes after build:** `docker image prune -f`
   (dangling images only) + `docker builder prune -f --keep-storage 2GB`.
   Neither touches running images, volumes, or bind-mounted data
   (`instance/`, `backups/`, `logs/` live on the host).

## What is deliberately NOT excluded

- `app/static/uploads/` — compose does **not** bind-mount it, so files baked
  into the image are what survives a rebuild (pre-existing behaviour, kept).
  Long-term: consider a bind mount for uploads so they persist properly.
- `app/radius/db/migrations/*.sql` — applied by the app at startup.
- `deploy/` — `gunicorn.conf.py` is referenced by the container CMD and
  `entrypoint.sh` by the ENTRYPOINT.

Runtime data is untouched: `instance/`, `logs/`, `backups/` are bind mounts
and were never supposed to be inside the image.

## Deploy commands (from now on)

```bash
# standard upgrade (git pull + build + prune + status):
sudo bash deploy/deploy.sh upgrade

# manual equivalent:
cd /path/to/radius-module
git pull --rebase
docker compose -f deploy/docker-compose.yml up -d --build
docker image prune -f
docker builder prune -f --keep-storage 2GB
```

## Verification (run on the server)

```bash
# how big is the repo dir / what would have entered the old context:
du -h -d1 . | sort -h

# clean build + size check:
docker compose -f deploy/docker-compose.yml build hoberadius
docker images hoberadius:latest          # expect ~600MB–1GB, not 10.7GB
docker history hoberadius:latest         # code COPY layers should be ~100–200MB total

# stack still healthy:
docker compose -f deploy/docker-compose.yml config -q
docker compose -f deploy/docker-compose.yml up -d --build
docker ps                                # hoberadius / freeradius / nginx / backup all Up
curl -fsS http://127.0.0.1:8000/admin/radius/_health

# disk after:
docker system df
```

Expected result: image ≈ python:3.12-slim (~130MB) + pip deps + ~90MB code
(app 33MB + translations 16MB + tools 3.5MB + deploy 0.4MB + static) —
roughly **0.6–1GB** depending on wheels, instead of 10.7GB.
