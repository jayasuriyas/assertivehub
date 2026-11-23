# 03 — Software Requirements Specification (SRS)

**Release:** v6 · **Version:** 1.0 draft · 23 Nov 2026
**Reads with:** `04-specifications.md` (event matrix, calendar, timer logic, widgets, screens, reports), `06-data-model.md`, `07-api-spec.md`.

**Priority keys (MoSCoW):** **M** = Must, **S** = Should, **C** = Could.
**Role abbreviations:** **SA** Super Admin · **CA** Company Admin (existing `admin`) · **HR** HR Admin · **BA** Branch Admin · **BM** Branch Manager · **MG** Manager (reporting line) · **EM** Employee.

---

## 1. Platform / shared requirements (FR-PLT)

These are the foundations all four modules depend on. They are built first, in workstream WS0.

| ID | Requirement | Pri |
|---|---|---|
| FR-PLT-01 | **Scope service.** One server function, `scope_for(user, at_date=None)`, returns the set of **branches** and **employee ids** the user may see. Rules: SA/CA → all; HR/BA → employees whose primary branch (on `at_date`) is in the user's authorised branches, plus employees with secondary access to those branches; BM → the same for their branch(es), plus their reporting-line team; MG → reporting-line team plus self (unchanged); EM → self. **Every** existing and new read/write path calls it. Nothing relies on hiding things in the UI | M |
| FR-PLT-02 | **Capability map.** A fixed role→capability table in code (see §6). New endpoints check *capability + scope*, never a bare rank | M |
| FR-PLT-03 | **Job runner.** A tenant-aware, DB-backed queue (`Job` table) processed by `manage.py hub_worker`, a long-running systemd service that loops over tenants. A cron fallback, `manage.py hub_tick`, runs every minute. It supports `run_at`, a lease (`locked_until`), max attempts, exponential back-off and a `unique_key` for idempotency | M |
| FR-PLT-04 | **Transactional outbox.** Business changes and their follow-up jobs (notifications, calendar sync) are written in the **same DB transaction**. No external HTTP call runs inside a request transaction | M |
| FR-PLT-05 | **Secrets at rest.** Provider credentials and OAuth tokens are encrypted (AES via `cryptography.Fernet`) with key(s) from env `HUB_ENC_KEYS` (supports rotation). They are never returned to the browser, never in `settings`, never in backups or logs. The UI shows masked values only (`••••3f9a`) | M |
| FR-PLT-06 | **Audit events.** A new `AuditEvent` table records actor, action, entity, entity id, branch, before/after (changed fields only), IP and time for all v6 entities, role/scope changes and transfers. The Audit log page merges it with the legacy `Config['audit']` list and filters by the viewer's scope | M |
| FR-PLT-07 | **Attachments.** Shared service for tasks, meetings and minutes. Files are stored under the tenant data dir (not base64 in JSON), max 10 MB each (configurable), max 10 per item. Allowed types: pdf, doc(x), xls(x), ppt(x), png, jpg, webp, txt, csv, zip. HTML/SVG/JS are rejected (same rules as `DOC_BAD`). SHA-256 is stored. Download goes through an authenticated, scope-checked endpoint with `Content-Disposition: attachment` | M |
| FR-PLT-08 | **Backup v3.** The automatic and manual backups include the new tables, **excluding secrets and tokens**. Restore: a v3 backup restores everything. A v2 (older) backup restores as today, then recreates the default Head Office, assigns all employees to it and clears meeting/time/notification data, after the SA confirms a warning that names what will be cleared | M |
| FR-PLT-09 | **`migrate_tenants` command.** Migrates every tenant DB. It copies each SQLite file first (`--backup`), continues past failures and prints a summary, and offers `--dry-run` / `--only <slug>` | M |
| FR-PLT-10 | **Module flags.** New modules `branches`, `notify` (WhatsApp/SMS), `meetings`, `timetrack`, `calsync` are added to `MODS` (off by default, per the existing policy "new modules start switched off"), to `saas/plans.py` and to `COLL_MODULE`/gating. **Off = hidden and every endpoint returns 403. Data is kept** | M |
| FR-PLT-11 | **Prefs merge.** The `prefs` action merges keys instead of replacing them, so theme, mode, dashboard layout and time zone survive each other's saves | M |
| FR-PLT-12 | **Server-owned fields are preserved.** On `save` the server copies server-owned fields (`branchId`, `createdBy`, `createdAt`, `src`, `deptSnap`) from the stored record, whatever the client sends. Only dedicated endpoints change them | M |
| FR-PLT-13 | **Server clock.** Every v6 response includes `now` (UTC ISO). The client keeps an offset so timers and countdowns display correctly even if the device clock is wrong | M |
| FR-PLT-14 | **Webhook ingress.** `/hooks/<provider>/<tenant-token>` (outside `/api/`, CSRF-exempt) checks the signature (e.g. Meta `X-Hub-Signature-256` HMAC), logs the payload (truncated, 30-day retention), rejects replays older than 5 minutes and queues processing as a job. It replies 200 quickly | M |
| FR-PLT-15 | **No `rev` bump.** Writes to v6 tables do **not** increment the global `rev` and do **not** trigger the full JSON backup on every write. v6 tables are backed up by the daily job, and the SQLite file copy remains the system of record | M |
| FR-PLT-16 | **Lightweight refresh.** v6 widgets refresh through their own endpoints, on screen focus and at most every 60 s. They never call `load` | M |

