# 01 — Current System Analysis (As-Is)

**Release:** v6 "Connected Operations" · **Status:** Draft for review · **Date:** 23 Nov 2026
**Method:** Every statement below comes from reading the code in this repository (commit `ccb05e3`) and the shape of the live database (`data/hub.sqlite3`; we looked only at counts and field names, not personal data). Nothing here is assumed.

---

## 1. Technology stack

| Layer | What is in use | Where |
|---|---|---|
| Language / framework | Python 3.15, **Django 6.1.3** | `requirements.txt`, `venv/` |
| App server | gunicorn 26.2 behind Nginx/Caddy | `README.txt` |
| Database | **SQLite**: one file for single-company installs (`data/hub.sqlite3`). In SaaS mode there is one file **per company** (`tenants/<slug>/hub.sqlite3`) plus `platform.sqlite3` | `config/settings.py`, `saas/tenancy.py` |
| Postgres | `psycopg2-binary` is listed in requirements but **not used** | `requirements.txt` |
| Frontend | **One HTML file**: vanilla JS SPA, ~4,000 lines, no build step, no framework | `core/app/index.html` |
| PWA | Service worker caches the app shell only (network-first). It is installable on Android and iOS | `core/app/sw.js` |
| Background work | **None running continuously.** One daily cron command (`saas_cron`) that only sends billing e-mails | `saas/management/commands/saas_cron.py` |
| E-mail | SMTP set through `HUB_EMAIL_*` env vars, sent **inside the web request** | `core/views.py: flush_mail` |
| Payments | Razorpay (SaaS only) | `saas/billing.py` |
| Security headers | Strict CSP. Inline script allowed only by its **SHA-256 hash**, which is computed automatically | `core/secmw.py` |
| Tests | **None.** `core/tests.py` is empty | |

## 2. Data architecture

The app is a **document store on top of Django**, not a relational schema.

| Table (model) | Purpose | Key facts |
|---|---|---|
| `Record(coll, rid, data JSON)` | Holds **19 business collections**: employees, tasks, clients, testimonials, events, notes, reviews, agenda, leaves, ratings, attreqs, notices, documents, assets, candidates, invoices, payslips, checklists, rota | Unique on `(coll, rid)`. All other fields live in JSON. **No foreign keys, no indexes on business fields** |
| `Attendance(day, eid, data JSON)` | One row per employee per day. `data = {s: P/L/A/V/W, p: [[in,out]...], tl: [...], by, br: self/device/manager/admin, sf: selfies}` | Unique `(day, eid)` |
| `Config(key, value JSON)` | `company`, `settings` (one big JSON object), `rev` (global data revision), `audit` (last 3,000 rows as one JSON list), `forgot`, `devkey`, `devseen` | `settings` is **sent to every admin's browser** |
| `Profile(user, role, emp_id, prefs)` | Login → role and linked employee record | `role ∈ superadmin, admin, manager, employee` |
| `LoginFail` | Login throttling | |

### 2.1 Employee data relationships (all are string ids inside JSON)

```
User 1─1 Profile ──emp_id──▶ Record[employees].id
Record[employees].reportsTo ──▶ Record[employees].id          (the manager hierarchy)
Record[tasks].eid ──▶ employee   Record[tasks].cid ──▶ client
Record[leaves|attreqs|reviews|payslips|documents|assets|rota].eid ──▶ employee
Attendance.eid ──▶ employee
Record[ratings].from/to ──▶ employee
```

**Employee fields as stored today:** `id, empId (AI-EMP-001), name, designation, dept, email, phone, blood, dob, joined, status (Active/Inactive), photo, reportsTo, core, shift, bioId, ctc, education, expYears, skills[], experience[]`.
- `dept` and `designation` are **free text**. Departments are suggested from a fixed list and from values already used. There is no master table.
- `phone` is **free text**, with no format check and no country code.
- **No branch, location or office field exists.** The nearest thing is `settings.checkin.sites[]` (name, latitude, longitude, radius), which is used only for GPS check-in.

**Task fields as stored today:** `id, title, desc, eid (single assignee), cid, pri (high/med/low), status (todo/doing/done), due, cat (work type), qty, reopens[], mistakes[], doneOn`.
- There is **no** creator, start date, estimate, time tracking, attachment, department or branch field.

**Calendar events today:** `events` collection `{title, type: holiday/important/celebration/meet/trip/other, date, end, yearly, note}`. Events are whole days: there is no time of day, no participants and no location. Birthdays and work anniversaries come from employee records.

