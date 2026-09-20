# Verification status

Date: September 19, 2026.

## Completed

- Reviewed the current official TypeSafe HTTP contract, zero-based Score example, Noul and Choice outputs, confidence documentation, and model identifiers.
- Reviewed the NYC dataset's current split/column-label documentation and Socrata query/ordering/timestamp documentation.
- Both scripts compile with `python -m py_compile`.
- Ran `python -m unittest discover -s tests -v`: **32 tests passed**. The full output is in `test_results.txt`.
- Baseline rendering was tested with no model calls allowed.
- The JEV output path was tested using explicitly mocked responses: all six standard map/PNG pairs and seven category map/PNG pairs rendered in temporary test directories.
- Tests verify date bounds, keyset tie-breaking, pagination, budget checks, CSV hashing, missing/invalid coordinates, duplicate IDs, ignored preexisting score columns, response type/range checks, cached reruns, explicit missing-key failure, retry attempt limits, unsupported sparse cells, per-cell means, and conservation of category probability mass.

## Live validation on Windows

- Downloaded September 1, 2026 NYC 311 records: 9,903 rows, 9,698 mappable.
- Completed 634 live JEV evaluations without retries using `jev-1.13.0`.
- Generated six HTML/PNG pairs over 2,043 occupied cells; verified the composite PNG decodes.
- All 32 offline tests passed on Windows with Python 3.14. Verified `.env` loading, existing environment precedence, and baseline/dry-run isolation without additional API calls.
- Local HTTP serving returned 200 for the index and interactive map. Direct file browsing showed blocked OSM tiles; use localhost for interactive maps.

## Remaining limitations

- No annotated outcome labels were used; model accuracy, calibration, and public-safety usefulness are not established.
- Python 3.10+ is the implementation target; this live run and latest offline suite used Windows/Python 3.14, not a full platform/version matrix.
- Automated browser pan/zoom validation was not performed. Interactive maps require external assets and tiles.
- Source data can change between downloads. The input hash documents the recorded run, not a guaranteed immutable upstream snapshot.

Synthetic records and mocked responses are confined to tests. Published maps use real downloaded reports and live JEV responses.
