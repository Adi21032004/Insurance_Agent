# Status

The pilot, Harness, Durable and Swarm builds are implemented. The backend stores claims, decisions, payments, handler sign-offs, runs, run claims, model calls and work times in SQLAlchemy Core tables. It uses SQLite locally and can use Postgres when `DATABASE_URL` is set. Policy, customer and handler records have their own tables; policy and customer context is joined into claim reads.

The UnderCover Agent reviews claims with a LangGraph workflow and saves each claim's progress in its own checkpoint thread. A dispatcher graph uses `Send` to fan out claim work and `max_concurrency` to enforce the handler's selected parallelism. It carries in-progress claims on from their saved threads. Transient model failures retry with backoff; other failures get one retry, with repeated failures left for the next dispatch or a handler as appropriate. Dispatch summaries come from persisted claim records.

The dashboard shows the queue, the latest dispatch and its claims, claim evidence, source reads, progress, decisions, sign-offs and payments. The Scoreboard starts evaluations on claim copies and shows their measures beside the handlers' answers. Evaluation answer keys are read only by seeding and are never exposed to the agent. Direct single-claim reviews remain available from the dashboard or with the CLI.

## Run it

Install with `uv sync` and `npm install`, then start the backend, agent service and dashboard with `npm run dev`. Start a dispatch from the queue page and choose the number of claim agents that may run in parallel. Start evaluations from the Scoreboard. Use `DATABASE_URL` for Postgres and durable graph checkpoints. Without it, the backend uses SQLite and the agent service uses an in-memory checkpoint saver. Use `UNDERCOVER_DB` to choose the local SQLite file. Run one claim locally with `uv run -m agent CLM-1001`.

`npm run seed` rebuilds the application tables in the configured database from the approved seed files and removes its current application records. The seed records keep their original USD amounts; the dashboard converts amounts to INR using `data/company.json`. Payments are simulated.

Automated checks use stub models, a deterministic agent-service stand-in and temporary SQLite databases. They do not call a live model. Supabase, live-model and Langfuse acceptance checks have not been run in this workspace.
