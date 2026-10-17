# Status

The pilot is in progress. Phases 1, 2, 3 and 4 are complete.

- `backend/`: SQLite seeds the 25 queue claims from approved business data files; read APIs return the queue and full claim context in INR. The API records progress and policy decisions, calculates payouts from selected source items, enforces handler sign-off, and records simulated payments.
- `agent/`: `run(claim_id, thread_id)` uses LangChain `create_agent` with the configured OpenRouter model. It reads claim data, policy wording and its instructions through backend APIs, records progress and decisions, requests backend payout calculations, and uses the guarded simulated payment operation.
- `dashboard/`: the handler workspace shows the 25-claim queue, claim evidence and policy context, INR amounts, agent decision and progress, read-only instructions, handler sign-off and simulated payment controls.
- `e2e/`: the pilot workflow test is in progress, using a deterministic stub model and an isolated temporary database.

The seed records retain their original USD values in SQLite. The API and payout calculations use the fixed pilot rate of ₹83.00 per USD; payouts over ₹83,000 require handler sign-off. Payments are local simulations. No evaluation or security claim data is loaded.

Install with `uv sync` and `npm install`, then run the app with `npm run dev`.
