# 09 — Edge Cases, Exception Handling and QA Test Scenarios

## 1. Edge cases and exception handling

### 1.1 Notifications
| ID | Edge case | Expected handling |
|---|---|---|
| EC-N01 | Phone stored as "98470 12345", "+91-9847012345" or "09847012345" | Normalised to `+919847012345`. Unparseable numbers are listed in the migration audit and never sent to |
| EC-N02 | Two employees share one phone number | Allowed (family phone), with a warning on the second profile. Each gets their own deliveries (dedupe includes `emp_id`) |
| EC-N03 | Provider webhook arrives before the send API call returns | The delivery is looked up by `provider_msg_id` after commit. Webhooks for unknown ids are kept 10 min and re-matched, then dropped |
| EC-N04 | Webhook statuses arrive out of order (read before delivered) | Status only moves forward in the order queued < sent < delivered < read. Earlier statuses only fill in missing timestamps |
| EC-N05 | Template rejected or paused by Meta after approval | Status synced daily and on a send error. Deliveries fall back to SMS/in-app. SA is warned on the dashboard |
| EC-N06 | Variable value is empty (e.g. a branch without a name) | Rendered with a fallback ("your branch"). A WhatsApp send never contains an empty parameter (Meta rejects them) |
| EC-N07 | Variable contains a newline (WhatsApp forbids it) | Newlines and runs of 4+ spaces are collapsed |
| EC-N08 | Bulk campaign of 2,000 while the provider tier allows 1,000 unique users/day | Pre-flight warning about the tier. Excess is queued for the next day or marked failed per provider response |
| EC-N09 | Worker down for 2 hours | On restart, overdue reminders for meetings that already started are **cancelled**, not sent late. Other events are sent with an "(delayed)" mark in history |
| EC-N10 | Employee opts out while a campaign is sending | Remaining queued deliveries to them are cancelled (BR-N4) |
| EC-N11 | Event fires for an employee in a deactivated branch | Sent normally (the employee is active). Branch name is rendered from the record |
| EC-N12 | Clock change / DST in a non-India branch | Scheduling uses `zoneinfo`. Reminders are computed from UTC start times |
| EC-N13 | Same task re-assigned A→B→A within 1 minute | A gets one N06 (dedupe per task+eid). B's queued N06 is cancelled (BR-N7) |
| EC-N14 | Quiet hours cross midnight (21:00–08:00) | Handled as a wrap-around window |
| EC-N15 | SMS over 160 characters / Unicode (Malayalam) | Counted as multipart (70 per part for Unicode). Cost estimate uses the part count |

### 1.2 Branches
| ID | Edge case | Expected handling |
|---|---|---|
| EC-B01 | Employee with no `joined` date at migration | Assignment starts at the earliest attendance day, else the migration date |
| EC-B02 | Transfer effective on the employee's joining date | Treated as the initial assignment (no transfer history row for the old branch) |
| EC-B03 | Back-dated transfer across a day already in a payslip | Allowed only from the start of the current month (FR-BR-07). Older periods need SA, with a warning that payslips are not recalculated |
| EC-B04 | Branch Admin is also an employee of that branch | Can approve others' leave, never their own (existing rule) |
| EC-B05 | Employee's reporting manager is in another branch | Allowed. The manager sees the employee as team (reporting line) without branch-admin rights over the branch |
| EC-B06 | HR Admin scope reduced while editing an employee | The next save is rejected (`NOT_FOUND`). The UI reloads |
| EC-B07 | Last HO employee transferred | Allowed. HO keeps zero employees and stays active |
| EC-B08 | Department renamed while a report is open | The report shows the new name (names are resolved at query time). The assignment snapshot keeps the historical name for history views |
| EC-B09 | Secondary access to the same branch as the primary | `VALIDATION` |
| EC-B10 | Attendance punch from a GPS site of another branch | Refused when site restriction is on: "This check-in point belongs to Chennai" |
| EC-B11 | Company goes back to one active branch | Branch UI hides again (FR-BR-18). Data is kept |
| EC-B12 | Legacy client marks attendance for an out-of-scope employee | Op rejected and counted in `rejected`. The toast "Some changes are not allowed for your role" appears (existing behaviour) |

