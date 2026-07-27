# Private Beta Launch Runbook: Render and Cloudflare

## Initial zero-cost validation deployment

This temporary validation deployment supersedes the paid Render resource
instructions elsewhere in this runbook:

- One Render Free web service is declared by `render.yaml`.
- External free PostgreSQL supplies `<external-database-url>` through the
  manually entered secret `DATABASE_URL`; no credentials are committed.
- No Render PostgreSQL, cron, worker, or other paid resource is created.
- Render Free does not provide pre-deploy commands, so the web start command is:

  ```bash
  python scheduler_project/manage.py migrate --noinput && python scheduler_project/manage.py check_hosted_release && exec gunicorn --chdir scheduler_project scheduler_project.wsgi:application --bind 0.0.0.0:$PORT --workers 2 --worker-class sync --timeout 120 --graceful-timeout 30 --access-logfile - --error-logfile -
  ```

  The `&&` chain prevents Gunicorn from starting if migrations or release
  validation fail.
- Automatic maintenance is not running in this free deployment. Initially run
  `python scheduler_project/manage.py run_demo_maintenance --confirm` manually.
  Automated scheduling can be added after validation or revenue. This accepted
  limitation is appropriate only for the small initial private tester cohort.
- Free services sleep when idle, so testers should expect sleep-related
  cold-start delays that can approach a minute.

This is validation infrastructure, not the final production architecture.
Paid-plan, Render-database, and cron instructions below are retained only as a
future reference and must not be applied during zero-cost validation.

## Purpose and safety boundary

This is the operator procedure for the first private hosted Pali Scheduler beta:

```text
Cloudflare Access
  → Render web service
  → Gunicorn and Django
  → Render PostgreSQL
  → Render maintenance cron
```

Follow the phases in order. Do not admit testers or set
`DEMO_ENTRY_ENABLED=true` until every required gate is checked. Use only
placeholders in retained notes and screenshots; never paste passwords, access
tokens, session values, database connection strings, or staff credentials into
this document, tickets, chat, or logs.

Conventions used below:

- **Mutation — none:** inspection only.
- **Mutation — infrastructure:** changes provider resources or access policy.
- **Mutation — application data:** changes PostgreSQL records.
- **Mutation — repository:** changes or records source-controlled content.
- **Stop:** do not proceed to the next phase.

## Phase 0 — Assign prerequisites

**Objective:** Ensure every decision and recovery responsibility has an owner
before creating billable or security-sensitive resources.

**Operator actions:**

- [ ] Record the protected deployment branch without assuming its name.
- [ ] Assign the Render account owner.
- [ ] Assign the Cloudflare account and DNS-zone owner.
- [ ] Approve `<beta-hostname>`.
- [ ] Confirm `ohio` as the Render region or stop and review the Blueprint.
- [ ] Approve a paid `starter` web plan.
- [ ] Approve a paid `basic-256mb` PostgreSQL plan, or a reviewed larger plan.
- [ ] Approve a paid `starter` cron plan.
- [ ] Record required PostgreSQL backup retention.
- [ ] Record the approved tester list using a secure operator system.
- [ ] Assign the beta support owner and contact channel.
- [ ] Assign the emergency-disable operator and backup operator.
- [ ] Approve `<canonical-organization-name>`.
- [ ] Assign the staff account owner.
- [ ] Assign the restore-test owner.

**Expected result:** Every item has a named owner and approved value in the
operator's secure launch record.

**Stop:** Any owner, hostname, plan, retention requirement, branch, or canonical
organization decision is missing.

**Recovery:** Assign the missing responsibility or obtain the missing decision;
do not use temporary personal ownership as an undocumented substitute.

**Mutation:** None.

## Phase 1 — Prepare the repository release

**Objective:** Produce one reviewed, reproducible release commit with no secrets
or unintended files.

**Operator actions:**

1. Inspect the complete migration set, including the cumulative demo capacity
   migration:

   ```bash
   git status --short
   git diff -- scheduler_project/members/migrations/
   ```

2. Review every intended and unintended worktree change:

   ```bash
   git diff
   git diff --cached
   ```

3. Inspect the Blueprint for secrets, credentials, real hostnames, provider
   identifiers, and unexpected resources:

   ```bash
   sed -n '1,260p' render.yaml
   ```

4. Run Blueprint and launch-runbook consistency tests:

   ```bash
   .venv/bin/python scheduler_project/manage.py test \
     scheduler_project.test_render_blueprint \
     scheduler_project.test_private_beta_launch_runbook
   ```

5. Run the complete discovered suite:

   ```bash
   .venv/bin/python scheduler_project/manage.py test \
     scheduler_project scheduler_app members
   ```

6. Run migration, Django, and whitespace checks:

   ```bash
   .venv/bin/python scheduler_project/manage.py makemigrations --check
   .venv/bin/python scheduler_project/manage.py check
   git diff --check
   ```

7. Stage only reviewed release files, inspect the staged diff, then commit:

   ```bash
   git add <reviewed-release-files>
   git diff --cached
   git commit -m "Prepare private beta release"
   ```

8. Record the immutable release commit:

   ```bash
   git rev-parse HEAD
   git show --stat --oneline HEAD
   ```

   Create a tag only if the repository's approved release procedure requires
   one; do not invent a tag or deployment branch convention here.

**Expected result:** All tests and checks pass, the intended release is one
reviewed commit, and its hash is recorded.

