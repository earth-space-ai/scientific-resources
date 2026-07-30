# Maintenance and synchronization

## Invariants

1. `data/opportunities.json` is the sole canonical opportunity data file.
2. `data/schema.json` is the public contract for that file.
3. Every published stable ID remains present in current canonical data and public history. Do not model retirement as disappearance.
4. Each current record has lifecycle fields: `first_seen`, `last_verified`, `retired_at`, `retirement_reason`, `superseded_by`, and `reactivated_at`.
5. Each current `1.1.0` record has public-safe `relevance`, `landscape`, and `resources` objects. Curated membership is derived from relevance and never stored as a second editable boolean.
6. History under `data/history/` is append-only. Existing snapshot directories are immutable and must not be overwritten.
6. Production builds require `data/opportunities.json` to byte-match the latest history snapshot.
7. Both host profiles are built in one invocation from the same in-memory object.
8. Generated standalone JSON remains byte-identical across the primary and mirror.
9. Generated HTML embeds an object equal to the standalone JSON.
10. A rebuild never changes snapshot facts or dates by itself.
11. Both HTML profiles use the exact root-relative data, provenance, and history paths under `/scientific-resources/`.
12. The primary is canonical at the inspected slashless URL `https://earth-space-ai.org/scientific-resources`; the mirror is self-canonical at `https://huangzesen.github.io/scientific-resources/` and alternates to the primary.
13. New `1.1.0` snapshots include a deterministic `funding_pulse.json` sidecar; legacy `1.0.0` snapshots are explicitly pulse-unavailable.
14. The public pages have no analytics, tracking, remote script, remote stylesheet, or remote font dependency.

## Reviewed refresh procedure

A factual refresh must be a deliberate, reviewed data change:

1. Check every record against official source pages; do not inspect only a seed list or the `grants` group. Treat endpoint availability, sponsor intake and tracker actionability as separate facts.
2. Calculate the inclusive actionability cutoff from the actual Pacific `page_date`: `cutoff = page_date + 15 calendar days`. For each open/upcoming fixed-cycle record, verify the machine date, sponsor wording and timezone. Day `+15` must be archived; day `+16` may remain actionable. Overdue fixed dates are not actionable.
3. Use only applicant-controlled final deadlines. Exclude review, decision, notification, publication and processing dates. A current fixed record with no machine date is release-blocking until it is corrected or explicitly modeled as `tbd`.
4. For a qualifying specific cycle, preserve the stable ID, official deadline, machine date and source evidence; set `status=closed`, `deadline_kind=closed`, `retired_at=page_date`, and a public reason that distinguishes tracker policy from sponsor closure; clear `application_url`, `apply_label` and `closing_soon`.
5. Do not close a continuously open program because one recurring cutoff enters the fence. Advance `next_deadline` to the next officially verified cutoff outside the fence. Model a multi-element umbrella as `tbd` with no single machine deadline; create element-level stable IDs before treating individual elements as actionable cycles.
6. Add a record only after assigning a new stable ID. Never remove an already published ID. Use retirement or supersession lifecycle fields instead of disappearance.
7. Set `page_date`, collection `verified_at`, and per-record verification values to actual review dates. Set `first_seen` for new records and update `last_verified` with `verified_date`. Never advance dates merely because a build runs.
8. Reactivation is an audited transition: clear retirement fields, restore actionable status/kind/route/deadline, and set a fresh `reactivated_at=page_date`. Preserve that reactivation date if the record is later retired again. Distinct named cycles should normally receive distinct stable IDs.
9. Recompute declared counts, derived view counts, relevance counts, and the separate 10-day `closing_soon` presentation flag from reviewed data.
10. Before writing history, run semantic validation and inspect the prospective manifest. A real archive transition must be `retired`, not merely `changed`; a real reactivation must be `reactivated`; recurring-cutoff and umbrella corrections are ordinary `changed` events.
11. Append exactly one immutable history snapshot for the reviewed current data. For the next parent candidate-data integration, run:

   ```sh
   python3 src/record_snapshot.py --expected-page-date 2026-07-30
   ```

   The recorder refuses overwrite, missing previously published IDs, invalid lifecycle state, fabricated transition events, and non-canonical JSON.
