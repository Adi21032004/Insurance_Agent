# Status

The pilot, Harness and Durable builds are implemented. The backend stores queue, decision, payment, sign-off, run, to-do and step records in SQLAlchemy Core tables. It uses SQLite locally and can use Postgres when `DATABASE_URL` is set. Policy, customer and handler records have their own tables; policy and customer context is joined into claim reads.

The claim agent runs as a LangGraph workflow. It records decisions and pays eligible approvals automatically. An over-limit approval pauses for a handler sign-off; the dashboard sends that sign-off through the backend to the agent service, which resumes the saved claim thread and pays once. Stopped work can resume from its checkpoint, and repeated decision, sign-off and payment requests are idempotent. Langfuse tracing is enabled when its keys are configured.

The queue agent plans and reserves a capped batch, processes claims, retries failures and reconciles unfinished to-dos. The dashboard shows the queue, run plan, claim evidence, source reads, progress, decisions, sign-offs and payments. End-to-end tests use a deterministic stand-in and a temporary SQLite database.

## Run it

Install with `uv sync` and `npm install`, then start the backend, agent service and dashboard with `npm run dev`. Use `DATABASE_URL` for Postgres and durable graph checkpoints. Without it, the backend uses SQLite and the agent service uses an in-memory checkpoint saver. Use `UNDERCOVER_DB` to choose the local SQLite file. Run a capped queue with `uv run -m agent --cap 5`, or one claim with `uv run -m agent CLM-1001`.

`npm run seed` rebuilds the application tables in the configured database from the approved seed files and removes its current application records. The seed records keep their original USD amounts; the dashboard converts amounts to INR using `data/company.json`. Payments are simulated.

Automated checks run with `npm run check`. The Supabase, live-model and Langfuse acceptance steps have not been run in this workspace.