**Stop:** Tests fail; migrations are unexpectedly generated; secrets appear;
the worktree contains unexplained changes; or staged content differs from the
reviewed release.

**Recovery:** Unstage only the unintended paths, correct the narrow issue, rerun
all checks, and review again. Do not discard unrelated user work.

**Mutation:** Repository only when staging, committing, or tagging.

## Phase 2 — Review and create the Render Blueprint

**Objective:** Create exactly the reviewed Render contract and no unexpected or
free resources.

**Operator actions:**

1. In the Render dashboard, start a new Blueprint and connect the intended
   repository.
2. Select the protected deployment branch recorded in Phase 0.
3. Select the repository-root `render.yaml`.
4. Before approving creation, verify the proposed resource list:

   - [ ] One `starter` Python web service: `pali-sched-beta-web`.
   - [ ] One `basic-256mb` PostgreSQL database:
     `pali-sched-beta-db`.
   - [ ] One `starter` Python cron job:
     `pali-sched-beta-maintenance`.
   - [ ] Every resource is in `ohio`.
   - [ ] No free plan, preview database, worker, Redis, or extra service.
   - [ ] `autoDeployTrigger` is `off` for web and cron.
   - [ ] Health path is `/health/ready/`.
   - [ ] Database public `ipAllowList` is empty.
   - [ ] Build, pre-deploy, start, schedule, and cron commands exactly match
     Phase 4 and Phase 11.
   - [ ] Both services show `DEMO_ENTRY_ENABLED=false`.

5. Review the estimated recurring charge and backup characteristics.
6. Only after the review passes, approve Blueprint creation/sync deliberately.

**Expected result:** Render proposes and then creates only the three approved
paid resources. Demo entry remains disabled.

**Stop:** Render proposes a free plan, unexpected resource, different region,
automatic deploy, public database access, altered command, or enabled entry.

**Recovery:** Cancel before creation, correct and review `render.yaml`, rerun
tests, record a new release commit, and restart this phase. If creation already
occurred, record billable resources and review them individually before making
changes; do not assume sync is cost-free or automatically reversible.

**Mutation:** Infrastructure when Blueprint creation/sync is approved.

## Phase 3 — Configure environment and secrets

**Objective:** Give web and cron consistent hosted configuration without
exposing secrets.

### Required environment inventory

| Setting | Service | Kind | Source | Initial policy | Verification |
|---|---|---|---|---|---|
| `DJANGO_SETTINGS_MODULE` | Both | Config | Blueprint | `scheduler_project.settings_hosted` | Compare both services |
| `DJANGO_SECRET_KEY` | Both | Secret | Operator secret store | Same strong value; never display | Presence only; compare securely |
| `DJANGO_ALLOWED_HOSTS` | Both | Config | Operator | Exact `<beta-hostname>` | Hosted import/release check |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | Both | Config | Operator | Exact `https://<beta-hostname>` | Hosted import/release check |
| `DJANGO_TRUST_PROXY_SSL_HEADER` | Both | Config | Blueprint | `true` | Compare service environment |
| `DJANGO_DATABASE_ENGINE` | Both | Config | Blueprint | `django.db.backends.postgresql` | Release check |
| `POSTGRES_DB` | Both | Config | Database binding | Same internal database | Binding inspection |
| `POSTGRES_HOST` | Both | Sensitive config | Operator from Render | `<render-internal-postgres-host>` | Compare without logging |
| `POSTGRES_PORT` | Both | Config | Blueprint | `5432` | Compare service environment |
| `POSTGRES_USER` | Both | Sensitive config | Database binding | Generated Render user | Binding inspection |
| `POSTGRES_PASSWORD` | Both | Secret | Database binding | Generated; never copy | Presence/binding only |
| `POSTGRES_SSLMODE` | Both | Config | Blueprint | `require` | Compare service environment |
| `DEMO_ENTRY_ENABLED` | Both | Control | Blueprint | **`false`** | Release logs and dashboard |
| `DEMO_SCAFFOLDING_ENABLED` | Both | Control | Blueprint | `true` for guarded bootstrap | Compare service environment |
| `DEMO_ORGANIZATION_IDENTIFIER` | Both | Config | Operator | Exact `<canonical-organization-name>` | Classification precondition |
| `DEMO_MAX_ACTIVE_SESSIONS` | Both | Config | Blueprint | `10` | Compare manifest/dashboard |
| `DEMO_MAX_ACTIVE_PREPARED_SESSIONS` | Both | Config | Blueprint | `4` | Compare manifest/dashboard |
| `DEMO_MAX_ACTIVE_CLEAN_SESSIONS` | Both | Config | Blueprint | `6` | Compare manifest/dashboard |
| `DEMO_GLOBAL_START_LIMIT` | Both | Config | Blueprint | `12` | Compare manifest/dashboard |
| `DEMO_GLOBAL_START_WINDOW_SECONDS` | Both | Config | Blueprint | `3600` | Compare manifest/dashboard |
| `DEMO_CLIENT_START_LIMIT` | Both | Config | Blueprint | `3` | Compare manifest/dashboard |
| `DEMO_CLIENT_START_WINDOW_SECONDS` | Both | Config | Blueprint | `900` | Compare manifest/dashboard |
| `DEMO_MAX_CONCURRENT_PREPARED_OPERATIONS` | Both | Config | Blueprint | `1` | Compare manifest/dashboard |
| `DEMO_PREPARED_RESET_LIMIT` | Both | Config | Blueprint | `6` | Compare manifest/dashboard |
| `DEMO_PREPARED_RESET_WINDOW_SECONDS` | Both | Config | Blueprint | `3600` | Compare manifest/dashboard |
| `DEMO_CAPACITY_RESERVATION_SECONDS` | Both | Config | Blueprint | `600` | Compare manifest/dashboard |
| `DEMO_PREPARED_OPERATION_LEASE_SECONDS` | Both | Config | Blueprint | `600` | Compare manifest/dashboard |
| `DEMO_ATTEMPT_RETENTION_DAYS` | Both | Config | Blueprint | `7` | Compare manifest/dashboard |
| `DEMO_MAINTENANCE_LEASE_SECONDS` | Both | Config | Blueprint | `900` | Compare manifest/dashboard |
| `DEMO_MAINTENANCE_CLEANUP_LIMIT` | Both | Config | Blueprint | `25` | Compare manifest/dashboard |
| `DEMO_MAINTENANCE_ATTEMPT_LIMIT` | Both | Config | Blueprint | `500` | Compare manifest/dashboard |
| `DEMO_MAINTENANCE_AUXILIARY_LIMIT` | Both | Config | Blueprint | `100` | Compare manifest/dashboard |
| `DJANGO_SECURE_HSTS_SECONDS` | Web | Config | Blueprint | `3600` | Release check/browser response |
| `DJANGO_DATA_UPLOAD_MAX_MEMORY_SIZE` | Web | Config | Blueprint | `1048576` | Compare service environment |
| `PORT` | Web | Runtime | Render process | Provider-generated | Gunicorn startup log |

