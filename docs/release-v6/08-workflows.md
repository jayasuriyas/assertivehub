# 08 — End-to-End Workflows

## 1. Integrated workflow (Module 5): the 17 steps traced through the system

| # | Step | Actor | Screen | API | Data written | Notification | Shared foundation used |
|---|---|---|---|---|---|---|---|
| 1 | Create the Bangalore branch | SA | S-02 | `branches.save` | `Branch` | — | Audit, plan limit |
| 2 | Assign employees to BLR | HR | S-04 / bulk | `transfers.create` / `emp.assign` | `Transfer`, `EmployeeBranchAssignment`, `employees.branchId` | N19 at effective date | Scope, job runner |
| 3 | Create a task with a 5 h estimate | Manager / BM | S-22 | `save` (tasks op) | `tasks` record + `est`, `createdBy`, `branchId` | → step 4 | FR-PLT-12 |
| 4 | Employee gets a WhatsApp alert | System | — | job `notify.dispatch` | `NotificationJob`, `NotificationDelivery` | **N06** | Notification service, consent |
| 5 | Start the task timer | Employee | S-23/S-24 | `time.start` | `WorkSession` (running), `WorkSessionEvent` | — | Server clock |
| 6 | Pause and resume | Employee | S-24 | `time.pause` / `time.resume` | Events, running totals | — | — |
| 7 | Schedule a team meeting | Manager | S-17 | `meetings.conflicts`, `meetings.save` | `Meeting`, `MeetingParticipant` | → 10 | Scope, rooms, leave |
| 8 | Meeting appears in the widget | Participants | W1 | `meetings.widget` | — | — | Permission-aware widget |
| 9 | Synced to the connected calendar | System | — | job `calsync.push` | `CalendarEventLink` | Provider invite or `.ics` | Job runner, encrypted tokens |
| 10 | WhatsApp/SMS reminders | System | — | job `notify.dispatch` (scheduled) | Deliveries (+ SMS fallback) | **N10, N11** | Notification service |
| 11 | Meeting is held | Organizer | S-18 | `meetings.attendance`, `meetings.complete` | Participant attendance, status | — | — |
| 12 | Record minutes | Organizer | S-19 | `meetings.minutes.save` (publish) | `MeetingMinutes` | **N20** | — |
| 13 | Action items become tasks | Organizer | S-19 | `meetings.actions.toTasks` | `tasks` records (`src=meeting`), `MeetingActionItem.task_rid` | **N06** | Task module (same records) |
| 14 | Complete the original task | Employee | S-23 | `time.complete` | Session stopped, task `done` | **N08** | — |
| 15 | System calculates actual time | System | S-23 | `time.summary` | (computed) | N09 if over | Timer logic 04 §3 |
| 16 | Manager views productivity reports | Manager | S-28 | `time.report` R-01/R-08 | — | — | Scope, attribution |
| 17 | SA views consolidated branch reports | SA | S-28 | `time.report`, R-12 | — | — | Branch attribution BR-B7 |

```mermaid
sequenceDiagram
    autonumber
    actor SA as Super Admin
    actor HR as HR Admin
    actor MG as Manager (BLR)
    actor EM as Employee (Rahul)
    participant API as /api (Django)
    participant DB as Tenant DB
    participant W as hub_worker
    participant WA as WhatsApp / SMS
    participant CAL as Google / Outlook

    SA->>API: branches.save (BLR)
    API->>DB: Branch + AuditEvent
    HR->>API: transfers.create (effective 1 Dec)
    API->>DB: Transfer(scheduled) + Job(apply at 00:00 IST)
    W->>DB: apply transfer → assignments, employees.branchId
    MG->>API: save tasks op (est 5h)
    API->>DB: task record + NotificationJob(N06) [same transaction]
    W->>WA: send template "task_assigned"
    WA-->>API: webhook delivered/read
    EM->>API: time.start / pause / resume
    API->>DB: WorkSession + events (server timestamps)
    MG->>API: meetings.save (team, room, reminders)
    API->>DB: Meeting + participants + Jobs(invite, reminders, calsync)
    W->>CAL: create event (deterministic id)
    W->>WA: invitations, later reminders (WA → SMS fallback)
    MG->>API: minutes.save(publish) + actions.toTasks
    API->>DB: minutes + new task records + N06 jobs
    EM->>API: time.complete
    API->>DB: session stopped, task done, N08 job
    MG->>API: time.report R-08 (branch BLR)
    SA->>API: report R-12 (all branches)
```

## 2. Notification pipeline

```mermaid
flowchart LR
    A[Business action<br/>save / meetings.save / time.* / punch] -->|same transaction| B[(NotificationJob<br/>unique event+entity+occurrence)]
    S[Scheduler job<br/>reminders, birthdays, attendance] --> B
    C[Campaign / Notice 'also send'] --> B
    B --> D{hub_worker: due?}
    D -->|yes| E[Still valid? BR-N7]
    E -->|no| X[cancel]
    E -->|yes| F[Resolve recipients<br/>scope, active, prefs, mandatory]
    F --> G[Per channel in order:<br/>enabled? template approved?<br/>verified+opt-in? cap? quiet hours?]
    G -->|skip| K[(Delivery: skipped + reason)]
    G -->|ok| H[(Delivery queued<br/>unique dedupe_key)]
    H --> I[Render variables at send time<br/>branch tz formatting]
    I --> J[Provider adapter send]
    J -->|temp error| R[retry 1m/5m/15m]
    J -->|perm error| P[failed_perm]
    J -->|accepted| Q[sent]
    Q --> WH[Webhook: delivered / read / failed]
    P --> FB{SMS fallback on?}
    WH -->|failed or not delivered in N min| FB
    FB -->|yes| H2[(Delivery SMS<br/>fallback_of = WA)]
    H --> IA[(Always: in-app item)]
```

