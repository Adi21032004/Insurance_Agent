# Status

The pilot is in progress. Phases 1 and 2 are complete.

- `backend/`: SQLite seeds the 25 queue claims from approved business data files; read APIs return the queue and full claim context in INR. The API records progress and policy decisions, calculates payouts from selected source items, enforces handler sign-off, and records simulated payments.
- `agent/`: shared instructions are defined; the existing health-check agent entry point is still in place.
- `dashboard/`: still shows backend health only.
- `e2e/`: still checks the health page only.

The seed records retain their original USD values in SQLite. The API and payout calculations use the fixed pilot rate of ₹83.00 per USD; payouts over ₹83,000 require handler sign-off. Payments are local simulations. No evaluation or security claim data is loaded.

Install with `uv sync` and `npm install`, then run the app with `npm run dev`.
