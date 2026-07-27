# Canonical Demo Environment Operator Runbook

## Purpose and scope

This runbook operates one explicitly configured, fictional demo organization. It
covers initial preparation, read-only inspection, confirmed setup, verification,
and restoration after a presentation.

The scaffolding is not:

- a whole-database reset or general organization-cleanup tool
- a customer-onboarding or credential-provisioning system
- a backup, snapshot, or disaster-recovery system
- a public or anonymous-demo system
- a reset mechanism for arbitrary organizations or schedules

Only use these commands when ownership of the target environment and exact demo
organization is known.

## Safety warnings

> **Inspect before every confirmed setup or reset.**
>
> Confirm the exact organization identifier in both the environment and command
> line. Never target `Default Organization`. Review the plan before permitting
> writes.

- Never store usernames with passwords, passwords, secret keys, or private
  customer data in this repository.
- Do not use `DEBUG=True` as authorization; it does not enable scaffolding.
- Do not run against an environment whose ownership is uncertain.
- Do not modify these commands to delete an organization or database.
- Maintain deployment-appropriate backups separately from this scaffolding.
- The checked-in local development settings are not public-deployment
  configuration.

## Preconditions

Before initial setup:

1. Install the dependencies from `requirements.txt`.
2. Apply committed migrations.
3. Ensure the exact demo `Organization` exists and is not `Default Organization`.
4. Ensure an existing user has an `OrganizationMembership` for that organization.
5. Know the username and separately managed password for that user.
6. Export both demo-scaffolding environment variables.
7. Explicitly classify the organization as `canonical_demo` using the guarded
   command below.

Initial confirmed apply can create the canonical locations, certifications,
activities, cohorts, instructors, schedule, participation exception,
availability exception, and stored generated schedule. It cannot create the
organization, user, membership, or credentials.

Reset is stricter: the canonical organization, membership, and `Demo Program
Week` schedule must already exist. Use confirmed apply—not reset—for first-time
setup.

## Account and membership provisioning

The project has no normal operator-facing membership-provisioning workflow.
`Organization` and `OrganizationMembership` are registered in Django admin, as
is Django's built-in User model.

An existing staff administrator can provision a demo operator as follows:

1. Open `/admin/` and log in with an existing authorized staff account.
2. Under Django's authentication section, add a User using a password obtained
   through the environment's approved secret-sharing procedure.
3. Leave the demo operator as a non-superuser and non-staff user unless a
   separately approved demonstration requires admin access.
4. Under **Members → Organizations**, create or verify the exact configured
   organization.
5. Under **Members → Organization memberships**, add one membership connecting
   the new User to that organization.
6. Verify that the same User is not already connected to another organization;
   the current membership model permits one organization per user.

If there is no authorized staff account, Django's standard interactive
`createsuperuser` command can establish an administrator according to the
environment's normal administration policy. It is not part of the demo
scaffolding, and its credentials must remain outside source control.

The scaffolding never creates users or memberships, chooses or changes
passwords, or attaches an arbitrary user automatically.

## Environment configuration

From the repository root, enter the Django project directory and configure the
exact intended environment:

```bash
cd scheduler_project
export DEMO_SCAFFOLDING_ENABLED=true
export DEMO_ORGANIZATION_IDENTIFIER="Configured Demo Organization"
```

`DEMO_SCAFFOLDING_ENABLED` is false by default. Accepted enabled values in the
current settings are `1`, `true`, `yes`, or `on`, case-insensitively.

The identifier supplied to a command must exactly equal
`DEMO_ORGANIZATION_IDENTIFIER`, and the resolved organization name must exactly
equal it. Partial matching is not supported. `Default Organization` is always
refused.

Scope these variables to the intended shell, process supervisor, or controlled
deployment environment. Do not place environment-specific credentials beside
them in source control.

## Local and hosted settings

Local development continues to use:

```text
scheduler_project.settings
```

It retains `DEBUG=True`, SQLite, plain-HTTP cookies, and enabled anonymous demo
entry for practical local work. These defaults are not suitable for hosting.

A private hosted process must deliberately select:

```bash
export DJANGO_SETTINGS_MODULE=scheduler_project.settings_hosted
```

