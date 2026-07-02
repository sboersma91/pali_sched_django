# FlowLine / Pali Scheduler Django Project

FlowLine is an established Django scheduling application. The repository is currently in a
foundation/stabilization phase: preserve existing workflows, make the development baseline
predictable, and document known debt before further feature work.

## Repository layout

```text
.
├── requirements.txt              # canonical Python dependency pin
├── .python-version               # recommended local Python series
└── scheduler_project/
    ├── manage.py                 # run Django commands from this directory
    ├── scheduler_project/        # Django settings and root URL configuration
    ├── scheduler_app/            # scheduling models, views, forms, templates, and tests
    └── members/                  # organization models and authentication templates
```

`scheduler_app/extra-files/` contains legacy/experimental scheduling helpers. They are retained
for reference and are not part of the supported development entry points. Do not promote or
remove them without first confirming their intended role.

## Supported development baseline

- **Canonical local baseline:** Python 3.14.4 with Django 5.2.15.
- **Also supported by the pinned Django release:** the latest patch of Python 3.10 through 3.14.
- **Dependency source of truth:** `requirements.txt`.

[Django added Python 3.14 compatibility in Django 5.2.8](https://docs.djangoproject.com/en/5.2/releases/5.2.8/). Older Django 5.2 patch releases can fail
under Python 3.14, including during template rendering. Always install the pinned requirements
rather than relying on a globally installed or stale virtual-environment copy of Django.

The project uses development-oriented settings (`DEBUG=True`, a checked-in development secret,
and SQLite). They are suitable for local work only, not production deployment.

## Local setup

From the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate                 # Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
cd scheduler_project
python manage.py migrate
python manage.py check
python manage.py test
python manage.py runserver
```

Useful local URLs:

- `http://127.0.0.1:8000/` — legacy landing page
- `http://127.0.0.1:8000/home_paid` — operational dashboard
- `http://127.0.0.1:8000/admin/` — Django admin
- `http://127.0.0.1:8000/login/` — login workflow

## SQLite and migration workflow

`scheduler_project/db.sqlite3` is a **local development artifact** and is intentionally ignored.
Each developer creates or updates their own database by running `python manage.py migrate`. Never
commit local SQLite contents: doing so mixes developer data with schema history and causes branch
merges to report misleading migration state.

Schema changes must be represented by migration files:

```bash
cd scheduler_project
python manage.py makemigrations --check --dry-run  # should report "No changes detected"
python manage.py showmigrations                    # inspect local application state
python manage.py migrate                           # apply committed migrations locally
```

When intentionally changing a model, generate and review the migration, run tests against a fresh
test database, and commit the model and migration together. Existing migration files form a linear
history and should not be edited or squashed during the stabilization phase.

## Baseline checks

Run these from `scheduler_project/` before committing application changes:

```bash
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test
```

Run these from the repository root:

```bash
git status
git diff --check
```

Django's test runner creates and destroys a separate test database. Test results therefore do not
depend on the contents or migration state of a developer's ignored `db.sqlite3`.

## Canonical Schedule block definitions

`scheduler_app/schedule_blocks.py` is the authoritative source for operational weekday choices,
the 20 stored Schedule slot keys and rendering order, block display labels, daytime/night kinds,
trip-window offsets, and special unavailable/unassigned values. Schedule generation initialization,
detail rendering, CSV export, and School block accounting consume these shared definitions.

The trip-window offsets are established operational boundaries rather than simple indexes of each
day's first displayed block. Keep them synchronized with current School accounting behavior. The
scheduling search algorithm still relies on existing slot-key naming conventions such as `night`
and paired `1`/`2` suffixes; changing those conventions requires separately scoped engine work.

## Authentication and organization architecture

The application uses Django's built-in `User` model and login/logout views. Operational scheduling
data is owned by `Organization`, and users are connected to one organization through
`OrganizationMembership`. Operational views enforce organization isolation through queryset
filtering, object lookup protection, form choice filtering, create ownership assignment, protected
CSV export, and organization-aware schedule generation.

See [Authentication and Authorization Architecture](docs/authentication-authorization-architecture.md)
for the current model, authorization rules, known limitations, and architecture decision record.

## Compatibility and known limitations

- Both legacy function-based CRUD routes and newer class-based operational routes remain in use.
  Treat them as compatibility workflows until route-level usage is deliberately reviewed.
- The `Default Organization` fallback remains for migration safety and legacy compatibility paths.
  Real users should have explicit `OrganizationMembership` records before production use.
- A few empty or legacy templates remain in the tree. They are intentionally retained because
  absence of direct references is not yet sufficient proof that external bookmarks or inherited
  compatibility flows do not depend on them.
- `scheduler_app/extra-files/` contains old scheduling experiments and is intentionally untouched.
- Scheduling-engine structure, model naming/typos, development settings, and production-readiness
  concerns are architectural debt, not baseline-cleanup work.

## Safe stabilization boundaries

Safe changes now include documentation, ignore rules, dependency/version clarification, migration
state hygiene, portable editor configuration, and reliable tests. Broad route/model renames,
removal of compatibility workflows, scheduling-engine refactors, database replacement, and
production configuration should wait for separately scoped work with behavioral coverage.
