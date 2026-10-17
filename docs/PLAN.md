# Pilot plan

## Scope and assumptions

Build the pilot in five phases, from the existing health-check skeleton. The pilot uses the 25 claims in the queue and the policy wording. It does not use the claims manual, evaluation files, sign-in, customer messaging or real payment services.

The brief requires converting USD to INR but does not specify a rate. For this pilot, use a fixed rate of **₹83.00 per USD**, stored as a named company setting. Apply it consistently to claim amounts, policy limits and deductibles, payout calculations, and the $1,000 sign-off threshold (₹83,000). Keep source USD values available for traceability; do calculations and display money in INR. Review this assumption before implementation.

All payment records are simulations stored locally in SQLite. A payout above ₹83,000 remains blocked until a handler signs off, including when the agent calls the payment operation.

## Phase 1: Seed the pilot data and expose claim reads

Create a small SQLite persistence layer initialized from the queue, customer, policy, plan, handler, company and policy wording files. Keep the database out of source control and allow tests to use a temporary database. Seed only the queue claims; do not read or import `data/evals/` or `data/security/`.

Add read APIs for the queue, one claim in full, and the read-only agent instructions. A full claim response includes the customer's history, policy, story, supporting documents, any decision and progress events. Queue rows expose the claim's status and INR amounts.

Success criteria:

- [x] `uv run pytest backend/tests/test_data.py backend/tests/test_api.py -q --no-cov` passes, including a clean temporary-database seed with 25 claims, all initially `waiting`.
- [x] `GET /api/claims` returns 25 queue entries with INR amounts and only the statuses `waiting`, `in progress`, `waiting for sign-off` or `decided`.
- [x] `GET /api/claims/CLM-1001` returns the claim story, supporting documents, policy, customer history and current status.
- [x] `GET /api/instructions` returns the same read-only instructions used by the agent.
- [x] Every new backend operation declares an `operation_id`, has a one-line docstring and has no optional inputs.

## Phase 2: Implement decisions, INR calculations and payment controls

Add domain services and persistence for progress events, decisions, handler sign-off and simulated payments. Keep policy eligibility tied to the supplied policy wording. Apply the plan limits, per-item limits, other-party reimbursements and deductible in INR. The backend owns arithmetic and the sign-off guard so model output cannot bypass them.

Keep payment as a separate operation that requires an amount. It may write a simulated payment record only when the recorded decision is approved, the amount matches the approved INR payout, and any required handler sign-off is present. A payment attempt above the threshold before sign-off returns a conflict and leaves the payment record unchanged. A handler sign-off records the handler and allows a later simulated payment.

Success criteria:

- [x] `uv run pytest backend/tests/test_decisions.py backend/tests/test_payments.py -q --no-cov` passes for approval, decline, referral, INR rounding, the ₹83,000 boundary, and the ₹83,000.01 sign-off case.
- [x] An approved payout of ₹83,000 can be recorded without sign-off; ₹83,000.01 cannot be paid before sign-off.
- [x] A blocked payment attempt creates no payment record; after a handler signs off, the matching simulated payment succeeds and is persisted.
- [x] The payment operation takes an explicit amount and returns a clear error for a mismatched amount or an unapproved claim.
- [x] Tests confirm that source USD values are converted once and that calculation steps and stored payouts are in INR.

## Phase 3: Build the UnderCover Agent and its backend tools

Replace the health-check agent with a LangChain `create_agent` harness. Keep `run(claim_id, thread_id)` as its single entry point. Its tools use the backend API to read claim context and policy wording, write progress events and decisions, request a payout calculation, and attempt payment. The agent does not read local claim files. The backend starts agent work for a claim and moves its queue status through `in progress` to `waiting for sign-off` or `decided`.

The agent must return approve, decline or refer; cite the relevant policy section; explain the decision in two or three sentences; and use the backend's INR calculation. Keep `MODEL_NAME` and the OpenRouter default behavior from the tech-stack brief. Tests use a stub chat model and never call a model provider.

Success criteria:

- [x] `uv run pytest agent/tests/test_agent.py backend/tests/test_agent_api.py -q --no-cov` passes with a stub model for approve, decline and refer outcomes.
- [x] The agent's backend tools use HTTP APIs only; tests fail if the agent attempts to open seed data directly.
- [x] A claim run records ordered progress events, a decision with a policy section, and the backend-calculated INR payout.
- [x] An over-threshold approval ends in `waiting for sign-off`; an agent payment attempt is rejected until a handler signs off.
- [x] The instruction text returned by the instructions API is exactly the system prompt used by the agent.

## Phase 4: Build the claims dashboard

Replace the health page with a handler workspace: a queue with clear status labels, a full claim view, decision and reasons, progress steps, and a read-only instructions view. Add controls to start a claim, select a handler for sign-off, and record a simulated payment after sign-off. Show all money in INR using the UnderCover brand colors, surfaces and fonts.

Success criteria:

- [x] `npm run check:ts` passes, including dashboard unit tests and strict TypeScript checks.
- [x] Dashboard tests cover queue loading, each status, a claim detail with supporting documents and history, progress updates, decision display, read-only instructions, sign-off and payment controls, and API failure states.
- [x] In the Playwright MCP browser at `http://localhost:5173`, the queue is readable, selecting a claim shows its full details and decision area, progress events are visible, and the instructions view cannot be edited.
- [x] The page displays INR amounts and uses the specified UnderCover colors without one-edge panel borders or heavy gradients.

## Phase 5: Integrate and check the pilot

Exercise the complete workflow with a stub model in automated tests: waiting claim, agent run, decision and progress, sign-off where required, and simulated payment. Confirm the API and dashboard agree on each persisted status. Keep the existing health check and verify that no model call or real payment is needed for checks.

Success criteria:

- [ ] `uv run pytest -q` passes with at least 80% backend and agent coverage.
- [ ] `npm run check` passes: Python formatting, lint, types and tests; TypeScript formatting, lint, types and tests; and Playwright end-to-end tests.
- [ ] Playwright end-to-end coverage verifies a claim can be opened and run with a stub model, that progress and the decision appear, and that an over-threshold payment is blocked until sign-off.
- [ ] In the Playwright MCP browser, the queue, claim detail and instructions view all load without console errors, and the displayed status updates after a claim is decided.
- [ ] `GET /api/health` still returns `{"status":"ok"}` and no real payment service is called.