**Operator actions:**

1. In the PostgreSQL dashboard, copy only the internal hostname into the
   secure environment editor for both `POSTGRES_HOST` values. Do not record the
   hostname in this runbook or a public ticket.
2. Supply the same `DJANGO_SECRET_KEY` to web and cron through Render's secret
   entry boundary.
3. Supply exact approved host, HTTPS CSRF origin, and canonical organization
   values to both services.
4. Verify all Render database bindings reference `pali-sched-beta-db`.
5. Compare both services against the table without revealing secret values.
6. Confirm `DEMO_ENTRY_ENABLED=false` immediately before saving.

**Expected result:** Both services import hosted settings against the same
private PostgreSQL database. All required values are present and entry is
disabled.

**Stop:** Any required value is absent or inconsistent; an external database
hostname is used; a password appears in documentation or logs; hosts are
wildcarded; CSRF uses HTTP; or entry is enabled.

**Recovery:** Revoke or rotate any exposed secret, correct the environment,
redeploy while entry remains disabled, and rerun release validation.

**Mutation:** Infrastructure configuration; saving environment changes may
trigger a Render deploy.

## Phase 4 — Validate the initial Render deployment

**Objective:** Verify the controlled build, release, runtime, and readiness
sequence.

**Expected Render commands, in order:**

```bash
pip install -r requirements.txt &&
python scheduler_project/manage.py collectstatic --noinput
```

```bash
python scheduler_project/manage.py migrate --noinput &&
python scheduler_project/manage.py check_hosted_release
```

```bash
gunicorn \
  --chdir scheduler_project \
  scheduler_project.wsgi:application \
  --bind 0.0.0.0:$PORT \
  --workers 2 \
  --worker-class sync \
  --timeout 120 \
  --graceful-timeout 30 \
  --access-logfile - \
  --error-logfile -
```

**Operator actions:**

1. Observe build logs: pinned dependencies install, then WhiteNoise static
   collection succeeds. Migrations must not run in build.
2. Observe pre-deploy logs: migrations run first, then
   `check_hosted_release`.
3. Confirm release-check categories report `OK`:

   - Hosted settings
   - Static configuration
   - Runtime server dependency
   - Demo limit configuration
   - Database connectivity
   - Migration state
   - Security checks

4. Confirm `Demo entry: DISABLED` and `Release readiness: PASS`.
5. Observe runtime logs: Gunicorn starts the correct WSGI target with two sync
   workers. Startup must not run migrations or maintenance.
6. Confirm Render reports `/health/ready/` healthy.
7. Inspect logs only in Render's access-controlled dashboard. Search for
   category and status; do not paste or export environment dumps.

**Expected result:** Deployment succeeds, readiness is healthy, and entry stays
disabled.

**Stop:** Dependency/static build fails; migration fails; any release category
fails; startup runs a prohibited command; Gunicorn exits; readiness is 503; or
logs expose secrets.

**Recovery:** Keep entry disabled. Correct the specific build, configuration, or
migration issue; rotate exposed secrets; then deploy deliberately and repeat
the entire phase. Do not make migrations part of web startup.

**Mutation:** Infrastructure and application schema during pre-deploy.

## Phase 5 — Configure the custom domain and HTTPS

**Objective:** Establish the stable HTTPS hostname that Cloudflare Access will
protect.

**Operator actions:**

1. Add `<beta-hostname>` to the Render web service.
2. In Cloudflare DNS, create the Render-required CNAME for
   `<beta-hostname>` pointing to `<render-generated-hostname>`. Follow Render's
   current Cloudflare DNS procedure; use DNS-only while Render validates and
   issues its certificate if required, then enable Cloudflare proxying.
