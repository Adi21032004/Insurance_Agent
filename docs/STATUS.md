# Status

The pilot is in progress. Phase 1 is complete.

- `backend/`: SQLite seeds the 25 queue claims from the approved business data files; read APIs return the queue and full claim context in INR, along with the read-only agent instructions and converted policy wording.
- `agent/`: shared instructions are defined; the existing health-check agent entry point is still in place.
- `dashboard/`: still shows backend health only.
- `e2e/`: still checks the health page only.

The seed records retain their original USD values in SQLite. The API uses the fixed pilot rate of ₹83.00 per USD. No evaluation or security claim data is loaded.

Install with `uv sync` and `npm install`, then run the app with `npm run dev`.