12. Run the generator and the complete tests:

   ```sh
   python3 src/generate.py
   python3 -m unittest discover -s tests -v
   ```

13. Inspect the data, `data/history/index.json`, the new snapshot directory, and generated changes. Confirm source URLs, application URLs, status counts, lifecycle transitions, canonical/alternate metadata, exact root-relative resource paths, and provenance digests.
14. Run the complete test suite again before release.

The original baseline hash assertion is a review tripwire for history immutability. An intentional factual refresh appends a new snapshot instead of changing existing history.

## Provenance manifests

Each host output includes `provenance.json`. The generator records:

- snapshot and collection verification dates;
- public host profile and alternate URL;
- canonical data SHA-256, record count, and status counts;
- SHA-256 digests for repository-relative build inputs; and
- SHA-256 digests for generated HTML, standalone JSON, root Funding Pulse, snapshot index, snapshot data, change manifests, and snapshot Funding Pulse sidecars.

No build clock time, machine name, absolute path, environment capture, or network result is included. Therefore an unchanged input tree produces byte-identical manifests.

## Exact owner-controlled checkout synchronization

The host wiring and the reviewed static-file copy are deliberately separate operations.

### One-time primary host wiring

The exact Next.js rewrite for `/scientific-resources/` belongs to the primary application checkout. The owner reviews and commits that one-time rewrite separately. `src/sync_checkouts.py` never creates, edits, or stages `next.config.mjs`. Once the rewrite is committed, subsequent releases use the committed rewrite plus the exact-copy helper below.

### Plan first

Both destination paths must be explicit Git worktree roots, and both worktrees must be clean, including untracked files. From this source repository, run the default read-only plan:

```sh
python3 src/sync_checkouts.py \
  --primary-checkout /path/to/primary-checkout \
  --mirror-checkout /path/to/mirror-checkout
```

The plan validates both Git roots, clean pre-states, build inputs, destination types, and symlink safety. It derives a complete allowlist from every regular generated file in the selected `dist/primary/` and `dist/mirror/` profiles, then prints each source-to-destination operation and its SHA-256 value without writing anything. The operation count therefore grows with immutable history and pulse sidecars.

### Apply the exact generated path set

After review, repeat the command with `--apply`. The helper performs only the planned generated-path copies:

| Build profile | Destination checkout directory | Exact files |
|---|---|---|
| `dist/primary/` | `<primary>/public/scientific-resources/` | `index.html`, `public_opportunities.json`, `funding_pulse.json`, `provenance.json`, `snapshots/...` |
| `dist/mirror/` | `<mirror>/scientific-resources/` | `index.html`, `public_opportunities.json`, `funding_pulse.json`, `provenance.json`, `snapshots/...` |

It never deletes a file and has no cleanup or deployment mode. After copying, it requires each checkout's Git status to contain no path outside the complete generated-path allowlist derived for that host profile. It then proves every destination file is byte-identical to, and has the same SHA-256 as, the selected primary or mirror build profile. A dirty pre-state, unsafe destination, collateral path, wrong profile, missing output, or parity failure is an error.

The helper does not commit, push, publish, or contact a remote service. The owner reviews the two destination diffs, commits them through each host's normal process, and pauses if both hosts cannot be advanced together.

## CI template and reviewed artifact

[`docs/validate-build-workflow.yml`](validate-build-workflow.yml) is the reviewed read-only Actions template. It keeps `contents: read`, checks out the source, builds both profiles, runs the full standard-library suite, rejects tracked `dist/` drift, and then uses `actions/upload-artifact@v4` to package `dist/` as `scientific-resources-dist`.

The template has no deployment credential, deployment job, or cross-repository write. It is intentionally not installed under `.github/workflows/`; activation is a separate maintainer-controlled change requiring GitHub workflow permission. The tracked `dist/` tree and local test/sync commands remain the deterministic reviewed release path in the meantime. Artifact upload, once activated, is review transport rather than publication.
