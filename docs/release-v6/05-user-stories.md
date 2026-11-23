# 05 — Detailed User Stories

US-01 … US-26 come from the brief. **US-27 … US-30** were added to cover workflows the brief requires but does not give as stories: manual-time approval, minutes and action items, RSVP, and phone consent.
Error codes are the standard API codes listed in `07-api-spec.md §1.3`. FR / BR / N / S references point to `03-SRS.md` and `04-specifications.md`.

**Index**
| ID | Module | Role | Title | Pri |
|---|---|---|---|---|
| US-01 | M1 | Super Admin | Configure WhatsApp and SMS providers | Must |
| US-02 | M1 | HR Admin | Send notifications to one or many employees | Must |
| US-03 | M1 | Super Admin / Company Admin | Configure notification events | Must |
| US-04 | M1 | Employee | Receive important notifications | Must |
| US-05 | M1 | Super Admin / Company Admin | View delivery history and failed messages | Must |
| US-06 | M2 | Super Admin | Create and manage branches | Must |
| US-07 | M2 | Super Admin | Assign administrators to branches | Must |
| US-08 | M2 | HR Admin | Assign employees to branches | Must |
| US-09 | M2 | Branch Admin | Manage my branch's employees | Must |
| US-10 | M2 | Super Admin / HR Admin | Transfer employees between branches | Must |
| US-11 | M2 | Management | Consolidated and branch-wise reports | Must |
| US-12 | M3 | Employee | Schedule meetings | Must |
| US-13 | M3 | Manager | Schedule meetings with many employees | Must |
| US-14 | M3 | User | Select meeting categories | Must |
| US-15 | M3 | User | Add agenda and description | Must |
| US-16 | M3 | User | Specify duration and location | Must |
| US-17 | M3 | User | Sync meetings with my calendar | Must |
| US-18 | M3 | Employee | Receive invitations and reminders | Must |
| US-19 | M3 | Manager | View all team meetings | Must |
| US-20 | M4 | Manager | Assign tasks with an estimate | Must |
| US-21 | M4 | Employee | Start a timer | Must |
| US-22 | M4 | Employee | Pause and resume the timer | Must |
| US-23 | M4 | Employee | Manual time entry with a reason | Must |
| US-24 | M4 | Manager | Compare estimated and actual time | Must |
| US-25 | M4 | Admin | View employee-wise working hours | Must |
| US-26 | M4 | Manager | View task-wise time logs | Must |
| US-27 | M4 | Manager | Approve or reject manual time | Must |
| US-28 | M3 | Organizer | Record minutes and turn action items into tasks | Must |
| US-29 | M3 | Participant | Respond to an invitation (RSVP) | Should |
| US-30 | M1 | Employee | Verify my phone and manage consent and preferences | Must |

---

## Module 1 — WhatsApp and SMS alerts

### US-01 · Configure WhatsApp and SMS providers
| Field | Detail |
|---|---|
| Module / Role | M1 · Super Admin (the brief says "Admin". Credentials are company-wide secrets, so only the SA has this capability) |
| Story | As a Super Admin, I want to configure WhatsApp and SMS providers so that the system can send notifications automatically. |
| Business value | Unlocks every automated alert (BO-1). One set-up per company |
| Preconditions | `notify` module available in the plan and switched on. WhatsApp: a Meta Business account with a WhatsApp phone number id, a permanent access token and an app secret. SMS (India): DLT entity id, an approved header and a gateway account |
| Traces | FR-NOT-01, 02, 03, 05, FR-PLT-05, 14 · S-07 |
| Priority / Dependencies | Must · WS0 secrets encryption, webhook ingress, job runner |

**Main workflow**
1. SA opens *Communication → WhatsApp*.
2. Chooses provider *Meta WhatsApp Cloud API*, enters phone number id, business account id, access token and app secret.
3. The system shows the **webhook URL** and a generated **verify token** to paste into Meta.
4. SA clicks **Test connection**. The system calls the provider (phone-number lookup) and shows the display number, quality rating and messaging tier.
5. SA clicks **Sync templates**. Approved templates are imported.
6. SA switches WhatsApp **on**. The status shows *Active*.
7. Repeats on *Communication → SMS* (MSG91: auth key, sender id/header, DLT entity id) and **Test SMS** to their own verified number.

**Alternative workflows**
- A1 *SMS only*: WhatsApp is left off. Events with WhatsApp in their channel order skip it (`channel_off`).
- A2 *Replace a credential*: "Replace" opens an empty field. The old value is never displayed. The change is audited.
- A3 *Disable a channel*: queued messages for that channel are cancelled (status `cancelled`, reason `channel_off`) after confirmation.

**Acceptance criteria**
- AC1 **Given** valid credentials, **when** the SA clicks *Test connection*, **then** the provider's phone number and tier are shown and the provider status becomes `verified`.
- AC2 **Given** an invalid token, **when** testing, **then** the provider's error message is shown, the status is `error` and the channel cannot be switched on.
- AC3 **Given** saved credentials, **when** any user (including the SA) loads the page or the `load` payload, **then** secrets appear only masked (last 4 characters) and never in the response, `settings`, logs or backups.
- AC4 **Given** the webhook is set in Meta, **when** Meta sends the verification challenge with the correct verify token, **then** the endpoint echoes the challenge. With a wrong token it returns 403.
- AC5 **Given** WhatsApp is switched off, **when** an event fires, **then** no WhatsApp delivery is attempted and a `skipped` record with reason `channel_off` is stored.
- AC6 **Given** a user with role CA, HR or BA, **when** they call any provider-config endpoint, **then** they receive 403 / `FORBIDDEN`.

**Validation rules:** all credential fields required to save; phone number id numeric; DLT entity id 19 digits; sender header 6 letters (India); webhook verify token generated, 32 characters.
**Error scenarios**
| Case | Code | Message |
|---|---|---|
| Provider unreachable during test | `PROVIDER_ERROR` | "Could not reach WhatsApp. Check your internet connection or try again in a minute." |
| Token expired | `PROVIDER_ERROR` | "WhatsApp says the access token is not valid. Create a permanent token in Meta Business settings." |
| Encryption key missing on server | `SERVER_CONFIG` (500, logged) | "Secure storage is not set up on the server. Ask your system administrator." |

---

### US-02 · Send notifications to one or many employees
| Field | Detail |
|---|---|
| Module / Role | M1 · HR Admin (also SA, CA, BA within scope; BM for branch announcements) |
| Story | As an HR Manager, I want to send notifications to individual employees or multiple employees. |
| Business value | Fast, read-for-sure communication (policies, urgent changes, events) without separate WhatsApp groups that expose personal numbers |
| Preconditions | At least one channel active (US-01). Approved/registered template available. Sender has the `notify.send` capability |
| Traces | FR-NOT-06…11, 22, 23, 25 · S-10, S-11 · BR-N1…N5 |
| Priority / Dependencies | Must · US-01, US-30 (consent), FR-PLT-01 (scope) |