The hosted module fails during startup unless all required values are valid:

```text
DJANGO_SECRET_KEY
DJANGO_ALLOWED_HOSTS
DJANGO_CSRF_TRUSTED_ORIGINS
POSTGRES_DB
POSTGRES_HOST
POSTGRES_USER
POSTGRES_PASSWORD
```

`DJANGO_ALLOWED_HOSTS` and `DJANGO_CSRF_TRUSTED_ORIGINS` are comma-delimited
lists of exact values. Trusted origins must use HTTPS and match an allowed
host. Hosted settings do not add localhost or accept wildcards.

Optional hosted controls are:

```text
POSTGRES_PORT                         default: 5432
POSTGRES_SSLMODE                      default: require
DJANGO_TRUST_PROXY_SSL_HEADER         default: false
DJANGO_SECURE_HSTS_SECONDS            default: 3600
DJANGO_DATA_UPLOAD_MAX_MEMORY_SIZE    default: 1048576
DEMO_ENTRY_ENABLED                    default: false
```

Boolean values accept only `1`, `true`, `yes`, `on`, `0`, `false`, `no`, or
`off`, ignoring case. Never store real secret or database values in this
runbook or source control.

Hosted settings require PostgreSQL configuration and the declared
`psycopg[binary]` driver. Selecting hosted settings does not create a database,
transfer SQLite data, connect during settings validation, or apply migrations.

HTTPS redirects, secure session and CSRF cookies, frame denial, content-type
nosniff, a same-origin referrer policy, a 1 MiB request-memory boundary, and a
hosted `STATIC_ROOT` are explicit. Proxy SSL-header trust is absent unless
`DJANGO_TRUST_PROXY_SSL_HEADER=true` is deliberately supplied.

The initial HSTS duration is one hour without subdomain inclusion or preload.
Increase it only after HTTPS and proxy behavior have been verified in the
hosted environment.

### Emergency anonymous-entry disable

Hosted entry is disabled by default. To permit invited visitors to create clean
or prepared sessions, explicitly set:

```bash
export DEMO_ENTRY_ENABLED=true
```

To stop new anonymous sessions during maintenance, capacity pressure, or an
incident:

```bash
export DEMO_ENTRY_ENABLED=false
```

Restart the application process so settings reload. The public demo landing
remains reachable and reports temporary unavailability, but it offers no start
forms. Both entry POST endpoints refuse before provisioning and create no
ownership or operational records.

Disabling entry does not expire or log out existing valid temporary visitors.
Prepared reset, customer and canonical workflows, expiration enforcement, and
the cleanup command remain available.

### Private-beta capacity and throttling

Hosted settings provide these bounded defaults:

```text
DEMO_MAX_ACTIVE_SESSIONS                    10
DEMO_MAX_ACTIVE_PREPARED_SESSIONS           4
DEMO_MAX_ACTIVE_CLEAN_SESSIONS              6
DEMO_GLOBAL_START_LIMIT                     12
DEMO_GLOBAL_START_WINDOW_SECONDS            3600
DEMO_CLIENT_START_LIMIT                     3
DEMO_CLIENT_START_WINDOW_SECONDS            900
DEMO_MAX_CONCURRENT_PREPARED_OPERATIONS     1
DEMO_PREPARED_RESET_LIMIT                   6
DEMO_PREPARED_RESET_WINDOW_SECONDS          3600
DEMO_CAPACITY_RESERVATION_SECONDS           600
DEMO_PREPARED_OPERATION_LEASE_SECONDS       600
```

Each name is also the hosted environment-variable name. Values must be positive
integers. Clean and prepared active caps cannot exceed the global cap. Changed
values require an application restart.

`provisioning` and unexpired `active` temporary sessions count toward capacity;
expired, `expiring`, `deleting`, and `failed` sessions do not. Short-lived
durable reservations close the count-and-create race and are included in both
global and per-mode counts.

Anonymous POST attempts that reach admission consume global and hashed-client
window capacity whether later provisioning succeeds or fails. CSRF rejection,
disabled entry, and authenticated-user refusal occur before anonymous
admission. Raw client addresses, request bodies, cookies, and authentication
material are not stored. Without explicit proxy SSL trust, the client key uses
`REMOTE_ADDR` and ignores forwarded addresses.

