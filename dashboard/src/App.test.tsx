import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { App } from "./App";
import {
  approvedDecision,
  jsonResponse,
  makeDetail,
  makeRun,
  makeSummary,
  mockApi,
  requestPath,
} from "./App.testFixtures";
import type { Decision } from "./api";
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
  expect(screen.getAllByText(/34,860\.00/).length).toBeGreaterThan(0);
  expect(screen.getByText("Supporting documents")).toBeDefined();
  expect(screen.getByText("Claim history")).toBeDefined();
});

it("shows queue states including handler review", async () => {
  const fetchMock = vi.fn((input: RequestInfo | URL): Promise<Response> => {
    const path = requestPath(input);
    if (path === "/api/agent/runs/current") {
      return Promise.resolve(jsonResponse({ run: null }));
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
  expect(await screen.findByText("Reviewed one claim.")).toBeDefined();
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
    await screen.findByText(/Simulated payment recorded:.*34,860\.00/),
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
      "Choose the ordered batch and keep its tasks current.",
    ),
  ).toBeDefined();
  expect(
    await screen.findByText("Use the policy wording and claims manual."),
  ).toBeDefined();
  expect(screen.queryByRole("textbox")).toBeNull();
});

it("starts a capped queue run and displays its saved tasks and summary", async () => {
  const fetchMock = mockApi();
  render(<App />);

  fireEvent.click(await screen.findByRole("button", { name: "Run queue" }));

  expect(
    await screen.findByRole("heading", { name: "Run summary" }),
  ).toBeDefined();
  expect(
    await screen.findByText(
      "Reviewed 5 claims: 3 approved, 1 declined, 1 referred, 0 need handler attention.",
    ),
  ).toBeDefined();
  expect(screen.getByText("CLM-1005")).toBeDefined();
  expect(screen.getAllByText("done")).toHaveLength(5);
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/agent/runs",
    expect.objectContaining({
      method: "POST",
      body: JSON.stringify({ max_claims: 5 }),
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

it("polls task changes and keeps the completed run after current becomes null", async () => {
  let currentRun = makeRun("in progress", 2);
  let currentReads = 0;
  const completedRun = makeRun("completed", 2);
  const fetchMock = vi.fn((input: RequestInfo | URL): Promise<Response> => {
    const path = requestPath(input);
    if (path === "/api/agent/runs/current") {
      currentReads += 1;
      if (currentReads > 2) {
        currentRun = completedRun;
        return Promise.resolve(jsonResponse({ run: null }));
      }
      return Promise.resolve(jsonResponse({ run: currentRun }));
    }
    if (path === "/api/agent/runs/RUN-0001") {
      return Promise.resolve(jsonResponse(currentRun));
    }
    if (path === "/api/claims") {
      return Promise.resolve(jsonResponse({ claims: [makeSummary()] }));
    }
    return Promise.resolve(jsonResponse(makeDetail()));
  });
  vi.stubGlobal("fetch", fetchMock);
  render(<App />);

  expect(
    await screen.findByRole("heading", { name: "Current run" }),
  ).toBeDefined();
  expect(
    await screen.findByRole(
      "heading",
      { name: "Run summary" },
      { timeout: 4000 },
    ),
  ).toBeDefined();
  expect(screen.getAllByText("done")).toHaveLength(2);
});

it("shows a clear error when a queue run cannot start", async () => {
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const path = requestPath(input);
    if (path === "/api/agent/runs" && init?.method === "POST") {
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
    if (path === "/api/agent/runs/current") {
      return Promise.resolve(jsonResponse({ run: null }));
    }
    if (path === "/api/claims") {
      return Promise.resolve(jsonResponse({ claims: [makeSummary()] }));
    }
    return Promise.resolve(jsonResponse(makeDetail()));
  });
  vi.stubGlobal("fetch", fetchMock);
  render(<App />);

  fireEvent.click(await screen.findByRole("button", { name: "Run queue" }));

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
