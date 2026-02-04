# DeltaLLM Admin Dashboard

A modern React-based admin dashboard for managing DeltaLLM organizations, teams, budgets, and audit logs.

## Features

- **Organization Management**: Create and manage organizations with budget controls
- **Team Management**: Organize users into teams within organizations
- **Budget Tracking**: Real-time spend tracking and budget enforcement at org/team level
- **Audit Logs**: Complete audit trail of all actions for compliance
- **Usage Analytics**: Visualize LLM usage and costs over time
- **Role-Based Access**: Different permissions for owners, admins, and members

## Tech Stack

- **React 18** - UI framework
- **TypeScript** - Type safety
- **TanStack Query** - Data fetching and caching
- **React Router** - Client-side routing
- **Tailwind CSS** - Styling
- **Lucide React** - Icons
- **Recharts** - Data visualization

## Getting Started

### Prerequisites

- Node.js 18+ 
- DeltaLLM API server running

### Installation

```bash
cd admin-dashboard
npm install
```

### Development

```bash
npm run dev
```

This will start the development server at `http://localhost:3000` with proxy to the API at `http://localhost:8000`.

### Build

```bash
npm run build
```

Output will be in the `dist` folder.

## Project Structure

```
src/
├── components/       # Reusable UI components
│   ├── Layout.tsx   # Main layout with sidebar
│   ├── DataTable.tsx # Generic data table
│   ├── BudgetProgress.tsx # Budget visualization
│   └── Modal.tsx    # Modal dialog
├── pages/           # Page components
│   ├── Dashboard.tsx
│   ├── Organizations.tsx
│   ├── OrganizationDetail.tsx
│   ├── Teams.tsx
│   ├── Budget.tsx
│   ├── AuditLogs.tsx
│   ├── Settings.tsx
│   └── Login.tsx
├── hooks/           # Custom React Query hooks
│   ├── useOrganizations.ts
│   ├── useTeams.ts
│   └── useBudget.ts
├── services/        # API client
│   └── api.ts
├── contexts/        # React contexts
│   └── AuthContext.tsx
├── types/           # TypeScript types
│   └── index.ts
└── App.tsx         # Main app component
```

## API Integration

The dashboard expects a DeltaLLM API server running at `http://localhost:8000` (configurable via `VITE_API_URL` env var).

Key endpoints used:
- `POST /auth/login` - Authentication
- `GET /auth/me` - Current user
- `GET/POST /org/*` - Organization management
- `GET/POST /team/*` - Team management
- `GET/POST /budget/*` - Budget and spend tracking
- `GET /audit/logs` - Audit logs

## Environment Variables

Create a `.env` file:

```
VITE_API_URL=http://localhost:8000
```

## License

MIT