Prepared provisioning and prepared reset share durable ten-minute operation
leases. Normal completion or failure releases the lease immediately; stale
leases and reservations are reclaimed during later admission. Clean
provisioning does not acquire the prepared lease.

Database persistence is required because process-local counters cannot
coordinate multiple WSGI workers. Provisioning attempts and prepared-reset
attempts are retained for their bounded-window queries; scheduled pruning
remains future maintenance work.

`DEMO_ENTRY_ENABLED=false` takes precedence over all capacity machinery: it
creates no attempt, reservation, or lease record.

Production server configuration, static serving middleware, private access
control, PostgreSQL migration/transfer, historical-attempt pruning, and
scheduled cleanup remain separate deployment work.

## One-time canonical classification

Before initial inspection or confirmed setup, classify the exact configured
organization once:

```bash
python manage.py classify_canonical_demo \
  --organization "Configured Demo Organization" \
  --confirm
```

The command supports only `customer` → `canonical_demo`, with a safe no-op when
the organization is already canonical. It requires an exact match among the
command argument, configured identifier, and stored organization name. It
refuses `Default Organization`, temporary demo organizations, missing
organizations, organizations without an existing membership, and organizations
owned by a `DemoSession`.

Classification changes only `Organization.purpose`. It does not create
organizations, users, credentials, memberships, schedules, operational records,
or demo data. It does not run setup or reset. Canonical inspection, confirmed
setup/apply, and reset all require this classification afterward.

## Initial setup

### 1. Inspect without writing

```bash
python manage.py inspect_demo_environment \
  --organization "Configured Demo Organization"
```

Expected success:

```text
Safe target inspected; no changes were made.
```

Review all `CREATE`, `UPDATE`, `RECONCILE`, `RESTORE`, `WARNINGS`, and `BLOCKERS`
sections. Inspection is read-only and does not normalize stored JSON.

### 2. Apply the canonical scenario

Only after reviewing the inspection plan:

```bash
python manage.py inspect_demo_environment \
  --organization "Configured Demo Organization" \
  --apply \
  --confirm
```

Expected success:

```text
Stable demo reference data applied atomically.
```

Confirmed apply may:

- create or reconcile canonical reference records and relationships
- create the canonical schedule during first-time setup
- reconcile participation and availability
- generate and store the schedule through the normal model lifecycle
- clear dirty manual moves and instructor-override state when regeneration is
  required
- validate operational replay and automatic assignments

It does not create the organization, account, membership, or credentials; delete
unrelated records; or reset another schedule.

### 3. Inspect again

```bash
python manage.py inspect_demo_environment \
  --organization "Configured Demo Organization"
```

Confirm that the report includes the canonical starting state as valid and that
stable records, staffing state, generated output, and clean operational state
are classified as unchanged rather than awaiting restoration.

### 4. Verify through the normal interface

1. Start the application through the normal local or controlled-environment
   procedure.
2. Open `/login/` and use the separately provisioned demo operator account.
3. Confirm the **Operational Dashboard** loads.
4. Open **Schedules**, then **Demo Program Week**.
5. Confirm the generated schedule and four activity groups render.
6. Open **Manage Instructor Participation**, **Manage Detailed Availability
   Exceptions**, and **View Instructor Assignment Schedule**.

Do not use the **Generate Schedule** button merely as a verification step; the
confirmed setup command has already generated and validated the baseline.

## Canonical starting-state contract

The expected baseline is:

- Organization: the exact environment-configured fictional organization
- Locations:
  - `Demo Commons`
  - `Demo Field`
  - `Demo Studio`
  - `Demo Workshop`
- Activities:
  - `Demo Navigation`
  - `Demo Creative Lab`
  - `Demo Team Challenge`
  - `Demo Technical Course`
  - `Demo Evening Program`
- Cohorts:
  - `Demo Cohort North`, two groups
  - `Demo Cohort South`, two groups
- Instructors:
  - `Alex Demo`
  - `Blair Demo`
  - `Casey Demo`
  - `Devon Demo`
  - `Emery Demo`
- Certifications:
  - `Demo Field Safety`
  - `Demo Technical Skills`
