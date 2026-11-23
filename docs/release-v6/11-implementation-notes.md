# 11. Implementation notes: what was built, deviations, how to test

Audience: engineering, QA and whoever deploys the release.

The release is implemented on branch `feature/v6-connected-operations`. This note records where the build differs from documents 03–10, where the code lives, and how it was verified.

## Where the code lives

| Area | Location |
|---|---|
| Models (31 new tables) | `core/models.py` (appended), migrations `0002_v6_schema.py` and `0003_v6_seed.py` |
| Server modules | `core/v6/`: `base` (actions, capabilities, errors), `scope` (branch isolation), `crypto`, `audit`, `jobs`, `branches`, `worktime`, `meetings`, `calsync`, `notify`, `files`, `reports`, `system` |
| Changes to existing code | `core/views.py` (scope-aware load/save, new roles, users with branches, prefs merge, v6 dispatch), `core/punch.py` (branch shift hours, branch on attendance) |
| URLs | `config/urls.py`, `saas/urls_tenant.py`, `saas/urls_platform.py` (webhooks, OAuth callback, file download, RSVP page) |
| Plans | `saas/plans.py` (meetings and time tracking in Core; WhatsApp/SMS, calendar sync and branches in the higher plan; branch limits 1 / 3 / unlimited) |
| Background work | `core/management/commands/hub_tick.py`, `hub_worker.py`, `migrate_tenants.py` |
| Screens | `core/app/index.html`, between `/* ===== v6 start` and `/* ===== v6 end ===== */`, plus a CSS block marked `v6 styles` |
| Tests | `core/tests/` (the old `core/tests.py` became the `test_legacy_permissions.py` module) |

The SRS (NFR-MT1) proposed top-level files such as `core/branches.py`. The code is in the `core/v6/` package instead, which keeps the release in one place.

## Decisions taken (recommended answers to OD-01 … OD-08)

All eight open decisions were implemented with the recommended option in [00-README](00-README.md).

## Deviations from the specification

| Spec | What was built | Why |
|---|---|---|
| FR-TIME-07 / BR-T1: parallel timers allowed by branch policy | **One running timer per employee, always.** The database enforces it (`one_running_per_emp`). No parallel-timer policy exists. Starting a second task offers "Stop and switch". | Simpler, and day totals can never pass 24 h. EC-T14 therefore cannot happen. |
| Leave and attendance-request e-mails go through the new notification service | The **existing** leave and attendance e-mails are unchanged. The new service adds in-app, WhatsApp and SMS alerts for leave next to them. | Existing e-mail keeps working even if the background worker is not running. |
| Meeting events on the e-mail channel | Meeting e-mails are the `.ics` invitations, update notices and cancellation notices only. There is no separate generic meeting e-mail. | Avoids sending everyone two e-mails per meeting. |
| Time policy as typed columns | `TimePolicy.values` (JSON) with server-side defaults and limits (`worktime.DEFAULTS`, `LIMITS`). It can be set for the whole company or for one branch. | New rules can be added without a migration. |
| Several providers per channel | One active provider per channel: Meta Cloud API for WhatsApp, MSG91 for SMS. | Matches the decided vendors. The adapter interface allows more providers later. |
| Encryption key from the environment only | `HUB_ENC_KEYS` if set; otherwise a key is created in `DATA_DIR/enc.key`. | Self-hosted installs work without extra setup. That file must be backed up with the database. |
| Reports catalogue R-01 … R-18 | R-01 … R-16 and R-18 are built. R-17 (calendar sync health) is covered by Settings > System health and the sync status shown on each meeting. | Not needed as a separate table. |

## Backward compatibility

- **New features start switched off.** All five modules are off by default. While they are off, the navigation, dashboard, forms and roles are unchanged. This was checked on a migrated copy of the live database (see Testing).
- **Data migration.**
  - Migration `0003` creates the head-office branch from the company settings.
  - Every employee gets a primary assignment starting on their joining date, or their first attendance day if there is no joining date.
  - Each employee record gets `branchId`.
  - Departments and designations are built from the existing records. Values that differ only in letter case are merged.
- **Existing data model stays.** Existing records keep the generic `Record` store. New entities use their own tables.
- **Server-owned fields.** These fields on existing records are set only by the server (`SERVER_FIELDS` in `views.py`); values sent by the browser are ignored:
  - `branchId` on employees, after creation
  - `createdBy`, `createdAt` and `branchId` on tasks
  - `branchId` on leave
- **PHP version.** The PHP version and its Hostinger `.htaccess` script hash are not part of this release, which is Django only.

## Testing

### Automated tests

Run `HUB_DATA_DIR=/tmp/hubtest python manage.py test core`: 94 tests, all passing.

- `test_legacy_permissions`: the old permission behaviour, unchanged.
- `test_scope`: branch isolation for every role, in load, save, users, audit, reports and attachments.
- `test_worktime`: the timer state machine, the single-timer rule, idempotency, auto-stop, manual time, adjustments and approvals.
- `test_meetings`: validation, conflicts, recurrence, RSVP, `.ics` stability, minutes and action items to tasks.
- `test_notify`: consent and opt-out, templates, quiet hours, caps, fallback, webhooks (signature check), OTP and secret masking.
- `test_branches`: transfers with history and report attribution, branch hours, the module switch, prefs merge and CSV formula injection.

### Browser smoke test (done for this release)

The real page was loaded in jsdom against a local server and clicked through as a super admin, an employee and an HR admin. Steps covered:

- Dashboard cards.
- Meetings: create with conflicts, open the detail, all tabs and the calendar.
- Timer: start, pause with a reason, resume and stop.
- Task form: time fields and their validation.
- Tracker, logs and approvals tabs.
- Branches: create, add a room, view transfers and departments.
- Messages tabs and the audience preview.
- All 17 reports.
- Employee branch filter.
- Profile branch panel and transfer preview.
- Notice and holiday branch targeting.
- New roles on the Users page.
- Settings panels.

Result: no script errors and no server errors.

The same run on a migrated copy of the live database, with the modules off, showed:

- the same navigation as before
- no new elements on any page
- only the original login roles

### Still to do manually before go-live

- In a real browser (Chrome, Safari, mobile), check layout and the timer bar above the mobile bottom bar.
- With real sandbox credentials, run the WhatsApp/SMS and Google/Microsoft tests in [09-qa-test-plan](09-qa-test-plan.md): TS-N*, TS-C*.
- Run the worker under systemd or cron and confirm that Settings > System health shows it running.