## 2. Module 1 — WhatsApp and SMS alert management (FR-NOT)

| ID | Requirement (traces to brief FR #) | Pri |
|---|---|---|
| FR-NOT-01 | (1) **WhatsApp Business API.** A provider adapter interface (`send_template`, `send_text_in_session`, `parse_webhook`, `sync_templates`, `test_connection`). First adapter: **Meta WhatsApp Cloud API**. A second adapter for one BSP (e.g. Gupshup or Twilio) is OD-03 | M |
| FR-NOT-02 | (2) **SMS gateway.** Same interface. First adapter: **MSG91** (supports Indian DLT fields: entity id, sender id/header, DLT template id). Twilio as an optional second adapter | M |
| FR-NOT-03 | (3) **Separate switches.** Company-level enable/disable for WhatsApp and SMS separately, plus per-event channel switches, plus a module flag. Disabled channels are never attempted, and deliveries are recorded as `skipped_channel_off` | M |
| FR-NOT-04 | (4) **Template management.** CRUD of templates per *event × channel × language* (English first; Malayalam, Tamil, Kannada, Telugu, Hindi supported as language codes). Fields: name, body, ordered variables, category, provider template name/id, DLT template id, status. Versioned: an edit creates a new version, and old deliveries keep their version | M |
| FR-NOT-05 | **Template approval sync.** For WhatsApp, "Submit to provider" and "Sync status" pull back approved/rejected/paused status. Only `approved` templates are used for business-initiated WhatsApp messages | M |
| FR-NOT-06 | (5) **Dynamic variables.** Supported: `employee_name, task_name, meeting_title, meeting_date, meeting_time, branch_name, leave_status`, plus `company_name, due_date, leave_dates, meeting_link, meeting_location, organizer_name, estimated_time, actual_time, announcement_title`. Variables are mapped by name to provider positions (`{{1}}`) or DLT `{#var#}`. Dates and times are formatted in the **recipient's branch time zone** (e.g. `Wed, 3 Dec`, `3:00 PM`) | M |
| FR-NOT-07 | **Preview and test send.** A template shows a live preview with sample data. Test-send goes only to the admin's own verified number | M |
| FR-NOT-08 | (6) **Individual send.** HR/BA/SA send a message to one or more chosen employees in their scope, using an approved template (WhatsApp) or a registered template (SMS) | M |
| FR-NOT-09 | (7, 8) **Department- and branch-wise send.** Audience picker: branch(es) × department(s) × designation, optionally active employees only. It shows the recipient count and how many will be skipped (no consent, no number, opted out) **before** sending | M |
| FR-NOT-10 | (9) **Bulk send.** Same as above with no upper limit inside scope. Sends to more than 200 recipients need a second confirmation that shows the estimated cost. Sending is rate-limited to the provider's tier (configurable messages/second) | M |
| FR-NOT-11 | (10) **Scheduled send.** Any manual send can be scheduled (date/time in the sender's branch time zone, at least 5 min ahead, at most 90 days). Scheduled sends can be edited or cancelled until 1 minute before | M |
| FR-NOT-12 | (11) **Automated triggers.** The events in `04-specifications.md §1` fire from business actions or scheduled checks. Each event can be enabled per company and overridden per branch (on/off, channels, timing offsets) | M |
| FR-NOT-13 | (12) **Delivery status.** Each recipient × channel attempt is tracked through `queued → sent → delivered → read` or `failed/undeliverable`, updated from provider webhooks. If a status stops updating, the delivery shows `sent (unconfirmed)` after 24 h | M |
| FR-NOT-14 | (13) **Retry.** Temporary failures (timeouts, 5xx, rate limit) are retried at 1, 5 and 15 min, then marked failed. Permanent failures (invalid number, template rejected, not a WhatsApp user, opted out) are not retried. Admins can **retry failed** messages one at a time or in bulk from History | M |
| FR-NOT-15 | (14) **Logs and history.** History screen filtered by date, event, channel, status, branch, employee and campaign, with export to CSV. The detail view shows the rendered message, template version, provider id, timestamps, error and fallback chain. Bodies are kept 180 days, metadata 2 years (configurable) | M |
| FR-NOT-16 | (15) **Employee preferences.** Per employee: preferred channel order (WhatsApp → SMS → in-app), per event group (Attendance, Leave, Tasks, Meetings, Celebrations, Announcements) on/off, quiet hours (default 21:00–08:00 branch time) and language. **Mandatory** events (leave decision, meeting cancellation) cannot be turned off, only redirected to another channel | M |
| FR-NOT-17 | (16) **SMS fallback.** Configurable per event: if the WhatsApp delivery is permanently failed, or not `delivered` within *N* minutes (default 10, set to 0 for "failure only"), send by SMS. The fallback is linked to the original delivery. Fallback never fires when the employee opted out of SMS | M |
| FR-NOT-18 | **Contact verification.** Phone numbers are normalised to E.164 (default country +91). Verification by SMS OTP (6 digits, 10-minute expiry, 5 tries) or WhatsApp reply. Admins may mark a number "attested" with a reason, which is audited. Changing the phone resets verification | M |
| FR-NOT-19 | **Consent.** Records opt-in per channel: when, how (self in app, OTP, WhatsApp keyword, admin on paper), notice text version and who recorded it. Opt-out by the employee in the app or by keyword (`STOP`) through the webhook. The consent history is never deleted, only appended to | M |
| FR-NOT-20 | **In-app channel.** Every notification is also stored as an in-app item, shown in the bell beside the existing computed "needs you" items, with read/unread state. It is the final fallback | M |
| FR-NOT-21 | **E-mail channel.** The existing leave and attendance-correction e-mail alerts move to this service, with the same content and the same `emailAlerts` switch, so existing behaviour is unchanged. Meeting invites use e-mail with `.ics` | M |
| FR-NOT-22 | **Duplicate prevention.** Each delivery has a `dedupe_key` (event + entity + occurrence + recipient + channel) with a **unique DB constraint**. A re-triggered event in the same occurrence never sends twice | M |
| FR-NOT-23 | **Cost control.** Optional monthly message cap per channel (tenant). Warning at 80 %. At 100 %, non-mandatory sends are blocked and mandatory ones go to in-app/e-mail. Usage report by month, event and branch | S |
| FR-NOT-24 | **Quiet hours.** Non-urgent events falling in quiet hours are delayed to the end of quiet hours. Urgent events (meeting starting within 2 h, meeting cancelled) are sent at once | S |
| FR-NOT-25 | **Branch announcements reuse the Notice board.** The notice form gets an *Audience* (all, or branches/departments) and *Also send via* (WhatsApp/SMS) using the `branch.announcement` template. There is no separate announcement entity | M |

## 3. Module 2 — Multi-branch management (FR-BR)

| ID | Requirement | Pri |
|---|---|---|
| FR-BR-01 | (1) **Branch CRUD.** Create, edit, view and **deactivate** (no hard delete) branches with these fields: name, unique code, address (line 1, line 2, city, state, country, PIN), contact number, e-mail, branch manager, working hours (start, end, break, working days), **time zone**, status, and a head-office flag. Deactivation reason is required | M |
| FR-BR-02 | **Head office.** Exactly one branch is HO. On upgrade the system creates **"Head Office" (code `HO`)** from current company settings and assigns every existing employee to it | M |
| FR-BR-03 | (2) **Assign employees.** Every employee has exactly one primary branch on any date. The employee form shows *Branch* (required) for HR/BA/SA. New employees default to the creator's branch | M |
| FR-BR-04 | (16) **Secondary branch access.** An employee may be linked to further branches (from/to dates, reason). They then appear in those branches' pickers for tasks and meetings. Attendance and leave stay with the primary branch | M |
| FR-BR-05 | (3) **Branch admins.** SA assigns one or more Branch Admin users per branch. A user may administer several branches | M |
| FR-BR-06 | (4) **Branch managers.** The branch record names a manager (an employee). Setting it offers to grant that person's login the Branch Manager role for that branch. Granting needs explicit confirmation and is audited | M |
| FR-BR-07 | (5, 6, 10) **Transfers.** The transfer form records from-branch (read-only), to-branch, **effective date**, new department, designation and reporting manager (optional), reason and notes. Future-dated transfers are *scheduled* and applied by a job at 00:00 in the destination branch's time zone. Back-dated transfers are allowed for SA/HR up to the start of the current month, and not before the employee's previous transfer | M |
| FR-BR-08 | **Transfer history.** The employee profile shows a timeline of branch, department, designation and manager periods. History is append-only. A *scheduled* transfer can be cancelled before it takes effect | M |
| FR-BR-09 | **Transfer side-effects** (shown as a checklist before confirming): open tasks stay with the employee (option: reassign, listed); future meetings stay; **pending leave and time approvals move to the new approver**; running timer unaffected; the old branch loses access to the profile after the effective date but keeps the history in its reports for earlier dates | M |
| FR-BR-10 | (7) **Department and designation masters.** Department list (name, code, status) and designation list (name, optional department, status) are built at migration from the values already used. A branch has a set of departments, each with an optional head. Employee `dept` stays a name string for compatibility but must exist in the master once it is enabled. Renaming a department updates every employee record in one transaction | M |
| FR-BR-11 | (8) **Branch-wise employee management.** The employee list has a branch filter (default = the user's branches). BA/HR can add, edit and deactivate employees only in their scope | M |
| FR-BR-12 | (9) **Branch-wise attendance.** The attendance register filters by branch. Late calculation uses: employee shift → **branch working hours** → company default. Check-in sites get an optional branch. If set, an employee can check in only at their branch's sites or secondary branches' sites | M |
| FR-BR-13 | **Branch holidays.** Calendar events of type holiday get an optional *Branches* list (empty = all). Working-day calculations, reminders and payroll absent-days respect branch holidays | M |
| FR-BR-14 | (10) **Branch-wise leave.** Leave lists and reports filter by branch. Branch Admins can approve leave for their branch's employees, in addition to the reporting-line manager. Nobody approves their own leave (unchanged rule) | M |
| FR-BR-15 | (11, 12) **Branch-wise tasks and meetings.** Tasks and meetings carry a branch (task defaults to the assignee's primary branch; meeting defaults to the organizer's). Lists filter by branch | M |
| FR-BR-16 | (13, 14) **Reports.** Every v6 report and the existing Work report and Monthly review get a branch filter. A consolidated **Branch comparison** report (SA/CA, or HR across their branches) is added. Records are attributed to the branch **in effect on the record's date** | M |
| FR-BR-17 | (15) **Branch-level permissions** are enforced in the backend for **all** existing and new actions (see the touch-point list in `10-implementation-plan.md §4`) | M |
| FR-BR-18 | **Single-branch invisibility.** While a company has only one active branch, branch fields, filters and the branch column are hidden everywhere, except Branch Management for the SA | S |
| FR-BR-19 | **Login scoping.** HR/BA see and manage only logins of employees in their scope. They can create logins with roles up to Branch Manager, never another admin | M |

## 4. Module 3 — Meeting schedule management (FR-MTG)

| ID | Requirement | Pri |
|---|---|---|
| FR-MTG-01 | **Create and edit meetings** with all fields in `04-specifications.md §5.3`. Any user with `meeting.create` (all roles by default) can organise. Employees can invite anyone in their own branch(es). Managers and above can invite anyone in their scope | M |
| FR-MTG-02 | **10 categories** (Online, Direct, Client, Team, Management, One-to-One, Interview, Training, Vendor, Other). The category sets defaults: location type, visibility and conditional fields | M |
| FR-MTG-03 | **Location type is a separate field** (Online / In office / External venue / Hybrid). Its default comes from the category but it can be changed (a client meeting can be online) | M |
| FR-MTG-04 | **Conditional logic.** Online → platform and URL (https only). In office → branch and room. External venue → address. Client → client picker (from Clients module, or free text) with company and location, and external participants allowed. Team → department picker that bulk-adds its members. Interview → candidate picker (if Recruitment is on), private by default. Other → custom type text (required) | M |
| FR-MTG-05 | **Duration.** Auto-calculated from start and end, or the user enters a duration and the end is calculated. Minimum 5 min, maximum 12 h. A meeting cannot span midnight (longer events use the Calendar module) | M |
| FR-MTG-06 | **Participants.** Internal (employee picker with availability), departments (expand to members), external (name, e-mail, optional phone and company). Roles: organizer, required, optional. Maximum 200 participants | M |
| FR-MTG-07 | **Conflict detection** at save and live in the form. **Room** double booking → **blocked**. **Person** overlap (organizer or required participants with another non-declined meeting), **approved leave**, **weekly off/branch holiday**, **outside branch working hours** and **external calendar busy** (if connected) → **warning listing the conflicts**. Saving needs "Schedule anyway" | M |
| FR-MTG-08 | **Rooms.** Room master per branch (name, capacity, facilities, status). Capacity below the participant count → warning | S |
| FR-MTG-09 | **Recurrence.** Daily, weekly (chosen weekdays), monthly (day of month, or *n*th weekday). Ends on a date or after *N* occurrences (max 52, or 12 months). Occurrences are created as individual meetings linked to a series. Edit or cancel *this occurrence*, *this and following* or *the whole series* | M |
| FR-MTG-10 | **Agenda and description.** Agenda as an ordered list of items (title, presenter optional, minutes optional), plus a rich-free plain-text description up to 5,000 characters | M |
| FR-MTG-11 | **Attachments** using FR-PLT-07 | M |
| FR-MTG-12 | **Reminders.** Default offsets from settings (e.g. 1 day and 15 min). The organizer can override with up to 3 offsets between 5 min and 7 days. Channels follow each participant's preferences | M |
| FR-MTG-13 | **Invitations** are sent on create, and to participants added later. Updates are sent only when *material* fields change (date, time, location, room, URL, cancellation). Removed participants get a cancellation | M |
| FR-MTG-14 | **RSVP.** Participants accept, decline (optional reason) or mark tentative from the app or the WhatsApp/e-mail link (signed, single-meeting token). The organizer sees the counts. Calendar RSVPs flow back when sync is on | M |
| FR-MTG-15 | **Status lifecycle:** `scheduled → in_progress → completed`, or `→ cancelled` (reason required). `in_progress` is shown automatically between start and end. The organizer marks *completed* (or it is flagged "Awaiting update" 2 h after the end) | M |
| FR-MTG-16 | **Minutes.** Summary, discussion points, decisions, action items (title, assignee, due date, priority, estimate) and follow-up meeting date. Saved as draft or **published**, which notifies participants in-app and by e-mail. Editable by the organizer (and their delegates) for 7 days after publishing, then locked, with corrections audited | M |
| FR-MTG-17 | **Action items → tasks.** "Create task" per item or "Create all". This creates a standard task (`src = meeting`) with title, assignee, due, priority, estimate, branch and a link back. The action item's status mirrors the task's status | M |
| FR-MTG-18 | **Follow-up meeting.** "Schedule follow-up" opens a pre-filled form (same participants, category, agenda from open action items) | S |
| FR-MTG-19 | **Attendance tracking.** After start, the organizer marks each internal participant Present, Late or Absent. External participants: Present or Absent. Used in the meeting attendance report | S |
| FR-MTG-20 | **Visibility.** `normal` (title and time visible to the scope's managers and admins) or `private` (others see "Busy"). One-to-One and Interview are private by default | M |
| FR-MTG-21 | **Meeting views.** *My meetings* (organised or invited), *All meetings* (scope-filtered, MG and above), *Meeting calendar* (day, week, month, with overlays for leave and holidays), *Meeting minutes* (list), *Meeting reports* | M |
| FR-MTG-22 | **Calendar module overlay.** The existing Calendar page shows the user's meetings in each day cell (with a toggle) and links to the meeting. Meetings are not copied into `events` | S |
| FR-MTG-23 | **Calendar sync** as specified in `04-specifications.md §2` | M |
| FR-MTG-24 | **Join button** for online meetings, active from 10 min before start until the end. It opens the URL in a new tab (`noopener`) | M |
| FR-MTG-25 | **Timer interplay.** If the user has a running task timer when a meeting starts, the widget offers "Pause timer for meeting" (pause reason = Meeting). Never automatic unless the policy enables it | S |
| FR-MTG-26 | **Record past meeting.** A meeting can be created for a past time with "Record only" (no invitations, no sync), so minutes of an unplanned meeting can be captured | S |

## 5. Module 4 — Work time calculation and task tracking (FR-TIME)

| ID | Requirement | Pri |
|---|---|---|
| FR-TIME-01 | **Task form enhancements** (added to the existing task form, nothing duplicated): *Estimated duration* (h:mm, 0:05–999:59), *Expected working hours per day* (h:mm, ≤ branch daily hours), *Start date* (≤ due), *Department* (defaults from assignee), *Branch* (defaults from assignee, read-only for MG/EM), *Attachments*, *Time tracking required* toggle, *Allow manual entries* toggle. Existing fields stay: title, details (description), work type (= *task category*), quantity, client, assignee, due, priority, status | M |
| FR-TIME-02 | **Creator recorded.** `createdBy` and `createdAt` are set by the server on creation | M |
| FR-TIME-03 | **Timer actions inside each task** (card and detail): Start work, Pause, Resume, Stop, Complete task, Add manual entry. Buttons appear according to state (see the state machine in `04-specifications.md §3`) | M |
| FR-TIME-04 | **Persistent timer bar.** While a session is open, a compact bar (desktop: top of main area; mobile: above the bottom nav) shows the task, elapsed work time (live), state, and Pause/Resume/Stop buttons, on every page | M |
| FR-TIME-05 | **Server-side timestamps.** All state changes use the server's time. Client time is stored for diagnostics only. Durations are computed on the server | M |
| FR-TIME-06 | **Recorded per action:** employee id, task id, session id, action, server timestamp, actor, reason code (pause), remarks, request id (idempotency), client timestamp, IP. **Per session:** start, end, total, work and pause durations, status, remarks, branch and department snapshot | M |
| FR-TIME-07 | **Single active timer.** By default an employee can have only **one running** session. Starting another offers "Switch: stop *A* and start *B*" (one atomic call). An employee may have at most one open (running or paused) session per task. Policy can allow parallel timers per branch (off by default) | M |
| FR-TIME-08 | **Multiple sessions per task** are supported without limit | M |
| FR-TIME-09 | **Survives closing the browser.** Sessions live on the server. Re-opening the app restores the bar. **Logout** pauses a running session when the policy `pauseOnLogout` is on (default: on) | M |
| FR-TIME-10 | **Auto-stop.** A job auto-stops open sessions at the earliest of: (a) a session longer than `maxSessionHours` (default 10), (b) branch day end + `autoStopGraceMin` (default 120), (c) 23:59 branch time. The session ends at the **cut-off time**, not when the job runs. It is flagged `needs_review` and the employee is notified (N18) | M |
| FR-TIME-11 | **Interruption reasons** on pause: Break, Meeting, Client call, Waiting for input, System issue, Switched task, Other (note required). Reported in the interruption report | S |
| FR-TIME-12 | **Manual time entry.** Date, start and end (or a duration), reason (required, ≥ 10 characters), category. Status `pending` until approved. It must not overlap the employee's other sessions, not be in the future, not be older than `manualMaxDaysBack` (default 7) and not push the day total above 16 h | M |
| FR-TIME-13 | **Approval.** Approvers: reporting manager, the branch's BM/BA, and CA/SA (never self). Approve or reject with a comment (required on reject). Bulk approve. Pending entries show separately and are **excluded from "actual"** until approved | M |
| FR-TIME-14 | **Adjustments.** Corrections to a closed session go through an *adjustment request* (new start/end, reason) with the same approval. The original values are kept and the adjustment links to them | M |
| FR-TIME-15 | **Protection.** Stopped sessions cannot be edited or deleted through any API, except: approved adjustment, or SA/CA override with reason (audited). Sessions are never hard-deleted. Voiding is audited | M |
| FR-TIME-16 | **Calculations** (see `04-specifications.md §3.4`): estimated, actual (approved), pending, remaining, variance and efficiency, shown on the task card (compact), task detail (full) and reports | M |
| FR-TIME-17 | **Policy configuration.** Company policy (SA/CA), branch override (BA) and task-level toggles (task creator / MG). Fields: allowParallel, pauseOnLogout, maxSessionHours, autoStopGraceMin, allowManual, manualMaxDaysBack, requireApprovalForManual (default on), requireCheckIn (default off; blocks starting a timer when not checked in today), blockOnLeave (default on), overrunWarnPct (default 80) | M |
| FR-TIME-18 | **Task status interplay.** *Start work* on a `todo` task moves it to `doing`. *Complete task* stops the open session and sets `done` (existing rules apply: the employee cannot reopen). A `done` task cannot be timed until a manager reopens it | M |
| FR-TIME-19 | **Reallocation.** Changing a task's assignee stops the previous assignee's open session (reason `reassigned`) and notifies both. Their logged time stays on the task, attributed to them | M |
| FR-TIME-20 | **Separate from attendance and payroll.** Task time appears in its own columns and reports, labelled "Task time". Attendance hours keep the label "Attendance hours". Payroll is unaffected | M |
| FR-TIME-21 | **Workload overview.** Per employee for the next 5 working days: capacity (branch hours − approved leave − meetings) vs. planned load (Σ remaining estimate spread across days to due, or the daily allocation) | S |
| FR-TIME-22 | **Deadline warning.** A task is *at risk* when remaining estimate > remaining capacity until due, or when actual ≥ `overrunWarnPct` % of estimate. Shown as a badge and triggers N07/N09 | S |

## 6. Role and permission matrix

Legend: **✓** = all records · **B** = in the user's authorised branch(es) · **T** = reporting-line team · **O** = own · **P** = as organizer/participant · **—** = no access.
CA keeps today's company-wide admin rights, so existing customers are unaffected.

| Capability | SA | CA | HR | BA | BM | MG | EM |
|---|---|---|---|---|---|---|---|
| **Branches**: create, edit, deactivate | ✓ | — | — | — | — | — | — |
| View branch list and details | ✓ | ✓ | B | B | B | O | O |
| Assign Branch Admins | ✓ | — | — | — | — | — | — |
| Grant Branch Manager role | ✓ | ✓ | B | B | — | — | — |
| Departments and designations master | ✓ | ✓ | view | view | view | view | — |
| Rooms | ✓ | ✓ | B | B | B | view | view |
| **Employees**: view | ✓ | ✓ | B | B | B | T | O |
| Employees: create, edit, deactivate | ✓ | ✓ | B | B | — | — | own fields |
| See salary (`ctc`) | ✓ | ✓ | B | — | — | — | O |
| **Transfer employees** | ✓ | ✓ | B (both branches) | — | — | — | — |
| Secondary branch access | ✓ | ✓ | B | B | — | — | — |
| Logins: manage | ✓ | ✓ (up to MG) | B (up to BM) | B (up to BM) | — | — | — |
| **Attendance**: view | ✓ | ✓ | B | B | B | T | O |
| Attendance: mark or correct | ✓ | ✓ | B | B | — | T (today) | — |
| **Leave**: approve | ✓ | ✓ | B | B | T | T | — |
| **Tasks**: view | ✓ | ✓ | B | B | B | T | O |
| Tasks: create, assign, edit | ✓ | ✓ | B | B | B | T | — |
| **Timer**: use on own tasks | ✓* | ✓* | ✓* | ✓* | ✓ | ✓ | ✓ |
| Time logs: view | ✓ | ✓ | B | B | B | T | O |
| Manual time: approve | ✓ | ✓ | — | B | B | T | — |
| Time policy | ✓ | ✓ | — | B (override) | — | task-level | — |
| **Meetings**: create | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ (own branches) |
| Meetings: view all in scope | ✓ | ✓ | B | B | B | T | P |
| Meetings: edit or cancel | ✓ | ✓ | B | B | B | P (organizer) | P (organizer) |
| Minutes: write and publish | organizer + SA/CA/BA(B) | | | | | | |
| **Notifications**: provider config | ✓ | — | — | — | — | — | — |
| Templates and event config | ✓ | ✓ | view | view | — | — | — |
| Send individual, department or branch | ✓ | ✓ | B | B | B (branch announcements only) | — | — |
| Bulk / scheduled | ✓ | ✓ | B | B | — | — | — |
| Notification history | ✓ | ✓ | B | B | — | — | O (own inbox) |
| Own notification preferences | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| **Calendar**: connect own calendar | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| **Reports**: consolidated / branch comparison | ✓ | ✓ | B (their branches) | — | — | — | — |
| Branch reports | ✓ | ✓ | B | B | B | T | O |
| Audit log | ✓ | ✓ | B | B | — | — | — |
| Company settings, restore | ✓ | — | — | — | — | — | — |

\* Only when the person is linked to an employee record (admins often are not).

**Role storage:** `Profile.role` takes the new values `hradmin`, `branchadmin`, `branchmgr`, mapped to rank 3, 3 and 2 so existing rank checks keep working. A new `UserBranchScope(user, branch)` table holds their branches. Every rank-3 shortcut in the code (e.g. `view_for` returning everything) is changed to "rank ≥ 3 **and** company-wide scope".

## 7. Business rules

### 7.1 Platform
- **BR-P1** Scope is enforced on the server for every read, write, export, attachment download and webhook-triggered action. A UI-only restriction is a defect.
- **BR-P2** A user acting on a record outside their scope gets **404** (not 403), so the record's existence is not revealed.
- **BR-P3** Server-owned fields are never taken from client input (FR-PLT-12).
- **BR-P4** All v6 timestamps are stored in UTC and shown in the viewer's branch time zone (or a personal override).
- **BR-P5** Secrets are never logged, exported, backed up or returned.

### 7.2 Notifications
- **BR-N1** A business-initiated WhatsApp message must use an **approved** template. If none exists for the event and language, try SMS, then in-app.
- **BR-N2** An SMS must use a template registered with DLT (with a DLT id set) when the provider country is India.
- **BR-N3** Send only to **verified** numbers with **opt-in** for that channel. Otherwise skip that channel and record the reason.
- **BR-N4** Opt-out takes effect immediately, including for already queued messages not yet sent.
- **BR-N5** One delivery per `dedupe_key`. Re-saving a task or a meeting without a material change sends nothing.
- **BR-N6** Mandatory events (leave decision, meeting cancelled, meeting time changed) always reach at least the in-app channel.
- **BR-N7** Messages for events about an entity that no longer qualifies at send time are cancelled. Examples: a reminder for a cancelled meeting, or a deadline reminder for a task completed in the meantime.
- **BR-N8** Inactive employees receive no notifications except the exit-related ones.
- **BR-N9** A message's variables are rendered at **send time** (latest data), except for history, which stores what was actually sent.

### 7.3 Branches
- **BR-B1** Every active employee has exactly one primary branch on every date from their joining date.
- **BR-B2** Primary-branch periods for one employee never overlap and leave no gaps.
- **BR-B3** Branch code: 2–10 characters, A–Z, 0–9 and hyphen, unique for all time (including inactive), **unchangeable once any record references it**.
- **BR-B4** Branch name is unique among active branches.
- **BR-B5** A branch cannot be deactivated while it has active primary employees, scheduled transfers into it, future meetings in its rooms or open scheduled campaigns. The system lists the blockers.
- **BR-B6** The head office cannot be deactivated. HO can be moved to another branch by the SA.
- **BR-B7** Historical records (attendance, leave, tasks, sessions, meetings) stay attributed to the branch in effect on their date. Transfers never re-label history.
- **BR-B8** Only one *scheduled* (future) transfer per employee at a time.
- **BR-B9** An HR Admin can transfer only between two branches they are both authorised for. Otherwise SA/CA must do it.
- **BR-B10** Deactivated branches appear in reports (marked inactive) and filters (under "Show inactive"), never in create/assign pickers.

### 7.4 Meetings
- **BR-M1** End is after start. Duration is 5 min–12 h, on the same date in the meeting's time zone.
- **BR-M2** A new meeting cannot start in the past (5 min tolerance), unless it is "Record only".
- **BR-M3** Rooms cannot be double-booked (overlap = start < other.end AND end > other.start, for non-cancelled meetings).
- **BR-M4** Person conflicts warn, never block.
- **BR-M5** Online meetings need a URL starting with `https://`, unless auto-generated by a connected calendar.
- **BR-M6** Only the organizer (or SA/CA/BA/BM in scope) can edit or cancel. Participants can only RSVP.
- **BR-M7** Cancelled and completed meetings cannot be edited (except minutes and attendance for completed ones).
- **BR-M8** External participants never receive WhatsApp/SMS unless their phone *and* consent are recorded. By default they get e-mail and `.ics` only.
- **BR-M9** Private meetings show only "Busy" plus the time to non-participants (admins in scope see the title).
- **BR-M10** Action items converted to tasks follow task rules. The assignee must be in the converter's task-assign scope.

### 7.5 Time tracking
- **BR-T1** One running session per employee unless the policy allows parallel timers. Never more than one open session per employee per task.
- **BR-T2** Paused time is never counted as work time.
- **BR-T3** Every action is recorded with a server timestamp.
- **BR-T4** Closing the browser does not change session state. Logout pauses only when `pauseOnLogout` is on.
- **BR-T5** Durations come only from server timestamps.
- **BR-T6** Manual entries need a reason (≥ 10 characters).
- **BR-T7** Manual entries and adjustments need approval (when `requireApprovalForManual`), and never from the requester themself.
- **BR-T8** Stopped sessions are immutable except through approved adjustments or an audited admin override.
- **BR-T9** Managers configure rules within the policy hierarchy: company → branch → task.
- **BR-T10** Task time is reported separately from attendance and payroll hours and never changes them.
- **BR-T11** Unlimited sessions per task.
- **BR-T12** Duplicate active timers are prevented by a DB constraint and an idempotent request id.
- **BR-T13** Timers can be started only by the task's assignee, on tasks that are not `done`, when the employee is active and (if `blockOnLeave`) not on approved full-day leave today.
- **BR-T14** Auto-stopped sessions end at the cut-off time. Time after the cut-off is never counted automatically.
- **BR-T15** Session time is attributed to the **calendar day of each work interval** in branch time. Reports split intervals at midnight.

## 8. Non-functional requirements

| ID | Category | Requirement |
|---|---|---|
| NFR-P1 | Performance | v6 endpoints p95 < 400 ms, timer actions p95 < 250 ms, measured with a tenant of 500 active employees, 25 branches, 2 years of attendance, 50k sessions and 20k meetings on the reference VPS (2 vCPU, 4 GB) |
| NFR-P2 | Performance | Reports over 12 months for 500 employees return the first page in < 3 s. CSV export ≤ 100k rows streams in < 30 s |
| NFR-P3 | Performance | v6 data is **never** added to `load`, except a small task-time summary map (≤ 50 bytes per task) and the branch list |
| NFR-P4 | Throughput | Dispatcher sustains 20 messages/s per tenant (limited by the provider tier). 1,000 bulk recipients are queued in < 5 s |
| NFR-S1 | Scalability | Supported in v6: ≤ 500 active employees and ≤ 50 branches per tenant on SQLite. Beyond that needs the v6.1 PostgreSQL track. All new queries are indexed and paged (default page 50, max 200) |
| NFR-S2 | Scalability | The job runner handles all tenants on one host within the 60 s tick. Back-pressure: at most 100 jobs per tenant per tick |
| NFR-A1 | Availability | Provider or calendar outages never block the app. Business actions succeed and jobs retry |
| NFR-A2 | Reliability | No lost timer actions: every acknowledged action is committed. Retrying the same request id is a no-op |
| NFR-SEC1 | Security | Scope tests (IDOR) for every v6 endpoint and every changed legacy action are part of CI |
| NFR-SEC2 | Security | Webhooks verify signatures. OAuth uses PKCE and a signed `state` with a 10-minute expiry |
| NFR-SEC3 | Security | Bulk send and export endpoints are rate-limited (per user: 10 bulk sends/hour, 30 exports/hour) |
| NFR-SEC4 | Security | Phone numbers are masked (`+91 98•••••321`) for roles without the employees-edit capability |
| NFR-PR1 | Privacy | Consent records are kept for the employment period + 3 years. Message bodies 180 days. Webhook payloads 30 days. All configurable downward |
| NFR-PR2 | Privacy | Employee self-service export of their own notification history and time logs (CSV) |
| NFR-U1 | Usability | Reuse the existing components (panel, modal, field, pill, toolbar, tiles, table `stk`, toast, confirm). No new visual language. Light and dark both supported |
| NFR-U2 | Accessibility | WCAG 2.1 AA: keyboard reachable, visible focus, labelled controls, `aria-live` for timer state changes, respects `prefers-reduced-motion` (already in the codebase) |
| NFR-M1 | Mobile | All new screens work at 360 px. The timer bar and meeting widget are usable with one thumb. Forms become slide-up sheets (existing pattern) |
| NFR-C1 | Compatibility | All existing actions, payload keys and screens keep working. Old open browser tabs keep saving safely (FR-PLT-12). Latest two versions of Chrome, Edge, Safari and Firefox, Android 10+, iOS 15+ |
| NFR-C2 | Compatibility | Single-company (non-SaaS) and SaaS editions both supported. Every new URL is added to both `config/urls.py` and `saas/urls_tenant.py` |
| NFR-O1 | Observability | Structured logs for jobs, deliveries and sync. An admin "System health" panel: queue depth, failures in the last 24 h, provider status, calendar connections with errors |
| NFR-MT1 | Maintainability | New code lives in separate modules (`core/branches.py`, `core/notify/`, `core/meetings.py`, `core/worktime.py`, `core/calsync/`, `core/jobs.py`), with unit tests ≥ 80 % line coverage on calculation and scope code |
| NFR-L1 | Localisation | Dates and times by branch time zone, `en-IN` formatting. Template languages per FR-NOT-04 |
| NFR-CSP | Security | No new third-party script origins. All integrations are server-side. The CSP stays as it is |