- Qualification-sensitive activity: `Demo Technical Course`, requiring
  `Demo Technical Skills`
- Eligible manual alternate: `Blair Demo`
- Participation: Alex, Blair, Casey, and Devon participate normally; `Emery
  Demo` is opted out
- Availability: `Casey Demo` is unavailable at `tue_am1`
- Schedule: `Demo Program Week`, with four groups and stored complete output
- Operational state:
  - `manual_moves = []`
  - `manual_instructor_overrides = []`
  - `instructor_override_revision = 0`
  - no replay conflicts, ignored overrides, or holding-area items
  - complete, valid automatic instructor assignments

Automatic assignments are calculated when viewed; the complete automatic plan
is not persisted as a separate database record.

## Post-demonstration reset

### 1. Inspect the drift

```bash
python manage.py inspect_demo_environment \
  --organization "Configured Demo Organization"
```

Review the reported stable drift, relationship drift, participation,
availability, generated state, manual moves, and instructor overrides.

### 2. Run the explicit reset

```bash
python manage.py reset_demo_environment \
  --organization "Configured Demo Organization" \
  --confirm
```

Expected changed-state success:

```text
Demo environment restored to the canonical starting state.
```

Expected no-op success:

```text
Demo environment already matches the canonical starting state.
```

Reset conditionally regenerates `Demo Program Week` when its generated or
operational state is dirty or invalid. It does not regenerate an already valid
baseline merely because reset was invoked.

Reset does not affect unrelated schedules, remove unrelated records, recreate
the organization, modify credentials, or reset the whole database.

### 3. Verify reset

Run inspection again:

```bash
python manage.py inspect_demo_environment \
  --organization "Configured Demo Organization"
```

Then reload **Demo Program Week** and **View Instructor Assignment Schedule** in
the application. Confirm there are no persisted manual activity moves or saved
manual instructor assignments and that staffing is complete.

## Failure recovery

All confirmed setup and reset writes run atomically. A reported generation,
acceptance, or assignment failure rolls back that command's changes. Correct the
underlying condition and inspect again; do not routinely delete the database.

| Failure | Meaning and write status | Correct response |
| --- | --- | --- |
| `Demo scaffolding is disabled.` | No writes; the explicit enable setting is false or absent. | Export `DEMO_SCAFFOLDING_ENABLED=true`, then inspect again. |
| `No allowed demo organization identifier is configured.` | No writes; the identifier is blank. | Set the exact nonblank `DEMO_ORGANIZATION_IDENTIFIER`. |
| Requested organization does not exactly match | No writes; command and setting disagree. | Correct the typo or environment selection. Never weaken exact matching. |
| `Default Organization can never be targeted...` | No writes; the compatibility fallback is prohibited. | Create or select a dedicated fictional organization and membership. |
| Configured organization does not exist | No writes. | Have an administrator create the exact organization, attach the approved membership, then inspect. |
| Existing organization membership required | No writes. | Use Django admin to attach the intended existing non-superuser account. |
| Reset requires the existing canonical demo schedule | No reset writes. | Run read-only inspection, then confirmed setup/apply to create the initial scenario. |
| Inspection blockers | No confirmed operation should proceed. | Read each blocker, correct ownership or ambiguous data, and rerun inspection. |
| Generation failure | The complete confirmed transaction is rolled back; diagnostics identify scheduling problems. | Review canonical location/activity/cohort relationships, then inspect and retry. Do not fabricate JSON. |
| Canonical starting-state acceptance failure | The transaction is rolled back. | Correct the reported group, slot, activity, replay, or operational-state condition; inspect and retry. |
| Automatic assignment validation failure | The transaction is rolled back. | Review participation, availability, qualifications, and single-instructor requirements; inspect and retry. |

Integrity or model-validation errors are deliberately not suppressed. If a
canonical natural key is ambiguous or corrupted, stop and investigate rather
than deleting records or bypassing safeguards.

## Explicit temporary visitor exit

Valid clean and prepared visitors can use **Exit demo** in the authenticated
workspace navigation. The control sends a CSRF-protected
`POST /demo/exit/` request and requires the exact `confirm_exit=yes`
confirmation. It contains no ownership identifiers.