## 3. API architecture

- **One endpoint:** `POST /api/?a=<action>` (GET is allowed only for `status`, `load`, `users`, `audit`).
- **CSRF defence:** the view is `csrf_exempt`, and every call must send the header `X-Requested-With: staffroom`. A custom header forces a CORS preflight, so other sites cannot call the API.
- **Every request runs inside one DB transaction** (`transaction.atomic`).
- **Sync model:**
  1. `a=load` returns the **entire dataset the user may see**, including **all attendance history**, as one JSON document (`view_for`).
  2. The browser edits its local copy, works out the changes against a snapshot, and sends `a=save {ops:[{c, op:set|del, rec}]}`.
  3. The server checks each op (`can_set` / `can_del` / `op_att`), applies it, and increments the **global `rev`**.
  4. Every client polls `load` every **45 s** and re-downloads **everything** when `rev` has changed.
  5. **Every successful save writes a full JSON backup** of the whole database to disk.
- Other actions: `login, logout, setup, restore, forgot, audit, users, user_save, user_del, user_signout, prefs, account, punch, qr, devinfo, selfie`.
- Device endpoints outside `/api/`: `/iclock/cdata`, `/iclock/getrequest` (ZKTeco ADMS) and `/device/punch` (key header).
- **URL lists are duplicated:** `config/urls.py` for single-company installs and `saas/urls_tenant.py` for SaaS. Every new route must be added to both.

## 4. Roles and permissions (as-is)

| Rank | Role | Data scope | Notes |
|---|---|---|---|
| 4 | `superadmin` | Everything | The only role that can change `settings`, the company name, restore or device info. There must always be at least one |
| 3 | `admin` | Everything, company-wide | Can create managers and employees only. Sees salary (`ctc`) |
| 2 | `manager` | **Direct reports only** (`reportsTo == me`) plus self | Approves their team's leave. Cannot approve their own |
| 1 | `employee` | Self | Can change only their own task `status` and a few profile fields |

- Permissions are **hard-coded per collection** in `can_set` / `can_del` and filtered in `view_for`. Optional modules use the `GEN` table `(write rank, read-all rank, own-only)`.
- **Module switches:** `settings.modules`, managed by the super admin. In SaaS the plan also limits modules (`saas/plans.py`, `COLL_MODULE`). Client-side mirrors are `MODS`, `VMOD`, `NAVG` and `navFor` in `index.html`.
- **There is no HR Admin, Branch Admin or Branch Manager role, and no data scope other than the reporting line.**

## 5. Existing modules relevant to this release

| Module | How it works today | Impact of v6 |
|---|---|---|
| **Attendance** | Manual marking, phone check-in (GPS, selfie, QR) and biometric devices. Late = first punch after shift start + grace. Self-made records are locked | Needs a branch field (branch working hours, branch sites), attendance reminders and late alerts |
| **Shift rota** | Shifts live in `settings.shifts`. Each person has a default shift and a weekly rota | Branch working hours become the default when a person has no shift |
| **Leave** | Full day, half day, 1 h or 2 h. Manager or admin approves. A yearly quota is **checked only in the browser** | Branch scope for approvers; notification events |
| **Tasks** | Kanban (to do / in progress / done), work types, quantity, reopens, client mistakes | **Enhanced in place**: estimate, timer, manual time, attachments, creator, branch |
| **Work report** (`workrep`) | Day, week or month totals. "Hours logged" comes from **attendance punches**, not tasks | Task work time must stay **separate** from these attendance hours (BR-T10) |
| **Calendar** | Month view of events, birthdays and anniversaries, plus sticky notes | Shows the user's meetings as an overlay. Meetings are **not** stored as events |
| **Notice board** | Announcements (`notices`) | **Branch announcements reuse notices**, with a branch audience and an optional WhatsApp/SMS send |
| **Clients** | Client master, requirements, payments | Client meetings **pick an existing client** |
| **Recruitment** | Candidates by stage | Interview meetings can link to a candidate |
| **Notifications** | The bell works out "what needs you" **in the browser** from loaded data. Nothing is stored and there is no read state. E-mail alerts exist **only for leave and attendance-correction requests**, sent inside the request with no log and no retry | Replaced by a **shared notification service**. The current e-mail alerts become its e-mail channel |
| **Audit log** | One JSON list in `Config['audit']`, last 3,000 rows `[time, user, coll, op, id]`, with no before/after values | A new `AuditEvent` table for v6 entities. The old log is still read |
| **Dashboard** | Greeting, period and department filters, "Needs you" queue, attendance charts, top performers, team pulse, out today | New widgets, a branch filter, configurable layout |
| **Payroll** | PF/ESI and **one set of professional-tax slabs** for the company | Follow-up: PT depends on the **branch's state** (out of scope, recorded as a risk) |

