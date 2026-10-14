# DeltaLLM Admin UI

React-based admin dashboard for managing and monitoring your DeltaLLM gateway.

## Features

- **Dashboard** — Overview stats, daily spend chart, model usage breakdown
- **Models** — Create, edit, and delete model deployments with provider configuration, pricing, and default parameters
- **API Keys** — Create, revoke, and regenerate keys with per-key budget controls
- **Organizations** — Organization management with RPM/TPM rate limits
- **Teams** — Team management with member assignment and rate limits
- **Users** — User management with block/unblock and rate limits
- **Usage** — Spend analytics with daily trends, per-model/key/team breakdowns, and request logs
- **Guardrails** — Configure content safety policies with scoped assignments (global, org, team, key)
- **Access Control** — Platform account management, org/team membership and role assignment
- **Settings** — Routing strategy, caching, health checks, fallback chain configuration

## Tech Stack

- React 19
- TypeScript
- Vite
- Tailwind CSS
- React Router v6
- Recharts (charts)
- Lucide React (icons)

## Quick Start

### Prerequisites

- Node.js 20+
- DeltaLLM backend running on port 8000 (see [root README](../README.md))

### Install dependencies

```bash
npm install
```

### Start the dev server

```bash
npm run dev
```

The dashboard opens at `http://localhost:5000`. API requests are proxied to the backend on port 8000.

### Build for production

```bash
npm run build
```

The build output goes to `dist/`. In production, the DeltaLLM backend serves these static files directly — no separate frontend server is needed.

### Lint

```bash
npm run lint
```

## Running with Docker

The UI is built and served by the backend in Docker deployments. No separate container is needed.

From the project root:

```bash
# Single instance
docker compose --profile single up -d

# High availability
docker compose --profile ha up -d
```

See the [root README](../README.md) for full Docker instructions.

## Project Structure

```
ui/
├── src/
│   ├── main.tsx              # App entry point
│   ├── App.tsx               # Router and layout
│   ├── api/                  # API client functions
│   ├── components/           # Reusable UI components
│   │   ├── Layout.tsx        # Sidebar navigation and layout shell
│   │   ├── ScopedGuardrailEditor.tsx
│   │   └── ...
│   ├── contexts/             # React contexts (auth)
│   ├── pages/                # Page components
│   │   ├── Dashboard.tsx
│   │   ├── Models.tsx
│   │   ├── ModelDetail.tsx
│   │   ├── ApiKeys.tsx
│   │   ├── Organizations.tsx
│   │   ├── OrganizationDetail.tsx
│   │   ├── Teams.tsx
│   │   ├── TeamDetail.tsx
│   │   ├── UsersPage.tsx
│   │   ├── Usage.tsx
│   │   ├── Guardrails.tsx
│   │   ├── RBACAccounts.tsx
│   │   ├── SettingsPage.tsx
│   │   ├── Login.tsx
│   │   ├── ForcePasswordChange.tsx
│   │   └── MFAEnrollment.tsx
│   └── index.css             # Tailwind imports and global styles
├── index.html
├── vite.config.ts            # Vite config with API proxy
├── tailwind.config.js
├── tsconfig.json
├── tsconfig.app.json
├── tsconfig.node.json
└── package.json
```

## Page Layout

Every signed-in page renders its header through `PageShell` in
`src/components/admin/shells/`. Do not build a page header by hand. The Playground is the
only exception: it is a full-height workspace, so it builds its own header with the same
breadcrumb, title and button styles.

- List and top-level pages use `IndexShell`. It adds the menu section as the breadcrumb.
- Pages below a list page (a detail page, a create or edit form) use `PageShell` with
  `breadcrumbs={[...navTrail('/models'), { label: name }]}`. `navTrail` comes from
  `src/lib/navigation.ts`, so breadcrumbs always match the side menu.
- Use the header slots instead of extra markup: `icon` or `avatar` (`EntityAvatar`), `badges`,
  `description`, `meta`, `actions`, `stats` (`DetailMetricCard` tiles), `toolbar`, `tabs`
  (`IconTabs` with `variant="header"`), and `notice`. Use `width="form"` for long forms.
- Route-based tabs use `tabClass` so they look like `IconTabs`.
- Header buttons use `Button` from `src/components/Button.tsx` at the default size: `primary`
  for the main action, `outline` for the others, `danger-outline` for delete or cancel, and
  `size="icon"` with an `aria-label` for icon-only buttons.

## Development Notes

- The Vite dev server proxies `/v1`, `/health`, `/auth`, `/ui/api`, and `/metrics` to the backend on port 8000
- Authentication uses session cookies (`deltallm_session`) set by the backend
- The login page supports both email/password and master key authentication
- SSO tab appears automatically when SSO is enabled in the backend config
- Pages restricted to platform admins (Guardrails, Settings, Access Control) are hidden from non-admin users
