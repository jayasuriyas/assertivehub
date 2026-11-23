# 07 — API Endpoint Requirements

## 1. Conventions (follow the existing API; do not introduce a second style)

### 1.1 Transport
- Same single endpoint: **`/api/?a=<module>.<action>`**. For example `a=time.start` or `a=branches.list`.
- Header **`X-Requested-With: staffroom`** is required (existing CSRF defence). JSON body. Same-origin session cookie.
- **Reads may use GET.** The `api()` view's GET allow-list (`status, load, users, audit`) becomes a set `READ_ACTIONS` that includes every v6 `*.list`, `*.get`, `*.state`, `*.widget` and `*.report` action. Everything else is POST.
- Existing wrapper: `transaction.atomic`, SaaS `block()` check (read-only expired tenants: POST blocked), `ApiError`. **New:** a dispatcher `V6_ACTIONS = {name: (handler, capability, module)}` checks the module flag (`MODULE_OFF`) and the capability (`FORBIDDEN`) before calling the handler. Handlers live in `core/<module>.py`.
- Every v6 response includes `now` (server UTC ISO, FR-PLT-13).
- Lists: `?page=1&size=50` (max 200), `sort=field|-field`, filters as query params. The response includes `{items, page, size, total}`.
- Exports: `a=<report>.export` streams `text/csv; charset=utf-8` with a BOM, and protects cells against formula injection.

### 1.2 Envelope
```json
{ "ok": true, "now": "2026-12-03T09:32:10Z", ... }
{ "ok": false, "error": "Human readable message", "code": "TIMER_ACTIVE", "details": { ... } }
```
`error` keeps the existing human-readable style. `code` is new and machine-readable, and the frontend switches on it.

### 1.3 Error codes
| Code | HTTP | Meaning |
|---|---|---|
| `VALIDATION` | 400 | `details.fields = {field: message}` |
| `NOT_SIGNED_IN` | 401 | Existing behaviour |
| `FORBIDDEN` | 403 | Missing capability |
| `NOT_FOUND` | 404 | Missing **or out of scope** (BR-P2) |
| `MODULE_OFF` | 403 | Module switched off or not in the plan |
| `PLAN_LIMIT` | 402 | e.g. branch count |
| `INVALID_STATE` | 409 | Action not allowed in the current state. `details.state` |
| `TIMER_ACTIVE` | 409 | `details.session` = the running session |
| `TASK_DONE` | 409 | |
| `ON_LEAVE` / `NOT_CHECKED_IN` | 409 | Time policy |
| `OVERLAP` | 409 | Time entries. `details.with` |
| `CONFLICT_ROOM` | 409 | `details.meetings` |
| `IMMUTABLE` | 409 | Locked field or record |
| `LOCKED` | 409 | Scheduled send about to start; minutes locked |
| `BRANCH_IN_USE` | 409 | `details.blockers` |
| `TRANSFER_EXISTS` | 409 | |
| `TEMPLATE_NOT_APPROVED` | 409 | |
| `CAP_REACHED` | 409 | Message cap |
| `RATE_LIMITED` | 429 | `details.retryAfter` |
| `PROVIDER_ERROR` | 502 | External provider failed during a synchronous test |
| `CONSENT_REQUIRED` | 409 | Calendar reconnect needed |
| `SERVER_CONFIG` | 500 | Missing encryption key or OAuth client config |

## 2. Changes to existing actions

| Action | Change |
|---|---|
| `load` | `view_for` applies `scope_for(user)` for **all** ranks (scoped admins included). Adds `branches`, `myScope`, `taskTime`. Collections filtered by branch where the record has an employee (`eid`) |
| `save` | `apply_ops` passes the scope. `can_set` / `can_del` / `op_att` check `in_scope(eid)` on admin paths too. FR-PLT-12 preservation. Task create sets `createdBy` / `createdAt` / `branchId`. Task assignee change → stops open timers (FR-TIME-19) and queues N06. Leave and attendance-correction events go to the notification service instead of `flush_mail` |
| `users`, `user_save`, `user_del`, `user_signout` | Scoped for HR/BA (FR-BR-19). `user_save` accepts `branches: [id]` for scoped roles |
| `audit` | Merges `AuditEvent` with the legacy list. Filtered by scope for non-company-wide admins |
| `selfie` | Scope check for rank 3 scoped roles |
| `prefs` | **Merges** keys. Accepts `dash` and `tz` |
| `logout` | Pauses a running timer when `pauseOnLogout` is on |
| `punch` | Writes `b` (branch). Late uses branch hours. Site restriction per branch. Fires N03 |
| `restore` | Accepts v3 (FR-PLT-08) |

