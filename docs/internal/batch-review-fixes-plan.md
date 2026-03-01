# Batch Review Fix Plan (Findings Remediation)

## Scope

- Fix auth ownership checks for files/batches.
- Fix batch cancel lifecycle convergence.
- Prevent completed items from being downgraded when telemetry fails.
- Align batch list visibility with batch get visibility.
- Add targeted tests to lock behavior.

## TODO

- [x] Phase 0: Lock scope and confirm impacted code paths.
- [x] Phase 1: Implement auth ownership hardening (null-safe team semantics).
- [x] Phase 2: Fix cancel lifecycle so cancel requests always converge.
- [x] Phase 3: Isolate telemetry/logging failures from completion state.
- [x] Phase 4: Align list/get visibility semantics (OR semantics).
- [x] Phase 5: Add regression tests and run focused suite.

## Notes

- Keep changes surgical: no unrelated refactors.
- Reuse existing modules/functions where possible.
- Preserve response schema/status codes except where bug fix changes edge-case behavior.
