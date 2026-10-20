# The claims pilot

From the head of claims operations at UnderCover Travel Insurance.

## How claims work today

UnderCover sells travel insurance on three plans: Economy, Premium and First. Our three claims handlers work through a queue of claims by hand. For each claim, a handler reads the customer's story and documents and checks them against the policy and our policy wording. Then they approve the claim with a payout, decline it or refer it. A claim is referred when it needs someone's judgment or more information, as the claims manual explains.

## The pilot

We want an AI agent, the UnderCover Agent, to work the queue the way a handler does. For each claim it should:

- read the claim, the customer's policy and claim history, and the relevant sections of the policy wording and claims manual
- approve, decline or refer the claim
- work out the payout when it approves
- explain the decision in two or three sentences, along with the relevant section of the policy

Our goal is to handle claims straight through, as accurately as our handlers do.

The queue agent selects an ordered batch of waiting claims up to a handler-set cap of 1 to 25. It reviews each selected claim with a fresh claim agent and leaves claims outside the batch waiting. A failed claim gets one retry in the same run. If the second attempt fails, the claim is marked `needs handler` and the queue agent continues with the remaining claims. Before it closes the run, the agent reconciles every selected claim and retries any selected work that remains unresolved.

## What the team needs to see

- The queue, with where each claim stands: waiting, in progress, waiting for sign-off, decided or needs handler.
- The agent's ordered to-do list for the current run, with each item to do, in progress, done or left for a handler.
- Each claim in full, with the agent's decision and its reasons.
- The policy wording and manual sections the agent read for each claim.
- What the agent is doing on a claim while it works, step by step.
- The queue and claim instructions, read only.

## Rules

- A payout over $1,000 needs a handler's sign-off. The agent can approve such a claim, but the payment waits until a handler signs off. The system must block the payment until then, even if the agent tries to pay.
- Decisions follow the policy wording and claims manual. The policy wording takes precedence when the two differ.
- A saved decision is never duplicated during recovery. An approval that needs handler sign-off stays unpaid until a handler signs off; an eligible approval with an unsaved payment resumes at payment. Repeated failures leave the claim for a handler.
- A claim marked `needs handler` is not selected automatically by a later queue run.
- Our handlers' own decisions on these claims are in `data/evals/`. We use them to check the agent's work. The agent must never see them.
- Amounts are in US dollars but convert them to Indian rupee and then proceed.

## Our records

Everything is in `data/`, described in `data/README.md`: the claims queue, the policies, the customers, the plans, the handlers, the policy wording and the claims manual. The agent looks up only the sections it needs. The held-out claims in `data/evals/` are for checking the agent later and stay out of the queue.

## Not for now

- No sign-in. Everyone who uses the screens sees everything.
- No real payments.
- No messages to customers.


