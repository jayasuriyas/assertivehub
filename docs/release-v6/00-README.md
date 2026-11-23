# Release v6 "Connected Operations": Requirements and Implementation Plan

**Product:** Assertive Hub (Django edition, single-company and SaaS) · **Date:** 23 Nov 2026 · **Status:** Draft for review. **No application code has been changed.**
**Scope:** WhatsApp/SMS alerts · Multi-branch management · Meeting schedule management · Task work-time tracking, planned as **one integrated release**.

## How to read this pack
| # | Document | For |
|---|---|---|
| 01 | [Current system analysis](01-current-system-analysis.md) | Everyone: what exists today and what constrains the design |
| 02 | [BRD](02-BRD.md) | Business owners, sales: objectives, KPIs, scope, scenarios, additional-feature analysis, packaging |
| 03 | [SRS](03-SRS.md) | Engineering, QA: functional requirements, permission matrix, business rules, NFRs |
| 04 | [Detailed specifications](04-specifications.md) | Engineering, design: event matrix, calendar sync, timer maths, widgets, screens, reports |
| 05 | [User stories](05-user-stories.md) | Everyone: 30 stories with Given/When/Then acceptance criteria |
| 06 | [Data model and ER diagram](06-data-model.md) | Backend |
| 07 | [API specification](07-api-spec.md) | Backend, frontend |
| 08 | [End-to-end workflows](08-workflows.md) | Everyone |
| 09 | [Edge cases and QA test plan](09-qa-test-plan.md) | QA, engineering |
| 10 | [Implementation plan and checklist](10-implementation-plan.md) | PM, engineering leads |
| 11 | [Implementation notes: deviations and testing](11-implementation-notes.md) | Engineering, QA, operations |

### Where each requested deliverable lives
| Requested deliverable | Location |
|---|---|
| Existing-system analysis (Module 8) | 01 |
| 1 BRD | 02 |
| 2 SRS | 03 |
| 3 User stories · 4 Acceptance criteria | 05 |
| 5 Functional requirements | 03 §1–5 |
| 6 Non-functional requirements | 03 §8 |
| 7 Business rules | 03 §7 |
| 8 End-to-end workflows | 08 (the 17-step integrated workflow is 08 §1) |
| 9 Role and permission matrix | 03 §6 |
| 10 Database changes and ER diagram | 06 |
| 11 API endpoints | 07 |
| 12 UI/UX screens (+ navigation) | 04 §5 |
| 13 Dashboard widgets | 04 §4 |
| 14 Notification event matrix | 04 §1 |
| 15 Calendar integration | 04 §2 |
| 16 Task timer calculation logic | 04 §3 |
| 17 Reporting | 04 §6 |
| 18 Edge cases and exceptions | 09 §1 |
| 19 QA test scenarios | 09 §2 |
| 20 Development checklist | 10 §7 |
| Additional features analysis (Module 7) | 02 §7 |

## Executive summary
The current system is a Django 6.1 app with one large single-page frontend. All business data is stored as **JSON documents** in a generic `Record` table, and each browser **downloads everything it may see** and syncs changes back. That design suits today's 15–150-person customers, but it shapes this release in three ways:

1. **New modules get proper relational tables and their own endpoints.** Timers, meetings, notifications and branch history need constraints, range queries and reporting that the JSON store cannot give. They are kept out of the full-download sync so they do not slow every user down.
2. **Branch isolation is the riskiest change.** Today, any admin receives the whole company's data and can write any employee's records. Every one of those paths (listed in 10 §4.1) must be narrowed by a single server-side scope check and covered by an automated permission-matrix test.
3. **There is no background worker.** Scheduled reminders, retries, calendar sync and timer auto-stop need a small, tenant-aware job runner. It is designed to be added without Redis or Celery.

The four modules share one notification service, one branch/scope service, one job runner and the existing employee and task records. Meeting action items become ordinary tasks, timers run on those same tasks, and branch attribution applies across every report. Existing single-office customers see no change until they switch the new modules on.

