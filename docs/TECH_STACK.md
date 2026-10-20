# Tech stack

- `backend/`: FastAPI and SQLite. Every API operation has an `operation_id`, a one-line docstring and no optional inputs. Paying a claim is a separate operation that takes the amount.
- `agent/`: LangChain's `create_agent` on OpenRouter. `run_queue(run_id, thread_id)` plans and processes a capped ordered batch; it creates a fresh `run(claim_id, thread_id)` claim agent for every selected claim and retry. It reaches claims, policy and manual sections, and both instruction sets only through the backend API. The default model is `deepseek/deepseek-v4.1-flash` with low reasoning effort. `MODEL_NAME` overrides it, such as `openrouter/free`.
- `dashboard/`: React, Vite and TypeScript.
- `e2e/`: Playwright tests.
- `data/`: the business data the backend starts from. The agent never sees `data/evals/`, which holds the answer key.

Python 3.12 with uv, all in the root `pyproject.toml`. Node 26. The OpenRouter key is `OPENROUTER_API_KEY` in `.env`. `UNDERCOVER_CLAIM_TIMEOUT_SECONDS` configures claim-call timeouts (120 seconds by default); `UNDERCOVER_RUN_LEASE_SECONDS` configures run leases (300 seconds by default).