3. Wait until Render reports the custom domain and certificate valid.
4. Verify HTTPS:

   ```bash
   curl -I https://<beta-hostname>/health/live/
   curl -I https://<beta-hostname>/health/ready/
   curl -I http://<beta-hostname>/health/live/
   ```

5. Confirm HTTPS responses are successful and HTTP redirects to HTTPS.
6. Confirm `DJANGO_ALLOWED_HOSTS` is only `<beta-hostname>` and
   `DJANGO_CSRF_TRUSTED_ORIGINS` is only
   `https://<beta-hostname>` unless another origin was separately approved.
7. Through browser developer tools, confirm session and CSRF cookies are
   `Secure`; do not configure a broad cookie domain.
8. Confirm Render readiness remains healthy.

**Expected result:** The exact custom hostname serves valid HTTPS, HTTP
redirects, secure cookies, and healthy readiness.

**Stop:** Certificate warnings, redirect loops, invalid host/CSRF errors,
insecure cookies, wildcard hosts, an HTTP CSRF origin, or failed readiness.

**Recovery:** Keep entry disabled. Revert Cloudflare proxying to the documented
certificate-validation state if necessary, correct DNS/host settings, wait for
certificate issuance, and retest.

**Mutation:** DNS, domain, certificate, and service configuration.

## Phase 6 — Put Cloudflare Access in front of the application

**Objective:** Enforce identity before any denied visitor reaches Django or
anonymous provisioning.

**Operator actions:**

1. In Cloudflare Zero Trust, create a **Self-hosted** Access application for
   the exact `<beta-hostname>` covering all paths.
2. Establish default deny. Add only an allow policy for the securely recorded
   approved-email list. Use `<approved-tester-email>` as the placeholder in
   dry-run screenshots or procedure examples, never a real tester identity.
3. Use Google identity or Cloudflare one-time PIN. Require identity-provider
   MFA where available.
4. Verify coverage includes:

   ```text
   /demo/
   /admin/
   /login/
   /logout/
   /demo/exit/
   /static/
   all operational routes
   ```

5. Do not use a shared query-string secret, hidden URL, Django login, robots
   exclusion, or `DEMO_ENTRY_ENABLED` as the outer access gate.
6. First test Render platform health with both health paths protected. If the
   platform probe cannot authenticate, create Access bypass rules for the two
   exact paths only:

   ```text
   /health/live/
   /health/ready/
   ```

   Do not bypass `/health/*` or another prefix. Cloudflare proxy protections
   remain active, and the responses must remain minimal.
7. In a clean browser context, run the access matrix:

   - [ ] Approved identity reaches the Django landing page.
   - [ ] Unapproved identity is denied by Cloudflare.
   - [ ] A visitor without Access authentication is denied.
   - [ ] Denied requests do not reach Django provisioning logs.
   - [ ] `/admin/` requires both Access and Django staff authentication.
   - [ ] Django logout returns to an Access-protected location and does not
     bypass Access.
   - [ ] Exact health exceptions work only if they were required.

**Expected result:** Every application path is default-denied before Django
except any narrowly approved exact health route.

**Stop:** An unapproved visitor reaches Django; a denied visitor triggers
provisioning; a broad bypass exists; `/admin/` lacks either layer; or logout
opens an unprotected route.

**Recovery:** Restore Cloudflare default deny, remove the faulty allow/bypass
rule, keep demo entry disabled, and repeat the full matrix.

**Mutation:** Cloudflare identity and access infrastructure.

## Phase 7 — Close direct Render-origin bypass

**Objective:** Make Cloudflare Access the only tester-facing route to Django.

**Operator actions:**

1. Confirm the custom hostname works through Cloudflare and passes Phase 6.
2. Record `<render-generated-hostname>` in the secure launch record.
3. From a clean client, test the generated hostname:

   ```bash
   curl -I https://<render-generated-hostname>/
   ```

4. In Render's custom-domain settings, use the supported control to disable the
   service's `onrender.com` subdomain. If managing this later through a reviewed
   Blueprint revision, the supported field is
   `renderSubdomainPolicy: disabled`, which requires an attached custom domain.
5. Confirm direct access is rejected:

   ```bash
   curl -I https://<render-generated-hostname>/
   ```

   Expect Render rejection, not a Django or Cloudflare response.
6. Reconfirm custom-domain Access authentication, application access, and
   `/health/ready/`.
7. Confirm Render continues to report the service healthy.

**Expected result:** The generated Render hostname cannot reach Django, while
the custom hostname and Render health checks still work.

**Stop:** The Render hostname reaches Django, custom-domain traffic bypasses
Access, or disabling the hostname breaks readiness.

**Recovery:** Keep entry disabled and Cloudflare default deny. Correct the
custom-domain or health configuration; do not re-enable tester admission merely
to preserve the origin hostname. Escalate to Render support if platform health
cannot coexist with supported origin shutdown.

**Mutation:** Render service routing configuration.

## Phase 8 — Bootstrap empty PostgreSQL ownership

**Objective:** Establish permanent staff and canonical ownership deliberately
on an empty hosted database.

**Operator actions:**

1. Confirm no local SQLite export or development history was imported.
2. Verify migration state in pre-deploy logs. `migrate` should already have
   run; do not rerun it casually. If verification is required from the Render
   shell:

   ```bash
   python scheduler_project/manage.py migrate --noinput
   python scheduler_project/manage.py check_hosted_release
   ```

   The first command should report no pending work.
