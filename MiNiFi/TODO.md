# TODO

## S3 metadata and restartability

- [x] Complete the S3 metadata flow with a per-object `manifest.json` at `<S3_PREFIX>/<RUN_TIMESTAMP>/<schema>/<object>/manifest.json`.
- [x] Keep immutable per-file sidecars at `<S3_PREFIX>/<RUN_TIMESTAMP>/<schema>/<object>/metadata/<filename>.json`.
- [x] Define and implement the serialised manifest finalizer that aggregates sidecars without lost updates.
- [x] Use `manifest.json` as the only authoritative checkpoint; do not create or evaluate `_SUCCESS`.
- [x] Support restart with the same `RunTimestamp`: skip completed objects using the S3 manifest before re-uploading deterministic Parquet keys.
- [ ] Define the exact S3 existence and integrity check, including the minimum accepted metadata and optional checksum/ETag handling.
- [ ] Define object and file status transitions: `IN_PROGRESS`, `SUCCESS`, and `FAILED`.
- [ ] Define the per-object policy for zero-row results. Until then, zero rows are a valid success.

## Deferred validation

- [ ] Add `record_count` to each Parquet/sidecar entry.
- [ ] Add `expected_record_count` and compare it with the sum of file record counts.
- [ ] Define how source filters, `queryLimit`, incremental bounds, and database snapshot consistency affect count validation.
- [ ] Define manifest and data schema versioning rules. The initial values are `manifest_version: "1.0"` and `schema_version: "1.0"`.

## Repository cleanup

- [ ] Identify and remove unused directories, legacy templates, and obsolete scripts after verifying all references and supported workflows.
