# 02 — Business Requirements Document (BRD)

**Product:** Assertive Hub (Employee Management System, single-company and SaaS editions)
**Release:** v6 "Connected Operations": WhatsApp/SMS alerts, multi-branch, meetings, task time tracking
**Version:** 1.0 draft · 23 Nov 2026 · **Owner:** Product · **Reviewers:** Engineering lead, QA lead, HR domain SME, Sales

---

## 1. Background

Assertive Hub is used by Indian SMEs (15–150 people per plan today) to run attendance, leave, tasks, clients, payroll and reviews. Customers are growing into **multi-office companies**, such as a Kerala head office with Bangalore, Chennai and Hyderabad branches. Three operational problems keep coming up:

1. **Communication does not reach people.** Staff do not open the web app often enough to see leave decisions, new tasks or meeting changes. E-mail is often ignored. In India, WhatsApp and SMS are the channels staff actually read.
2. **One company, one flat structure.** The product cannot separate branches. A Bangalore admin either sees the whole company or nothing. Reports cannot compare branches, and transfers lose history.
3. **Meetings and effort are invisible.** Meetings are arranged outside the system and lost after they happen. Managers know whether a task is done but **not how long it took**, so estimates never improve and overruns are discovered too late.

## 2. Business objectives and success measures

| ID | Objective | KPI (measured 90 days after go-live, per active customer) | Target |
|---|---|---|---|
| BO-1 | Get time-critical information to employees reliably | Share of triggered alerts delivered on ≥1 channel within 5 min | ≥ 95 % |
| BO-2 | Fewer missed check-ins and late arrivals | Late arrivals per 100 attendance days (vs. 60 days before go-live) | −20 % |
| BO-3 | Run multi-branch companies from one system | Multi-branch customers using branch-scoped admins | ≥ 70 % |
| BO-4 | Keep history accurate across transfers | Reports matching source records after transfers (QA reconciliation) | 100 % |
| BO-5 | Meetings scheduled, attended and followed up in-system | Completed meetings with minutes recorded | ≥ 50 % |
| BO-6 | Meeting outcomes turn into tracked work | Action items converted to tasks | ≥ 60 % |
| BO-7 | Make effort visible and estimates better | Tasks with an estimate that also have tracked time | ≥ 70 % |
| BO-8 | Better estimates over time | Median abs(variance %) on completed tasks, month 3 vs. month 1 | −25 % |
| BO-9 | Commercial: upsell | Business→Pro upgrades citing branches or WhatsApp | Tracked (no target in v6) |

## 3. Stakeholders and users

| Stakeholder / persona | Interest |
|---|---|
| **Super Admin** (owner/director) | Company-wide control, consolidated reports, branch set-up, provider set-up, cost of messages |
| **Company Admin** (existing `admin`) | Day-to-day administration across all branches (existing behaviour kept) |
| **HR Admin** (new) | Employee records, transfers, leave and attendance administration for authorised branches; announcements |
| **Branch Admin** (new) | Runs one branch: employees, attendance, leave, tasks, meetings, rooms, branch reports |
| **Branch Manager** (new) | Leads a branch: sees all branch employees, plans branch tasks and meetings, branch reports |
| **Manager** (existing, reporting-line based) | Their team's tasks, leave, time approvals, team meetings |
| **Employee** | Receives alerts, tracks time on tasks, attends meetings, sees their own data |
| **External participant** (client, vendor, candidate) | Receives a meeting invite (e-mail/calendar). Never logs in |
| **SaaS operator** (platform owner) | Plan packaging, provider onboarding, cost control, compliance |
| **Engineering / QA** | Delivery inside the existing architecture without regressions |

## 4. Scope

### 4.1 In scope (v6)

