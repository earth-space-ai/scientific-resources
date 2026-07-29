# Maintenance and synchronization

## Invariants

1. `data/opportunities.json` is the sole canonical opportunity data file.
2. `data/schema.json` is the public contract for that file.
3. Both host profiles are built in one invocation from the same in-memory object.
4. Generated standalone JSON remains byte-identical across the primary and mirror.
5. Generated HTML embeds an object equal to the standalone JSON.
6. A rebuild never changes snapshot facts or dates by itself.
7. Both HTML profiles use the exact root-relative data and provenance paths `/scientific-resources/public_opportunities.json` and `/scientific-resources/provenance.json`.
8. The primary is canonical at the inspected slashless URL `https://earth-space-ai.org/scientific-resources`; the mirror is self-canonical at `https://huangzesen.github.io/scientific-resources/` and alternates to the primary.
9. The public pages have no analytics, tracking, remote script, remote stylesheet, or remote font dependency.

## Reviewed refresh procedure

A factual refresh must be a deliberate, reviewed data change:

1. Check each record against its official source pages. Treat endpoint availability separately from named-program status.
2. Update only public fields defined by the schema. Add or remove a record only with an intentional count and ID review.
3. Set `page_date`, collection `verified_at`, and per-record verification values to the actual review dates. Never advance dates merely because a build runs. Collection verification completed at `2026-07-29T04:27:05Z` for the current snapshot; `page_date` remains `2026-07-28` because the configured Pacific date did not roll over.
4. Recompute declared counts and `closing_soon` values from the reviewed data.
5. Run the generator and the complete tests:

   ```sh
   python3 src/generate.py
   python3 -m unittest discover -s tests -v
   ```

6. Inspect the data and generated changes. Confirm source URLs, application URLs, status counts, canonical/alternate metadata, exact root-relative resource paths, and provenance digests.
7. Run the complete test suite again before release.

The fixed snapshot-hash assertion is a review tripwire. An intentional factual refresh must update that expected hash in the same reviewed change after all semantic checks pass.

## Provenance manifests

Each host output includes `provenance.json`. The generator records:

- snapshot and collection verification dates;
- public host profile and alternate URL;
- canonical data SHA-256, record count, and status counts;
- SHA-256 digests for repository-relative build inputs; and
- SHA-256 digests for generated HTML and standalone JSON.

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

The plan validates both Git roots, clean pre-states, build inputs, destination types, and symlink safety. It prints six source-to-destination operations and their SHA-256 values but writes nothing.

### Apply the exact file pair

After review, repeat the command with `--apply`. The helper performs only these copies:

| Build profile | Destination checkout directory | Exact files |
|---|---|---|
| `dist/primary/` | `<primary>/public/scientific-resources/` | `index.html`, `public_opportunities.json`, `provenance.json` |
| `dist/mirror/` | `<mirror>/scientific-resources/` | `index.html`, `public_opportunities.json`, `provenance.json` |

It never deletes a file and has no cleanup or deployment mode. After copying, it requires each checkout's Git status to contain no path outside its exact three-file allowlist. It then proves every destination file is byte-identical to, and has the same SHA-256 as, the selected primary or mirror build profile. A dirty pre-state, unsafe destination, collateral path, wrong profile, missing output, or parity failure is an error.

The helper does not commit, push, publish, or contact a remote service. The owner reviews the two destination diffs, commits them through each host's normal process, and pauses if both hosts cannot be advanced together.

## CI template and reviewed artifact

[`docs/validate-build-workflow.yml`](validate-build-workflow.yml) is the reviewed read-only Actions template. It keeps `contents: read`, checks out the source, builds both profiles, runs the full standard-library suite, rejects tracked `dist/` drift, and then uses `actions/upload-artifact@v4` to package `dist/` as `scientific-resources-dist`.

The template has no deployment credential, deployment job, or cross-repository write. It is intentionally not installed under `.github/workflows/`; activation is a separate maintainer-controlled change requiring GitHub workflow permission. The tracked `dist/` tree and local test/sync commands remain the deterministic reviewed release path in the meantime. Artifact upload, once activated, is review transport rather than publication.