## 3. Branch transfer

```mermaid
flowchart TD
    T0[HR/SA opens Transfer] --> T1[transfers.preview<br/>side-effects checklist]
    T1 --> T2{Valid?<br/>dates, scope both branches,<br/>no scheduled transfer}
    T2 -->|no| TE[Show error]
    T2 -->|yes| T3[transfers.create]
    T3 --> T4{Effective date}
    T4 -->|today or back-dated| T5[Apply now]
    T4 -->|future| T6[Status scheduled<br/>Job at 00:00 destination tz]
    T6 --> T5
    T5 --> T7[End old primary assignment day-1<br/>Start new assignment<br/>Update branchId, reportsTo, dept, designation]
    T7 --> T8[Re-route pending leave and time approvals]
    T8 --> T9[Apply chosen task reassignments<br/>stop open timers on reassigned tasks]
    T9 --> T10[AuditEvent + N19]
    T10 --> T11[Old branch loses profile access<br/>history stays in old branch reports]
```

## 4. Meeting lifecycle

```mermaid
stateDiagram-v2
    [*] --> Scheduled: save (invites, reminders, sync queued)
    Scheduled --> Scheduled: material edit (seq+1, N12, sync update)
    Scheduled --> InProgress: start time reached (derived)
    InProgress --> Completed: organizer marks completed
    InProgress --> AwaitingUpdate: end + 2h without update (flag)
    AwaitingUpdate --> Completed: organizer marks completed
    Scheduled --> Cancelled: cancel (reason, N13, sync cancel, reminders cancelled)
    Completed --> Completed: attendance, minutes draft → published (N20), action items → tasks
    Cancelled --> [*]
    Completed --> [*]
```

**Calendar sync per meeting save:** commit the meeting → queue `calsync.push(meeting, seq)` → the worker loads the organizer connection → if absent, queue an `.ics` e-mail per participant (same UID, SEQUENCE) → if present, upsert the provider event (deterministic id or `transactionId`), store the etag → on error, back off (04 §2.6). Because the `unique_key` is `calsync:{meeting}:{seq}`, a sequence is never pushed twice. An older sequence still in the queue is skipped when a newer one exists.

## 5. Task timer session

```mermaid
sequenceDiagram
    actor EM as Employee
    participant UI as Browser (timer bar)
    participant API as time.* actions
    participant DB as DB (WorkSession)
    participant W as hub_worker

    EM->>UI: ▶ Start work (task B)
    UI->>API: time.start {taskId:B, reqId, clientTs}
    API->>DB: running session exists? (A)
    alt A running and switch=false
        API-->>UI: 409 TIMER_ACTIVE {session A}
        UI->>EM: "Stop A and start B?"
        EM->>UI: Switch
        UI->>API: time.start {taskId:B, switch:true, reqId2}
        API->>DB: stop A at t, start B at t (one transaction)
    else none running
        API->>DB: insert session B (status running, started_at = server now)
    end
    API-->>UI: {session, now}
    UI->>UI: offset = now - Date.now(), render live time
    EM->>UI: ⏸ Pause (Client call)
    UI->>API: time.pause
    API->>DB: work += t - last; status paused; event
    Note over UI: Browser closed. Session continues on the server
    W->>DB: auto-stop check every minute
    W->>DB: cut-off reached → ended_at = cutoff, auto_stopped, needs_review
    W->>W: queue N18
```

## 6. Manual time entry and approval

```mermaid
flowchart LR
    M1[Employee: Add manual entry] --> M2{Validate<br/>reason ≥10, not future,<br/>≤ N days back, no overlap,<br/>day total ≤ 16h}
    M2 -->|fail| M3[Show error]
    M2 -->|ok| M4{requireApproval?}
    M4 -->|no| M7[approved + audit]
    M4 -->|yes| M5[pending → N16 to approvers<br/>reportsTo / BM / BA / CA / SA, never self]
    M5 --> M6{Decision}
    M6 -->|approve| M7
    M6 -->|reject + comment| M8[rejected → N17]
    M7 --> M9[Counts in Actual → N17]
```

## 7. Branch-scope enforcement (every request)

```mermaid
flowchart LR
    R[Request /api/?a=X] --> H[X-Requested-With check] --> SA[SaaS block check] --> AU[need_auth]
    AU --> MF{module on?} -->|no| E1[403 MODULE_OFF]
    MF -->|yes| CP{capability?} -->|no| E2[403 FORBIDDEN]
    CP -->|yes| SC[scope = scope_for user, date]
    SC --> Q[Query filtered by scope<br/>branch_id IN scope.branches OR emp_id IN scope.emps]
    Q --> TGT{target in scope?} -->|no| E3[404 NOT_FOUND]
    TGT -->|yes| ACT[Perform action] --> AUD[AuditEvent] --> RESP[Response]
```