### 1.3 Meetings and calendar
| ID | Edge case | Expected handling |
|---|---|---|
| EC-M01 | Organizer leaves the company (inactive) with future meetings | Meetings stay. SA/BA in scope sees "Organizer inactive" and can reassign the organizer or cancel. Calendar events stay in the old account (cannot be moved); new syncs go to the new organizer |
| EC-M02 | Participant transferred to another branch | Stays a participant. Visibility follows the participant rule |
| EC-M03 | Recurring series crosses a branch holiday | That occurrence is flagged in conflicts at creation. It is not auto-cancelled |
| EC-M04 | Edit "this and following" on occurrence 1 | Same as editing the whole series |
| EC-M05 | Meeting edited after minutes were published | Only attendance and minutes can change (BR-M7) |
| EC-M06 | External participant has no e-mail | Not allowed. E-mail is required for externals (invites go by e-mail) |
| EC-M07 | Same person invited twice (individually and via department) | Deduplicated by `(meeting, emp_id)` |
| EC-M08 | Room deactivated with future bookings | Blocked until the bookings are moved, like BR-B5 |
| EC-M09 | Organizer's calendar token revoked between save and sync | Link → `revoked`. Falls back to `.ics` for this meeting. Organizer notified |
| EC-M10 | Provider event deleted by the organizer in Google | `deleted_externally`. Not recreated. Banner on the meeting: "Removed from Google Calendar. Resync?" |
| EC-M11 | Meeting spans a participant's shift end | "Outside working hours" warning for that participant |
| EC-M12 | Join URL is a non-https or `javascript:` link | `VALIDATION` (https only). Display uses the existing `safeUrl` |
| EC-M13 | Two organizers book the last free room at the same time | The transaction re-checks the overlap. The second gets `CONFLICT_ROOM` |
| EC-M14 | 200-participant meeting invites | Queued as 200 deliveries. The organizer sees progress. Rate-limited |
| EC-M15 | RSVP link forwarded to another person | The token is bound to the participant. Internal users must sign in as that person. External links only change that external's RSVP |

### 1.4 Time tracking
| ID | Edge case | Expected handling |
|---|---|---|
| EC-T01 | Network drops after the server committed Start; the client retries | The same `reqId` returns the original session (no duplicate) |
| EC-T02 | Device clock 20 min wrong | The display uses the server offset. Stored times are server times |
| EC-T03 | Timer left running over the weekend | Auto-stop at the first cut-off (Friday). `needs_review` |
| EC-T04 | Task deleted (admin) with sessions | Task deletion is **blocked** while sessions exist. "Archive" is offered instead (the task stays in reports) |
| EC-T05 | Task reassigned while the old assignee is mid-session | Session stopped at reassignment time (reason `reassigned`). Old assignee notified |
| EC-T06 | Employee marked inactive with an open session | Auto-stopped at deactivation time |
| EC-T07 | Estimate changed after work started | Variance uses the current estimate. The audit keeps history. The task detail shows "Estimate changed 3 h → 5 h on 2 Dec" |
| EC-T08 | Manual entry partly overlaps an auto-stopped session's cut-off | Overlap is checked against stored intervals. The employee should use an adjustment instead (the hint says so) |
| EC-T09 | Approver leaves the company with pending approvals | Re-routed to the next approver in the chain (BM → BA → CA) |
| EC-T10 | Parallel timers allowed in branch A, an employee transfers to branch B (not allowed) with 2 running | Both continue until stopped. A new Start in B follows B's policy |
| EC-T11 | Employee completes the task from the board (status drag) instead of the timer | Same as `time.complete`: the open session is stopped at that moment |
| EC-T12 | Manager reopens a done task | Timing is allowed again. Previous sessions are kept |
| EC-T13 | Zero-length session (Start then Stop within 2 s) | Stored (audit) but excluded from session counts in reports (work < 10 s) |
| EC-T14 | Day total would exceed 24 h because of parallel timers | Allowed only under the parallel policy. Flagged in reports |

### 1.5 Platform
| ID | Edge case | Expected handling |
|---|---|---|
| EC-P01 | `HUB_ENC_KEYS` missing on the server | v6 provider and calendar features show `SERVER_CONFIG`. The rest of the app works |
| EC-P02 | Encryption key rotated | New writes use the new key. Old tokens are decrypted with the old key id, then re-encrypted lazily |
| EC-P03 | SaaS tenant expired (read-only) | Scheduled campaigns and reminders **pause** (`block()` applies to the worker too). Timers cannot start. Running ones auto-stop |
| EC-P04 | Restore of a v2 backup | FR-PLT-08 warning and behaviour |
| EC-P05 | One tenant's jobs fail repeatedly | Back-off per job. Other tenants are unaffected (per-tenant job cap per tick) |
| EC-P06 | Attachment with a `.pdf` name but HTML content | Rejected by content sniffing (magic bytes), not only the extension |