**Main workflow**
1. HR opens *Communication → Send*.
2. Chooses the audience: **Individuals** (picker) / **Department** / **Branch** / **Designation**, combined as AND filters, only within HR's authorised branches.
3. The system shows the **recipient preview**: total, will receive per channel, skipped by reason.
4. HR picks the template (only approved ones are listed for WhatsApp), fills the free variables (for example `announcement_title`) and sees a preview per language.
5. HR chooses *Send now* or *Schedule* (date/time).
6. Over 200 recipients: a second confirmation shows the estimated cost.
7. The system creates a **campaign**, queues the deliveries and shows progress (queued / sent / delivered / failed).

**Alternative workflows**
- A1 *Schedule*: the campaign is `scheduled`. It can be edited or cancelled until 1 minute before (FR-NOT-11).
- A2 *Branch announcement from Notice board*: publish a notice with audience + "Also send via WhatsApp" → same pipeline (FR-NOT-25).
- A3 *Recipient becomes inactive before a scheduled send*: skipped with `inactive`.

**Acceptance criteria**
- AC1 **Given** HR is authorised for HO and BLR, **when** they open the audience picker, **then** only HO and BLR branches and their employees are selectable, and an API call with a MAA employee id returns `NOT_FOUND` for that recipient.
- AC2 **Given** 142 matched recipients of whom 5 have no consent, 3 no number and 1 opted out, **when** previewing, **then** the preview shows "142 · 133 will receive · 9 skipped (5 no consent, 3 no number, 1 opted out)".
- AC3 **Given** a campaign is sent, **when** it is processed, **then** each recipient gets **at most one** delivery per channel (unique `dedupe_key`), even if HR presses Send twice.
- AC4 **Given** a scheduled campaign at 10:00, **when** HR edits it at 09:58, **then** the edit is rejected with `LOCKED` ("Sending has started or is about to start").
- AC5 **Given** a WhatsApp template not yet approved, **when** composing, **then** it is not offered for WhatsApp, and an API call using it returns `TEMPLATE_NOT_APPROVED`.
- AC6 **Given** the monthly cap is reached, **when** sending a non-mandatory campaign, **then** sending is blocked with `CAP_REACHED`, and the SA is told the current usage.

**Validation rules:** ≥ 1 recipient after filters; template and channel required; all required variables filled (non-empty, ≤ 200 characters each; WhatsApp variables cannot contain new lines or more than 4 consecutive spaces, per Meta); schedule ≥ now + 5 min and ≤ 90 days; bulk rate limit 10 campaigns per user per hour.
**Error scenarios:** `VALIDATION` (missing variable), `TEMPLATE_NOT_APPROVED`, `CAP_REACHED`, `RATE_LIMITED`, `LOCKED`, `NOT_FOUND` (out-of-scope recipient).

---

### US-03 · Configure notification events
| Field | Detail |
|---|---|
| Module / Role | M1 · Super Admin, Company Admin (HR, BA view; BA may set branch overrides for timing only) |
| Story | As an Admin, I want to configure notification events so that messages are triggered automatically based on system activities. |
| Business value | Each company tunes noise vs. coverage; branches with different shifts get correct timings |
| Preconditions | Event catalogue N01–N20 seeded with defaults at migration |
| Traces | FR-NOT-12, 16, 17, 24 · S-09 · `04 §1` |
| Priority / Dependencies | Must · US-01, templates (FR-NOT-04) |

**Main workflow**
1. Admin opens *Communication → Events*. A table lists every event with its enabled switch, channel order, SMS fallback (on/off, minutes), timing offsets, quiet-hours rule and template status per channel.
2. Admin edits, for example, *Attendance reminder*: enabled, offset 15 min before shift, WhatsApp then in-app, no SMS fallback.
3. Admin opens *Branch overrides* for BLR: offset 10 min.
4. Saves. The change is audited and applies to events triggered from now on.

**Alternative workflows**
- A1 Template missing for an enabled channel → warning badge "No approved template: this channel will be skipped".
- A2 Disabling an event cancels its queued, not-yet-sent deliveries after confirmation.

**Acceptance criteria**
- AC1 **Given** *Task assigned* is enabled with WhatsApp, **when** a manager assigns a task to Rahul (verified and opted in), **then** one WhatsApp delivery is queued within 60 s.
- AC2 **Given** *Attendance reminder* is offset 10 min for BLR (shift 09:30), **when** it is a BLR working day and Rahul has not checked in by 09:20, **then** he receives the reminder at 09:20 ± 1 min. If he checks in at 09:15, nothing is sent.
- AC3 **Given** the event is disabled, **when** the trigger happens, **then** no delivery is created (not even in-app).
- AC4 **Given** *Leave decided* (mandatory), **when** the admin tries to remove all channels, **then** the in-app channel stays locked on.
- AC5 **Given** a BA, **when** they try to change company-level channels, **then** `FORBIDDEN`. Branch timing overrides are allowed.

**Validation rules:** offsets: reminders 5 min–7 days, attendance −120…+120 min; fallback wait 0–120 min; at least one channel for mandatory events.
**Error scenarios:** `VALIDATION`, `FORBIDDEN`.

---

### US-04 · Receive important notifications
| Field | Detail |
|---|---|
| Module / Role | M1 · Employee |
| Story | As an Employee, I want to receive important notifications about attendance, leave, tasks and meetings. |
| Business value | Employees act on time: arrive on time, know their leave status, start tasks, join meetings |
| Preconditions | Employee active with a verified phone and opt-in (or in-app/e-mail only). Event enabled |
| Traces | FR-NOT-12, 13, 16, 17, 20, 22 · N02–N13 · BR-N3…N9 |
| Priority / Dependencies | Must · US-01, US-03, US-30 |

**Main workflow**
1. A business action happens (for example, leave approved).
2. Recipients are resolved per `04 §1`.
3. The employee receives a WhatsApp message (*"Hi Rahul, your leave on 4–5 Dec is Approved."*) and an in-app item in the bell.
4. Tapping the in-app item opens the leave page.

**Alternative workflows**
- A1 *WhatsApp fails permanently* (number not on WhatsApp) → SMS sent (if fallback is on and SMS is opted in).
- A2 *Quiet hours* (21:00–08:00) → non-urgent messages wait until 08:00. A meeting starting within 2 h is sent at once.
- A3 *No phone or consent* → in-app (and e-mail where configured) only.

**Acceptance criteria**
- AC1 **Given** a leave request is approved, **when** the decision is saved, **then** the employee gets exactly one WhatsApp message and one in-app item, with `leave_status = Approved`.
- AC2 **Given** WhatsApp returns "not a WhatsApp user", **when** fallback is on, **then** one SMS is sent within 1 min and linked to the failed WhatsApp delivery.
- AC3 **Given** the same task is saved three times without changing the assignee, **when** processing, **then** only one *Task assigned* notification exists.
- AC4 **Given** the employee switched off *Celebrations*, **when** their birthday arrives, **then** no WhatsApp message is sent (in-app stays, since in-app celebrations are shown to everyone as today).
- AC5 **Given** a meeting reminder is queued, **when** the meeting is cancelled before the send time, **then** the reminder is `cancelled` and the cancellation notice is sent instead.
- AC6 **Given** an inactive employee, **when** any non-exit event fires, **then** nothing is sent.

