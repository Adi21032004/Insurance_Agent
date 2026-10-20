# Harness plan

## Scope and assumptions

Build the harness on top of the completed pilot in six phases. The queue agent will own the order of the selected claims, while the backend persists run state and enforces claim, decision and payment safeguards. A run cap counts distinct claims selected for that run; retry attempts do not use another slot, and claims outside the cap remain waiting. A failed claim gets one retry. After its second failed attempt, it is marked `needs handler` and is not selected automatically again.

The queue run and named-claim run use the same persisted run and to-do records. To make a second failure visible in the claims queue without confusing it with sign-off, add `needs handler` as a claim status. To-do items use exactly `to do`, `in progress`, `done` and `left for handler`.

The manual is version 2026.2 and applies to wording version 2026.1, as stated in `data/claims_manual.md`. Record the manual version and the policy and manual sections read on each decision. Convert monetary amounts in retrieved manual sections to INR in the backend before returning them to the agent.

Automated checks use stub models only. The final real-model trial is a separate acceptance check, run against a newly seeded local database and capped at five claims.

## Phase 1: Persist runs, to-do items, retry state and database safeguards

Extend the SQLite workflow with run records, ordered to-do items, run summaries and per-claim failure counts. Add `needs handler` as a claim status.

**Run rules**
- **(decision)** Only one run can be active at a time. `POST /api/agent/runs` while a run is active returns 409 with the active `run_id`.
- `max_claims` is a required integer from 1 to 25 (configurable). Anything else returns 422.
- `GET /api/agent/runs/current` returns `{"run": null}` with 200 when no run is active, so dashboard polling stays quiet.
- `GET /api/agent/runs/{run_id}` returns that run's tasks and summary after it finishes, so the dashboard can show completion after the active-run endpoint becomes null.
- Selecting claims is a backend operation. It rejects a selection that exceeds the cap, repeats a claim, or includes a claim that isn't `waiting`.
- Selected claims are assigned to the run (`run_id` on the claim). Claim acquisition is one conditional update (`... WHERE status = 'waiting'`), so two workers can't take the same claim.
- **(decision)** The failure count belongs to the claim, persists across runs, and resets when the claim completes. A claim that fails once in run A and once in run B goes to `needs handler`.

**Database safeguards**
- Add unique constraints so a claim can have only one decision and one payment, plus an idempotency key on decision, sign-off and payment writes.
- The pilot-schema upgrade checks for existing duplicate decisions or payments first. It either keeps the earliest record and logs the rest, or stops with a report. It never fails halfway through.

Keep `POST /api/claims/{claim_id}/run` and make it create the same run and to-do records for one claim. Every new API input is explicit, with an `operation_id`, a one-line docstring and no optional inputs.

Success criteria:

- [x] `uv run pytest backend/tests/test_runs.py backend/tests/test_api.py -q --no-cov` passes against an isolated temporary database, covering empty-database initialization and an upgrade from the pilot schema that keeps all claims and decisions.
- [x] The upgrade test includes a pilot database with a duplicate decision. The migration handles it as described and the unique constraint is in place afterwards.
- [x] `POST /api/agent/runs` with `{"max_claims": 5}` returns `run_id`, `status` and `max_claims`. `GET /api/agent/runs/current` returns the same run with its ordered to-do items, and returns `{"run": null}` when none is active.
- [x] `GET /api/agent/runs/{run_id}` returns the completed run and its summary after `GET /api/agent/runs/current` returns `{"run": null}`.
- [x] A second `POST` while a run is active returns 409. `max_claims` of 0, -1, 26 and a non-integer each return a clear 4xx.
- [x] Selection tests reject an over-cap selection, a duplicate claim, and a non-`waiting` claim, and leave no partial state.
- [x] Two simultaneous acquisitions of the same claim give exactly one winner.
- [x] To-do API tests persist and return every allowed state: `to do`, `in progress`, `done` and `left for handler`. Invalid states return a clear 4xx.
- [x] A claim can be set to `needs handler` and appears in the claims queue API with that status.
- [x] Starting a named claim still returns `in progress`, creates a one-claim run, and exposes its to-do list through the current-run API.
- [x] Inserting a second decision or payment for the same claim fails at the database level.
- [x] Every new backend operation has an `operation_id`, a one-line docstring and only required inputs.


