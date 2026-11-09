import { vi } from "vitest";
import type {
  ClaimDetail,
  ClaimStatus,
  ClaimSummary,
  EvaluationRun,
  DispatchRun,
  Decision,
  ProgressEvent,
} from "./api";
export const approvedDecision: Decision = {
  outcome: "approve",
  reason:
    "The carrier confirms a covered delay. The receipts support the expenses.",
  policy_section: "Section 3: Trip Delay",
  calculation: [{ step: "Hotel and meals", amount_inr: 34860 }],
  payout_inr: 34860,
  needs_sign_off: false,
  signed_off_by: null,
  signed_off_at: null,
};

export const progressEvent: ProgressEvent = {
  step: "Review claim",
  detail: "Checking the story and receipts.",
  created_at: "2026-10-17 10:00:00",
};

export function makeSummary(
  status: ClaimStatus = "waiting",
  decision: Decision | null = null,
): ClaimSummary {
  return {
    claim_id: "CLM-1001",
    claim_type: "trip_delay",
    summary: "Flight home canceled by the airline",
    amount_claimed_inr: 34860,
    status,
    decision,
  };
}

export function makeDetail(
  status: ClaimStatus = "waiting",
  decision: Decision | null = null,
  signOffHandlerId: string | null = null,
  payment: ClaimDetail["payment"] = null,
  progress: ProgressEvent[] = [],
): ClaimDetail {
  let signedOffBy: string | null = null;
  if (signOffHandlerId === "HND-01") {
    signedOffBy = "Dana Whitfield";
  } else if (signOffHandlerId !== null) {
    signedOffBy = "Marcus Oyelaran";
  }

  return {
    claim: {
      claim_id: "CLM-1001",
      policy_id: "POL-2001",
      customer_id: "CUS-3001",
      claim_type: "trip_delay",
      submitted_on: "2026-06-24",
      incident_date: "2026-06-18",
      incident_location: "Rome, Italy",
      summary: "Flight home canceled by the airline",
      story: "My flight home was canceled because of a crew problem.",
      items: [
        {
          description: "Airport hotel",
          category: "accommodation",
          amount_inr: 20335,
        },
        {
          description: "Dinner and breakfast",
          category: "meals",
          amount_inr: 7885,
        },
      ],
      amount_claimed_inr: 34860,
      documents: [
        {
          name: "Carrier cancellation notice",
          type: "carrier_report",
          content: "The flight was canceled because of crew availability.",
        },
      ],
    },
    policy: {
      policy_id: "POL-2001",
      plan: "premium",
      add_ons: [],
      purchased_on: "2026-04-22",
      trip: {
        destination: "Rome, Italy",
        departs_on: "2026-06-10",
        returns_on: "2026-06-18",
        trip_cost_inr: 257300,
      },
      travelers: ["Maya Patel"],
    },
    customer: {
      customer_id: "CUS-3001",
      name: "Maya Patel",
      email: "maya.patel@example.com",
      phone: "+1 201 555 0143",
      address: {
        street: "48 Wayne Street, Apt 5C",
        city: "Jersey City",
        state: "NJ",
        zip: "07302",
      },
      customer_since: "2023-04-11",
      previous_claims: [
        {
          claim_id: "CLM-0412",
          filed_on: "2024-08-19",
          incident_date: "2024-08-09",
          claim_type: "baggage_delay",
          summary: "Checked bag arrived late in Lisbon",
          outcome: "approved",
          amount_paid_inr: 11620,
        },
      ],
    },
    plan: {
      plan: "premium",
      name: "UnderCover Premium",
      trip_cancellation_max_inr: 415000,
      trip_delay_waiting_hours: 6,
      trip_delay_max_inr: 66400,
      baggage_delay_waiting_hours: 12,
      baggage_delay_max_inr: 24900,
      baggage_max_inr: 124500,
      per_item_limit_inr: 24900,
      passport_max_inr: 20750,
      emergency_medical_max_inr: 8300000,
      deductible_inr: 4150,
    },
    add_ons: [],
    company: { payout_approval_limit_inr: 83000, usd_to_inr_rate: 83 },
    handlers: [
      {
        handler_id: "HND-01",
        name: "Dana Whitfield",
        role: "Senior Claims Handler",
      },
      { handler_id: "HND-02", name: "Marcus Oyelaran", role: "Claims Handler" },
    ],
    policy_wording: "Section 3: Trip Delay\nAll amounts are in rupees.",
    status,
    decision:
      decision === null
        ? null
        : {
            ...decision,
            signed_off_by: signedOffBy,
            signed_off_at:
              signOffHandlerId === null ? null : "2026-10-17 14:05:00",
          },
    sign_off_handler_id: signOffHandlerId,
    sign_off_at: signOffHandlerId === null ? null : "2026-10-17 14:05:00",
    payment,
    progress,
    failure_count: 0,
    source_reads: [
      {
        source: "policy_wording",
        section_id: "policy_wording:03",
        heading: "Section 3: Trip Delay",
        version: "2026.1",
        content: "The wording covers eligible expenses after a covered delay.",
        created_at: "2026-10-17 10:00:00",
      },
      {
        source: "claims_manual",
        section_id: "claims_manual:07",
        heading: "Section 7: Trip Delay Claims",
        version: "2026.2",
        content: "Review receipts for reasonable expenses.",
        created_at: "2026-10-17 10:01:00",
      },
    ],
  };
}