## 3. New actions

Capability names: `branch.manage`, `branch.view`, `emp.transfer`, `org.master`, `room.manage`, `notify.config`, `notify.templates`, `notify.send`, `notify.bulk`, `notify.history`, `meeting.create`, `meeting.view_scope`, `meeting.manage_scope`, `time.track`, `time.view_scope`, `time.approve`, `time.policy`, `report.branch`, `report.consolidated`. The role-to-capability mapping is the matrix in `03-SRS.md §6`.

### 3.1 Branches and organisation
| Action | M | Capability | Request | Response / notes |
|---|---|---|---|---|
| `branches.list` | GET | branch.view | `status, q, page` | Branches in scope with headcount |
| `branches.get` | GET | branch.view | `id` | Branch + departments, rooms, admins, manager |
| `branches.save` | POST | branch.manage | Branch fields (`id` for edit) | `{branch}`; `IMMUTABLE` on code change once used; `PLAN_LIMIT` |
| `branches.deactivate` | POST | branch.manage | `id, reason` | `BRANCH_IN_USE` + blockers |
| `branches.reactivate` | POST | branch.manage | `id` | |
| `branches.setHeadOffice` | POST | branch.manage | `id` | |
| `branches.admins.set` | POST | branch.manage | `branchId, userIds[]` | Revokes affected sessions |
| `branches.manager.set` | POST | branch.manage | `branchId, empId, grantRole:bool` | |
| `branches.overview` | GET | report.branch | `date` | W5 data |
| `emp.assignments` | GET | branch.view + scope | `empId` | Timeline |
| `emp.assign` | POST | emp edit + scope | `empIds[], branchId, effectiveFrom, notify:bool` | Initial or bulk primary assignment |
| `emp.secondary.save` | POST | emp edit + scope | `empId, branchId, from, to, reason` | |
| `emp.secondary.end` | POST | emp edit + scope | `id, to` | |
| `transfers.preview` | POST | emp.transfer | `empId, toBranchId, effectiveDate` | Side-effects checklist (FR-BR-09) |
| `transfers.create` | POST | emp.transfer | `empId, toBranchId, effectiveDate, dept?, designation?, reportsTo?, reason, notes?, reassign:[{taskId, toEmpId}]` | `{transfer}` scheduled or applied. `TRANSFER_EXISTS`, `FORBIDDEN` (BR-B9) |
| `transfers.cancel` | POST | emp.transfer | `id` | Scheduled only |
| `transfers.list` | GET | emp.transfer | filters | R-17 |
| `org.departments.list/save/merge` | GET/POST | org.master (list: any staff) | `name, code, status` / `fromIds[], toId` | Rename/merge cascades employee `dept` in one transaction and returns `affected` |
| `org.branchDepartments.set` | POST | org.master | `branchId, departments:[{id, headEmp}]` | |
| `org.designations.list/save` | GET/POST | org.master | | |
| `rooms.list` | GET | any staff | `branchId, date?` | With bookings for the date |
| `rooms.save` | POST | room.manage + scope | Room fields | |

