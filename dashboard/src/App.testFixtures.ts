import { vi } from "vitest";
import type {
  AgentRun,
  ClaimDetail,
  ClaimStatus,
  ClaimSummary,
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

export function makeRun(
  status: AgentRun["status"] = "completed",
  maxClaims = 5,
): AgentRun {
  return {
    run_id: "RUN-0001",
    run_type: "queue",
    max_claims: maxClaims,
    status,
    summary:
      status === "completed"
        ? `Reviewed ${maxClaims.toString()} claims: 3 approved, 1 declined, 1 referred, 0 need handler attention.`
        : null,
    todos: Array.from({ length: maxClaims }, (_, index) => ({
      todo_id: index + 1,
      position: index,
      claim_id: `CLM-${(1001 + index).toString()}`,
      description: `Review CLM-${(1001 + index).toString()}`,
      status:
        status === "completed" ? ("done" as const) : ("in progress" as const),
      attempt_count: 1,
    })),
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

function queueMaxClaims(init?: RequestInit): number {
  if (typeof init?.body !== "string") {
    throw new Error("Queue run request body must be JSON text.");
  }
  const body = JSON.parse(init.body) as { max_claims: number };
  return body.max_claims;
}

export function mockApi(initialDetail: ClaimDetail = makeDetail()) {
  let currentDetail = initialDetail;
  const instructions = {
    queue_instructions: "Choose the ordered batch and keep its tasks current.",
    claim_instructions: "Use the policy wording and claims manual.",
  };
  let savedRun: AgentRun | null = null;
  const getRoutes: Partial<Record<string, (init?: RequestInit) => Response>> = {
    "/api/agent/runs/current": () =>
      jsonResponse({
        run: savedRun?.status === "in progress" ? savedRun : null,
      }),
    "/api/agent/runs/RUN-0001": () => jsonResponse(savedRun ?? makeRun()),
    "/api/claims": () =>
      jsonResponse({
        claims: [makeSummary(currentDetail.status, currentDetail.decision)],
      }),
    "/api/claims/CLM-1001": () => jsonResponse(currentDetail),
    "/api/instructions": () => jsonResponse(instructions),
  };
  const postRoutes: Partial<Record<string, (init?: RequestInit) => Response>> =
    {
      "/api/agent/runs": (init) => {
        const maxClaims = queueMaxClaims(init);
        savedRun = makeRun("completed", maxClaims);
        return jsonResponse({
          run_id: savedRun.run_id,
          status: "in progress",
          max_claims: maxClaims,
        });
      },
      "/api/claims/CLM-1001/run": () => {
        savedRun = {
          ...makeRun("completed", 1),
          run_type: "claim",
          todos: [
            {
              todo_id: 1,
              position: 0,
              claim_id: "CLM-1001",
              description: "Review CLM-1001",
              status: "done",
              attempt_count: 1,
            },
          ],
          summary: "Reviewed one claim.",
        };
        currentDetail = makeDetail("decided", approvedDecision, null, null, [
          progressEvent,
        ]);
        return jsonResponse({ status: "in progress", run_id: savedRun.run_id });
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