3. Create exactly one initial staff/superuser account through the
   access-controlled Render shell:

   ```bash
   python scheduler_project/manage.py createsuperuser
   ```

   Enter the password only at the secure prompt.
4. Sign in through Cloudflare Access and Django staff authentication.
5. Through Django admin or an separately reviewed Django shell procedure,
   create `<canonical-organization-name>` and one permanent membership linking
   the staff user to it. No repository management command creates these
   ownership records.
6. Verify the organization name exactly matches
   `DEMO_ORGANIZATION_IDENTIFIER`.

**Expected result:** Current migrations, one deliberate staff owner, one
permanent canonical candidate organization, and one valid membership exist.

**Stop:** Unexpected preexisting data, an imported SQLite history, migration
failure, mismatched identifier, missing membership, or accidental temporary
demo ownership.

**Recovery:** Keep entry disabled. Inspect migration and ownership state.
Correct only the narrowly identified record through the approved boundary. Do
not delete the whole database or invent an organization-creation command.

**Mutation:** Application schema if pending migrations exist; application data
for staff, organization, and membership creation.

## Phase 9 — Construct and verify the canonical demo

**Objective:** Build the canonical scenario only inside the approved permanent
organization.

**Operator actions and expected gates:**

1. Classify the exact organization:

   ```bash
   python scheduler_project/manage.py classify_canonical_demo \
     --organization "<canonical-organization-name>" \
     --confirm
   ```

   Expect the stored purpose to be `canonical_demo`, or a safe already-
   canonical no-op.

2. Inspect without writing:

   ```bash
   python scheduler_project/manage.py inspect_demo_environment \
     --organization "<canonical-organization-name>"
   ```

   Expect `Safe target inspected; no changes were made.` Review every create,
   update, reconcile, restore, warning, and blocker.

3. Apply only the reviewed plan:

   ```bash
   python scheduler_project/manage.py inspect_demo_environment \
     --organization "<canonical-organization-name>" \
     --apply \
     --confirm
   ```

   Expect `Stable demo reference data applied atomically.`

4. Inspect again:

   ```bash
   python scheduler_project/manage.py inspect_demo_environment \
     --organization "<canonical-organization-name>"
   ```

   Expect the canonical starting state to be valid and stable records to be
   unchanged.

5. Verify guarded reset:

   ```bash
   python scheduler_project/manage.py reset_demo_environment \
     --organization "<canonical-organization-name>" \
     --confirm
   ```

   Expect either canonical restoration or an already-canonical no-op.

6. Perform final read-only inspection:

   ```bash
   python scheduler_project/manage.py inspect_demo_environment \
     --organization "<canonical-organization-name>"
   ```

   Confirm complete generation and staffing, valid replay, no manual moves,
   no instructor overrides, no holding items, and no pending restoration.

**Expected result:** The exact permanent organization holds a complete, valid,
resettable canonical scenario.

**Stop:** Foreign records, wrong purpose or identifier, unexpected mutation,
blockers, assignment/generation failure, incomplete output, replay conflicts,
or a command targeting temporary/customer ownership.

**Recovery:** Keep entry disabled. Retain sanitized output, rerun read-only
inspection, correct the specific prerequisite, and use the guarded reset only
against the exact canonical organization. Do not broaden deletion manually.

**Mutation:** Application data for classification, confirmed apply, and reset;
inspection steps are read-only.

## Phase 10 — Verify health and runtime

**Objective:** Prove process, database, routing, and logging behavior without
unrestricted traffic.

### Health matrix

| Check | Safe action | Expected | Mutation |
|---|---|---|---|
| Liveness | `curl -i https://<beta-hostname>/health/live/` | 200 and `{"status":"ok"}` | None |
| Readiness | `curl -i https://<beta-hostname>/health/ready/` | 200 and `{"status":"ready"}` | None |
| Database failure | In a separate safe test environment, remove database access temporarily | Readiness 503; liveness remains 200 | Test infrastructure |
| Pending migration | In a separate disposable environment, deploy code with a deliberately pending migration | Readiness 503 | Test infrastructure |
| Disclosure | Review bodies and logs | No host, credential, migration name, count, or exception detail | None |
| Side effects | Compare demo throttle/activity state before and after polling | No throttle consumption or activity update | None |
| Access boundary | Inspect Cloudflare rules | Only exact path exceptions, if required | None |

Do not break the beta database or create a fake pending migration in the live
environment to test failure behavior.

**Operator actions:**

1. Run the safe live checks in the matrix.
2. Use an isolated test environment or the repository health tests for failure
   modes.
3. Review Render runtime logs for the correct WSGI target, two synchronous
   workers, access logs, and error logs.
4. During a deliberate test deployment with no visitors, observe graceful
   shutdown/startup and readiness transition.
5. Benchmark prepared provisioning and reset behind Cloudflare with approved
   operator traffic. Record high-percentile duration and review the provisional
   120-second timeout before admission.

**Expected result:** Safe health checks, two sync workers, searchable logs,
graceful deployment, and a justified timeout.

**Stop:** Health leaks details or mutates demo state; worker configuration is
wrong; graceful deploy fails; or representative work approaches/exceeds the
timeout.