**Validation / errors:** handled by the dispatcher. Each skip is recorded with a reason. No user-facing errors.

---

### US-05 · View delivery history and failed messages
| Field | Detail |
|---|---|
| Module / Role | M1 · Super Admin, Company Admin (HR and BA scoped to their branches) |
| Story | As an Admin, I want to view notification delivery history and failed messages. |
| Business value | Find out why someone "never got the message", fix bad data, check costs |
| Preconditions | Deliveries exist |
| Traces | FR-NOT-13, 14, 15 · S-12 · R-16 |
| Priority / Dependencies | Must · webhooks (FR-PLT-14) |

**Main workflow**
1. Admin opens *Communication → History*. The default view is the last 7 days, all statuses.
2. Filters by status = Failed, branch = MAA.
3. Opens a delivery to see the rendered message, template version, provider id, timeline (queued 10:00:01, sent 10:00:02, failed 10:00:05 "131026 Message undeliverable") and the fallback SMS (delivered 10:00:40).
4. Selects 12 failed messages → **Retry** (only temporary failures can be retried; permanent ones show "Fix number first").
5. Exports CSV.

**Acceptance criteria**
- AC1 **Given** a BA for MAA, **when** they open History, **then** only deliveries to MAA employees (as of the send date) are listed, and the API returns `NOT_FOUND` for others.
- AC2 **Given** a provider webhook marks a message `delivered`, **when** History is refreshed, **then** the status and timestamp show within 60 s.
- AC3 **Given** a permanently failed delivery (invalid number), **when** Retry is pressed, **then** it is refused with "Number invalid: update the employee's phone", and nothing is sent.
- AC4 **Given** a temporarily failed delivery after 3 automatic attempts, **when** Retry is pressed, **then** a new attempt is made, and the attempt count and audit entry are recorded.
- AC5 **Given** 10,000 deliveries in the range, **when** exporting, **then** a CSV with all rows downloads with cells protected against formula injection.

**Validation:** date range ≤ 366 days; bulk retry ≤ 500 at a time. **Errors:** `RATE_LIMITED`, `VALIDATION`.

---

## Module 2 — Multi-branch

### US-06 · Create and manage branches
| Field | Detail |
|---|---|
| Module / Role | M2 · Super Admin |
| Story | As a Super Admin, I want to create and manage multiple branches. |
| Business value | One system for the whole company (BO-3) |
| Preconditions | `branches` module on (plan allows more than one branch). HO exists (created by migration) |
| Traces | FR-BR-01, 02, 18 · BR-B3…B6, B10 · S-01…S-03 |
| Priority / Dependencies | Must · WS0 migration |

**Main workflow**
1. SA opens *Branch Management* and clicks **Add branch**.
2. Enters: name "Bangalore", code "BLR", address, Karnataka, India, Bengaluru, contact, e-mail, manager (optional now), working hours 09:30–18:30, break 60, Mon–Sat, time zone Asia/Kolkata, status Active.
3. Saves. The branch appears in the list. Branch filters now appear across the app (FR-BR-18).
4. Later the SA edits the working hours. The change applies from the next day. Late calculations for past days do not change.
5. The SA deactivates branch "HYD-2" with reason "Office closed".

**Alternative workflows**
- A1 *Deactivate is blocked*: the system lists blockers (e.g. "4 active employees, 2 future meetings in its rooms") with links (BR-B5).
- A2 *Reactivate* an inactive branch: allowed. The code stays the same.
- A3 *Change head office*: SA marks another branch as HO. The previous HO loses the flag (one HO at all times).

**Acceptance criteria**
- AC1 **Given** code "BLR" exists (even inactive), **when** creating another "BLR", **then** `VALIDATION` "Branch code already used".
- AC2 **Given** the branch has employees, **when** the SA edits the code, **then** the field is read-only and the API rejects the change with `IMMUTABLE`.
- AC3 **Given** a branch with active employees, **when** deactivating, **then** `BRANCH_IN_USE` with the blocker counts.
- AC4 **Given** a deactivated branch, **when** opening the employee form, task form or meeting form, **then** it is not offered. **When** opening reports with "Show inactive", **then** its history is shown, labelled *(inactive)*.
- AC5 **Given** a BA, **when** calling `branches.save`, **then** `FORBIDDEN`.
- AC6 **Given** an upgrade of an existing single-office company, **when** migration finishes, **then** exactly one branch "Head Office (HO)" exists, carrying the company's working hours, and every employee is assigned to it from their joining date.

**Validation rules:** name 2–80 characters, unique among active branches; code per BR-B3; e-mail valid; contact E.164; PIN 6 digits (India) or 3–10 characters elsewhere; working days ≥ 1; end > start; break 0–240 min; IANA time zone.
**Error scenarios:** `VALIDATION`, `IMMUTABLE`, `BRANCH_IN_USE`, `FORBIDDEN`, `PLAN_LIMIT` (branch count over the plan limit).

---

### US-07 · Assign administrators to branches
| Field | Detail |
|---|---|
| Module / Role | M2 · Super Admin |
| Story | As a Super Admin, I want to assign administrators to specific branches. |
| Business value | Delegation without exposing other branches' data |
| Preconditions | Branch exists; the user has a login |
| Traces | FR-BR-05, 06, 19 · FR-PLT-01, 02 · §6 matrix |
| Priority / Dependencies | Must · scope service |

**Main workflow**
1. SA opens *Users* → edits Meera → Role **HR Admin** → *Branches*: HO, BLR. Saves.
2. Or SA opens *Branch BLR → Admins & manager* → **Add Branch Admin** → picks Kiran. Kiran's role becomes **Branch Admin** with branch BLR.
3. Kiran's sessions are signed out (the existing rule when a role changes) and his next login is scoped to BLR.

**Alternative workflows**
- A1 *Grant Branch Manager* from the branch's manager field: a confirmation dialog appears. Arjun's login gets role **Branch Manager** for BLR.
- A2 *Remove* a scope: the user loses access at once (sessions revoked).

**Acceptance criteria**
- AC1 **Given** Kiran is BA for BLR, **when** he loads the app, **then** employees, attendance, leave, tasks, meetings, reports and audit show only BLR data, plus records of employees with secondary access to BLR.
- AC2 **Given** Kiran sends a `save` op editing an HO employee (crafted request), **when** processed, **then** the op is rejected (counted in `rejected`) and nothing changes.
- AC3 **Given** HR/BA roles, **when** they create logins, **then** they can assign only Employee, Manager or Branch Manager roles, and only for employees in their scope.
- AC4 **Given** the last SA, **when** anyone tries to demote them, **then** the existing rule "at least one active super admin" still applies.
- AC5 **Given** role or scope changes, **when** saved, **then** an `AuditEvent` records before/after and the target user's sessions are revoked.