For an active session, exit changes `active` to `expiring` and shortens
`expires_at` to the earlier of its existing value and trusted server time. It
never extends expiration or changes `last_activity_at`, mode, scenario version,
ownership, or operational records. An already-expiring session is an
idempotent no-op. Failed, provisioning, and deleting sessions are not
reclassified, but a browser that still appears tied to temporary ownership is
logged out safely.

Exit then invalidates browser authentication, redirects to `/demo/`, and
reports that retained temporary data will be removed shortly. It does not
delete data synchronously or call cleanup. The next separately operated cleanup
run can classify the shortened expiration as eligible. Exit remains available
when `DEMO_ENTRY_ENABLED` is false and consumes no provisioning throttle,
capacity reservation, prepared-operation lease, or reset allowance.

These operations are intentionally distinct:

- Normal logout ends browser authentication but does not change demo lifecycle.
- Prepared reset retains authentication and restores prepared scenario data.
- Exit demo ends authentication and marks temporary ownership for later cleanup.
- Cleanup is the guarded operator process that eventually deletes eligible
  temporary ownership and operational data.

## Recurring demo maintenance boundary

`run_demo_maintenance` is the single bounded command intended for a future
private-hosted scheduler. It remains dry-run-only unless explicitly confirmed:

```bash
python manage.py run_demo_maintenance
python manage.py run_demo_maintenance --confirm
python manage.py run_demo_maintenance \
  --confirm \
  --cleanup-limit 25 \
  --attempt-limit 500 \
  --auxiliary-limit 100
```

An optional `--before` value accepts a nonfuture, timezone-aware ISO-8601
ownership-cleanup cutoff. There is no force or unlimited mode.

One confirmed cycle acquires a distinct, expiring `demo_maintenance` database
lease and then independently:

1. invokes the existing guarded temporary-ownership cleanup service;
2. prunes only expired capacity reservations and operation leases;
3. prunes provisioning and reset attempts older than the configured retention;
4. invokes Django's supported expired framework-session cleanup; and
5. reports category results and remaining backlog before releasing the lease.

The initial retention is seven days and must remain longer than every active
start/reset throttle window. Initial limits are 25 ownership inspections, 500
rows per attempt model, and 100 rows per auxiliary category. Attempt and
auxiliary pruning is deterministic and oldest-first. Active reservations,
prepared-operation leases, recent throttle attempts, and unexpired Django
sessions remain untouched.

`DemoSession` represents application ownership; Django session rows represent
browser authentication. Maintenance never decodes session contents or infers
ownership from them. Either record type may briefly exist without the other,
and clearing an expired browser session is not proof that application ownership
was removed.

Command exit behavior is:

- `0`: successful dry run or confirmed cycle, including remaining backlog;
- `1`: one or more independently reported execution-category failures;
- `2` through Django's command-error handling: invalid arguments or an
  overlapping confirmed run.

For future private hosting, first run dry mode manually and review every
category. After PostgreSQL is in use, configure exactly one external scheduler
instance to run:

```bash
python manage.py run_demo_maintenance --confirm
```

Recommended cadence: every 15 minutes. Use platform overlap prevention where
available; the application lease is secondary protection. Alert on nonzero
exit, persistent backlog, and recurring blocked or failed ownership. Never run
maintenance from web startup, middleware, or visitor logout. No scheduler or
provider manifest is configured by this project.

## Private-hosted runtime and release checks

Install the pinned production dependencies with the rest of
`requirements.txt`. The hosted process uses Gunicorn's synchronous WSGI worker
against the existing application target:

```bash
DJANGO_SETTINGS_MODULE=scheduler_project.settings_hosted \
gunicorn scheduler_project.wsgi:application \
  --bind 0.0.0.0:${PORT:-8000} \
  --workers 2 \
  --worker-class sync \
  --timeout 120 \
  --graceful-timeout 30 \
  --access-logfile - \
  --error-logfile -
```

The initial private-beta recommendation is two synchronous workers, one thread
per worker, one web process type, standard output/error logging, no preload, and
no autoscaling assumption. The 120-second timeout is provisional: benchmark
prepared provisioning and reset in the representative hosted environment,
measure their high-percentile duration, and set a conservative margin before
launch. Maintenance must remain a separate externally invoked command; it is
never started by WSGI import or Gunicorn hooks.