**Recovery:** Keep entry disabled, correct configuration or timeout through a
reviewed release, and repeat this phase.

**Mutation:** None for health/log review; infrastructure during an isolated
failure or deployment test.

## Phase 11 — Verify maintenance

**Objective:** Prove bounded cleanup works manually and through the single cron
job before entry opens.

**Operator actions:**

1. In an access-controlled Render shell with the production environment, run
   dry mode:

   ```bash
   python scheduler_project/manage.py run_demo_maintenance
   ```

2. Review cutoffs, cleanup and retention limits, lease state, ownership
   backlog, blocked units, attempt/auxiliary plans, and Django-session plan.
3. If the plan is expected and safe, run one confirmed cycle:

   ```bash
   python scheduler_project/manage.py run_demo_maintenance --confirm
   ```

4. Record the exit code and sanitized category summary. Confirm zero or bounded
   expected changes, lease release, and no canonical/customer deletion.
5. In Render, verify the one cron job has:

   ```cron
   */15 * * * *
   ```

   and:

   ```bash
   python scheduler_project/manage.py run_demo_maintenance --confirm
   ```

6. Observe at least one scheduled execution. Confirm success status and logs,
   and confirm overlapping execution is not created.

**Expected result:** Dry mode is understandable, a confirmed bounded cycle
exits zero, the lease releases, and the scheduled execution succeeds.

**Stop:** Nonzero exit, unexpected deletion, canonical/customer targeting,
unreleased lease, repeated blocked ownership, persistent unexpected backlog,
missing logs, wrong schedule, or duplicate scheduler.

**Recovery:** Keep entry disabled. Preserve sanitized logs, return to dry mode,
inspect targeted ownership through existing guarded commands, and correct the
specific failure. Never bypass ownership safeguards.

**Mutation:** Confirmed manual and cron cycles may change temporary operational
data; dry mode and log review do not.

## Phase 12 — Verify backup and restore

**Objective:** Demonstrate recoverability rather than merely confirming that a
backup feature is enabled.

**Operator actions:**

1. Confirm the paid PostgreSQL plan's automatic backups and record retention.
2. Create or identify a pre-admission recovery point according to Render's
   supported backup procedure.
3. Restore that point into a **separate** PostgreSQL database. Do not
   replace the active database binding.
4. Attach only an isolated verification environment or secure operator
   connection to the restored database.
5. Against the restored database, verify:

   ```bash
   python scheduler_project/manage.py check_hosted_release
   python scheduler_project/manage.py inspect_demo_environment \
     --organization "<canonical-organization-name>"
   ```

6. Confirm migration state, permanent staff/canonical ownership, membership,
   complete canonical scenario, and understood staff-login recovery.
7. Record restore start/end times and sanitized results. Remove the verification
   resource only through a separately approved, precisely targeted provider
   action after evidence is retained.

**Expected result:** A separate restored database passes release and canonical
inspection, with a measured recovery duration.

**Stop:** No automatic backup, undocumented retention, restore overwrites the
active database, migration/canonical verification fails, or staff recovery is
unclear.

**Recovery:** Keep entry disabled. Correct the backup tier or restore procedure,
create a new separate restore, and repeat until recovery is proven.

**Mutation:** Backup and temporary restore infrastructure; read-only checks
against restored application data.

Temporary visitor data may be discarded during recovery. Staff identity,
canonical ownership, membership, and canonical configuration must be
recoverable.

## Phase 13 — Verify privacy disclosure

**Objective:** Prevent testers from treating temporary demo storage as a place
for sensitive or real-world data.

**Required unavoidable notice:**

> This is a temporary demo workspace. Do not enter real names, contact details,
> confidential school information, participant data, or other sensitive
> information. Demo data expires and is removed through scheduled maintenance.

**Operator actions:**

1. Open the Cloudflare-protected demo landing page before submitting an entry
   form.
2. Confirm the notice is visible without requiring navigation to another page.
3. Confirm the same warning is included in tester onboarding.

**Expected result:** Every tester sees the disclosure before starting a clean or
prepared demo.

**Stop:** The notice is absent, avoidable, obscured, or materially weakened.

**Recovery:** Open a separate, bounded UI assignment to add and test the notice,
deploy it with entry disabled, and repeat this phase.

**Mutation:** None during verification; a separate UI change is required if
missing.

**Repository implementation status:** The required notice is implemented in
`demo_landing.html` before the clean and prepared entry controls and outside the
entry-enabled conditional. Deployment verification remains mandatory: absence,
obscuring, or materially weakened wording on the deployed `/demo/` page is a
launch blocker.

## Phase 14 — Enable entry deliberately

**Objective:** Open anonymous provisioning only after infrastructure, access,
maintenance, recovery, privacy, and emergency controls pass.

**Pre-enable gate:**

- [ ] Cloudflare approved and denied identity tests pass.
- [ ] Direct Render origin is disabled.
- [ ] Readiness is healthy.
- [ ] Canonical final inspection passes.
- [ ] Manual and scheduled maintenance pass.
- [ ] Separate-database restore test passes.
- [ ] Privacy notice is visible.
- [ ] Staff authentication works.
- [ ] Capacity values match Phase 3.
- [ ] Emergency operator is available.

**Operator actions:**

