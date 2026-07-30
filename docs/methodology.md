# Public methodology

## Snapshot interpretation

This tracker is a dated research aid, not a live eligibility or availability service. The current public dataset records facts reviewed for 2026-07-29 in `America/Los_Angeles`; its collection-level verification timestamp is 2026-07-30T01:16:56Z. The configured Pacific date did not roll over, so `page_date` remains 2026-07-29. Publication or rebuilding does not advance those dates.

The current snapshot has 48 records:

| Tracker state | Records |
|---|---:|
| Actionable open | 32 |
| Actionable upcoming | 1 |
| Archived | 15 |

The three public groups contain 13 credit programs, 19 HPC/GPU programs, and 16 grant programs.

The public archive is append-only by stable ID. A published ID is not removed merely because a call is retired; the record remains visible, stays `status=closed`, and carries lifecycle metadata explaining the retirement or reactivation state. Historical snapshots are immutable JSON artifacts and are selected client-side from same-origin `/scientific-resources/snapshots/...` paths.

## Evidence policy

Only provider, agency, facility, foundation, or provider-linked application pages are accepted as evidence. Aggregators and secondhand lists are not evidence for a status decision. Every record keeps at least one public official-source URL and a dated verification value.

The canonical dataset preserves 93 official-source URL entries. Thirty-one records have an application URL; all 31 are actionable open records in this snapshot. Application controls are omitted for upcoming and archived records, even when an archived record’s official form remains live.

## Status definitions

- **Open:** official evidence showed current intake and the modeled cycle remained outside the tracker’s inclusive 15-calendar-day lead-time archive fence.
- **Upcoming:** official evidence promised a future round outside the archive fence, while intake was not yet open or a route had not been published.
- **Archived (`status=closed`):** non-actionable in this tracker because the sponsor closed intake, the named cycle passed, or a verified fixed applicant deadline was within 15 calendar days. A visible `retirement_reason` distinguishes tracker policy from sponsor closure.
- **Stale endpoint:** an old route no longer served the named call. This is an endpoint condition, not a fourth tracker state.

## Inclusive 15-day actionability policy

Let `V` be the reviewed `page_date` in `America/Los_Angeles`. For a specific modeled cycle, let `D` be the earliest still-relevant, officially published, applicant-controlled final deadline with its source timezone preserved. An otherwise open or upcoming record is archived when `D <= V + 15 calendar days`; the boundary is inclusive and applies across all groups. A deadline on day `+15` is archived, while day `+16` may remain actionable.

Policy archival is not a sponsor-closure claim. The record preserves its stable ID, official deadline text, machine date, sources and evidence; removes its public apply control; sets `retired_at`; and displays a reason explaining whether sponsor intake was still open. Overdue fixed dates are also non-actionable.

A continuously open program with recurring evaluation cutoffs does not close when an intermediate cutoff enters the fence. After official verification, the record advances to the next cutoff outside the fence. A multi-element umbrella has `deadline_kind=tbd` and no single machine deadline; individual program elements require distinct stable IDs before they can be evaluated as cycles. Review, decision, notification, publication and processing dates are not applicant deadlines.

## Lifecycle and reactivation

A new archive transition sets `retired_at` and `retirement_reason`; the immutable change manifest classifies the event as `retired`. Reactivation clears those retirement fields, restores an actionable state and route, and sets a fresh `reactivated_at` equal to the review date. If the record is later archived again, the prior `reactivated_at` remains in current data while the newer retirement is recorded; immutable snapshots retain the full sequence. Distinct named cycles should use distinct stable IDs rather than reactivating an old cycle.

## Endpoint and program separation

Page availability, sponsor intake and tracker actionability are checked separately. An available form can serve an archived or repurposed call, and an evergreen page can outlive its intake. Conversely, a moved page can point to a valid replacement route. Automated HTTP success alone must never change a record to open. The tracker suppresses apply controls for archived records even when the official endpoint remains live.

## Dates and closing-soon flags

`page_date` is the factual snapshot date. Collection and per-record `verified_at` values record when evidence was reviewed. A record’s `closing_soon` flag remains a deterministic 10-day presentation field: it is true only for an actionable open record with a machine-readable deadline on or before ten calendar days after `page_date`. It is separate from the stronger 15-day archive gate. The current snapshot has zero actionable closing-soon records.

## Public data boundary

`data/opportunities.json` contains only the fields declared by `data/schema.json`: stable public identity, provider and grouping, tracker state, resource and deadline descriptions, generic eligibility, endpoint note, public application and official-source URLs, verification dates, and lifecycle fields. Both hosts use this exact object after it has been recorded as the latest reviewed history snapshot.

## Limitations

Statuses, dates, amounts, rules, timezones and linked pages can change without notice. Source wording can be internally inconsistent; unresolved timezone ambiguity is preserved rather than silently normalized. Readers must confirm current details on official pages. The tracker is independent and is not affiliated with or endorsed by listed providers.