## 2. QA test scenarios

Notation: **Type** F = functional, N = negative, S = security, I = integration, P = performance, R = regression, M = migration. Each row is a test case. QA expands it into steps and data in the test-management tool.

### 2.1 Platform and migration
| ID | Type | Scenario | Expected |
|---|---|---|---|
| TS-P01 | M | Migrate a copy of the production DB (3 employees, 1 task) | HO created. 3 assignments. `branchId` set. Departments seeded. App loads. Existing screens are identical (screenshot diff) |
| TS-P02 | M | `migrate_tenants --backup` on 20 tenant copies, one corrupted | 19 succeed, 1 is reported. Backups exist. Exit summary is correct |
| TS-P03 | M | Run migration 0003 twice | No duplicate HO or assignments |
| TS-P04 | R | Deploy the previous release's code on the migrated DB | Works (additive schema) |
| TS-P05 | F | Prefs: save theme, then a dashboard layout, then the mode | All three persisted (merge) |
| TS-P06 | S | Old browser tab saves an employee without `branchId` | `branchId` preserved |
| TS-P07 | S | Admin client sends `tasks` op with forged `createdBy`, `branchId` | Server values kept |
| TS-P08 | F | Backup v3 download | Contains v6 tables. **No** secrets or tokens (grep for token fragments) |
| TS-P09 | F | Restore a v2 backup after upgrade | Warning shown. After confirm: HO recreated, v6 data cleared as described |
| TS-P10 | I | Kill the worker for 30 min, restart | Jobs resume. Stale meeting reminders are cancelled (EC-N09) |
| TS-P11 | S | Upload a `.svg`, `.html`, and HTML renamed to `.pdf` | All rejected |
| TS-P12 | S | Download another branch's task attachment by id | 404 |

### 2.2 Notifications
| ID | Type | Scenario | Expected |
|---|---|---|---|
| TS-N01 | F | Configure Meta Cloud API with a sandbox number; test connection | Verified; number shown |
| TS-N02 | N | Wrong token | `PROVIDER_ERROR`, cannot enable |
| TS-N03 | S | Inspect `load`, `notify.providers.list` and logs for the secret | Never present except masked |
| TS-N04 | S | Webhook with an invalid signature | 403, logged, not processed |
| TS-N05 | F | Each event N01–N20 fires under its trigger (one test per event) | One delivery per recipient/channel, correct variables, correct template |
| TS-N06 | N | Re-save the same task 3× | One N06 |
| TS-N07 | F | WA permanently fails → SMS fallback | SMS delivered, linked `fallback_of` |
| TS-N08 | F | WA not delivered within 10 min (fallback_after=10) | SMS sent at 10 min |
| TS-N09 | F | Quiet hours: N06 at 22:00 | Sent at 08:00 |
| TS-N10 | F | Quiet hours: meeting reminder for an 07:30 meeting | Sent on time |
| TS-N11 | F | Opt-out via STOP keyword webhook | Opt-in false; queued WA cancelled; consent event stored |
| TS-N12 | F | OTP verification success, expiry, 5 failures | Verified / expired / locked 30 min |
| TS-N13 | F | Campaign to branch MAA × department Sales | Preview counts match the delivered + skipped totals |
| TS-N14 | F | Campaign over 200 recipients | Second confirmation with cost |
| TS-N15 | F | Scheduled campaign: edit at T−2 min OK, T−30 s `LOCKED`, cancel | As stated |
| TS-N16 | S | HR (HO, BLR) sends to a MAA employee id via the API | That recipient → not found; none sent |
| TS-N17 | F | Monthly cap reached | Non-mandatory blocked (`CAP_REACHED`); mandatory → in-app/e-mail |
| TS-N18 | F | History filters, detail timeline, retry temp-failed, refuse retry of perm-failed | As stated |
| TS-N19 | F | Existing e-mail alert for a leave request (regression) | Same subject and body as before, now with a delivery log entry |
| TS-N20 | F | Notice board "Also send via WhatsApp" for audience BLR | Only BLR employees receive N15 |
| TS-N21 | P | 1,000-recipient campaign | Queued < 5 s; dispatch at the configured rate; no duplicates |