1. Create a reviewed configuration change from:

   ```text
   DEMO_ENTRY_ENABLED=false
   ```

   to:

   ```text
   DEMO_ENTRY_ENABLED=true
   ```

   Because the Blueprint currently owns this explicit value, use a reviewed
   Blueprint/release change rather than leaving an undocumented dashboard
   override. Keep web and cron declarations consistent.
2. Approve the deliberate Blueprint sync/deploy. Render environment changes
   require a new deploy/restart before Django observes them.
3. Confirm release logs explicitly report `Demo entry: ENABLED`.
4. Before admitting testers, smoke-test behind Cloudflare:

   - [ ] Landing page shows clean and prepared forms.
   - [ ] Clean entry creates only isolated temporary ownership.
   - [ ] Prepared entry creates a complete isolated scenario.
   - [ ] Capacity denial is safe and does not overprovision.
   - [ ] Prepared-operation lease prevents overlap.
   - [ ] Visitor exit ends authentication and marks cleanup ownership.
   - [ ] Expiration blocks continued use.
   - [ ] Maintenance remains successful.
   - [ ] Emergency disable has been rehearsed.

**Expected result:** Entry is enabled through a recorded deployment and the
complete smoke test passes without bypassing Access or ownership controls.

**Stop:** Any pre-enable item is unchecked, deployment differs from review,
release checks fail, or any smoke test fails.

**Recovery:** Immediately follow Phase 16, keep testers unadmitted, diagnose
with sanitized logs, and repeat prerequisite phases.

**Mutation:** Repository/configuration, Render deployment, and temporary demo
application data during smoke tests.

## Phase 15 — Admit testers gradually

**Objective:** Limit first exposure while observing real provisioning and
database behavior.

**Operator actions:**

1. Add only securely approved identities to Cloudflare Access.
2. Send identity-provider or one-time-PIN instructions; do not send Django
   account credentials to anonymous testers.
3. Send the privacy warning and beta support contact.
4. Admit **1–3 testers** first.
5. Observe provisioning duration, database resource use, readiness, 5xx,
   capacity behavior, and cron execution.
6. Expand in small batches only after the initial group is stable. Do not
   immediately admit the full ten-session capacity.

**Expected result:** Only approved testers enter; the first small cohort remains
within runtime, database, and maintenance limits.

**Stop:** Unapproved admission, support unavailability, timeout pressure,
readiness degradation, persistent 5xx, cleanup backlog, or unexpected capacity
denial.

**Recovery:** Stop adding identities, use Phase 16 if needed, stabilize the
system, and repeat the relevant verification phase before resuming.

**Mutation:** Cloudflare allow policy and temporary application data.

## Phase 16 — Emergency disable

**Objective:** Stop all new anonymous provisioning quickly without deleting
permanent or canonical data.

**Operator actions:**

1. In Render, set the web service:

   ```text
   DEMO_ENTRY_ENABLED=false
   ```

   Use the fastest controlled environment update and deploy/restart. Also
   restore the Blueprint declaration to false in the incident follow-up so the
   safe state remains source-controlled.
2. Confirm the new service process has the updated setting.
3. Verify the landing-page forms disappear.
4. Verify clean and prepared POST endpoints return unavailable.
5. Verify already-active visitors retain the intended approved access; if the
   incident requires full isolation, proceed to step 8.
6. Review sanitized web, readiness, provisioning, capacity, and cron logs.
7. Run maintenance dry mode:

   ```bash
   python scheduler_project/manage.py run_demo_maintenance
   ```

8. For a serious access or application incident, remove/disable Cloudflare
   allow rules so default deny remains, then disable the Render web service if
   required.
9. Preserve timestamps, release identifiers, categories, and sanitized
   evidence. Never record secrets or session values.
10. Leave PostgreSQL intact pending review. Do not delete canonical or customer
    data.

**Expected result:** New clean and prepared provisioning is unavailable; the
incident boundary is recorded; permanent data remains intact.

**Stop:** Do not reopen entry while the cause, access boundary, data impact, or
recovery state is unknown.

**Recovery:** Apply the relevant Phase 17 rollback, repeat all affected launch
gates, and use Phase 14 for deliberate re-enablement.

**Mutation:** Render configuration/deployment; optionally Cloudflare access and
web-service availability. Maintenance dry mode is read-only.

## Phase 17 — Roll back safely

**Objective:** Restore a known safe boundary without compounding failures.

### Application release failure

- Keep entry disabled.
- Roll back to the recorded prior Render deployment.
- Inspect migration state; do not reverse migrations automatically.
- Run:

  ```bash
  python scheduler_project/manage.py check_hosted_release
  ```

- Require readiness before proceeding.

### Database migration failure

- Stop the deployment and keep entry disabled.
- Review Render pre-deploy logs and Django migration state.
- Do not repeatedly rerun destructive operations.
- Restore into a separate database when recovery investigation requires it.
- Change the active binding only through a separately reviewed recovery plan.

### Access-gate failure

- Restore Cloudflare default deny.
- Disable demo entry.
- Keep the Render hostname disabled.
- Admit no testers until Phase 6 and Phase 7 pass.

### Canonical bootstrap failure

- Keep entry disabled.
- Run read-only canonical inspection.
- Use only the guarded exact-organization reset where appropriate.
- Do not broaden deletion or manually alter temporary/customer ownership.

### Maintenance failure

- Disable entry when capacity or cleanup safety is at risk.
- Run dry mode and targeted cleanup diagnostics.
- Preserve ownership safeguards; never add a force/unlimited workaround.