Hosted settings place WhiteNoise immediately after Django
`SecurityMiddleware` and use
`whitenoise.storage.CompressedManifestStaticFilesStorage`. Build the collected
static directory before starting the web process:

```bash
DJANGO_SETTINGS_MODULE=scheduler_project.settings_hosted \
python manage.py collectstatic --noinput
```

WhiteNoise serves only collected static assets from `STATIC_ROOT`; it does not
serve uploaded media. Existing CDN-loaded Bootstrap remains unchanged.

Two exact public health routes are available:

- `GET` or `HEAD /health/live/` returns `{"status":"ok"}` without database,
  cache, ownership, or capacity work.
- `GET` or `HEAD /health/ready/` returns `{"status":"ready"}` only when the
  database is reachable, no migration is pending, and the capacity-coordinator
  schema can be queried. Failure returns HTTP 503 with
  `{"status":"not_ready"}`.

Health responses intentionally omit hosts, database names, migration names,
counts, exception details, credentials, tokens, and release hashes. Poll
readiness at a normal hosting-health cadence; it performs migration-state
queries on each request and is deliberately not cached yet.

Before releasing, select hosted settings and run:

```bash
DJANGO_SETTINGS_MODULE=scheduler_project.settings_hosted \
python manage.py check_hosted_release
```

The command is read-only. It checks hosted security configuration, PostgreSQL
selection and connectivity, migration state, WhiteNoise/static configuration,
the Gunicorn dependency, Django system/deployment checks, and demo capacity and
maintenance settings. It reports `DEMO_ENTRY_ENABLED` prominently but does not
change it; both enabled and disabled states may pass, with disabled remaining
the fail-closed hosted default.

The cautious initial HSTS policy accepts exactly `security.W005` and
`security.W021`: subdomain inclusion and browser preload remain disabled until
the complete private-hosted domain boundary is known. Any other deployment
warning fails the release check.

Release sequencing remains explicit:

1. install dependencies;
2. select and validate hosted settings;
3. apply reviewed migrations through a separate operator-controlled step;
4. run `check_hosted_release`;
5. run `collectstatic --noinput`; and
6. start Gunicorn.

The release check never runs migrations, collects static files, starts
Gunicorn, provisions demos, or invokes maintenance. No provider manifest,
external access gate, PostgreSQL server, scheduler, monitoring, alerting, or
backup system is configured here.

## Expired isolated-session cleanup

Cleanup is an explicit operator procedure. It is not scheduled automatically.
Run it from the same configured application environment used to provision
isolated demo sessions.

Preview the oldest 25 sessions at or before the current time:

```bash
python manage.py cleanup_demo_sessions
```

The command is dry-run by default. Its plan reports each session identifier,
expiration, status, safe ownership identifiers, eligibility decision, and
organization-scoped record counts. It does not print credentials or session
secrets.

Use a bounded batch or an exact UTC cutoff when investigating:

```bash
python manage.py cleanup_demo_sessions --limit 10
python manage.py cleanup_demo_sessions --before 2026-07-25T16:00:00+00:00
```

Target one session for a narrow retry:

```bash
python manage.py cleanup_demo_sessions \
  --session-id 00000000-0000-0000-0000-000000000000
```

After reviewing the plan, repeat the same command with `--confirm` to delete
eligible sessions:

```bash
python manage.py cleanup_demo_sessions --limit 10 --confirm
```

A session is eligible only when all of these conditions hold:

- its expiration is at or before the cutoff;
- its status is `active`, `expiring`, `failed`, or `deleting`;
- its organization purpose is `temporary_demo`;
- its user is neither staff nor superuser; and
- the temporary user, organization, membership, and demo-session ownership
  counts form one isolated unit.

`provisioning` sessions are blocked rather than deleted. Customer,
`canonical_demo`, and `Default Organization` data are also blocked regardless
of age or name similarity. There is no force option that bypasses these
protections.

Each eligible session is cleaned in its own database transaction. Dependent
schedule, school, instructor, activity, and reference records are removed
before membership, session, organization, and user ownership records. A failure
rolls back that session, records sanitized retry metadata on its `DemoSession`,
and does not prevent later sessions in the batch from being attempted.
Partially cleaned `deleting` sessions may be retried with the targeted command.
Repeated dry runs and confirmed runs are safe; already deleted sessions do not
reappear.

