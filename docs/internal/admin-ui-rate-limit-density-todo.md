# Admin UI Rate-Limit Density

Keep the Organizations and Teams index tables compact when multiple rate limits are configured.

## Checklist

- [x] Add one shared rate-limit summary component for list cells.
- [x] Show only the first two limits inline.
- [x] Show the remaining limits behind a `+N more` hover affordance.
- [x] Reuse the same rendering pattern on both Organizations and Teams lists.
- [ ] Add dedicated frontend rendering tests if a lightweight UI test runner is introduced.