**Expected result:** The system returns to a known disabled, access-controlled,
inspectable state without automatic migration reversal or data deletion.

**Stop:** Rollback readiness fails, migration state is unclear, origin bypass
reappears, or data ownership cannot be proven.

**Recovery:** Escalate to the assigned emergency and restore owners, preserve
the database, and use the separately restored database from Phase 12 for
recovery planning.

**Mutation:** Depends on incident: deployment, access configuration, or
application data only through existing guarded commands.

## Phase 18 — Daily private-beta operations

**Objective:** Detect a degrading beta before it becomes an access, capacity, or
recovery incident.

**Daily operator checklist:**

- [ ] `/health/ready/` is healthy.
- [ ] Web 5xx and readiness warnings reviewed.
- [ ] Most recent cron execution succeeded.
- [ ] Cleanup backlog is bounded and expected.
- [ ] No provisioning/deleting sessions are stuck.
- [ ] Capacity denials are understood.
- [ ] Prepared-operation busy responses and durations are reviewed.
- [ ] Latest database backup is current.
- [ ] Cloudflare approved-identity list is current.
- [ ] Demo entry state is intentional.
- [ ] Support issues are reviewed and assigned.

**Expected result:** Each item is checked or has a named incident owner.

**Stop:** For access bypass, readiness failure, backup failure, unbounded
cleanup, or uncontrolled provisioning, invoke Phase 16.

**Recovery:** Follow the matching emergency/rollback procedure and record only
sanitized operational evidence.

**Mutation:** Usually none; access-list corrections or emergency disable change
infrastructure configuration.

## Phase 19 — Final go/no-go gate

**Objective:** Make one evidence-backed admission decision after every
repository, provider, application, recovery, and support gate has passed.

Any unchecked access, migration, readiness, maintenance, restore, privacy, or
emergency-control item is a **no-go**.

### Repository

- [ ] Complete suite passes.
- [ ] Blueprint and consistency tests pass.
- [ ] Release commit is recorded.
- [ ] No secrets or provider identifiers are committed.

### Render

- [ ] All three resources are paid and in the correct region.
- [ ] Environment inventory is complete and consistent.
- [ ] PostgreSQL public allowlist is empty.
- [ ] Build, migration, release check, and Gunicorn startup pass.
- [ ] Readiness passes.
- [ ] Cron schedule and execution pass.
- [ ] Web, error, release, and cron logs are visible.
- [ ] Automatic backup and retention are confirmed.

### Cloudflare

- [ ] Access application matches the exact hostname.
- [ ] Default deny is active.
- [ ] Only approved identities are allowed.
- [ ] MFA or one-time PIN is verified.
- [ ] Any health exceptions match exact paths only.
- [ ] Direct Render origin bypass is closed.

### Django

- [ ] Migrations are current.
- [ ] `check_hosted_release` passes.
- [ ] Staff authentication works behind Access.
- [ ] Canonical classification and membership are valid.
- [ ] Final scenario inspection passes.
- [ ] Entry remained disabled throughout validation.
- [ ] Required privacy notice is visible before entry.

### Recovery

- [ ] Separate-database restore test passes.
- [ ] Restore duration and owner are recorded.
- [ ] Emergency disable is rehearsed.
- [ ] Rollback boundaries are understood.
- [ ] Responsible emergency operator is available.

### Admission

- [ ] Entry was deliberately enabled by a recorded release.
- [ ] Full post-enable smoke test passes.
- [ ] First cohort is limited to 1–3 testers.
- [ ] Support contact is active.

**Expected result:** Every item is checked and supported by retained, sanitized
evidence.

**Stop:** Any required item is unchecked.

**Recovery:** Return to the earliest incomplete phase. Do not waive a launch
gate because later checks passed.

**Mutation:** None; this is the final decision record.

## Responsible command map

Run these only in the explicitly identified environment and phase:

```bash
python scheduler_project/manage.py migrate --noinput
python scheduler_project/manage.py check_hosted_release
python scheduler_project/manage.py createsuperuser
python scheduler_project/manage.py classify_canonical_demo \
  --organization "<canonical-organization-name>" \
  --confirm
python scheduler_project/manage.py inspect_demo_environment \
  --organization "<canonical-organization-name>"
python scheduler_project/manage.py inspect_demo_environment \
  --organization "<canonical-organization-name>" \
  --apply \
  --confirm
python scheduler_project/manage.py reset_demo_environment \
  --organization "<canonical-organization-name>" \
  --confirm
python scheduler_project/manage.py run_demo_maintenance
python scheduler_project/manage.py run_demo_maintenance --confirm
```

No command transfers SQLite data, creates a canonical organization, bypasses
Cloudflare Access, or enables demo entry.

## Final launch record

Record without secrets:

- Release commit:
- Launch decision time:
- Render owner:
- Cloudflare owner:
- Emergency operator:
- Restore-test owner:
- Restore duration:
- Initial tester count:
- Go/no-go decision:
## Prepared-demo choices

`/demo/` exposes the smaller guided `canonical-v1` example, the realistic
working `working-v1` example, and a clean empty workspace. Some realistic
schedules intentionally produce infeasibility diagnostics; those are expected
input outcomes, not application failures. Prepared scenarios are
repository-owned and require no hosted access to development organizations,
development databases, or local SQLite.