This operation does not scan, decode, or delete Django session-table rows and
never handles browser session keys. An old cookie for a deleted user can no
longer authenticate that user. Run Django's separate `clearsessions` command
for routine removal of expired framework-session rows; it is not a substitute
for ownership cleanup. Neither procedure should involve routine whole-database
deletion.

For routine operations, begin with a small dry-run batch, retain the command
output, review every blocked or unexpected record count, and only then run the
identical confirmed command. If the output says more sessions remain, repeat
the dry-run/confirm cycle. Scheduling this command is deliberately outside the
current scope.

## Prepared visitor reset

An active prepared visitor can reset their own isolated environment from the
canonical prepared schedule page. The control submits a CSRF-protected POST and
requires confirmation that changes inside the temporary demo will be removed.

Reset restores the original prepared example while preserving the visitor's
temporary user, organization, membership, DemoSession identity, prepared mode,
scenario version, and absolute expiration. It cannot target canonical or
customer data, another visitor, a clean demo, or an expired session.

## Known limitations

- One explicitly configured canonical demo organization and schedule
- One organization per user; no organization switching
- No automatic account or membership provisioning
- No public or anonymous demo configuration
- No per-visitor clone or browser-session isolation
- No web setup or reset control
- No automatic expired-session cleanup scheduler
- No persisted complete automatic-assignment plan
- One required instructor per activity occurrence in the current assignment
  pipeline
- Exact placements may change with current scheduling logic
- Reset is canonical-scenario-specific, not general-purpose

## Render Blueprint review

The repository-root `render.yaml` is a reviewable Render-side contract for the
first private beta. It declares exactly one paid Python web service, one paid
PostgreSQL database in the same region, and one paid cron job scheduled every
15 minutes. Automatic deploys are disabled so releases remain deliberate.

Render runs from the repository root. The web service:

1. installs `requirements.txt` and runs
   `python scheduler_project/manage.py collectstatic --noinput` during build;
2. runs `migrate --noinput` and then `check_hosted_release` during pre-deploy;
3. starts Gunicorn against `scheduler_project.wsgi:application`; and
4. uses `/health/ready/` as its traffic-readiness check.

The cron job installs the same requirements and runs only:

```bash
python scheduler_project/manage.py run_demo_maintenance --confirm
```

The manifest selects hosted settings, trusts Render's HTTPS proxy header, keeps
`DEMO_ENTRY_ENABLED=false`, and records the approved capacity, throttle, lease,
and maintenance limits. Its 120-second Gunicorn timeout is provisional and
must be benchmarked with representative prepared provisioning and reset work
before testers are admitted.

Render Blueprint database references provide the database name, user, and
password without committing credentials. Render's documented Blueprint
properties do not expose the database's discrete internal hostname, so
`POSTGRES_HOST` is an unsynced operator value; `POSTGRES_PORT` is the standard
PostgreSQL port `5432`. Supply the database's internal hostname, not an external
endpoint.

During initial Blueprint creation, the operator must also supply the same
values to both the web and cron services for:

- `DJANGO_SECRET_KEY`
- `DJANGO_ALLOWED_HOSTS`
- `DJANGO_CSRF_TRUSTED_ORIGINS`
- `DEMO_ORGANIZATION_IDENTIFIER`
- `POSTGRES_HOST`

For example, after a real hostname is approved,
`DJANGO_ALLOWED_HOSTS` contains only that hostname and
`DJANGO_CSRF_TRUSTED_ORIGINS` contains its exact `https://` origin. These are
examples for operator review, not active values in the manifest.

The Blueprint deliberately does not declare a branch, custom domain,
Cloudflare configuration, DNS, certificates, staff credentials, canonical
data, backup policy, alerts, or entry enablement. Select the protected
deployment branch during Blueprint creation. The generated Render hostname is
not a tester URL: configure the custom domain and Cloudflare Access manually,
verify health behavior, and disable direct `onrender.com` access before
enabling demo entry.