| Module | Scope summary |
|---|---|
| M1 WhatsApp and SMS alerts | Provider set-up (WhatsApp Business API, SMS gateway), templates with variables, 18 automated events, manual/department/branch/bulk/scheduled sends, delivery tracking, retry, SMS fallback, history, employee preferences, phone verification and consent, quiet hours, cost cap |
| M2 Multi-branch | Branch master, head office, employee primary and secondary branches, effective-dated transfers with history, branch admins and managers, department and designation mapping, branch-level data isolation **enforced on the server**, branch working hours and holidays, branch-wise and consolidated reports |
| M3 Meetings | 10 categories, full form with conditional logic, rooms, participants (internal and external), conflict detection, recurrence, reminders, RSVP, status, minutes, action items → tasks, attendance, dashboard widget, meeting calendar, Google/Outlook sync, ICS invites |
| M4 Task time tracking | Estimate and daily allocation on tasks, start/pause/resume/stop/complete timer, server-side calculation, manual entries with approval, adjustments, auto-stop rules, interruption reasons, variance and efficiency, 11 reports with filter, sort and export |
| M5 Integration | Shared employee, branch, permission and notification foundations; the end-to-end workflow in §6 |
| M6 Dashboard and navigation | New widgets, configurable and permission-aware dashboard, navigation regrouped with in-page tabs |
| M7 Additional features | As recommended in §7 |

### 4.2 Out of scope (v6), with reason

| Item | Reason / when |
|---|---|
| Moving the core database to PostgreSQL | Needed beyond ~500 employees per tenant (see NFR-S). Planned as the **v6.1 scalability track** |
| Rewriting the full `load` sync to paged APIs for existing modules | Same as above. v6 avoids making it worse |
| Payroll professional tax per branch state | Real need (PT slabs differ by state) but it is a payroll change. **v6.1** |
| Custom role builder / editable permission matrix | v6 ships fixed roles plus branch scopes. The matrix is shown read-only |
| Two-way editing of meetings from an external calendar | v6 supports system→calendar sync and reading RSVPs back. Applying edits made in an external calendar is **phase 2** (provider rules make it error-prone) |
| Video-call recording, transcription, AI minutes | Not requested |
| Inbound WhatsApp chat or helpdesk | Only opt-in/opt-out keywords and delivery webhooks are handled |
| Payroll use of task time | BR-T10: task time is kept separate from attendance and payroll hours |
| Native mobile apps | The existing PWA is used |

## 5. Assumptions and constraints

**Assumptions**
- A1. Customers provide their own WhatsApp Business account and SMS sender (bring-your-own credentials). In SaaS, a platform-managed shared sender is a later option.
- A2. Most branches are in India (Asia/Kolkata), but the design supports one time zone per branch.
- A3. Each employee has exactly one primary branch at any date. Some (managers, trainers) also work in secondary branches.
- A4. Existing single-office customers must see **no change** until they create a second branch.
- A5. The team is roughly 2 backend developers, 2 frontend developers, 1 QA and a part-time BA/PM. The sizing in 10 assumes this.

**Constraints (regulatory and platform; these cause real delays)**
- C1. **WhatsApp Business Platform:** business-initiated messages outside the 24-hour service window must use **pre-approved templates**. Templates are categorised (utility, marketing, authentication) and priced differently. Birthday wishes and announcements are likely to be classed as **marketing**. Business verification and template approval take days to weeks.
- C2. **SMS in India (TRAI TCCCPR / DLT):** the sender (principal entity), header (sender ID) and **every content template** must be registered on a DLT portal. A message that does not match a registered template is blocked by the operator. Promotional content has time windows and DND limits.
- C3. **Digital Personal Data Protection Act 2023:** phone numbers are personal data. Sending needs a clear purpose, notice and consent where applicable, opt-out, retention limits and the employer acting as data fiduciary. In SaaS, Assertive Hub is the processor (as the SaaS README already notes).
- C4. **Google OAuth:** the `calendar.events` scope is *sensitive*, so the app must pass Google verification before customers outside the developer's organisation can use it. Redirect URIs must match exactly: **wildcard subdomains are not allowed**, so SaaS needs one platform-level callback.
- C5. **Microsoft identity platform:** needs a multi-tenant app registration. Some customer organisations require admin consent for calendar scopes.
- C6. Architecture constraints from 01 §7: SQLite per tenant, the full-load sync, and no worker.

