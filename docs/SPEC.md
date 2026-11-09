# UnderCover claims platform

From the head of claims operations at UnderCover Travel Insurance.

## How claims are handled

UnderCover sells travel insurance on three plans: Economy, Premium and First. Our three claims handlers work through a queue of claims. For each claim, a handler reads the customer's story and documents and checks them against the policy, policy wording and claims manual. Then they approve the claim with a payout, decline it or refer it. A claim is referred when it needs someone's judgment or more information, as the claims manual explains.

The UnderCover Agent reviews claims in parallel. A handler starts a dispatch from the queue page and chooses how many claim agents may work at once, from 1 to 25. The dispatcher selects every waiting claim and every claim left in progress, oldest submitted first. It resumes each claim from its own saved thread and leaves claims waiting for sign-off or a handler alone. A claim that fails because of a rate limit or timeout waits and resumes from its checkpoint; another failure gets one more attempt. Work still in progress is available to the next dispatch.

Each claim review is a LangGraph workflow with a checkpoint for its thread. An approval over the sign-off limit pauses and stays unpaid. When a handler signs off in the dashboard, the backend asks the agent service to resume that same claim thread and pay the approved amount. A saved decision or payment is never recorded twice. The dispatcher has no model: it chooses claims and coordinates claim agents, and the backend builds its summary from saved records.

Evaluations use copies of labelled claims so they never change the live queue. The Scoreboard starts an evaluation or held-out set, including the five-claim tiny subset, and compares the agent's decisions with the handlers' answers. The agent sees only the claim copies, never the answer key.

## What the team needs to see

- The queue, with where each claim stands: waiting, in progress, waiting for sign-off, decided or needs handler.
- The latest dispatch, including each claim's status and decision, model cost, elapsed time and a summary of what it left behind.
- Each claim in full, with the agent's decision and reasons.
- The policy wording and manual sections the agent read for each claim.
- What the agent is doing on a claim while it works, step by step.
- The claim agent's instructions, read only.
- A Scoreboard with evaluation runs, their measures and the agent's results beside the handlers' decisions.

## Rules

- A payout over $1,000 needs a handler's sign-off. The agent can approve such a claim, but the payment waits until a handler signs off. The system must block the payment until then, even if the agent tries to pay.
- Decisions follow the policy wording and claims manual. The policy wording takes precedence when the two differ.
- A saved decision is never duplicated during recovery. An approval that needs handler sign-off stays unpaid until a handler signs off; an eligible approval with an unsaved payment resumes at payment. Repeated failures leave the claim for a handler.
- A claim marked `needs handler` is not selected automatically by a later dispatch.
- Our handlers' own decisions on these claims are in `data/evals/`. We use them to check the agent's work. The agent must never see them.
- Amounts are in US dollars but convert them to Indian rupee and then proceed.
- Record the tokens, cost and work time for each claim and run.

## Our records

Everything is in `data/`, described in `data/README.md`: the claims queue, policies, customers, plans, handlers, policy wording and claims manual. The agent looks up only the sections it needs. The held-out claims and handler decisions in `data/evals/` are for evaluation and stay out of the live queue.

## Not for now

- No sign-in. Everyone who uses the screens sees everything.
- No real payments.
- No messages to customers.