### 2.3 Branches
| ID | Type | Scenario | Expected |
|---|---|---|---|
| TS-B01 | F | Create a branch with every field; validation for each field | Saved / field errors |
| TS-B02 | N | Duplicate code (including an inactive branch) | `VALIDATION` |
| TS-B03 | N | Edit the code after an employee is assigned | `IMMUTABLE` |
| TS-B04 | N | Deactivate with active employees / future room bookings | `BRANCH_IN_USE` + blockers |
| TS-B05 | F | Deactivated branch hidden in pickers, shown in reports with "Show inactive" | As stated |
| TS-B06 | S | **Scope matrix test**: for each role (SA, CA, HR[HO,BLR], BA[BLR], BM[BLR], MG, EM) × each action (load, save ops for every collection, users, audit, selfie, all v6 actions, exports, attachment download, search) assert visible/allowed/denied per the 03 §6 matrix | 100 % match; automated in CI |
| TS-B07 | F | Future-dated transfer applied by the job at 00:00 destination tz | Assignments, branchId, reportsTo updated; N19; audit |
| TS-B08 | F | Back-dated transfer within the month | Reports split by date |
| TS-B09 | N | Second scheduled transfer | `TRANSFER_EXISTS` |
| TS-B10 | N | HR not authorised for the destination | `FORBIDDEN` |
| TS-B11 | F | Transfer with reassignment of open tasks and a pending leave | Tasks reassigned; timers stopped; leave approver changed |
| TS-B12 | F | Branch working hours used for late calculation; employee shift overrides | Correct L/P status |
| TS-B13 | F | Branch-specific holiday | Excluded from that branch's working days and reminders only |
| TS-B14 | F | Secondary access: visible in the task/meeting picker, not in attendance | As stated |
| TS-B15 | F | Department rename cascades | All employees updated in one transaction |
| TS-B16 | F | Branch comparison reconciles with branch-level reports | Equal figures (AC1 US-11) |
| TS-B17 | F | Single-branch company | No branch UI anywhere except Branch Management |

### 2.4 Meetings
| ID | Type | Scenario | Expected |
|---|---|---|---|
| TS-M01 | F | Create one meeting of each of the 10 categories | Correct conditional fields and defaults |
| TS-M02 | F | Category switch clears hidden fields on save | As stated |
| TS-M03 | N | End ≤ start; > 12 h; past start without Record only; http URL | `VALIDATION` each |
| TS-M04 | N | Room overlap; touching times | Blocked; allowed |
| TS-M05 | F | Person conflict (meeting, leave, weekly off, outside hours, external busy) | Each listed; "Schedule anyway" saves |
| TS-M06 | F | Weekly recurrence 12×; edit this / following / series; cancel one occurrence | Correct occurrences and notifications (one series invite) |
| TS-M07 | F | Reminders at 1 day and 15 min; declined participant | Sent / not sent |
| TS-M08 | F | Material vs non-material edit | N12 only for material changes; edits within 2 min combined |
| TS-M09 | F | RSVP from the app and from the signed link; expired link | As stated |
| TS-M10 | F | Widget tabs, filters per role, Join button window, quick create, calendar view | As per 04 §4 |
| TS-M11 | S | Private 1:1 seen by a manager / CA | "Busy" / full |
| TS-M12 | F | Minutes draft → publish → lock after 7 days | As stated |
| TS-M13 | F | Action items → tasks (one, all, twice) | Tasks created once, linked, N06 |
| TS-M14 | F | Attendance marking and meeting attendance report | Correct % |
| TS-M15 | I | Google: connect, create, update, cancel, RSVP read-back, revoke token | As per 04 §2 |
| TS-M16 | I | Outlook: the same as TS-M15 | As stated |
| TS-M17 | I | Organizer not connected | `.ics` REQUEST/CANCEL with a stable UID and incrementing SEQUENCE; Gmail and Outlook show one event, updated in place |
| TS-M18 | I | Sync job runs twice for the same sequence | One provider event |
| TS-M19 | I | Provider 500s ×6 | Back-off; status error; System health entry |
| TS-M20 | F | Existing Calendar page shows the meeting overlay; events unchanged | As stated (regression) |

