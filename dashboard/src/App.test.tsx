import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { App } from "./App";
import type {
  ClaimDetail,
  ClaimStatus,
  ClaimSummary,
  Decision,
  ProgressEvent,
} from "./api";

const approvedDecision: Decision = {
  outcome: "approve",
  reason:
    "The carrier confirms a covered delay. The receipts support the expenses.",
  policy_section: "Section 3: Trip Delay",
  calculation: [{ step: "Hotel and meals", amount_inr: 34860 }],
  payout_inr: 34860,
  needs_sign_off: false,
};

const progressEvent: ProgressEvent = {
  step: "Review claim",
  detail: "Checking the story and receipts.",
  created_at: "2026-10-17 10:00:00",
};

function makeSummary(
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

function makeDetail(
  status: ClaimStatus = "waiting",
  decision: Decision | null = null,
  signOffHandlerId: string | null = null,
  payment: ClaimDetail["payment"] = null,
  progress: ProgressEvent[] = [],
): ClaimDetail {
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
    decision,
    sign_off_handler_id: signOffHandlerId,
    payment,
    progress,
  };
}

function requestPath(input: RequestInfo | URL): string {
  if (typeof input === "string") return input;
  if (input instanceof URL) return input.toString();
  return input.url;
}

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body));
}

function mockApi(initialDetail: ClaimDetail = makeDetail()) {
  let currentDetail = initialDetail;
  const instructions =
    "Use the policy wording. Keep the decision explanation brief.";
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const path = requestPath(input);
    if (path === "/api/claims" && init?.method !== "POST") {
      return Promise.resolve(
        jsonResponse({
          claims: [makeSummary(currentDetail.status, currentDetail.decision)],
        }),
      );
    }
    if (path === "/api/claims/CLM-1001" && init?.method !== "POST") {
      return Promise.resolve(jsonResponse(currentDetail));
    }
    if (path === "/api/instructions") {
      return Promise.resolve(jsonResponse({ instructions }));
    }
    if (path.endsWith("/run")) {
      currentDetail = makeDetail("decided", approvedDecision, null, null, [
        progressEvent,
      ]);
      return Promise.resolve(jsonResponse({ status: "in progress" }));
    }
    if (path.endsWith("/sign-off")) {
      currentDetail = makeDetail("decided", approvedDecision, "HND-01");
      return Promise.resolve(
        jsonResponse({ status: "decided", handler_id: "HND-01" }),
      );
    }
    if (path.endsWith("/pay")) {
      currentDetail = makeDetail("decided", approvedDecision, null, {
        amount_inr: approvedDecision.payout_inr,
        simulated: true,
      });
      return Promise.resolve(
        jsonResponse({ status: "paid", simulated: true, amount_inr: 34860 }),
      );
    }
    return Promise.resolve(
      new Response(JSON.stringify({ detail: "Not found" }), { status: 404 }),
    );
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("shows the queue and full claim details with INR amounts", async () => {
  mockApi();
  render(<App />);

  expect(
    await screen.findByRole("heading", {
      name: "Flight home canceled by the airline",
    }),
  ).toBeDefined();
  expect(
    await screen.findByRole("heading", { name: "Maya Patel" }),
  ).toBeDefined();
  expect(
    screen.getByText("My flight home was canceled because of a crew problem."),
  ).toBeDefined();
  expect(screen.getAllByText("₹34,860.00").length).toBeGreaterThan(0);
  expect(screen.getByText("Supporting documents")).toBeDefined();
  expect(screen.getByText("Claim history")).toBeDefined();
});

it("shows all four queue states", async () => {
  const fetchMock = vi.fn((input: RequestInfo | URL): Promise<Response> => {
    if (requestPath(input) === "/api/claims") {
      return Promise.resolve(
        jsonResponse({
          claims: [
            makeSummary("waiting"),
            { ...makeSummary("in progress"), claim_id: "CLM-1002" },
            { ...makeSummary("waiting for sign-off"), claim_id: "CLM-1003" },
            { ...makeSummary("decided"), claim_id: "CLM-1004" },
          ],
        }),
      );
    }
    return Promise.resolve(jsonResponse(makeDetail()));
  });
  vi.stubGlobal("fetch", fetchMock);
  render(<App />);

  expect(await screen.findByText("waiting for sign-off")).toBeDefined();
  expect(screen.getByText("in progress")).toBeDefined();
  expect(screen.getByText("decided")).toBeDefined();
});

it("starts a claim and shows its progress and decision", async () => {
  const fetchMock = mockApi();
  render(<App />);

  fireEvent.click(
    await screen.findByRole("button", { name: "Start agent review" }),
  );

  expect(await screen.findByText("Approved")).toBeDefined();
  expect(
    await screen.findByText("Checking the story and receipts."),
  ).toBeDefined();
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/claims/CLM-1001/run",
    expect.objectContaining({ method: "POST" }),
  );
});

it("signs off a claim and refreshes its queue status", async () => {
  const waitingForSignoff = makeDetail("waiting for sign-off", {
    ...approvedDecision,
    payout_inr: 100000,
    needs_sign_off: true,
  });
  const fetchMock = mockApi(waitingForSignoff);
  render(<App />);

  fireEvent.click(
    await screen.findByRole("button", { name: "Sign off payout" }),
  );

  expect(await screen.findByText("Signed off by HND-01")).toBeDefined();
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/claims/CLM-1001/sign-off",
    expect.objectContaining({ method: "POST" }),
  );
});

it("records a simulated payment for an approved claim", async () => {
  const approved = makeDetail("decided", approvedDecision);
  const fetchMock = mockApi(approved);
  render(<App />);

  fireEvent.click(
    await screen.findByRole("button", { name: "Record simulated payment" }),
  );

  expect(
    await screen.findByText("Simulated payment recorded: ₹34,860.00"),
  ).toBeDefined();
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/claims/CLM-1001/pay",
    expect.objectContaining({ method: "POST" }),
  );
});

it("shows the read-only agent instructions", async () => {
  mockApi();
  render(<App />);

  fireEvent.click(screen.getByRole("button", { name: "Agent instructions" }));

  expect(
    await screen.findByText(
      "Use the policy wording. Keep the decision explanation brief.",
    ),
  ).toBeDefined();
  expect(screen.queryByRole("textbox")).toBeNull();
});

it("reports queue API errors", async () => {
  vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));
  render(<App />);

  const alert = await screen.findByRole("alert");
  expect(alert.textContent).toContain("offline");
});

it("does not offer sign-off or payment for a declined claim", async () => {
  const declined: Decision = {
    ...approvedDecision,
    outcome: "decline",
    payout_inr: 0,
  };
  mockApi(makeDetail("decided", declined));
  render(<App />);

  await screen.findByText("Declined");
  expect(screen.queryByRole("button", { name: "Sign off payout" })).toBeNull();
  expect(
    screen.queryByRole("button", { name: "Record simulated payment" }),
  ).toBeNull();
});
