---
name: analyst
description: Create detailed technical specifications from Product Requirements Documents (PRDs) for software engineering projects. Use when tasked with analyzing a PRD and producing comprehensive technical specs that break down implementation into actionable worktrees. Triggers on phrases like "analyze PRD", "create technical spec", "produce implementation spec", or when assigned as an analyst agent.
---

# Analyst Skill

Transform Product Requirements Documents into actionable technical specifications for implementation teams.

## Process

### 1. Read and Analyze PRD
Read the PRD file provided in the task. Identify:
- Scope boundaries (what's in/out)
- Key functional requirements
- API contracts and data models
- Integration points
- Non-functional requirements (performance, security, reliability)

### 2. Produce Technical Specification
Create a comprehensive spec document with these sections:

#### Section 1: API Contract Definitions
- Exact request/response schemas (Pydantic models)
- HTTP methods, paths, status codes
- Error response taxonomy
- Streaming formats (SSE, WebSocket)
- Authentication mechanisms

#### Section 2: Database Schema
- All tables with fields, types, constraints
- Indexes for query performance
- Relations between entities
- Migration considerations

#### Section 3: Component Architecture
- Interface definitions (abstract base classes)
- Public methods and signatures
- Dependency injection patterns
- Event/hook systems

#### Section 4: Data Flow & Lifecycle
- Step-by-step request processing flow
- State machines
- Async processing queues
- Caching strategies

#### Section 5: Configuration Schema
- YAML structure
- Environment variable patterns
- Validation rules
- Default values

#### Section 6: Integration Points
- External service interfaces
- Shared data contracts with other phases
- Event schemas for pub/sub
- API versioning strategy

#### Section 7: Worktree Breakdown
Specific, assignable tasks for each worktree agent:
- Task name
- Input dependencies
- Output deliverables
- Integration checkpoints
- Acceptance criteria

#### Section 8: Tech Stack Recommendations
- Framework choices with justification
- Library selections
- Testing strategy
- Project structure

### 3. Reference Guidelines

For detailed examples of each section, see [references/spec-examples.md](references/spec-examples.md).

For database schema patterns, see [references/db-patterns.md](references/db-patterns.md).

## Output Requirements

- **File location**: `docs/phase{N}-{name}-spec.md`
- **Format**: Markdown with code blocks for schemas/interfaces
- **Precision**: Include exact field names, types, and constraints
- **Traceability**: Reference PRD section numbers
- **Completeness**: Cover all P0 requirements from PRD

## Rules

1. Be specific - no vague descriptions
2. Include code examples (Pydantic, TypeScript interfaces, SQL)
3. Define integration contracts explicitly
4. Identify dependencies between worktrees
5. Flag risks or unknowns from PRD
6. Keep the spec under 500 lines; use references/ for detailed examples
