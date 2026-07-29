# Scientific Resources

A static, official-source tracker for scientific compute allocations, cloud and API credits, and AI-for-science or open-science grants.

- **Primary:** <https://earth-space-ai.org/scientific-resources/>
- **Synchronized mirror:** <https://huangzesen.github.io/scientific-resources/>
- **Source repository:** `Earth-Space-AI/scientific-resources`

The inspected primary route normalizes its canonical and social URL to the slashless `https://earth-space-ai.org/scientific-resources`. The mirror intentionally remains self-canonical at `https://huangzesen.github.io/scientific-resources/` and links to the primary as its alternate.

## Snapshot scope

The canonical public dataset is a reviewed snapshot dated **2026-07-28** (`America/Los_Angeles`), with the completed collection verification timestamp **2026-07-29T04:27:05Z**. The configured Pacific date did not roll over. The dataset contains 32 public records: 22 open, 2 upcoming, and 8 closed. These are preserved snapshot facts, not a claim that the same statuses remain current today.

`data/opportunities.json` is the sole canonical opportunity data file. Both host outputs are generated from that exact object. The fresh verification changed only the top-level collection `verified_at`; the 32 record facts, statuses, deadlines, URLs, and per-record timestamps remain unchanged. Do not hand-edit generated JSON or HTML.

## Build

Requirements: Python 3.10 or newer; no third-party Python packages.

```sh
python3 src/generate.py
```

The build writes three tracked files to each host profile:

```text
dist/primary/index.html
dist/primary/public_opportunities.json
dist/primary/provenance.json
dist/mirror/index.html
dist/mirror/public_opportunities.json
dist/mirror/provenance.json
```

The standalone JSON files are byte-identical to the canonical dataset. The HTML files embed the same JSON object and link their standalone data and provenance at `/scientific-resources/public_opportunities.json` and `/scientific-resources/provenance.json`. Host-specific differences are limited to canonical, alternate, social metadata, and visible primary/mirror navigation. Each provenance manifest contains only repository-relative input names, public host metadata, counts, and SHA-256 digests.

## Validate

```sh
python3 -m unittest discover -s tests -v
```

The standard-library test suite validates the public schema, fixed snapshot hash, record and status counts, group matrix, deadline facts, unique IDs, URL rules, public-field allowlist, privacy gates, embedded/standalone parity, exact host metadata and root-relative resource paths, semantic pre-rendering, self-contained assets, provenance digests, deterministic rebuilding, and safe two-checkout synchronization.

## Owner-controlled checkout synchronization

`src/sync_checkouts.py` accepts explicit clean Git checkout paths. Its default is a read-only plan:

```sh
python3 src/sync_checkouts.py \
  --primary-checkout /path/to/primary-checkout \
  --mirror-checkout /path/to/mirror-checkout
```

After reviewing the plan, the owner may add `--apply`. The helper copies only `{index.html, public_opportunities.json, provenance.json}` from `dist/primary/` to the primary checkout's `public/scientific-resources/` and from `dist/mirror/` to the mirror checkout's `scientific-resources/`. It rejects a dirty pre-state, never deletes files, never edits `next.config.mjs`, and succeeds only after Git-status confinement plus byte/SHA-256 parity checks.

The primary application's one-time exact Next.js rewrite is a separate owner-reviewed and committed host change. Subsequent releases use that committed rewrite plus this helper; the helper does not create or alter the rewrite.

## Documentation

- [Methodology](docs/methodology.md)
- [Maintenance and synchronization](docs/maintenance.md)

## Release boundary

A reviewed read-only Actions workflow template is included at [`docs/validate-build-workflow.yml`](docs/validate-build-workflow.yml). It builds and tests both profiles, rejects tracked `dist/` drift, and uploads the reviewed `dist/` pair as one artifact, with no deployment job or cross-repository write. The template is intentionally inactive: activation requires a separate maintainer-controlled change through a GitHub credential with workflow permission. Until then, run the documented local validation commands and use the exact-sync helper; publication remains a deliberate owner action after both checkout diffs are reviewed.

## License

MIT. See [LICENSE](LICENSE).
