# Scientific Resources

A static, official-source tracker for scientific compute allocations, cloud and API credits, and AI-for-science or open-science grants.

- **Primary:** <https://earth-space-ai.org/scientific-resources/>
- **Synchronized mirror:** <https://huangzesen.github.io/scientific-resources/>
- **Source repository:** `Earth-Space-AI/scientific-resources`

The inspected primary route normalizes its canonical and social URL to the slashless `https://earth-space-ai.org/scientific-resources`. The mirror intentionally remains self-canonical at `https://huangzesen.github.io/scientific-resources/` and links to the primary as its alternate.

## Snapshot scope

The canonical public dataset is a reviewed snapshot dated **2026-07-29** (`America/Los_Angeles`), with collection verification timestamp **2026-07-30T01:16:56Z**. The configured Pacific date did not roll over. The dataset contains 48 public records: 32 actionable open, 1 actionable upcoming, and 15 archived. These are preserved snapshot facts, not a claim that the same statuses remain current today.

A specific modeled cycle with an officially verified fixed applicant deadline on or before `page_date + 15 calendar days` is moved out of the actionable view. The boundary is inclusive and group-neutral. Policy archival is not a claim that the sponsor closed intake: the record preserves its official deadline and sources, removes its apply control, and shows a public `retirement_reason`. A continuously open program with recurring cutoffs advances to the next verified cutoff outside the fence rather than closing the whole program; a multi-element umbrella has no single fixed deadline.

`data/opportunities.json` is the sole canonical current opportunity data file. Every stable ID remains present after publication; retirement is represented with lifecycle fields, not by deleting the record. Each current record carries `first_seen`, `last_verified`, `retired_at`, `retirement_reason`, `superseded_by`, and `reactivated_at`. The exact original 32-record dataset is preserved as the first immutable history snapshot at `data/history/snapshots/2026-07-28-a5af1ed92d34/`.

## Build

Requirements: Python 3.10 or newer; no third-party Python packages.

```sh
python3 src/generate.py
```

The production build writes the current data, provenance, and immutable history files to each host profile:

```text
dist/primary/index.html
dist/primary/public_opportunities.json
dist/primary/provenance.json
dist/primary/snapshots/index.json
dist/primary/snapshots/<snapshot-id>/public_opportunities.json
dist/primary/snapshots/<snapshot-id>/change_manifest.json
dist/mirror/index.html
dist/mirror/public_opportunities.json
dist/mirror/provenance.json
dist/mirror/snapshots/index.json
dist/mirror/snapshots/<snapshot-id>/public_opportunities.json
dist/mirror/snapshots/<snapshot-id>/change_manifest.json
```

The generator refuses production output unless `data/opportunities.json` byte-matches the latest entry in `data/history/index.json`. This protects publication from unrecorded current-data edits. After reviewed candidate-data edits for the next snapshot, append history first:

```sh
python3 src/record_snapshot.py --expected-page-date 2026-07-29
python3 src/generate.py
```

The standalone current JSON files are byte-identical to the latest recorded snapshot. The HTML files embed that same object and load historical JSON only from same-origin root-relative `/scientific-resources/snapshots/...` paths. Host-specific differences are limited to canonical, alternate, social metadata, and visible primary/mirror navigation. Each provenance manifest contains only repository-relative input names, public host metadata, counts, and SHA-256 digests, including history artifacts.

## Validate

```sh
python3 -m unittest discover -s tests -v
```

The standard-library test suite validates the public schema, original baseline hash and source commit, the inclusive 15-day archive boundary, recurring and umbrella exceptions, lifecycle and repeated-reactivation invariants, recorder refusal paths, manifest transition logic, current/latest parity refusal, record and status counts, URL rules, public-field allowlist, privacy gates, embedded/standalone parity, server/browser archive rendering, exact host metadata and root-relative resource paths, self-contained assets, provenance digests, and safe two-checkout synchronization.

## Owner-controlled checkout synchronization

`src/sync_checkouts.py` accepts explicit clean Git checkout paths. Its default is a read-only plan:

```sh
python3 src/sync_checkouts.py \
  --primary-checkout /path/to/primary-checkout \
  --mirror-checkout /path/to/mirror-checkout
```

After reviewing the plan, the owner may add `--apply`. The helper copies only the exact generated files under each `dist/<profile>/` root: `index.html`, `public_opportunities.json`, `provenance.json`, and `snapshots/...`. It rejects a dirty pre-state, never deletes files, never edits `next.config.mjs`, and succeeds only after Git-status confinement plus byte/SHA-256 parity checks.

The primary application's one-time exact Next.js rewrite is a separate owner-reviewed and committed host change. Subsequent releases use that committed rewrite plus this helper; the helper does not create or alter the rewrite.

## Documentation

- [Methodology](docs/methodology.md)
- [Maintenance and synchronization](docs/maintenance.md)

## Release boundary

A reviewed read-only Actions workflow template is included at [`docs/validate-build-workflow.yml`](docs/validate-build-workflow.yml). It builds and tests both profiles, rejects tracked `dist/` drift, and uploads the reviewed `dist/` pair as one artifact, with no deployment job or cross-repository write. The template is intentionally inactive: activation requires a separate maintainer-controlled change through a GitHub credential with workflow permission. Until then, run the documented local validation commands and use the exact-sync helper; publication remains a deliberate owner action after both checkout diffs are reviewed.

## License

MIT. See [LICENSE](LICENSE).