## 6. Existing calendar integrations

**None.** There is no OAuth, no Google or Microsoft code and no ICS generation. The only outbound integrations are Razorpay, open-meteo (weather) and IP geolocation.

## 7. Findings that shape the design

| # | Finding | Consequence for v6 |
|---|---|---|
| F1 | Business data lives in JSON with no constraints or indexes | Timers need "only one running timer" guarantees, meetings need conflict queries, reports need aggregation. **New modules use proper relational tables** (decision D1) |
| F2 | `load` sends everything to the browser and any change makes every client re-download it | New modules **must not** be added to the `load` blob or bump the global `rev`. They get their own paged endpoints (NFR-P3) |
| F3 | Admins (rank ≥ 3) receive the whole database without filtering (`if rank >= 3: return db`) | Branch isolation needs changes in `view_for`, `can_set`, `can_del`, `op_att`, `users`, `audit` and `selfie`. **This is the highest regression risk** |
| F4 | Admins' and managers' task and employee writes are accepted as whole objects (`return new`) | Server-owned fields (`branchId`, `createdBy`, time totals) must be **preserved from the stored copy** so stale or edited clients cannot change them (BR-P3) |
| F5 | `settings` JSON goes to every admin's browser | WhatsApp/SMS credentials and OAuth tokens **must never be stored in `settings`**. They go in encrypted server-only tables |
| F6 | No background worker | A small tenant-aware job runner is needed for scheduled messages, reminders, retries, calendar sync and timer auto-stop (D5) |
| F7 | E-mail is sent inside the request transaction | External calls go through an **outbox** that is committed with the business change and sent after commit |
| F8 | Backup and restore cover only `Record`, `Attendance` and `Config` | Backup format v3 must include the new tables, and restoring an older backup must behave predictably (FR-PLT-08) |
| F9 | SaaS tenants are migrated only when created (`provision`) | A `migrate_tenants` command is needed before any release that adds tables |
| F10 | The `prefs` action **overwrites** `Profile.prefs` with only theme and mode | Must be changed to merge, or saved dashboard widget layouts would be wiped |
| F11 | `dept` and `designation` are free text | A light department and designation master is needed for branch mapping and reporting, migrated from the values already in use |
| F12 | Times are naive local (`datetime.now()`, server `TIME_ZONE=Asia/Kolkata`) | New modules store **UTC** with a branch time zone, because meetings and calendars need it. Existing modules are unchanged |
| F13 | No automated tests | Characterisation tests of the current permission rules are written **before** branch scoping is added (WS0) |

## 8. Pre-existing issues noticed (outside this release's scope, reported for awareness)

1. `config/settings.py` hard-codes `DEBUG = False` and `ALLOWED_HOSTS = ['assertivehub.in', ...]`. The README says these come from environment variables, so local runs at `127.0.0.1` will reject requests.
2. The `tenancy/` package is dead code. It is not in `INSTALLED_APPS` and refers to a `config.urls_platform` module that does not exist. The live SaaS code is in `saas/`.
3. The leave quota is enforced only in the browser (the README says so). It can be bypassed through the API.
4. A full JSON backup is written on **every save**. This costs O(database size) per save, which becomes a throughput limit as data grows.
5. `revoke_sessions` decodes every session row, which is O(sessions).

## 9. What is reused vs. new

| Reused as-is | Extended | New |
|---|---|---|
| Auth, sessions, login throttling, CSP, `X-Requested-With` convention, `{ok,error}` envelope, `ApiError`, module switches, plan gating, modal/form/toast/pill/panel UI components, CSV formula-injection protection, ID validation (`ID_RE`), document type checks (`DOC_BAD`) | Employee record (+branch), task record (+estimate, start, creator, branch, source), events (+branch audience), notices (+branch audience, +send by WhatsApp/SMS), dashboard, bell, work report, audit page, Settings > Modules, backup/restore, `prefs` | Branches, departments, designations, rooms, meetings, participants, minutes, action items, work sessions, notification providers, templates, events config, deliveries, consent, calendar connections and links, attachments, job queue, audit events |