## 6. Business scenarios (realistic, end-to-end)

### Scenario S1: Opening the Bangalore branch (M2 → M1 → M4 → M3)
1. Priya (Super Admin, Kerala HO) creates branch **BLR – Bangalore**: Karnataka, 09:30–18:30, Mon–Sat, time zone Asia/Kolkata. She names Arjun as branch manager and gives him the **Branch Manager** role for BLR.
2. Meera (HR Admin for HO and BLR) transfers 6 employees from HO to BLR **effective 1 Dec**. Their November attendance stays in HO's reports and December onward goes to BLR. Two of them keep **secondary access** to HO, where they still support HO clients.
3. On 1 Dec at 00:00 IST the transfers take effect. Each employee gets the WhatsApp template *"Hi {{employee_name}}, from today you are part of {{branch_name}}…"*.
4. Arjun creates the task **"Landing page for Client X"**: estimate 5 h, 2 h/day, due 4 Dec, assigned to Rahul. Rahul receives the **WhatsApp "New task"** alert within a minute.
5. Rahul presses **Start work** at 10:02. He pauses at 11:30 ("Client call"), resumes at 11:45 and stops at 13:15. That session counts as **3 h 0 m worked and 15 m paused**.
6. Arjun schedules a **Team meeting**, "Sprint sync", Wed 15:00–15:30, in room *BLR-Board*, for the BLR Design department. The system warns that Rahul has an overlapping interview (a person conflict, so it warns but allows). Arjun keeps it.
7. The meeting appears in every participant's **Meeting Schedule widget** and in Arjun's **Google Calendar** (he connected it). Participants without a connection receive an **e-mail with an .ics invite**.
8. 15 minutes before, participants get a **WhatsApp reminder**. Sneha has no WhatsApp, so her message **fails and falls back to SMS**.
9. After the meeting Arjun records minutes: 2 decisions and 3 action items. He converts the action items into **tasks** with one click, and the assignees are notified.
10. Rahul completes the original task on Thursday. Total actual time is **4 h 30 m** against a 5 h estimate: **variance −30 m, efficiency 111.1 %**.
11. Arjun opens **Estimated vs Actual** for BLR. Priya opens the **Branch comparison** report covering HO, BLR, MAA and HYD.

### Scenario S2: Leave across branches
An HYD employee applies for leave. Their reporting manager sits in HO. The manager is notified (in-app and WhatsApp) and approves. The employee gets "Leave **Approved**". The HYD Branch Admin sees the leave in HYD's leave report. The HO Branch Admin does **not**: approvers act on the reporting line, but data visibility follows the branch.

### Scenario S3: A failed WhatsApp campaign
HR sends a Diwali branch announcement to 140 Chennai employees. 9 numbers are not on WhatsApp and are **fallen back to SMS**. 2 numbers are invalid and recorded as **failed (invalid number)**. 1 employee had opted out and is **skipped (opted out)**. Notification History shows each outcome, and HR can export the failures and fix the phone numbers.

### Scenario S4: A forgotten timer
Anil leaves a timer running and goes home. At shift end + 60 min (the branch rule) the system **auto-stops** it at the cut-off time, flags it **Needs review** and messages Anil. Next morning Anil sends an adjustment ("Stopped at 18:40, forgot to stop"). His manager approves it, and the original values stay in the audit trail.

## 7. Analysis of additional recommended features (Module 7)

Each idea is judged on its business value, dependencies and complexity. **Recommended** items are in v6. **Deferred** items are planned for v6.1.

