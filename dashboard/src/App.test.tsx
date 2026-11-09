import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { App } from "./App";
import {
  approvedDecision,
  jsonResponse,
  makeDetail,
  makeEvaluation,
  makeSummary,
  mockApi,
  requestPath,
} from "./App.testFixtures";
import type { ClaimDetail, Decision } from "./api";
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
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
  expect(screen.getAllByText(/34,860\.00/).length).toBeGreaterThan(0);
  expect(screen.getByText("Supporting documents")).toBeDefined();
  expect(screen.getByText("Claim history")).toBeDefined();
});

it("shows queue states including handler review", async () => {
  const fetchMock = vi.fn((input: RequestInfo | URL): Promise<Response> => {
    const path = requestPath(input);
    if (path === "/api/dispatches/latest") {
      return Promise.resolve(jsonResponse(null));
    }
    if (path === "/api/claims") {
      return Promise.resolve(
        jsonResponse({
          claims: [
            makeSummary("waiting"),
            { ...makeSummary("in progress"), claim_id: "CLM-1002" },
            { ...makeSummary("waiting for sign-off"), claim_id: "CLM-1003" },
            { ...makeSummary("decided"), claim_id: "CLM-1004" },
            { ...makeSummary("needs handler"), claim_id: "CLM-1005" },
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
  expect(screen.getByText("needs handler")).toBeDefined();
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

  expect(
    await screen.findByText(
      "Signed off by Dana Whitfield at 2:05 PM. Payment is processing.",
    ),
  ).toBeDefined();
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/claims/CLM-1001/sign-off",
    expect.objectContaining({ method: "POST" }),
  );
});

it("shows when an approved payout is waiting for the agent to pay it", async () => {
  mockApi(makeDetail("in progress", approvedDecision));
  render(<App />);

  expect(
    await screen.findByText("Waiting for the agent to pay it."),
  ).toBeDefined();
  expect(
    screen.queryByRole("button", { name: "Record simulated payment" }),
  ).toBeNull();
});

it("shows the signed-off handler and time with the completed payment", async () => {
  const paid: ClaimDetail["payment"] = {
    amount_inr: 100000,
    simulated: true,
    signed_off_by: "Dana Whitfield",
    signed_off_at: "2026-10-17 14:05:00",
  };
  mockApi(makeDetail("decided", approvedDecision, "HND-01", paid));
  render(<App />);

  expect(
    await screen.findByText(
      "Paid ₹1,00,000.00, signed off by Dana Whitfield at 2:05 PM",
    ),
  ).toBeDefined();
});

it("shows the read-only agent instructions", async () => {
  mockApi();
  render(<App />);

  fireEvent.click(screen.getByRole("button", { name: "Agent instructions" }));

  expect(
    await screen.findByText("Use the policy wording and claims manual."),
  ).toBeDefined();
  expect(
    screen.queryByText("Choose the ordered batch and keep its tasks current."),
  ).toBeNull();
  expect(screen.queryByRole("textbox")).toBeNull();
});

it("starts a dispatch with three claim agents", async () => {
  const fetchMock = mockApi();
  render(<App />);

  fireEvent.click(
    await screen.findByRole("button", { name: "Start a dispatch" }),
  );

  expect(await screen.findByText("Dispatch #1")).toBeDefined();
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/dispatches",
    expect.objectContaining({
      method: "POST",
      body: JSON.stringify({ agents: 3 }),
    }),
  );
});

it("shows the policy and manual sections read for the selected claim", async () => {
  mockApi();
  render(<App />);

  expect(
    await screen.findByRole("heading", {
      name: "Policy and manual sections read",
    }),
  ).toBeDefined();
  expect(screen.getByText("Section 7: Trip Delay Claims")).toBeDefined();
  expect(screen.getByText(/claims manual/)).toBeDefined();
  const sourceSummary = screen
    .getByText("Section 3: Trip Delay")
    .closest("summary");
  if (sourceSummary === null)
    throw new Error("Policy source summary is missing");
  fireEvent.click(sourceSummary);
  expect(
    await screen.findByText(
      "The wording covers eligible expenses after a covered delay.",
    ),
  ).toBeDefined();
});

it("shows the scoreboard and starts an evaluation", async () => {
  const fetchMock = vi.fn((input: RequestInfo | URL): Promise<Response> => {
    const path = requestPath(input);
    if (path === "/api/dispatches/latest") {
      return Promise.resolve(jsonResponse(null));
    }
    if (path === "/api/evaluations") {
      return Promise.resolve(jsonResponse([]));
    }
    if (path === "/api/claims") {
      return Promise.resolve(jsonResponse({ claims: [makeSummary()] }));
    }
    return Promise.resolve(jsonResponse(makeDetail()));
  });
  vi.stubGlobal("fetch", fetchMock);
  render(<App />);

  fireEvent.click(screen.getByRole("button", { name: "Scoreboard" }));
  expect(
    await screen.findByRole("heading", { name: "Scoreboard" }),
  ).toBeDefined();
  fireEvent.click(
    await screen.findByRole("button", { name: "Start an evaluation" }),
  );
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/evaluations",
    expect.objectContaining({
      method: "POST",
      body: JSON.stringify({
        evaluation_set: "evaluation",
        tiny: false,
        agents: 3,
      }),
    }),
  );
});

it("lists evaluation measures and opens a scored evaluation page", async () => {
  const evaluation = makeEvaluation();
  const fetchMock = vi.fn((input: RequestInfo | URL): Promise<Response> => {
    const path = requestPath(input);
    if (path === "/api/dispatches/latest") {
      return Promise.resolve(jsonResponse(null));
    }
    if (path === "/api/evaluations") {
      return Promise.resolve(jsonResponse([evaluation]));
    }
    if (path === `/api/evaluations/${evaluation.run_id}`) {
      return Promise.resolve(jsonResponse(evaluation));
    }
    if (path === "/api/claims") {
      return Promise.resolve(jsonResponse({ claims: [makeSummary()] }));
    }
    return Promise.resolve(jsonResponse(makeDetail()));
  });
  vi.stubGlobal("fetch", fetchMock);
  render(<App />);

  fireEvent.click(screen.getByRole("button", { name: "Scoreboard" }));
  expect(await screen.findByText("80%")).toBeDefined();
  fireEvent.click(await screen.findByRole("button", { name: "#1" }));

  expect(
    await screen.findByRole("heading", { name: "Held-out set" }),
  ).toBeDefined();
  expect(screen.getAllByText("40 s").length).toBeGreaterThan(0);
  expect(screen.getByText("The evidence supports the claim.")).toBeDefined();
  expect(screen.getByText("Right")).toBeDefined();
});

it("shows a clear error when a dispatch cannot start", async () => {
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const path = requestPath(input);
    if (path === "/api/dispatches" && init?.method === "POST") {
      return Promise.resolve(
        jsonResponse(
          {
            detail: {
              message: "Another agent run is already active",
              run_id: "RUN-9",
            },
          },
          409,
        ),
      );
    }
    if (path === "/api/dispatches/latest") {
      return Promise.resolve(jsonResponse(null));
    }
    if (path === "/api/claims") {
      return Promise.resolve(jsonResponse({ claims: [makeSummary()] }));
    }
    return Promise.resolve(jsonResponse(makeDetail()));
  });
  vi.stubGlobal("fetch", fetchMock);
  render(<App />);

  fireEvent.click(
    await screen.findByRole("button", { name: "Start a dispatch" }),
  );

  expect(
    await screen.findByText("Another agent run is already active"),
  ).toBeDefined();
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
