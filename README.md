# Pali Scheduler Django Project

## 1) What this project appears to be

This repository appears to be an older **Django (Python)** scheduling application named **Pali Scheduler**. It includes:

- A main scheduling app (`scheduler_app`) with models for locations, courses, schools, and schedules.
- A members/auth app (`members`) for login/logout/registration flows.
- SQLite as the default local database.

Based on the current codebase state, the project is in a **foundation/stabilization phase** (orientation, documentation, setup clarity, and safe incremental cleanup) rather than active feature expansion.

---

## 2) Current project structure

```text
.
├── AGENTS.md
├── README.md
├── requirements.txt
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
│   │   ├── tests.py              # smoke tests (currently 5 tests)
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

## 3) Local development setup (verified baseline)

### Prerequisites

- Python 3.10+ (3.11 is a safe modern default)
- `pip`
- A virtual environment tool (`venv` is fine)

### Dependency status

A pinned dependency manifest now exists at repository root:

- `requirements.txt` with `Django==4.1.5`

### Setup steps (verified in local `.venv` workflow)

From the repository root:

```bash
# 1) create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 2) install dependencies
python3 -m pip install -r requirements.txt

# 3) move into Django project folder
cd scheduler_project

# 4) apply migrations
python3 manage.py migrate
```

---

## 4) Run the Django development server

From `scheduler_project/`:

```bash
python3 manage.py runserver
```

Then open:

- `http://127.0.0.1:8000/` (main app)
- `http://127.0.0.1:8000/admin/` (admin)
- `http://127.0.0.1:8000/members/login_user` (custom member login route)

---

## 5) Run checks/tests

### Test execution contract

The backend and browser suites are intentionally separate:

- **Backend tests are required.** They do not require Playwright or Chromium.
- **Browser interaction tests are optional.** The runner clearly reports `SKIPPED`
  and exits successfully when Playwright or Chromium is unavailable.
- **All available tests** always run the backend suite, then run the browser suite
  when the browser prerequisites are available.

From the repository root:

```bash
# required backend-only suite
./scripts/test_backend.sh

# optional Playwright interaction suite
./scripts/test_browser.sh

# required backend suite plus browser tests when available
./scripts/test_all_available.sh
```

The focused Playwright suite exercises progressive schedule movement in a real
browser while continuing to submit the existing server-rendered move form.

### Install browser-test prerequisites locally

```bash
python3 -m pip install -r requirements-dev.txt
python3 -m playwright install chromium
./scripts/test_browser.sh
```

For Linux CI images that need Playwright system packages, use:

```bash
python3 -m pip install -r requirements-dev.txt
python3 -m playwright install --with-deps chromium
REQUIRE_BROWSER_TESTS=1 ./scripts/test_browser.sh
```

`REQUIRE_BROWSER_TESTS=1` turns an unavailable-browser skip into a clear failure
for CI jobs where the browser suite is expected to execute.

The underlying Django commands remain available when needed:

```bash
cd scheduler_project
python3 manage.py check
python3 manage.py test scheduler_app members  # backend only
python3 manage.py test browser_tests           # browser suite
```

---

## 6) Known current limitations / likely setup issues

These are known risks during this stabilization phase:

- **Behavioral coverage is still shallow:** current tests are smoke-level only and do not yet validate deeper scheduling behavior.
- **Potential form/model mismatch issues:** some form fields appear out of sync with model field names.
- **Potential routing name mismatch:** at least one redirect target name may not match configured route names.
- **Import-time DB logic in models:** some model module code performs DB access at import time, which can be fragile during migrations/tests.
- **Development-only settings:** `DEBUG=True`, static secret key in settings (acceptable for local dev only, not production).

These are not fixed in this documentation update; they are listed to keep onboarding practical and transparent.

---

## 7) Suggested next foundation tasks

1. **Expand smoke tests into small behavioral checks**
   - Add low-risk tests for key views/forms and expected HTTP responses.

2. **Perform a narrow integrity pass (no feature changes)**
   - Validate forms, route names, and startup flow.
   - Fix only obvious breakpoints in small reviewable commits.

3. **Document app architecture in more detail**
   - Add a short “data flow” section mapping models, views, templates, and URLs.

---

## Quick start (short version)

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
cd scheduler_project
python3 manage.py migrate
python3 manage.py runserver
```
