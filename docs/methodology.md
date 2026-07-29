# Public methodology

## Snapshot interpretation

This tracker is a dated research aid, not a live eligibility or availability service. The current public dataset records facts reviewed for 2026-07-28 in `America/Los_Angeles`; its completed collection-level verification timestamp is 2026-07-29T04:27:05Z. The configured Pacific date did not roll over, so `page_date` remains 2026-07-28. Publication or rebuilding does not advance those dates.

The current snapshot has 32 records:

| Status | Records |
|---|---:|
| Open | 22 |
| Upcoming | 2 |
| Closed | 8 |

The three public groups contain 9 credit programs, 14 HPC/GPU programs, and 9 grant programs.

The public archive is append-only by stable ID. A published ID is not removed merely because a call is retired; the record remains visible, stays `status=closed`, and carries lifecycle metadata explaining the retirement or reactivation state. Historical snapshots are immutable JSON artifacts and are selected client-side from same-origin `/scientific-resources/snapshots/...` paths.

## Evidence policy

Only provider, agency, facility, foundation, or provider-linked application pages are accepted as evidence. Aggregators and secondhand lists are not evidence for a status decision. Every record keeps at least one public official-source URL and a dated verification value.

The canonical dataset preserves 77 official-source URLs. Twenty-two records have an application URL; all 22 are recorded as open in this snapshot. Application URLs are omitted for upcoming and closed records.

## Status definitions

- **Open:** an official page exposed a current intake or stated that proposals or requests could be submitted on the snapshot date.
- **Upcoming:** an official page promised a future round or resource, while an intake was not yet open or a route had not been published.
- **Closed:** the named call's deadline had passed or the official page said intake was closed.
- **Stale endpoint:** an old route no longer served the named call. This is an endpoint condition, not a fourth program status.

## Endpoint and program separation

Page availability and named-program openness are checked separately. An available form can serve a closed or repurposed call, and an evergreen page can outlive its intake. Conversely, a moved page can point to a valid replacement route. Automated HTTP success alone must never change a record to open.

## Dates and closing-soon flags

`page_date` is the factual snapshot date. `verified_at` records when evidence was collected. A record's `closing_soon` flag is deterministic: it is true only for an open record with a machine-readable deadline on or before ten calendar days after `page_date`. The current snapshot has three such records.

## Public data boundary

`data/opportunities.json` contains only the fields declared by `data/schema.json`: stable public identity, provider and grouping, status, resource and deadline descriptions, generic eligibility, endpoint note, public application and official-source URLs, verification dates, and lifecycle fields. Both hosts use this exact object after it has been recorded as the latest reviewed history snapshot.

## Limitations

Statuses, dates, amounts, rules, and linked pages can change without notice. Readers must confirm current details on official pages. The tracker is independent and is not affiliated with or endorsed by listed providers.