## Key design decisions (recommended; approve or challenge)
| ID | Decision | Why |
|---|---|---|
| D1 | v6 entities are **relational Django models** in `core`. Existing JSON collections only get additive fields | Constraints (one running timer, room booking, no duplicate messages), indexed range queries and SQL reports |
| D2 | `Branch` + **effective-dated assignment history** as source of truth. `employees.branchId` is a server-maintained mirror | Correct history after transfers (BR-B7) and fast legacy filtering |
| D3 | New roles **HR Admin, Branch Admin, Branch Manager** map onto existing ranks (3, 3, 2) plus a branch-scope table. The existing **Admin** stays company-wide | Existing rank checks keep working and existing customers are unaffected |
| D4 | **One notification service** with a transactional outbox, channels WhatsApp/SMS/e-mail/in-app, consent and dedupe. Today's leave e-mail alerts move into it unchanged | No duplicated messaging code. Reliable, auditable, no sends inside requests |
| D5 | **Own DB-backed job runner** (`hub_worker` + `hub_tick` cron) | Fits SQLite-per-tenant. No new infrastructure. Revisit Celery with PostgreSQL in v6.1 |
| D6 | **Meetings are separate from Calendar events.** The Calendar page overlays meetings | Events are all-day company dates. Meetings need times, people, rooms and minutes |
| D7 | Calendar: **one provider event per meeting, in the organizer's calendar**, a stable iCal UID, and `.ics` e-mail when the organizer is not connected. External edits are flagged, not applied, in v6 | Avoids duplicate events, which is the most common failure in calendar integrations |
| D8 | **Server-side timer** with running totals and an append-only event log. Idempotent request ids | Accuracy, survives browser close, auditable, O(1) reads |
| D9 | Task time is **information only**. It is separate from attendance and payroll hours, and the performance score is unchanged | BR-T10. Avoids disputes over surveillance |
| D10 | Sub-pages are **in-page tabs**. Nav groups and the mobile bar are kept. Modules are **off by default** | UX consistency and safe, incremental rollout |
| D11 | **Branch announcements reuse the Notice board** (audience + "also send via") | No duplicate announcement entity |

## Open decisions for the product owner
| ID | Question | Recommendation |
|---|---|---|
| OD-01 | SaaS packaging and pricing for v6 features | As in 02 §8: meetings and time tracking in all plans; WhatsApp/SMS, calendar sync and multi-branch from Business |
| OD-02 | Who holds the WhatsApp/SMS account in SaaS | **Bring-your-own** credentials per company for v6; a platform-managed sender later |
| OD-03 | A second WhatsApp provider (BSP such as Gupshup, Interakt or Twilio) in v6? | Only if pilot customers cannot onboard on Meta directly. The adapter interface allows adding it in ~1 sprint |
| OD-04 | Birthday wishes over WhatsApp (likely *marketing* category: higher price, stricter opt-in) | Off by default; in-app on |
| OD-05 | Can Branch Admins approve leave as well as reporting managers? | Yes (FR-BR-14) |
| OD-06 | Should logout pause a running timer? | Yes by default, configurable |
| OD-07 | Apply edits made in Google/Outlook back to the meeting automatically? | Not in v6; flag them for the organizer (phase 2) |
| OD-08 | Commit to the v6.1 scalability track (PostgreSQL, paged load) | Yes. v6 is sized for ≤ 500 employees and ≤ 50 branches per tenant (NFR-S1) |

## Start now: external lead times
These run in parallel with development and are on the critical path for WhatsApp/SMS and calendar going live: **Meta WhatsApp business verification and template approval** (days to weeks), **TRAI DLT** entity, header and template registration for SMS in India (1–3 weeks), **Google OAuth verification** for the sensitive `calendar.events` scope (several weeks), and a **Microsoft** multi-tenant app registration and publisher verification.

## Pre-existing issues noticed (not part of v6)
Hard-coded `DEBUG` and `ALLOWED_HOSTS` in `config/settings.py` (contrary to the README), the unused `tenancy/` package, the leave quota checked only in the browser, a full JSON backup written on every save, and no automated tests. Details are in 01 §8.

## Glossary
**SA** Super Admin · **CA** Company Admin (existing `admin`) · **HR** HR Admin · **BA** Branch Admin · **BM** Branch Manager · **MG** Manager (reporting line) · **EM** Employee · **HO** Head office · **Scope** the branches and employees a user may see · **E / A / P** estimated / actual / pending time · **DLT** India's SMS registration system · **BSP** WhatsApp Business Solution Provider · **ICS / .ics** calendar invitation file format · **Outbox** jobs stored with the business change and sent after commit.