### 3.2 Notifications
| Action | M | Capability | Request | Response / notes |
|---|---|---|---|---|
| `notify.providers.list` | GET | notify.config | | Masked secrets, webhook URL + verify token |
| `notify.providers.save` | POST | notify.config | `channel, provider, config, secrets?` (secrets only when replacing) | Secrets never echoed back |
| `notify.providers.test` | POST | notify.config | `id, to?` | `{ok, details}` or `PROVIDER_ERROR` |
| `notify.providers.toggle` | POST | notify.config | `id, enabled, cancelQueued:bool` | |
| `notify.templates.list` | GET | notify.templates (view: HR/BA) | `event, channel, lang` | |
| `notify.templates.save` | POST | notify.templates | Template fields | New version |
| `notify.templates.preview` | POST | notify.templates | `id or body, sample` | Rendered text |
| `notify.templates.submit` / `.sync` | POST | notify.templates | `id` / — | Provider approval |
| `notify.events.list` / `.save` | GET/POST | notify.config (BA: branch timing override only) | | |
| `notify.audience.preview` | POST | notify.send | `audience, channel, templateId` | `{total, willReceive:{whatsapp, sms, inapp}, skipped:{reason: n}, costEstimate}` |
| `notify.campaigns.create` | POST | notify.send (+ notify.bulk if > 200 recipients or scheduled) | `audience, channel, templateId, variables, scheduledFor?, confirmToken?` | Over 200: first call returns `{needsConfirm, count, cost, confirmToken}` |
| `notify.campaigns.list/get/update/cancel` | GET/POST | notify.send | | `LOCKED` within 1 min |
| `notify.deliveries.list` | GET | notify.history (EM: own) | Filters `from, to, event, channel, status, branchId, empId, campaignId` | Paged |
| `notify.deliveries.get` | GET | notify.history | `id` | Timeline + fallback chain |
| `notify.deliveries.retry` | POST | notify.history | `ids[]` (≤ 500) | `{retried, refused:[{id, reason}]}` |
| `notify.deliveries.export` | GET | notify.history | filters | CSV |
| `notify.inbox` | GET | self | `unreadOnly, page` | In-app items |
| `notify.inbox.read` | POST | self | `ids[] or all` | |
| `notify.prefs.get` / `.save` | GET/POST | self | `groups, order, quiet, lang` | Mandatory groups ignored if switched off |
| `notify.contact.sendOtp` | POST | self | `phone` | Rate-limited: 3 per hour |
| `notify.contact.verify` | POST | self | `code` | 5 tries, then 30-min lock |
| `notify.contact.optin` | POST | self | `channel, on, noticeVersion` | ConsentEvent |
| `notify.contact.attest` | POST | emp edit + scope | `empId, channel, reason` | Admin attestation |
| `notify.usage` | GET | notify.config | `month` | Counts and cost by event, channel and branch |

### 3.3 Meetings
| Action | M | Capability | Request | Response / notes |
|---|---|---|---|---|
| `meetings.list` | GET | meeting.create (own) / meeting.view_scope | `view=my\|all, from, to, branchId, category, dept, empId, status, page` | Private meetings masked for non-participants |
| `meetings.get` | GET | participant or scope | `id` | Full detail, minutes, action items, sync status |
| `meetings.widget` | GET | any | `tab=today\|upcoming\|completed\|cancelled, date, filters` | ≤ 8 items + `nextUp` |
| `meetings.calendar` | GET | any | `from, to, filters` | Compact items for the day/week/month grid + leave/holiday overlays |
| `meetings.conflicts` | POST | meeting.create | `start, end, tz, participants[], roomId?, excludeMeetingId?` | `{room:[…], people:[{empId, type: meeting\|leave\|off\|hours\|external_busy, detail}]}` |
| `meetings.save` | POST | organizer / manage_scope | Meeting fields + `participants`, `recurrence?`, `scope=this\|following\|series` (edits), `confirmConflicts:bool`, `reqId` | `CONFLICT_ROOM` (409); person conflicts without `confirmConflicts` → 409 `INVALID_STATE` with `details.conflicts` |
| `meetings.cancel` | POST | organizer / manage_scope | `id, reason, scope` | N13, calendar cancel |
| `meetings.complete` | POST | organizer / manage_scope | `id` | |
| `meetings.rsvp` | POST | participant | `id, response, note?` | |
| `meetings.attendance` | POST | organizer / manage_scope | `id, marks:[{participantId, attendance}]` | |
| `meetings.minutes.save` | POST | organizer / manage_scope | `meetingId, summary, discussion[], decisions[], followUpDate?, publish:bool` | `LOCKED` after 7 days |
| `meetings.actions.save` | POST | organizer / manage_scope | `meetingId, items:[{id?, title, assigneeEmp, dueDate, priority, estMin?}]` | |
| `meetings.actions.toTasks` | POST | organizer + task-assign scope | `itemIds[]` | Idempotent per item. `{created:[{itemId, taskId}], refused:[…]}` |
| `meetings.report` / `.export` | GET | meeting.view_scope | R-13/14/15 filters | |