## Phase 2: Add section-level policy and manual lookups

Seed the claims manual as an approved business document. Add backend search and section-read operations for both the policy wording and manual, and expose tools that use those operations. Return only matched sections, with source and heading metadata; record each section read against the claim so the handler can see what informed the decision. Convert monetary values in returned sections to INR. Update `docs/SPEC.md` to make the manual part of claim handling, remove it from “Not for now”, and include the new handler-review status. Update the instructions response to provide separate queue-run and claim-review instructions.

Success criteria:

- [x] `uv run pytest backend/tests/test_knowledge.py backend/tests/test_api.py -q --no-cov` passes for manual and wording search, section reads, section metadata, INR conversion, and unknown sections.
- [x] API calls can search and read a specific section of each document; the responses contain the source name and section heading, and do not return the complete manual or complete wording.
- [x] A manual section containing a dollar limit is returned with its amount converted to INR, while its section meaning and wording remain intact.
- [x] The claim API returns the policy and manual sections read for that claim, and `docs/SPEC.md` describes the manual as part of the decision process.
- [x] The instructions API returns distinct queue and claim instructions that tell the agent to use only backend lookups and never handler evaluation decisions.
- [x] `POST /api/agent/knowledge/search` returns section IDs, headings and excerpts for the requested source; `POST /api/agent/knowledge/read` returns only the requested section with its source, heading and version, and records the read on the claim. `GET /api/instructions` returns distinct queue and claim instructions.

## Phase 3: Build the queue agent and fresh claim helpers

Make the queue agent plan an ordered batch from waiting claims, keep its to-do list current through the backend, and finish with a short handler summary. It hands each selected claim to a newly created claim agent with a clean conversation and a unique thread. That helper works through the existing backend claim, payout, decision and payment tools plus the new knowledge lookup tools. Keep claim progress and source reads attached to the claim. Change the default OpenRouter model to `deepseek/deepseek-v4.1-flash` with low reasoning effort; `MODEL_NAME` continues to override it.

Success criteria:

- [x] `uv run pytest agent/tests/test_agent.py backend/tests/test_runs.py -q --no-cov` passes using stub models, with no model-provider calls.
- [x] A stubbed queue run capped at five selects five distinct waiting claims in the agent's planned order, completes their to-do items, returns a handler summary, and leaves every unselected claim waiting.
- [x] The named-claim entry point reviews only the requested claim and persists the same run and to-do state as a queue run.
- [x] Tests show every claim helper starts with a fresh message history and unique thread, while its progress events and read sections are recorded against the correct claim.
- [x] Tests confirm the claim helper retrieves only needed manual and wording sections, records the section references and manual version with its decision, and obtains the payout from the backend.
- [x] Model configuration tests confirm the default model is `deepseek/deepseek-v4.1-flash` with low reasoning effort and that `MODEL_NAME` overrides the default.

## Phase 4: Recover failed claims and finish each capped run

Handle claim-level timeouts, provider errors and failed steps without stopping the rest of the run. Persist every failure and attempt. Decide what to do with a failure from the claim's **persisted state**, not from where the agent thinks it stopped.

**Failure handling**
- **No decision saved yet.** The first failure returns the claim to `waiting`, keeps it in the same run, and the run retries it without using another cap slot. The second failure marks the claim `needs handler` and its to-do item `left for handler`.
- **Decision already saved.** The claim never returns to `waiting` and never gets a second decision. The run resumes only the remaining idempotent steps (sign-off, payment). If those steps fail twice, the claim goes to `needs handler` with its to-do item `left for handler`, and the decision stays recorded.
- A failure on one claim never stops later selected claims.

**Crash and timeout recovery**
- **(decision)** Each in-progress claim and run holds a lease (heartbeat timestamp), and claim-level timeouts use a configured value.
- On startup, and before any new run, recovery finds expired leases. It marks an orphaned run `interrupted` and handles each stuck claim by the same persisted-state rules above, counting the interruption as a failure.