**Validation:** HR/BA must have ≥ 1 branch; BM must have ≥ 1 branch; scopes only on active branches.
**Errors:** `VALIDATION` ("Choose at least one branch"), `FORBIDDEN`.

---

### US-08 · Assign employees to branches
| Field | Detail |
|---|---|
| Module / Role | M2 · HR Admin (also SA, CA; BA for their own branch) |
| Story | As an HR Manager, I want to assign employees to branches. |
| Business value | Every person belongs somewhere, so reports and permissions are correct (BR-B1) |
| Preconditions | Branches exist; HR authorised for the target branch |
| Traces | FR-BR-03, 04, 10, 11 · BR-B1, B2 |
| Priority / Dependencies | Must · US-06 |

**Main workflow**
1. HR adds a new employee. The form now has **Branch** (required; defaults to HR's first branch), department (from the master, filtered to departments mapped to the branch) and designation.
2. Saves. The server creates the employee and a primary assignment effective from the joining date.
3. For a trainer working in BLR and MAA, HR opens the profile → *Branch history* → **Add secondary access** → MAA, from 1 Dec, reason "Trainer".

**Alternative workflows**
- A1 *Bulk assign* (initial roll-out): from the employee list, select employees → **Set branch** → effective date. This creates assignments (same rules as a transfer, without notifications by default).
- A2 *CSV import* of a branch column: v6.1 (out of scope).

**Acceptance criteria**
- AC1 **Given** the branches module is on with more than one branch, **when** saving an employee without a branch, **then** `VALIDATION` "Choose a branch".
- AC2 **Given** an employee saved by an old open browser tab (no `branchId` in the payload), **when** processed, **then** the stored `branchId` is preserved (FR-PLT-12).
- AC3 **Given** secondary access to MAA, **when** the MAA BA assigns a task or invites the trainer to a meeting, **then** the trainer is selectable. **When** the MAA BA opens the trainer's attendance, **then** it is not visible (it belongs to the primary branch).
- AC4 **Given** HR authorised only for HO, **when** assigning someone to BLR, **then** `FORBIDDEN`.

**Validation:** department must belong to the branch's department set (warning if it does not, unless a strict mapping is configured); secondary access cannot duplicate the primary branch, and its dates must lie within employment.
**Errors:** `VALIDATION`, `FORBIDDEN`, `OVERLAP`.

---

### US-09 · Branch Admin manages branch employees
| Field | Detail |
|---|---|
| Module / Role | M2 · Branch Admin |
| Story | As a Branch Admin, I want to manage employees belonging to my branch. |
| Business value | Branches run day-to-day operations without HO involvement |
| Preconditions | User has the BA role for ≥ 1 branch |
| Traces | FR-BR-11…15, 17, 19 · BR-P1, P2 |
| Priority / Dependencies | Must · US-07, scope touch-points (10 §4) |

**Main workflow**
1. Kiran (BA, BLR) opens Employees. Only BLR employees are listed (and HO trainers with BLR secondary access, labelled "secondary").
2. Edits an employee's designation, phone and photo. Saves.
3. Marks attendance corrections, approves leave, assigns tasks, schedules meetings and views BLR reports.

**Acceptance criteria**
- AC1 **Given** Kiran, **when** any list, report, export, attachment download, search (sidebar search) or dashboard widget loads, **then** no HO, MAA or HYD-only employee data is present in the response payload (verified at API level).
- AC2 **Given** a crafted `selfie` request for an HO employee, **when** called, **then** `NOT_FOUND`.
- AC3 **Given** the audit page, **when** Kiran opens it, **then** only events about BLR entities are shown.
- AC4 **Given** Kiran edits an employee's salary field, **when** saving, **then** the change is ignored (BA has no salary capability) and the field is not shown.
- AC5 **Given** Kiran tries to transfer an employee, **when** calling `branches.transfer`, **then** `FORBIDDEN` (transfers are HR/CA/SA).

**Errors:** `FORBIDDEN`, `NOT_FOUND`.

---

### US-10 · Transfer employees between branches
| Field | Detail |
|---|---|
| Module / Role | M2 · Super Admin (also CA; HR when authorised for both branches, BR-B9) |
| Story | As a Super Admin, I want to transfer employees between branches. |
| Business value | Accurate history and continuity of work (BO-4) |
| Preconditions | Employee active; destination branch active |
| Traces | FR-BR-07, 08, 09 · BR-B1, B2, B7, B8 · S-04, S-05 · N19 |
| Priority / Dependencies | Must · US-06, job runner (scheduled effect), notifications |

**Main workflow**
1. SA opens Rahul's profile → **Transfer**.
2. Chooses BLR, effective **1 Dec 2026**, new manager Arjun, same department and designation, reason "Team expansion".
3. The side-effects checklist shows: 3 open tasks (keep / reassign), 2 future meetings (kept), 1 pending leave approval (re-routes to Arjun), 0 running timers.
4. Confirms. The transfer is **Scheduled**. On 1 Dec 00:00 IST the job makes it effective: it ends the HO assignment on 30 Nov, starts BLR from 1 Dec, updates `branchId`, `reportsTo`, department and designation, re-routes pending approvals and sends N19.

**Alternative workflows**
- A1 *Effective today*: applied immediately.
- A2 *Back-dated* (SA/HR, ≥ start of current month, after the last transfer): attendance and sessions from that date are attributed to the new branch in reports. A warning shows the record counts affected.
- A3 *Cancel a scheduled transfer* before it takes effect.
- A4 *Transfer with reassignment of open tasks*: the chosen tasks get a new assignee, any open timer stops (FR-TIME-19) and both people are notified.

**Acceptance criteria**
- AC1 **Given** a transfer effective 1 Dec, **when** viewing HO's November attendance report on 5 Dec, **then** Rahul's November days are in HO and his December days are in BLR (BR-B7).
- AC2 **Given** a scheduled transfer exists, **when** creating another for the same employee, **then** `TRANSFER_EXISTS`.
- AC3 **Given** a back-dated transfer before the employee's previous transfer, **when** saving, **then** `VALIDATION`.
- AC4 **Given** the transfer took effect, **when** the HO BA opens Rahul's profile, **then** `NOT_FOUND`. HO reports still include his pre-December records.
- AC5 **Given** the transfer, **when** completed, **then** the branch history shows both periods with no gap or overlap, and an `AuditEvent` exists.
- AC6 **Given** HR authorised for HO and BLR but not MAA, **when** transferring HO→MAA, **then** `FORBIDDEN`.

**Validation:** effective date ≥ start of current month (back-dating), ≤ today + 180 days; destination ≠ current branch (unless only department or designation changes, which is not a transfer); reason required (3–300 characters).
**Errors:** `VALIDATION`, `TRANSFER_EXISTS`, `FORBIDDEN`, `BRANCH_INACTIVE`.

---

### US-11 · Consolidated and branch-wise reports
| Field | Detail |
|---|---|
| Module / Role | M2 · Management (SA, CA; HR across their branches; BA/BM for their branch) |
| Story | As Management, I want to view consolidated company reports and branch-wise performance. |
| Business value | Compare branches; spot problems (late arrivals, overruns, failed messages) |
| Preconditions | Data exists; branches module on |
| Traces | FR-BR-16 · R-12 and every report's branch filter |
| Priority / Dependencies | Must · branch attribution (assignment history) |

**Main workflow**
1. SA opens *Reports → Branch comparison*, chooses Last month.
2. Sees one row per branch with the R-12 metrics and a grouped bar chart. The company total row uses weighted figures (not an average of averages).
3. Clicks BLR → drills down to BLR's Work-time and Attendance reports with the filters carried over.
4. Exports CSV.

**Acceptance criteria**
- AC1 **Given** 4 branches, **when** an SA runs the report, **then** each branch's figures equal the sum of its branch-level reports for the same period (reconciliation test).
- AC2 **Given** an HR Admin for HO and BLR, **when** running it, **then** only HO and BLR rows and a "Your branches" total are shown.
- AC3 **Given** a BA, **when** opening Branch comparison, **then** it is not listed and the API returns `FORBIDDEN`.
- AC4 **Given** an employee transferred mid-month, **when** reporting that month, **then** their figures are split between branches by date.
- AC5 **Given** an inactive branch with history, **when** "Show inactive" is on, **then** its row appears, labelled *(inactive)*.

**Validation:** range ≤ 366 days. **Errors:** `FORBIDDEN`, `VALIDATION`.

---

## Module 3 — Meetings

### US-12 · Employee schedules a meeting
| Field | Detail |
|---|---|
| Module / Role | M3 · Employee |
| Story | As an Employee, I want to schedule meetings so that I can organize work discussions. |
| Business value | Meetings planned in the same place as the work. Invites and reminders are automatic |
| Preconditions | `meetings` module on; the employee has a linked employee record |
| Traces | FR-MTG-01…07, 12, 13 · S-17 · BR-M1…M5 |
| Priority / Dependencies | Must · notifications (invites), scope |

**Main workflow**
1. Employee clicks **New meeting** (W1 Quick create or *Meetings*).
2. Fills in title, category, date, start/end (duration auto-filled), participants (colleagues in their branch, with availability badges), location type and its conditional fields, agenda and reminders.
3. The live conflict panel shows any person or room conflicts.
4. Saves. The meeting is `scheduled`. Invitations go out (N10). The meeting appears in all participants' widgets.

**Alternative workflows**
- A1 *Person conflict* → "Schedule anyway" with the conflicts listed.
- A2 *Room conflict* → the save is blocked. The room picker shows free rooms.
- A3 *Record only* (past time) → no invites (FR-MTG-26).

**Acceptance criteria**
- AC1 **Given** valid input, **when** saving, **then** the meeting is stored with a UTC start/end and the branch time zone, and each internal participant receives one invitation.
- AC2 **Given** an employee, **when** they search participants, **then** only employees in their primary and secondary branches are offered, and a crafted participant id outside scope → `NOT_FOUND`.
- AC3 **Given** the end is before the start, **when** saving, **then** `VALIDATION` "End time must be after start time".
- AC4 **Given** a start in the past without *Record only*, **when** saving, **then** `VALIDATION`.

**Validation:** per §5.3 of 04. **Errors:** `VALIDATION`, `CONFLICT_ROOM`, `NOT_FOUND`, `MODULE_OFF`.

---

### US-13 · Manager schedules meetings with many employees
| Field | Detail |
|---|---|
| Module / Role | M3 · Manager (also BM, BA, HR, CA, SA) |
| Story | As a Manager, I want to schedule meetings with multiple employees. |
| Business value | Team syncs, trainings and reviews set up in seconds |
| Preconditions | As US-12 |
| Traces | FR-MTG-04 (Team), 06, 07, 09 |
| Priority / Dependencies | Must · US-12 |

**Main workflow**
1. Manager picks **Team meeting** → department picker → "Design (BLR)" adds its 9 members (each removable). Adds 2 individuals.
2. Sets **Recurrence**: weekly on Wednesday, 12 occurrences.
3. The conflict panel shows 2 people with conflicts on 3 of the dates. The manager proceeds.
4. Saves → 12 linked meetings are created. Invitations are sent once for the series (one message: "Weekly on Wed 15:00–15:30, from 3 Dec").

**Alternative workflows**
- A1 Edit *this and following* to move from 15:00 to 16:00 from occurrence 5 → occurrences 5–12 are updated (N12 sent once per participant with the series summary).
- A2 Cancel a single occurrence (holiday) → N13 for that date only.

**Acceptance criteria**
- AC1 **Given** 11 participants, **when** saving, **then** all 11 receive exactly one series invitation (not 12 × 11).
- AC2 **Given** 12 occurrences, **when** reminders are set to 15 min, **then** each occurrence sends its own reminder 15 min before.
- AC3 **Given** a participant on approved leave on one date, **when** viewing conflicts, **then** that date shows "On leave: Sneha".
- AC4 **Given** a manager whose scope is their reporting-line team, **when** adding a department, **then** only members within their scope are added and the others are listed as "not in your team" (not added).
- AC5 **Given** more than 200 participants, **when** saving, **then** `VALIDATION`.

---

### US-14 · Select meeting categories
| Field | Detail |
|---|---|
| Module / Role | M3 · Any user |
| Story | As a User, I want to select meeting categories. |
| Business value | Correct form, defaults and reporting per type |
| Traces | FR-MTG-02, 03, 04, 20 |
| Priority | Must |

**Main workflow:** choosing a category changes the visible fields and defaults instantly (no reload), per the conditional logic in FR-MTG-04.

**Acceptance criteria**
- AC1 **Given** *Online meeting*, **then** platform and URL are shown and required. The room and address fields are hidden.
- AC2 **Given** *Direct meeting*, **then** location type defaults to *In office*, and branch and room are shown.
- AC3 **Given** *Client meeting*, **then** the client picker (from Clients), company, location and external participants are shown, and location type can still be changed to Online.
- AC4 **Given** *Team meeting*, **then** the department picker is shown and required.
- AC5 **Given** *Other*, **then** "Custom type" is required. **Given** *One-to-One* or *Interview*, **then** visibility defaults to Private.
- AC6 **Given** a category change hides fields that already have values, **when** saving, **then** the hidden values are not saved (prevents stale URLs on in-person meetings).

---

### US-15 · Add agenda and description
| Field | Detail |
|---|---|
| Module / Role | M3 · Any user (organizer) |
| Story | As a User, I want to add meeting agendas and descriptions. |
| Business value | Prepared participants, shorter meetings |
| Traces | FR-MTG-10, 11 |
| Priority | Must |

**Acceptance criteria**
- AC1 **Given** agenda items, **when** saving, **then** their order is kept and shown in the invitation (e-mail/.ics) and the meeting detail.
- AC2 **Given** a description of more than 5,000 characters, **then** `VALIDATION`.
- AC3 **Given** attachments, **when** a participant downloads one, **then** it downloads. **When** a non-participant outside scope requests it, **then** `NOT_FOUND`.
- AC4 **Given** HTML or script in the description, **when** displayed, **then** it is shown as text (escaped, using the existing `esc()`).

**Validation:** ≤ 30 agenda items, each title 1–150 characters; optional minutes per item 1–480; attachments per FR-PLT-07.

---

### US-16 · Specify duration and location
| Field | Detail |
|---|---|
| Module / Role | M3 · Any user |
| Story | As a User, I want to specify meeting duration and location. |
| Business value | Correct calendar blocks, rooms and joining instructions |
| Traces | FR-MTG-03…05, 07, 08, 24 · BR-M1, M3, M5 |
| Priority | Must |

**Acceptance criteria**
- AC1 **Given** start 15:00 and duration 45 min, **then** end shows 15:45. **Given** the end is changed to 16:00, **then** duration shows 1 h.
- AC2 **Given** room *BLR-Board* booked 15:00–16:00, **when** booking 15:30–16:30, **then** `CONFLICT_ROOM`. **When** booking 16:00–17:00, **then** it is allowed (touching is not overlap).
- AC3 **Given** an online meeting with URL `http://…`, **then** `VALIDATION` "Use a secure (https) link".
- AC4 **Given** 14 participants and a room with capacity 8, **then** a capacity warning is shown (not blocking).
- AC5 **Given** an online meeting, **when** within 10 min of start until the end, **then** the Join button is active, and it opens the URL in a new tab.

---

### US-17 · Sync meetings with my calendar
| Field | Detail |
|---|---|
| Module / Role | M3 · Any user |
| Story | As a User, I want to synchronize meetings with my calendar. |
| Business value | People live in their calendars, so meetings must appear there |
| Preconditions | `calsync` module on; OAuth app configured (platform or env) |
| Traces | FR-MTG-23 · `04 §2` · S-30 |
| Priority / Dependencies | Must · WS0 secrets, job runner; Google verification (lead time) |

**Main workflow**
1. User opens *Settings → Calendar → Connect Google Calendar* and reads the consent notice.
2. Approves on Google and returns to the app: "Connected as rahul@acme.in".
3. Rahul organises a meeting. It appears in his Google Calendar within 1 min, with attendees invited by Google.
4. He changes the time. The Google event updates.
5. He cancels. The Google event is cancelled and attendees are notified by Google.

**Alternative workflows**
- A1 *Not connected*: participants receive `.ics` e-mail invites from the system.
- A2 *Token revoked*: banner "Reconnect your calendar". Fallback to `.ics`.
- A3 *Disconnect*: choose to keep or remove synced meetings.

**Acceptance criteria**
- AC1 **Given** a connected organizer, **when** the meeting is saved, **then** exactly one provider event exists for it, even if the sync job runs twice (deterministic id / `transactionId`).
- AC2 **Given** an update of a material field, **then** the provider event is updated (not recreated) within 2 min.
- AC3 **Given** a cancellation, **then** the provider event is cancelled/deleted and the link status is `deleted`.
- AC4 **Given** an attendee accepts in Google, **when** the push or poll arrives, **then** their RSVP in the app becomes *Accepted*.
- AC5 **Given** the provider returns 5xx, **then** the sync retries on the back-off schedule. After the maximum attempts the meeting shows "Not synced" and System health lists it. The meeting itself is unaffected.
- AC6 **Given** a user who did not connect, **then** no system process reads or writes their external calendar.

**Errors:** `PROVIDER_ERROR`, `CONSENT_REQUIRED`, `MODULE_OFF`.

---

### US-18 · Receive meeting invitations and reminders
| Field | Detail |
|---|---|
| Module / Role | M3 · Employee |
| Story | As an Employee, I want to receive meeting invitations and reminders. |
| Business value | Fewer no-shows (BO-5) |
| Traces | N10–N13 · FR-MTG-12, 13 |
| Priority / Dependencies | Must · M1 |

**Acceptance criteria**
- AC1 **Given** I am invited, **then** I get one invitation per my channel preferences (WhatsApp with title, date, time, location/link, organizer) and an in-app item with RSVP buttons.
- AC2 **Given** reminders of 1 day and 15 min, **then** I get both, unless I declined.
- AC3 **Given** the meeting time changes, **then** I get one update message (several edits within 2 min are combined), and the old reminders are replaced.
- AC4 **Given** I am removed or the meeting is cancelled, **then** I get a cancellation, and no further reminders.
- AC5 **Given** quiet hours and a reminder at 07:45 for an 08:00 meeting, **then** it is sent at 07:45 (urgent: under 2 h).

---

### US-19 · Manager views all team meetings
| Field | Detail |
|---|---|
| Module / Role | M3 · Manager (scope per role: MG team, BM/BA branch, HR branches, CA/SA all) |
| Story | As a Manager, I want to view all team meetings. |
| Business value | Spot overload and meeting-heavy weeks; plan around them |
| Traces | FR-MTG-20, 21 · S-15, S-16 · W1 |
| Priority | Must |

**Acceptance criteria**
- AC1 **Given** a manager with 6 team members, **when** opening *All meetings*, **then** every meeting where at least one team member is organizer or participant is listed.
- AC2 **Given** a private 1:1 between two team members, **then** the manager sees it as "Busy" with the time only. A CA/SA sees the title.
- AC3 **Given** filters (date, category, department, employee, branch per capability), **then** the list and calendar apply them, and the filters are remembered per device.
- AC4 **Given** a meeting with no team member involved, **then** it is not listed, and the API returns `NOT_FOUND` for it.

---

## Module 4 — Time tracking

### US-20 · Assign tasks with an estimate
| Field | Detail |
|---|---|
| Module / Role | M4 · Manager (also BM, BA, HR, CA, SA) |
| Story | As a Manager, I want to assign tasks with estimated completion time. |
| Business value | Planning, workload balancing, estimate accuracy (BO-7, BO-8) |
| Preconditions | `timetrack` module on |
| Traces | FR-TIME-01, 02, 17, 19, 21, 22 · S-22 · N06 |
| Priority / Dependencies | Must · task form (existing), attachments, branches |

**Main workflow**
1. Manager opens **New task** (the existing form, enhanced).
2. Fills in the existing fields plus **Estimated duration 5:00**, **Hours per day 2:00**, **Start date 2 Dec**, due 4 Dec. Department and Branch default from the assignee. Adds an attachment.
3. Saves. The server sets `createdBy` and `createdAt` and the branch/department snapshot. N06 is sent to the assignee.

**Alternative workflows**
- A1 *No estimate* is allowed (optional by policy). Variance and efficiency then show "—".
- A2 *Edit the estimate later*: allowed for the creator or a manager. Changes are audited. Reports use the current estimate and the audit shows the history.

**Acceptance criteria**
- AC1 **Given** estimate 5:00 and hours/day 2:00, **when** saving, **then** the task stores `est = 300`, `dailyMin = 120`, and the card shows "0 m / 5 h".
- AC2 **Given** start date after due date, **then** `VALIDATION` "Start date must be on or before the due date".
- AC3 **Given** hours/day greater than the branch day length (e.g. 9:00 when the branch works 8 h), **then** `VALIDATION`.
- AC4 **Given** an employee (not manager) edits a task via API with an `est` change, **then** the change is ignored (existing rule: employees change only status).
- AC5 **Given** remaining estimate is greater than remaining capacity before due, **then** the card shows an **At risk** badge and N07 is sent once.

**Validation:** estimate 0:05–999:59; hours/day 0:15–branch day length; dates valid ISO; attachments per FR-PLT-07.

---

### US-21 · Start the task timer
| Field | Detail |
|---|---|
| Module / Role | M4 · Employee |
| Story | As an Employee, I want to start a timer when I begin working on a task. |
| Business value | Accurate effort data without timesheets |
| Preconditions | Task assigned to me, not done; I am active; policy allows (check-in or leave rules) |
| Traces | FR-TIME-03…10, 18 · BR-T1, T3, T5, T12, T13 · `04 §3` |
| Priority / Dependencies | Must · WS0 (server clock) |

**Main workflow**
1. Employee clicks **▶ Start work** on the task card.
2. The server creates a session (`running`, started = server now). The task moves to *In progress* if it was *To do*.
3. The timer bar appears on all pages with the live time.

**Alternative workflows**
- A1 *Another timer is running*: dialog "You're working on *Poster for Diwali* (1 h 12 m). Stop it and start this task?" → **Switch** (atomic, X10) or Cancel.
- A2 *Policy requireCheckIn and not checked in*: "Check in first" with a link to Check-in.
- A3 *On approved full-day leave*: blocked (`ON_LEAVE`).
- A4 *Closes the browser and reopens*: the bar is restored with the correct elapsed time.
- A5 *Double-tap on mobile*: the second request has the same `reqId` → same result, no second session.

**Acceptance criteria**
- AC1 **Given** no open session, **when** Start is pressed at device time 10:00:30 but server time 10:02:00, **then** the session starts at **10:02:00** and the bar shows 0:00 counting up.
- AC2 **Given** a running session on task A, **when** Start on B without switch, **then** `TIMER_ACTIVE` with A's details. With switch, A ends and B starts at the same server timestamp.
- AC3 **Given** a task in *done*, **then** Start is hidden, and the API returns `TASK_DONE`.
- AC4 **Given** a task assigned to someone else, **then** `NOT_FOUND`.
- AC5 **Given** two simultaneous Start requests from two devices, **then** exactly one session exists (DB constraint) and the other gets `TIMER_ACTIVE`.

---

### US-22 · Pause and resume the timer
| Field | Detail |
|---|---|
| Module / Role | M4 · Employee |
| Story | As an Employee, I want to pause and resume the timer. |
| Business value | Breaks and interruptions are not counted (BR-T2) and the reasons are visible |
| Traces | FR-TIME-03, 06, 11 · BR-T2 · X2 |
| Priority | Must |

**Main workflow:** **⏸ Pause** → choose a reason (Break / Meeting / Client call / Waiting for input / System issue / Switched task / Other + note) → the bar shows *Paused* with the pause time counting. **▶ Resume** → running again.

**Acceptance criteria**
- AC1 **Given** start 10:00, pause 10:45, resume 11:00, stop 12:00, **then** work = 1 h 45 m and pause = 15 m (X2).
- AC2 **Given** a paused session, **when** Pause is pressed again (e.g. second tab), **then** `INVALID_STATE` with the current state, and nothing changes.
- AC3 **Given** reason *Other* without a note, **then** `VALIDATION`.
- AC4 **Given** a paused session left until auto-stop, **then** it auto-stops at the cut-off (X6), and the paused time is not counted as work.
- AC5 **Given** a meeting starts while running and FR-MTG-25 is on, **then** the widget offers "Pause for meeting", which pauses with reason Meeting.

---

### US-23 · Manual time entry with a reason
| Field | Detail |
|---|---|
| Module / Role | M4 · Employee |
| Story | As an Employee, I want to manually enter work duration with a reason. |
| Business value | Captures work done offline, at a client site or when the timer was forgotten |
| Preconditions | Policy `allowManual` and task-level allow are on |
| Traces | FR-TIME-12…15 · BR-T6, T7 · N16 |
| Priority / Dependencies | Must · US-27 |

**Main workflow**
1. In the task detail → **Add manual entry** → date, start 14:00, end 15:30 (or duration 1:30), reason "Worked at client office, no laptop", category *Client site*.
2. Submits. The status is *Pending approval*. Approvers are notified (N16).
3. The task's time panel shows "Pending 1 h 30 m" separately from actual.

**Alternative workflows**
- A1 *Adjustment* of an existing session (forgot to stop): from the session row → **Request correction** → new end time + reason.
- A2 *Withdraw* a pending entry.
- A3 *Policy without approval* (`requireApprovalForManual = off`): the entry is approved automatically and audited.

**Acceptance criteria**
- AC1 **Given** a reason shorter than 10 characters, **then** `VALIDATION` "Please give a reason (at least 10 characters)".
- AC2 **Given** an entry overlapping an existing session or entry of mine, **then** `OVERLAP` with the conflicting range.
- AC3 **Given** a date 9 days ago with `manualMaxDaysBack = 7`, **then** `VALIDATION`.
- AC4 **Given** a future time, **then** `VALIDATION`.
- AC5 **Given** a pending entry, **then** it is excluded from *Actual* and shown in *Pending* (X4).
- AC6 **Given** the day total would exceed 16 h, **then** `VALIDATION`.

---

### US-24 · Compare estimated and actual time
| Field | Detail |
|---|---|
| Module / Role | M4 · Manager |
| Story | As a Manager, I want to compare estimated time and actual time. |
| Business value | Better estimates, early overrun detection |
| Traces | FR-TIME-16, 22 · R-08, R-10 · `04 §3.4–3.5` |
| Priority | Must |

**Acceptance criteria**
- AC1 **Given** X1 (E 5 h; sessions 1 h 30 m + 2 h + 1 h), **then** the task detail shows Estimated 5 h, Actual 4 h 30 m, Remaining 30 m, Variance −30 m (−10.0 %), Efficiency 111.1 %.
- AC2 **Given** X3 (E 2 h, A 2 h 30 m), **then** Remaining shows "Over by 30 m", Efficiency 80.0 %, and the task is listed in R-10.
- AC3 **Given** X9, **then** the R-08 summary shows aggregate efficiency 100.0 %.
- AC4 **Given** a task without an estimate, **then** variance and efficiency show "—" and it is excluded from aggregates (count shown as "No estimate: n").
- AC5 **Given** the report filters (branch, department, employee, category, date), **then** the results respect both filters and scope.

---

### US-25 · View employee-wise working hours
| Field | Detail |
|---|---|
| Module / Role | M4 · Admin (CA/SA all; HR/BA branch; BM branch; MG team; EM self) |
| Story | As an Admin, I want to view employee-wise working hours. |
| Business value | Understand utilisation; fair workload |
| Traces | R-01, R-05/06/07 · BR-T10, T15 |
| Priority | Must |

**Acceptance criteria**
- AC1 **Given** an employee with 6 h task time and 8 h 30 m attendance hours on a day, **then** R-01 shows both in separate labelled columns, and payroll and the existing Work report are unchanged.
- AC2 **Given** a session 23:30–00:45 (night policy), **then** the daily report attributes 30 m and 45 m to the respective days (X7).
- AC3 **Given** an employee transferred mid-month, **then** the branch filter splits their hours by assignment period (session snapshot).
- AC4 **Given** export, **then** the CSV contains the same rows and totals as the screen.

---

### US-26 · View task-wise time logs
| Field | Detail |
|---|---|
| Module / Role | M4 · Manager |
| Story | As a Manager, I want to view task-wise time logs. |
| Business value | Transparency and audit of how time was spent |
| Traces | FR-TIME-06, 15 · S-21 *Work logs* · R-02 |
| Priority | Must |

**Acceptance criteria**
- AC1 **Given** a task with 3 sessions and 1 approved manual entry, **then** the log lists 4 rows with start, end, work, pause, type (timer/manual), status, pause reasons and remarks, and the totals equal the task's Actual.
- AC2 **Given** an auto-stopped session, **then** it shows *Auto-stopped at 20:30* and *Needs review*.
- AC3 **Given** a session with an approved adjustment, **then** both the original and the adjusted values are visible, with approver and reason.
- AC4 **Given** a user outside scope, **then** `NOT_FOUND`.
- AC5 **Given** anyone attempts to edit or delete a stopped session via any API, **then** `IMMUTABLE` (except the audited SA/CA override).

---

### US-27 · Approve or reject manual time *(added)*
| Field | Detail |
|---|---|
| Module / Role | M4 · Manager (reporting line), BM/BA (branch), CA/SA |
| Story | As a Manager, I want to approve or reject manual time entries and adjustments so that actual time stays trustworthy. |
| Business value | Required by BR-T7; prevents inflation |
| Traces | FR-TIME-13, 14 · S-26 · N16, N17 · R-11 |
| Priority / Dependencies | Must · US-23 |

**Main workflow:** *Tasks → Time tracker → Approvals* (or W4 / bell) → review entry (task, date, range, reason, the employee's other sessions that day) → **Approve** or **Reject** (comment required) → the employee is notified (N17) and Actual updates.

**Acceptance criteria**
- AC1 **Given** my own entry, **then** I cannot approve it (`FORBIDDEN`), and it is routed to my approver.
- AC2 **Given** approve, **then** Actual includes it immediately, and the audit records approver and time.
- AC3 **Given** reject without a comment, **then** `VALIDATION`.
- AC4 **Given** bulk approve of 20 entries, **then** each one is validated (overlap rechecked). Invalid ones are skipped with a reason.
- AC5 **Given** the employee is transferred while an entry is pending, **then** it re-routes to the new approver (FR-BR-09).

---

### US-28 · Record minutes and turn action items into tasks *(added)*
| Field | Detail |
|---|---|
| Module / Role | M3 · Organizer (SA/CA/BA in scope may also edit) |
| Story | As a meeting organizer, I want to record minutes and turn action items into tasks so that decisions lead to tracked work. |
| Business value | BO-5, BO-6 |
| Traces | FR-MTG-15…19 · S-19 · N06, N20 |
| Priority / Dependencies | Must · task module, US-20 fields |

**Main workflow:** after the meeting → **Mark completed** → *Minutes*: summary, discussion points, decisions, action items (title, assignee, due, priority, estimate) → **Create tasks** → **Publish** (participants notified) → follow-up date → "Schedule follow-up".

**Acceptance criteria**
- AC1 **Given** 3 action items, **when** *Create all*, **then** 3 tasks exist with `src = {type: meeting, id}` and branch = meeting branch, the assignees receive N06, and the items show links to the tasks.
- AC2 **Given** a linked task is completed, **then** the action item shows *Done*.
- AC3 **Given** an assignee outside the organizer's task-assign scope, **then** that item cannot be converted (`FORBIDDEN`) and the organizer is told who can assign it.
- AC4 **Given** minutes published 8 days ago, **then** editing is locked for the organizer. SA/CA may correct with a reason (audited).
- AC5 **Given** "Create task" is clicked twice, **then** only one task is created (idempotent per action item).

---

### US-29 · Respond to an invitation (RSVP) *(added)*
| Field | Detail |
|---|---|
| Module / Role | M3 · Participant |
| Story | As a participant, I want to accept, decline or mark tentative so that the organizer can plan. |
| Traces | FR-MTG-14 |
| Priority | Should |

**Acceptance criteria**
- AC1 **Given** an invitation, **when** I tap *Decline* in the app or via the signed link in WhatsApp/e-mail, **then** my RSVP is *Declined* (optional reason), the organizer sees the updated counts, and I get no further reminders.
- AC2 **Given** the signed link is older than the meeting's end or was issued for another person, **then** "This link has expired" and nothing changes.
- AC3 **Given** I accept in Google Calendar (organizer synced), **then** the app shows *Accepted* (US-17 AC4).

---

### US-30 · Verify my phone and manage consent and preferences *(added)*
| Field | Detail |
|---|---|
| Module / Role | M1 · Employee (admins may attest on paper consent) |
| Story | As an Employee, I want to verify my phone and choose how I receive alerts so that I get what I need and nothing I have not agreed to. |
| Business value | Legal basis (DPDP), delivery quality, fewer complaints |
| Traces | FR-NOT-16, 18, 19 · S-13 · BR-N3, N4 |
| Priority | Must |

**Main workflow:** *Settings → My notifications* → phone (+91 prefilled from profile) → **Send code** (SMS OTP) → enter 6 digits → verified → read the notice → switch on WhatsApp and/or SMS → set groups, channel order and quiet hours.

**Acceptance criteria**
- AC1 **Given** a correct OTP within 10 min, **then** the phone is verified and the consent records are created with time, method and notice version.
- AC2 **Given** 5 wrong attempts, **then** verification is locked for 30 min (`RATE_LIMITED`).
- AC3 **Given** I reply STOP on WhatsApp, **then** WhatsApp opt-in is withdrawn within 1 min, queued WhatsApp messages to me are cancelled, and mandatory events reach me in-app.
- AC4 **Given** an admin changes my phone, **then** verification resets and WhatsApp/SMS stop until I verify again.
- AC5 **Given** an admin attests consent, **then** a reason is required and the record shows "attested by <admin>".
