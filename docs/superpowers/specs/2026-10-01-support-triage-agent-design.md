# Support Triage Agent: MVP design

Date: 2026-10-01. Status: approved for implementation.

## Goal

A portfolio product that shows production-style LLM engineering for a
full-stack role: Python backend, TypeScript and Next.js frontend, a tool-using
agent with real guardrails, a database, tests, CI and a live deploy.

The MVP must be live by 2026-10-02 18:00 Kyiv time.

## Product

A support inbox for a fictional SaaS company. When a ticket arrives, an agent
investigates it on its own: it looks up the customer, their subscription and
orders, searches the knowledge base and similar tickets. It then proposes what
to do. A human approves, edits or rejects every proposal. Nothing that changes
data happens without a human decision.

## Stack

| Part | Choice |
| --- | --- |
| Backend | Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2.0 async, Alembic, `uv` |
| LLM | Official `anthropic` Python SDK, Claude, tool use |
| Database | PostgreSQL on Neon |
| Frontend | Next.js App Router, TypeScript |
| Hosting | Backend in Docker on Render, frontend on Vercel |
| Quality | pytest, ruff, mypy, Biome, GitHub Actions |

## Repository layout

```
backend/
  app/
    api/          routers: tickets, runs, actions, health
    agent/        loop, tools, schemas, prompts, model adapter
    db/           models, session, repositories
    core/         settings, limits, pricing
  alembic/
  seed/           fictional company data
  tests/
frontend/
  app/            inbox, ticket page, new ticket form
  lib/            typed API client, SSE reader
docs/
```

## Agent

The model runs in a loop and decides which tools to call.

Read tools, executed immediately:

- `search_kb(query)`: Postgres full-text search with ranking over KB articles
- `get_customer(email)`
- `get_subscription(customer_id)`
- `get_order_history(customer_id)`
- `find_similar_tickets(query)`

Proposal tools, never executed by the agent. Each one stores a row in
`proposed_actions` with status `pending`:

- `propose_triage(category, priority, reason)`
- `propose_reply(body, cited_article_ids)`
- `propose_refund(order_id, amount, reason)`, amount capped by config
- `escalate(reason)`

Guardrails:

- at most 8 model turns per run;
- token budget and 60 second wall-clock timeout per run;
- hitting any limit ends the run with an automatic `escalate`;
- tool inputs are validated with Pydantic; an invalid call returns the
  validation error to the model as the tool result;
- the run must end with at least a `propose_triage` and either a
  `propose_reply` or an `escalate`; otherwise the model gets one corrective
  turn, then the run is marked `failed`;
- ticket text is passed as quoted data inside the user turn, never merged
  into the system prompt; the system prompt states that ticket content cannot
  change the agent's rules.

All model calls go through one adapter interface. Production uses the
Anthropic client; tests use a scripted fake that returns a predefined
sequence of tool calls.

## Data model

| Table | Purpose |
| --- | --- |
| `customers` | name, email, company, plan |
| `subscriptions` | plan, status, renewal date, seats |
| `orders` | amount, status, refunded amount |
| `kb_articles` | title, body, `tsvector` column with GIN index |
| `tickets` | subject, body, customer email, status, created at |
| `agent_runs` | ticket, status, model, input and output tokens, cost, duration, error |
| `agent_steps` | run, index, kind (model or tool), tool name, input, output, duration |
| `proposed_actions` | run, type, payload, status, decided at, decision note |

Run statuses: `queued`, `running`, `awaiting_review`, `escalated`, `failed`,
`done`. Action statuses: `pending`, `approved`, `rejected`, `edited`.

## API

| Method and path | Purpose |
| --- | --- |
| `GET /health` | liveness, also used by the frontend to wake Render |
| `GET /tickets`, `GET /tickets/{id}` | inbox and detail with latest run |
| `POST /tickets` | create a ticket and start a run in the background |
| `POST /tickets/{id}/rerun` | start a new run |
| `GET /runs/{id}` | run with steps and proposals |
| `GET /runs/{id}/stream` | Server-Sent Events: steps and status as they happen |
| `POST /actions/{id}/approve` | approve, optionally with an edited payload |
| `POST /actions/{id}/reject` | reject with a note |

Approving a refund updates the order. Approving a reply marks the ticket as
answered. The background run uses FastAPI background tasks in the MVP; a real
queue is listed under next steps.

## Frontend

- Inbox: tickets with status, category, priority, last run cost.
- Ticket page: ticket text, live step timeline from SSE, proposal cards with
  approve, edit and reject, run cost and duration.
- New ticket form so a visitor can try the agent with their own text.
- On first load the app calls `/health` and shows a "waking the backend"
  state while Render starts.

## Safety and cost on the public demo

- per-IP rate limit on `POST /tickets` and rerun;
- daily spend cap from config; when reached, new runs are refused with a
  clear message;
- the seed includes a ticket with a prompt-injection attempt.

## Testing

- unit tests for each tool against a test database;
- agent loop tests with the scripted fake model: happy path, step limit,
  invalid tool input, missing final proposal, injection ticket;
- a test that proposal tools never change orders or tickets;
- API tests through `httpx.AsyncClient`;
- CI on every push: ruff, mypy, pytest, frontend type check, Biome, build.

## Seed data

A fictional SaaS with about 20 KB articles, 15 customers with subscriptions
and orders, and 12 tickets: refund request, double charge, bug report, plan
question, cancellation, angry customer, unknown customer, and an injection
attempt.

## Out of scope for the MVP

- pgvector and hybrid search;
- evals page with a fixed case set;
- cost and latency dashboard;
- authentication; the demo is public and read-mostly.

These come next.