### 3.4 Time tracking
| Action | M | Capability | Request | Response / notes |
|---|---|---|---|---|
| `time.state` | GET | time.track | — | `{open:[session], running: session\|null, now}` |
| `time.start` | POST | time.track (assignee) | `taskId, switch:bool, reqId, clientTs` | `{session, stopped?: session}`. Errors `TIMER_ACTIVE, TASK_DONE, ON_LEAVE, NOT_CHECKED_IN, NOT_FOUND` |
| `time.pause` | POST | owner | `sessionId, reason, note?, reqId, clientTs` | `INVALID_STATE` |
| `time.resume` | POST | owner | `sessionId, reqId, clientTs` | Also checks `TIMER_ACTIVE` (another task started meanwhile) |
| `time.stop` | POST | owner | `sessionId, remarks?, reqId, clientTs` | |
| `time.complete` | POST | assignee | `taskId, remarks?, reqId` | Stops the open session, sets the task `done`, N08 |
| `time.manual.create` | POST | time.track (task allows manual) | `taskId, date, start?, end?, minutes?, reason, category, reqId` | `OVERLAP, VALIDATION` |
| `time.manual.withdraw` | POST | owner | `sessionId` | Pending only |
| `time.adjust.create` | POST | owner | `sessionId, newStart, newEnd, reason` | |
| `time.approvals.list` | GET | time.approve | `status=pending, empId, from, to` | Excludes own |
| `time.approvals.decide` | POST | time.approve | `ids[], decision: approve\|reject, comment?` | Per-item results |
| `time.logs` | GET | time.view_scope (EM: own) | `taskId \| empId, from, to, page` | Sessions with events |
| `time.summary` | GET | self / scope | `taskIds[] \| empId, period` | W4 + task panel |
| `time.workload` | GET | time.view_scope | `from, days=5, empIds?` | W7 / R-19 |
| `time.policy.get` / `.save` | GET/POST | time.policy | `scope, branchId?, fields` | |
| `time.void` | POST | SA/CA | `sessionId, reason` | Audited override |
| `time.report` / `.export` | GET | time.view_scope | `report=R-01…R-11, filters, group, sort` | |

### 3.5 Calendar sync
| Action | M | Capability | Request | Response |
|---|---|---|---|---|
| `cal.connections` | GET | self | — | Status per provider |
| `cal.connect` | POST | self | `provider` | `{authUrl}` (signed state, PKCE) |
| `cal.disconnect` | POST | self | `provider, removeEvents:bool` | |
| `cal.resync` | POST | organizer | `meetingId` | Requeues the link |
| `cal.freebusy` | POST | meeting.create | `empIds[], from, to` | Busy blocks for connected users only |

### 3.6 Platform
| Action / route | M | Capability | Notes |
|---|---|---|---|
| `att.upload` | POST (multipart) | owner edit right | `ownerType, ownerId, file` → `{attachment}` |
| `att.list` | GET | owner view right | |
| `att.delete` | POST | uploader or owner editor | Soft delete |
| `GET /files/<id>` | GET | owner view right (session) | Streams with `Content-Disposition: attachment`, `X-Content-Type-Options: nosniff` |
| `system.health` | GET | SA | Queues, failures, providers, calendar errors |
| `reports.catalog` | GET | any | Reports the user may run |

## 4. Non-API routes (add to **both** `config/urls.py` and `saas/urls_tenant.py`; the OAuth callback goes in `saas/urls_platform.py` too)
| Route | Purpose | Security |
|---|---|---|
| `GET/POST /hooks/whatsapp/<token>` | Meta verification challenge + delivery and status webhooks + inbound STOP/START keywords | `token` = provider `webhook_token`; `X-Hub-Signature-256` HMAC with the app secret; 5-minute replay window; CSRF-exempt; 200 within 2 s, processing queued |
| `POST /hooks/sms/<token>` | SMS delivery reports (DLR) + STOP | Provider token; IP allow-list where the provider publishes one |
| `POST /hooks/google/<token>` | Calendar push notifications | Channel token check (`X-Goog-Channel-Token`) |
| `POST /hooks/microsoft/<token>` | Graph change notifications (+ validationToken echo) | `clientState` check |
| `GET /oauth/<provider>/callback` | OAuth return | Signed `state`, PKCE, single use, 10-minute expiry |
| `GET /m/<signed-token>` | RSVP / join landing from WhatsApp and e-mail links | Signed (meeting, participant, expires at meeting end), single meeting; requires sign-in for internal users to see details |