export function makeDispatch(
  status: DispatchRun["status"] = "finished",
  agents = 3,
): DispatchRun {
  return {
    run_id: "dispatch-1",
    run_order: 1,
    status,
    agents,
    model: "openai/gpt-6-luna",
    reasoning_effort: "low",
    created_at: "2026-10-17T14:00:00",
    finished_at: status === "running" ? null : "2026-10-17T14:01:00",
    summary:
      status === "finished"
        ? {
            approved: 1,
            declined: 0,
            referred: 0,
            waiting_for_sign_off: [],
            left_for_handler: [],
            unfinished: [],
          }
        : null,
    model_cost: 0.0125,
    seconds: 40,
    run_seconds: status === "finished" ? 60 : null,
    claims: [
      {
        claim_id: "CLM-1001",
        customer_name: "Maya Patel",
        summary: "Flight home canceled by the airline",
        status: status === "running" ? "in progress" : "decided",
        decision: approvedDecision,
        payout_inr: approvedDecision.payout_inr,
        started_at: "2026-10-17T14:00:02",
        seconds: 40,
        model_cost: 0.0125,
      },
    ],
  };
}

export function makeEvaluation(): EvaluationRun {
  const dispatch = makeDispatch("finished", 3);
  return {
    ...dispatch,
    status: "completed",
    evaluation_set: "held_out",
    tiny: true,
    measures: {
      decision_accuracy: 0.8,
      accuracy: 0.4,
      cost_of_mistakes: 620,
      straight_through: 0.6,
      model_cost_per_claim: 0.0125,
      seconds_per_claim: 40,
      run_seconds: 120,
    },
    claims: [
      {
        ...dispatch.claims[0],
        claim_id: "CLM-1104-E1",
        copy_of: "CLM-1104",
        handler_decision: "approve",
        handler_payout: 250,
        agent_decision: "approve",
        agent_payout: 250,
        right: true,
        decision_right: true,
        mistake_cost: 0,
        reason: "The evidence supports the claim.",
      },
    ],
  };
}

export function requestPath(input: RequestInfo | URL): string {
  if (typeof input === "string") return input;
  if (input instanceof URL) return input.toString();
  return input.url;
}

export function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status });
}

export function mockApi(initialDetail: ClaimDetail = makeDetail()) {
  let currentDetail = initialDetail;
  const instructions = {
    claim: "Use the policy wording and claims manual.",
  };
  let savedDispatch: DispatchRun | null = null;
  const getRoutes: Partial<Record<string, (init?: RequestInit) => Response>> = {
    "/api/dispatches/latest": () => jsonResponse(savedDispatch),
    "/api/evaluations": () => jsonResponse([]),
    "/api/claims": () =>
      jsonResponse({
        claims: [makeSummary(currentDetail.status, currentDetail.decision)],
      }),
    "/api/claims/CLM-1001": () => jsonResponse(currentDetail),
    "/api/instructions": () => jsonResponse(instructions),
  };
  const postRoutes: Partial<Record<string, (init?: RequestInit) => Response>> =
    {
      "/api/dispatches": (init) => {
        if (typeof init?.body !== "string") {
          throw new Error("Dispatch request body must be JSON text.");
        }
        const body = JSON.parse(init.body) as { agents: number };
        savedDispatch = makeDispatch("running", body.agents);
        return jsonResponse({
          status: "accepted",
          agents: body.agents,
        });
      },
      "/api/claims/CLM-1001/run": () => {
        currentDetail = makeDetail("decided", approvedDecision, null, null, [
          progressEvent,
        ]);
        return jsonResponse({ status: "in progress", run_id: "claim-run-1" });
      },
      "/api/claims/CLM-1001/sign-off": () => {
        currentDetail = makeDetail("in progress", approvedDecision, "HND-01");
        return jsonResponse({ status: "in progress", handler_id: "HND-01" });
      },
      "/api/claims/CLM-1001/payment": () => {
        currentDetail = makeDetail("decided", approvedDecision, "HND-01", {
          amount_inr: approvedDecision.payout_inr,
          simulated: true,
          signed_off_by: "Dana Whitfield",
          signed_off_at: "2026-10-17 14:05:00",
        });
        return jsonResponse(currentDetail);
      },
    };
  const notFound = () =>
    new Response(JSON.stringify({ detail: "Not found" }), { status: 404 });
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const path = requestPath(input);
    const routes = init?.method === "POST" ? postRoutes : getRoutes;
    const handler = routes[path];
    return Promise.resolve(handler === undefined ? notFound() : handler(init));
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}