### 2.5 Time tracking
| ID | Type | Scenario | Expected |
|---|---|---|---|
| TS-T01 | F | Worked examples X1–X10 (04 §3.5) as automated unit tests | Exact values |
| TS-T02 | F | Start → todo becomes doing; Complete → done | As stated |
| TS-T03 | N | Start on another person's task / done task / while on leave / not checked in (policy) | `NOT_FOUND` / `TASK_DONE` / `ON_LEAVE` / `NOT_CHECKED_IN` |
| TS-T04 | N | Second timer without switch; switch | `TIMER_ACTIVE`; atomic switch |
| TS-T05 | S | Concurrent Start from 2 devices (parallel requests) | One session |
| TS-T06 | F | Replay of the same `reqId` | Same response, no duplicate |
| TS-T07 | F | Close the browser for 2 h and reopen | Bar restored with correct elapsed time |
| TS-T08 | F | Logout with pauseOnLogout on / off | Paused / still running |
| TS-T09 | F | Auto-stop: max session, day end + grace, 23:59 | Ends at the cut-off; needs_review; N18 |
| TS-T10 | F | Device clock off by 20 min | Correct display and storage |
| TS-T11 | F | Manual entry validations (reason, future, days back, overlap, 16 h) | Each error |
| TS-T12 | F | Approve / reject (comment) / self-approve / bulk | As stated |
| TS-T13 | F | Adjustment request approved | Original kept; actual updated; audit |
| TS-T14 | S | Edit or delete a stopped session via any API path (incl. crafted `save` op) | `IMMUTABLE` / rejected |
| TS-T15 | F | Reassign a task with an open session | Stopped; both notified |
| TS-T16 | F | N09 at 80 % and 100 % | Each once |
| TS-T17 | F | Reports R-01…R-11: filters, sort, paging, export equal to the screen | As stated |
| TS-T18 | R | Work report "Hours logged" (attendance), payroll LOP, performance score | Unchanged values before/after |
| TS-T19 | P | 500 employees starting/stopping timers at 09:30 (burst of 500 requests / 60 s) | p95 < 250 ms; no errors; no `rev` bump |
| TS-T20 | F | Timer bar on mobile (360 px), screen reader announcement | Usable; `aria-live` |

### 2.6 Dashboard, navigation, regression
| ID | Type | Scenario | Expected |
|---|---|---|---|
| TS-D01 | F | Widgets shown per role (W1–W7) | Per 04 §4 |
| TS-D02 | F | Customise: hide, reorder, reset to role default | Persisted per user |
| TS-D03 | S | Widget endpoints called by a role without the capability | 403, widget not rendered |
| TS-D04 | F | Branch filter on the dashboard for multi-branch users | All compatible widgets filter |
| TS-D05 | R | **Full regression of existing modules** with all v6 modules **off**: login, setup, users, employees, attendance (manual, check-in GPS/selfie/QR, biometric ADMS + `/device/punch`), shift rota, leave (all kinds + quota), tasks board, clients, agenda, payments, calendar, notes, performance, monthly review, work report + CSV, ratings (anonymous), notices, documents, assets, payroll + payslip + PF/ESI, checklists, recruitment, invoices, audit, settings accordion, backup/restore, SaaS signup, billing, owner panel, read-only expiry | Identical behaviour to the previous release |
| TS-D06 | R | The same regression with all v6 modules **on** and a single branch | Identical except the new features |
| TS-D07 | R | PWA install and offline shell; light/dark; reduced motion | Unchanged |
| TS-D08 | S | CSP: no console CSP violations on any screen; the page hash recomputes after the HTML change | Clean |
| TS-D09 | P | `load` payload size before/after for 150 employees | Increase ≤ 5 % |

## 3. Test data set (synthetic, never production personal data)
- **4 branches:** HO (Kerala), BLR (Karnataka), MAA (Tamil Nadu), HYD (Telangana, inactive at the end of the test); plus one branch in `Asia/Dubai` for time-zone tests.
- **60 employees**, with these special cases: core-team members, two transfers (one future, one back-dated), three with secondary access, invalid and duplicate phones, one without a phone, one opted out, one inactive with history.
- Users for every role, including an HR Admin with 2 branches and a BA who is also an employee.
- 3 months of attendance, 200 tasks (half with estimates), 500 sessions, 80 meetings (including a weekly series), and provider sandbox accounts (Meta test number, MSG91 test route, Google and Microsoft test tenants).

## 4. Exit criteria
- 0 open Critical/High defects. ≤ 5 Medium with workarounds agreed by Product.
- TS-B06 scope matrix 100 % passing. TS-D05/D06 regression 100 % passing.
- Performance NFR-P1/P2 met on the reference VPS.
- Security review (internal or external) has no open High findings.
- Migration rehearsed on copies of every production tenant DB.
- UAT sign-off by 2 pilot customers (one multi-branch).
