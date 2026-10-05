pip install -r requirements.txt
python manage.py migrate
python seeder.py
python manage.py shell -c "from posts.tasks import setup_all_schedulers; setup_all_schedulers()"

.venv\Scripts\python.exe manage.py qcluster
.venv\Scripts\python.exe manage.py runserver 0.0.0.0:8000



# Postly — Social Media Publishing Tool

## Quick Start

Every time you work on this project you need **two terminals running simultaneously** — one for the web server, one for the background worker.

---

## Terminal 1 — Web Server

```bash
python manage.py runserver
```

**What it does:** Starts the Django development server at `http://127.0.0.1:8000/`

**When to run:** Every time you want to use the app.

---

## Terminal 2 — Background Worker (Queue Cluster)

```bash
python manage.py qcluster
```

**What it does:** Runs the background task processor that handles:
- Publishing posts asynchronously (prevents the browser from timing out)
- Executing scheduled posts at their scheduled time
- Automatically retrying failed posts

**When to run:** Every time you want publishing, scheduling, or retries to actually work.  
**⚠️ Without this running, scheduled posts will never publish.**

---

## First-Time Setup (Run Once)

These commands only need to be run once after a fresh clone or migration.

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Run database migrations

```bash
python manage.py migrate
```

### 3. Seed the database (creates admin user + platforms)

```bash
python seeder.py
```

Default credentials after seeding:
- **URL:** http://127.0.0.1:8000/
- **Username:** `admin`
- **Password:** `Admin@1234`

### 4. Set up recurring schedulers (run once, persists in DB)

```bash
python manage.py shell -c "from posts.tasks import setup_all_schedulers; setup_all_schedulers()"
```

**What it does:** Creates two repeating background jobs in the database:
- `process_scheduled_posts` — runs every minute, publishes any posts whose scheduled time has arrived
- `process_retries` — runs every minute, retries any failed posts that are due for a retry

**⚠️ If you skip this step, scheduled posts will never publish automatically.**

---

## Environment Variables (.env)

Create a `.env` file in the project root. See `.env.example` for the full list.

| Variable | Required | Description |
|---|---|---|
| `DJANGO_SECRET_KEY` | Yes (prod) | Django secret key |
| `FIELD_ENCRYPTION_KEY` | Yes | Encrypts stored OAuth tokens. Generate with the command below. |
| `LINKEDIN_CLIENT_ID` | Yes | From your LinkedIn app |
| `LINKEDIN_CLIENT_SECRET` | Yes | From your LinkedIn app |
| `LINKEDIN_REDIRECT_URI` | Optional | Defaults to `http://127.0.0.1:8000/posts/social-accounts/linkedin/callback/` |
| `DJANGO_DEBUG` | Optional | `True` for dev, `False` for prod |

**Generate an encryption key:**
```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

---

## Monitoring & Debugging

### Check what scheduled tasks exist

```bash
python manage.py qschedule
```

Should show two entries: `process_scheduled_posts` and `process_retries`.  
If missing, re-run step 4 from First-Time Setup.

### Monitor the task queue live

```bash
python manage.py qmonitor
```

Shows active workers, queued tasks, completed tasks, and failures.

### Manually trigger the scheduled post processor

Useful if `qcluster` wasn't running and you have overdue scheduled posts:

```bash
python manage.py shell -c "from posts.tasks import process_scheduled_posts; result = process_scheduled_posts(); print(result)"
```

### Manually trigger the retry processor

```bash
python manage.py shell -c "from posts.tasks import process_retries; result = process_retries(); print(result)"
```

### Check Django-Q task history (successes and failures)

Go to: **Django Admin → Django Q → Successful tasks / Failed tasks**  
URL: http://127.0.0.1:8000/admin/django_q/

---

## How Scheduling Works

```
User schedules a post for 3:00 PM
        ↓
PostTarget created in DB with status=SCHEDULED, scheduled_at=3:00 PM
        ↓
Django-Q Schedule.ONCE job created for publish_post_task at 3:00 PM
        ↓
qcluster is running...
        ↓
3:00 PM arrives → process_scheduled_posts() picks it up
        ↓
Status → PUBLISHING → publish_post_task() runs → LinkedIn API called
        ↓
Status → SUCCESS (or FAILED → auto-retried)
```

**Why `qcluster` must be running:** The scheduler ticks every minute inside `qcluster`. Without it, nothing checks whether a post's scheduled time has arrived.

---

## DEBUG Mode Behaviour

In `DEBUG=True` (local dev), `"sync": True` in `Q_CLUSTER` means:

- **Publish Now** — runs synchronously in the same request (instant, no `qcluster` needed)
- **Scheduled posts** — still need `qcluster` to fire at the right time

To fully simulate production scheduling locally, start `qcluster` in Terminal 2.

---

## Platform Capabilities

Platform limits (max text length, image support, video support, etc.) are defined in one place:

```
posts/services/platform_capabilities.py
```

When adding a new platform (Facebook, Twitter, etc.), update this file first. Templates and adapters read from it automatically — no hardcoding needed.

---

## Project Structure

```
posts/
  services/
    platform_capabilities.py  ← Platform limits & capabilities (single source of truth)
    publishing_manager.py      ← All publishing tools (used by views + future AI agents)
    linkedin_publisher.py      ← LinkedIn API adapter
  tasks.py                     ← Background task definitions (qcluster runs these)
  views.py                     ← HTTP request handlers
  templatetags/
    post_tags.py               ← Custom template tags (status_badge, platform_color, etc.)

config/
  settings.py                  ← Django settings (Q_CLUSTER config here)

templates/
  posts/
    detail.html                ← Post detail page
    create.html                ← Post create page
    includes/
      publish_panel.html       ← Reusable publish/schedule panel
      status_badge.html        ← (kept for reference, replaced by template tag)
```

---

## Common Issues

### Scheduled post didn't publish

1. Was `qcluster` running? Start it and wait up to 1 minute.
2. Are the schedulers set up? Run `python manage.py qschedule` to check.
3. Manually trigger: `python manage.py shell -c "from posts.tasks import process_scheduled_posts; process_scheduled_posts()"`

### "FIELD_ENCRYPTION_KEY defined incorrectly"

Your `.env` is missing or has an invalid key. Generate one:
```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```
Add it to `.env` as `FIELD_ENCRYPTION_KEY=<generated_value>`

### LinkedIn posts not publishing

- Check the **Publishing History** section on the post detail page for the error message
- Most common causes: expired token (reconnect the account), content too long (LinkedIn max 3000 chars)

### Post status still shows "Draft" after publishing

The signals that update post status should fire automatically. If they don't:
```bash
python manage.py shell -c "
from posts.models import Post
for p in Post.objects.all():
    p.recalculate_status()
    print(p.pk, p.status)
"
```