| # | Feature | Business value | Depends on | Complexity | Decision |
|---|---|---|---|---|---|
| 1 | Employee workload overview | Managers see who is overloaded before assigning: open estimated hours remaining against daily capacity over the next 5 working days | M4 estimates, leave, branch hours | M | **Recommended (v6)**, read-only widget and report |
| 2 | Task reallocation | Reassigning work is already possible by editing the assignee. v6 adds rules: stop the old assignee's timer, keep their logged time, notify both | M4, M1 | S | **Recommended (v6)**, as rules on the existing edit, not a new screen |
| 3 | Meeting conflict detection | Prevents double booking of people and rooms | M3, leave, calendar free/busy | M | **Required** (in M3 acceptance criteria) |
| 4 | Employee availability | Shows free/busy (meetings, leave, off days) when choosing participants | 3 | S once 3 exists | **Recommended (v6)**, in the participant picker only |
| 5 | Work interruption reasons | Explains pauses. The data shows what causes productivity loss | M4 | S | **Recommended (v6)**, a fixed list plus "Other" |
| 6 | Manual time approval | Required by BR-T7 | M4 | — | **Required** |
| 7 | Branch transfer history | Required by M2 | M2 | — | **Required** |
| 8 | Notification preferences | Required (M1 FR-15) and needed for consent law | M1 | — | **Required** |
| 9 | Meeting action-item tracking | Makes meetings productive. Converting to tasks is required | M3, tasks | S | **Recommended (v6)**: item status mirrors its task |
| 10 | Productivity trends | Shows managers efficiency and variance over time | M4 data (needs about 4 weeks of history) | M | **Deferred to v6.1**: little value until data has built up |
| 11 | Branch performance comparison | Required for US-11 | M2 reports | M | **Required** (Branch comparison report) |
| 12 | Employee work summary | A per-person weekly summary (tasks, time, meetings) | M3, M4 | S | **Recommended (v6)** as a profile tab. The e-mail/WhatsApp digest is **deferred** because of message cost |
| 13 | Task deadline warning | Warns at risk, not only after the due date: remaining estimate > remaining capacity before due | M4 estimates and daily allocation | S | **Recommended (v6)**, with event N07 |
| 14 | Recurring meetings | Weekly syncs and 1:1s are the most common meetings | M3 | M | **Recommended (v6)**: daily, weekly and monthly. Complex rules are deferred |
| 15 | Meeting attendance tracking | Accountability for internal meetings | M3 | S | **Recommended (v6)**, marked by the organizer after the meeting |

**Not recommended:** automatic productivity scoring from timer data that changes the existing performance `score()`. It is likely to be disputed by employees, and timer behaviour differs by role. v6 keeps time metrics as **information only**. The existing performance score is unchanged.

## 8. Packaging recommendation (SaaS)

| Capability | Starter | Business | Pro |
|---|---|---|---|
| Meetings (internal calendar, widget, minutes, action items) | ✓ | ✓ | ✓ |
| Task time tracking and reports | ✓ | ✓ | ✓ |
| In-app and e-mail notifications through the new service | ✓ | ✓ | ✓ |
| WhatsApp and SMS alerts (BYO provider) | — | ✓ | ✓ |
| Google/Outlook calendar sync | — | ✓ | ✓ |
| Multiple branches | 1 (HO only) | Up to 3 | Unlimited |
| Branch-scoped admin roles, consolidated reports | — | ✓ | ✓ |

Final pricing is open decision **OD-01** in `00-README.md`.

## 9. Business risks

| Risk | Impact | Mitigation |
|---|---|---|
| WhatsApp or DLT approvals delay go-live | M1 cannot send in production | Start registrations in **week 1**. Release with in-app and e-mail working, and turn WhatsApp/SMS on per customer when approved |
| Message costs surprise customers | Churn, disputes | Monthly cap per tenant, cost estimate before bulk sends, usage report |
| Employees see the timer as surveillance | Low adoption, HR complaints | Time is used for planning only. Clear in-app notice. No screenshots or activity tracking. Employees see their own data first |
| Branch isolation bug leaks data | Severe trust and legal impact | Server-side scope service, IDOR test suite, security review before release |
| Regressions in existing modules | Customer disruption | Characterisation tests first, module flags off by default, staged tenant rollout |
| SQLite write contention from frequent timer actions | Slowness | Timer actions are small single-row writes that do not bump the global `rev` or write backups. Load-tested (NFR-P) |
