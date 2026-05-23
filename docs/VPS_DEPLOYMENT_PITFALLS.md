# VPS Deployment Pitfalls And Fixes

This file records deployment/runtime mistakes we already hit, so future VPS
updates can avoid repeating them.

## Golden Deploy Command For Ubuntu VPS

Use `python3`, not `python`, and use `python3 -m pip`, not bare `pip`.

```bash
cd /opt/hoberadius
git pull origin main
python3 -m pip install -r requirements.txt
python3 -m compileall app
python3 -m pytest tests/test_web_print_templates_ui.py tests/test_card_renderer.py tests/test_operations_foundation.py -q
```

If pip is missing:

```bash
apt update
apt install -y python3-pip
python3 -m pip install -r requirements.txt
```

Find the real service name before restarting:

```bash
systemctl list-units --type=service --all | grep -Ei 'hobe|radius|gunicorn|flask|uwsgi'
```

Then restart the service that actually exists:

```bash
sudo systemctl restart <real-service-name>
sudo systemctl status <real-service-name> --no-pager
```

## Errors We Hit

### `Command 'pip' not found`

Cause:
Ubuntu does not always install the `pip` command by default.

Fix:

```bash
apt update
apt install -y python3-pip
python3 -m pip install -r requirements.txt
```

Prevention:
Always use `python3 -m pip` in deploy docs.

### `Command 'python' not found`

Cause:
Ubuntu often ships Python as `python3` only.

Fix:

```bash
python3 -m compileall app
python3 -m pytest -q
```

Optional compatibility package:

```bash
apt install -y python-is-python3
```

Prevention:
Deploy commands should use `python3`.

### `Failed to restart hoberadius.service: Unit hoberadius.service not found`

Cause:
The service name on the VPS is not necessarily `hoberadius`.

Fix:

```bash
systemctl list-units --type=service --all | grep -Ei 'hobe|radius|gunicorn|flask|uwsgi'
sudo systemctl restart <real-service-name>
```

Prevention:
Never assume the service name. Detect it first.

### `systemctl: unrecognized option '--no-pagercd'`

Cause:
Two shell commands were pasted together with no newline:
`--no-pager` + `cd`.

Fix:
Run commands one line at a time:

```bash
sudo systemctl status <real-service-name> --no-pager
cd /opt/hoberadius
```

Prevention:
Keep deploy instructions as copy-safe blocks and avoid joining unrelated
commands on one line.

### `sqlite3.OperationalError: database is locked`

Where it happened:
Generating card batches while SQLite was being written by another process.

Likely causes:
- Two Flask/dev-server workers writing the same SQLite DB.
- A long-running request or background worker holding a write lock.
- Debug reloader spawned duplicate processes.

Fix checklist:

```bash
pkill -f "flask run" || true
pkill -f "gunicorn" || true
sudo systemctl restart <real-service-name>
```

For local Windows development, stop duplicate Python/Flask processes before
generating large card batches.

Prevention:
- Avoid running two servers against the same SQLite database.
- Keep heavy export work in async print jobs.
- Prefer WAL/busy-timeout settings for SQLite when heavy writes are expected.

### `sqlite3.IntegrityError: UNIQUE constraint failed: _migrations.name`

Cause:
The migration runner attempted to insert the same migration name twice.

Likely triggers:
- Two app processes ran migrations at the same time.
- Debug reloader or duplicate server startup raced during `_init_db`.

Fix checklist:

```bash
pkill -f "flask run" || true
pkill -f "gunicorn" || true
sudo systemctl restart <real-service-name>
```

If it persists, inspect `_migrations` before changing data:

```bash
sqlite3 <database-file> "select name, applied_at from _migrations order by applied_at desc limit 20;"
```

Prevention:
- Run migrations from one process only.
- Avoid duplicate local Flask debug processes against the same DB.
- Keep migration inserts idempotent where possible.

### Print template page lost changes after refresh/update

Cause:
Some print-template UI work was still in local working tree/stash and was not
committed to `origin/main`. After updating from GitHub, the committed version
did not contain all UI changes.

Fix applied:
The print designer work was restored and committed:

```text
fa9d045 Restore card print designer operations room
```

Prevention:
Before VPS update or GitHub push, always verify:

```bash
git status --short --branch
git diff --name-only
git diff --cached --name-only
git ls-files --others --exclude-standard
git log --oneline -n 5
```

If any required work is in `git stash list`, do not deploy until the relevant
stash is either restored and committed or intentionally parked.

### Uploaded card artwork missing from exported PDF

Cause:
Uploaded-image card designs and system-generated presets were sharing one
rendering path, so decorations and background handling could override or hide
uploaded artwork in export.

Fix direction:
- Keep uploaded-artwork rendering separate from preset rendering.
- For uploaded artwork, only overlay the required dynamic elements:
  username, password, QR/barcode, optional price/link/footer notes.
- Do not draw preset-only decorations on top of uploaded artwork.

Prevention:
Regression tests should cover:
- Uploaded image appears in preview model.
- Uploaded image appears in sample PDF.
- Uploaded image appears in batch PDF.
- Preset rendering remains unchanged.

### Arabic text broken in exported PDF/card image

Cause:
Arabic shaping/direction needs explicit handling before raster/PDF rendering.

Fix direction:
- Keep `arabic-reshaper` and `python-bidi` installed from `requirements.txt`.
- Shape Arabic text before drawing it into the card snapshot.
- Treat the card as a fixed internal canvas, then place the finished snapshot
  into the PDF sheet.

Prevention:
Test Arabic templates in both preview and exported PDF before deploy.

### Large card export is slow or appears stuck

Cause:
Synchronous PDF generation for 500/1000/5000+ cards can take time and makes
the browser look frozen.

Fix direction:
- Use async print jobs.
- Poll job status.
- Show progress steps: batch selected, template selected, render started,
  pages generated, PDF ready, failed with reason.

Prevention:
Do not rely on one blocking HTTP request for large exports.

### MikroTik timeout spam in logs

Example:

```text
MT hotspot/active: connect failure router=... address=... timed out
mt_reconciler: router=... unreachable
dhcp-lease sync: router=... failed
```

Cause:
Routers are configured but unreachable from the server or API port `8728` is
closed/blocked.

Fix checklist:
- Confirm router IP is reachable from VPS.
- Confirm RouterOS API service is enabled.
- Confirm port `8728` or `8729` is open.
- Confirm firewall/routing/VPN path.
- Disable or pause unreachable test routers to reduce noise.

Prevention:
- Add router health status in UI.
- Do not poll unreachable routers too aggressively.
- Use backoff for repeated failures.

## Pre-Deploy Checklist

Run locally before pushing:

```bash
git status --short --branch
python -m compileall app
python -m pytest tests/test_web_print_templates_ui.py tests/test_card_renderer.py tests/test_operations_foundation.py -q
git diff --check
git log --oneline -n 5
```

Run on Ubuntu VPS after pulling:

```bash
cd /opt/hoberadius
git pull origin main
python3 -m pip install -r requirements.txt
python3 -m compileall app
python3 -m pytest tests/test_web_print_templates_ui.py tests/test_card_renderer.py tests/test_operations_foundation.py -q
systemctl list-units --type=service --all | grep -Ei 'hobe|radius|gunicorn|flask|uwsgi'
sudo systemctl restart <real-service-name>
sudo systemctl status <real-service-name> --no-pager
```
