# Pali Scheduler Django Project

## 1) What this project appears to be

This repository appears to be an older **Django (Python)** scheduling application named **Pali Scheduler**. It includes:

- A main scheduling app (`scheduler_app`) with models for locations, courses, schools, and schedules.
- A members/auth app (`members`) for login/logout/registration flows.
- SQLite as the default local database.

Based on the current codebase state, the project seems to be in a **foundation/stabilization phase** (orientation, documentation, setup clarity, and safe incremental cleanup) rather than active feature expansion.

---

## 2) Current project structure

```text
.
├── AGENTS.md
├── scheduler_project/
│   ├── manage.py
│   ├── db.sqlite3
│   ├── scheduler_project/        # Django project config package
│   │   ├── settings.py
│   │   ├── urls.py
│   │   ├── asgi.py
│   │   └── wsgi.py
│   ├── scheduler_app/            # Main scheduling domain app
│   │   ├── models.py
│   │   ├── views.py
│   │   ├── forms.py
│   │   ├── urls.py
│   │   ├── migrations/
│   │   ├── templates/
│   │   └── extra-files/          # legacy/experimental helper files
│   └── members/                  # Auth/member-related app
│       ├── views.py
│       ├── urls.py
│       ├── templates/
│       └── migrations/
```

---

## 3) Local development setup

> This section is practical and beginner-friendly, but dependency setup is currently incomplete in-repo.

### Prerequisites

- Python 3.10+ (3.11 is a safe modern default)
- `pip`
- A virtual environment tool (`venv` is fine)

### Dependency status (important)

There is currently **no confirmed dependency manifest** (`requirements.txt`, `pyproject.toml`, or `Pipfile`) at repository root. That means environment setup is currently incomplete and must be reconstructed.

### Recommended setup steps (partially unverified)

From the repository root:

```bash
# 1) create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 2) install Django (version inferred from settings comments)
pip install "django==3.2.9"

# 3) move into Django project folder
cd scheduler_project

# 4) run migrations
python manage.py migrate
```

Because there is no pinned dependency file, these commands are **recommended but currently unverified as a complete setup**.

---

## 4) Run the Django development server

From `scheduler_project/`:

```bash
python manage.py runserver
```

Then open:

- `http://127.0.0.1:8000/` (main app)
- `http://127.0.0.1:8000/admin/` (admin)
- `http://127.0.0.1:8000/members/login_user` (custom member login route)

---

## 5) Run basic checks/tests (if available)

From `scheduler_project/`:

```bash
# framework/system checks
python manage.py check

# run test suite
python manage.py test
```

Current tests are mostly placeholders, so `test` currently functions more as a smoke check than full regression coverage.

If dependencies are missing, treat these commands as **recommended but unverified** until the environment is fully pinned.

---

## 6) Known current limitations / likely setup issues

These are observed from inspection and should be treated as known risks:

- **Dependency setup incomplete:** no committed dependency lock/manifest at repo root.
- **Potential form/model mismatch issues:** some form fields appear out of sync with model field names.
- **Potential routing name mismatch:** at least one redirect target name may not match configured route names.
- **Import-time DB logic in models:** some model module code performs DB access at import time, which can be fragile during migrations/tests.
- **Limited automated tests:** test modules exist but are mostly placeholders.
- **Development-only settings:** `DEBUG=True`, static secret key in settings (acceptable for local dev only, not production).

These are not fixed in this documentation update; they are listed to improve onboarding clarity.

---

## 7) Suggested next foundation tasks

1. **Create a real dependency baseline**
   - Add `requirements.txt` (or `pyproject.toml`) with pinned versions.
   - Document exact Python and Django versions used.

2. **Add a minimal smoke-test safety net**
   - Add basic tests for URL resolution, homepage rendering, and core app import/startup.

3. **Perform a narrow integrity pass (no feature changes)**
   - Validate forms, route names, and startup flow.
   - Fix only obvious breakpoints in small reviewable commits.

---

## Quick start (short version)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install "django==3.2.9"   # recommended, unverified baseline
cd scheduler_project
python manage.py migrate
python manage.py runserver
```