**Reconciliation**
- **(decision)** The agent must select `min(max_claims, waiting claims at selection time)`. The backend checks this when the run closes.
- Before completing, the run reconciles every selected claim. Each one is `done` or `needs handler`, none is `waiting` or `in progress`, and no eligible waiting claim within the cap was skipped. If one was missed, the run retries it and does not finish.
- Decision, sign-off and payment guards stay as they are. A decided claim cannot be decided or paid twice, and replays are safe because of the Phase 1 constraints and idempotency keys.
- Each retry attempt gets a fresh message history and a unique thread ID that includes the attempt number.

Success criteria:

- [ ] `uv run pytest agent/tests/test_agent.py backend/tests/test_runs.py backend/tests/test_agent_api.py -q --no-cov` passes with stubbed timeouts, provider errors and tool failures.
- [ ] A first failure before any decision returns the claim to `waiting`, records the failure, and the run retries the same claim. A successful retry finishes it without using another cap slot.
- [ ] A second failure before any decision marks the claim `needs handler` and its to-do item `left for handler`, and later selected claims still complete.
- [ ] A failure injected after the decision is saved never creates a second decision and never sets the claim to `waiting`. The run resumes sign-off or payment, and two failures there give `needs handler` with the decision intact.
- [ ] Replaying decision, sign-off or payment steps, including after a partial failure, creates no duplicate decision or payment.
- [ ] A simulated process crash mid-claim leaves expired leases. Recovery marks the run `interrupted` and resolves each stuck claim by its persisted state, with none left `in progress`.
- [ ] Final reconciliation retries any selected claim left `waiting` and does not finish the run while one is missed. A run where the agent selected fewer than `min(cap, waiting)` is rejected or corrected.
- [ ] A `needs handler` claim is not selected by a later run, and the cross-run failure count follows the Phase 1 decision.
- [ ] Retry attempts use fresh message history and a thread ID unique per attempt.
- [ ] No test path ends with a claim `in progress` after the run finishes.

## Phase 5: Show runs, to-dos and knowledge reads in the dashboard

Add a queue-run control with an explicit claim cap and keep the single-claim control. Show the current run's ordered to-do list, each item's state, and the run summary when finished. Add `needs handler` to the queue status display. On a claim page, show the policy and manual sections the agent read. Update the read-only instructions page to let handlers inspect both sets of agent instructions.

Success criteria:

- [ ] `npm run check:ts` passes, including dashboard tests and strict TypeScript checks.
- [ ] Dashboard tests cover starting a capped run, starting one named claim, live to-do state changes, the completed summary, `needs handler`, claim source sections, both instruction sets and API failure states.
- [ ] In the Playwright browser at `http://localhost:5173`, the queue can start a five-claim run, displays the current to-do list and summary, and shows an individual claim's recorded policy and manual sections.
- [ ] The instructions page displays the queue and claim instructions as read-only text, and the queue reflects a claim moved to `needs handler`.

## Phase 6: Integrate, update product docs and run acceptance checks

Update `docs/TECH_STACK.md` with the new default model and queue/subagent entry points, and update `docs/STATUS.md` after the implementation is complete. Run the complete automated suite and review the full workflow in the browser. Then, as a separate live-model acceptance check, start with a newly seeded local database and run the default model with a cap of five.

Success criteria:

- [ ] `uv run pytest -q` passes with at least 80% backend and agent coverage; all agent tests use stubs.
- [ ] `npm run check` passes: Python formatting, lint, types and tests; TypeScript formatting, lint, types and tests; and Playwright end-to-end tests.
- [ ] Playwright end-to-end tests cover a capped run, an unselected waiting claim, to-do progress, section reads, a claim failure that does not stop the run, a retry, a second failure routed to a handler, and missed-claim reconciliation.
- [ ] In the Playwright browser, the queue, run summary, to-do list, claim detail, section-read display and instructions page load and update without console errors.
- [ ] `docs/SPEC.md`, `docs/TECH_STACK.md` and `docs/STATUS.md` describe the implemented manual use, run and retry behavior, handler-review status, and default model. The pilot's `docs/PLAN.md` remains unchanged.
- [ ] With a fresh seeded local database and the default model enabled, an API or dashboard run capped at five processes five distinct claims, leaves the other claims waiting, and shows the run summary, to-dos and claim steps in the dashboard.