Committing or reviewing the manifest creates no infrastructure. Validate it in
Render before approving a Blueprint sync, confirm the current paid plan and
backup characteristics, and keep demo entry disabled throughout provisioning,
migration, canonical bootstrap, access-gate validation, and restore testing.

For the ordered provider-creation, access-gate, canonical bootstrap, recovery,
and tester-admission procedure, use the
[private beta launch runbook](private-beta-launch-runbook.md).

## Temporary-data disclosure

The public `/demo/` landing page displays an unavoidable informational notice
before all three entry choices. It identifies the workspace as temporary and for
testing and evaluation, warns against real or sensitive information, and
explains that demo data expires and is removed through scheduled maintenance.
The notice remains visible when new demo entry is disabled; it is not a consent
checkbox and creates no acceptance record.

Before private-beta admission, verify the deployed notice is visible to an
anonymous visitor, appears before the clean and prepared controls, remains
visible beside the temporary-unavailability message, and has not been hidden or
weakened by provider configuration or a later template change.

## Realistic scenario source-copy boundary

`Default Organization` is protected legacy ownership and cannot be used
directly as a prepared-scenario export source. Anonymous provisioning must
never query it, and its records must not be moved, reassigned, renamed, or
deleted to support a demo.

The guarded operator command below plans a one-way deep copy into the named,
permanent customer organization intended for later `working-v1` extraction:

```bash
python manage.py copy_default_dataset_to_organization \
  --target-organization "Realistic Demo Source"
```

Dry run is the default. It reports included record and relationship counts,
schedule source states, normalized target schedule-state policy, excluded
account/lifecycle categories, warnings, blockers, and expected mutations. It
does not create the target or modify either organization.

After separate operator review and approval, confirmed syntax is:

```bash
python manage.py copy_default_dataset_to_organization \
  --target-organization "Realistic Demo Source" \
  --confirm
```

Confirmed execution uses one atomic transaction. It locks and revalidates
`Default Organization`, creates or uses one empty permanent customer target,
creates every target record with a new primary key, rebuilds relationships
through explicit source-to-target maps, and verifies the complete normalized
target graph before commit. It compares the protected source graph and purpose
before and after construction; any source change or target mismatch rolls back
the complete target.

Schedule names, dates, cohort selections, and other declarative inputs are
copied. Target `sched_data` is cleared rather than copying generated output,
manual moves, instructor overrides, or source occurrence/object references.
Schedules are not regenerated by this command. Complete and input-infeasible
source schedules remain represented as target schedule inputs and may be
processed later by the separately approved scenario extraction workflow.

Users, credentials, memberships, Django sessions, `DemoSession` ownership,
capacity and attempt records, leases, cleanup metadata, and admin logs are
excluded. The command never chooses an operator account. Add a target
membership manually through the approved admin boundary only if ordinary UI
inspection is needed.

An existing exact normalized copy is a safe no-op. An existing partial,
populated, protected-purpose, or drifted target is refused; there is no force,
merge, overwrite, or reconciliation mode.

The named target remains development-only extraction input. The committed
`working-v1` artifact is the hosted provisioning and reset source.
## Repository-owned prepared scenarios

## Initial free validation hosting

The initial tester deployment uses one Render Free web service connected by a
manually supplied `DATABASE_URL` to External free PostgreSQL. It declares no
Render database and no cron resource. Automatic maintenance is not running;
operators must initially run `run_demo_maintenance --confirm` manually.
Automated scheduling can be added after validation or revenue, and this
temporary limitation is accepted for the small private tester cohort.

Render Free sleeps when idle. Testers should expect sleep and cold-start delays.
This is a zero-cost validation deployment, not the final production
infrastructure.

The demo entry page offers three isolated temporary paths:

- `canonical-v1` is the smaller guided example.
- `working-v1` is the realistic working example with seven schedules.
- Clean demo starts with no scheduling records.

Some `working-v1` schedules intentionally demonstrate controlled
infeasibility diagnostics. Both prepared scenarios are committed repository
data; hosted provisioning and reset do not read `Realistic Demo Source`,
`Default Organization`, a development database, or local SQLite. Developers
may run `python manage.py extract_working_demo_scenario` against the protected
source organization to validate extraction reference counts; hosted runtime
does not run that command.
