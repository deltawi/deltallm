# Admin UI Sidebar Layout

Keep the desktop sidebar stable, compact, and adjustable without introducing horizontal overflow.

## Checklist

- [x] Remove the current horizontal overflow from the desktop sidebar.
- [x] Truncate long nav labels and footer identity text safely.
- [x] Add `overflow-x-hidden` to the sidebar shell and nav region.
- [x] Add desktop-only sidebar resizing with bounded width.
- [x] Persist the chosen sidebar width locally.
- [x] Keep the mobile drawer behavior unchanged.
- [ ] Add dedicated frontend interaction tests if a lightweight UI test runner is introduced.
