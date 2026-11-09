import { expect, test } from "@playwright/test";
import { resumedClaims, startedRequests } from "./api";

test("the dashboard keeps the health check and completes a standard claim", async ({
  page,
}) => {
  await page.goto("/");
  const health = await page.request.get("/api/health");
  expect(await health.json()).toEqual({ status: "ok" });

  const claim = page.getByRole("button", { name: /CLM-1001/ });
  await expect(claim).toBeVisible();
  await expect(
    page.getByRole("heading", { name: /Flight home canceled/ }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Start agent review" }).click();

  await expect(
    page.getByText("Checking the story, policy and supporting documents."),
  ).toBeVisible();
  await expect(page.getByText("Approved", { exact: true })).toBeVisible();
  await expect(page.getByText("Paid ₹34,860.00")).toBeVisible();
  await expect(claim).toContainText("decided");
  await page.getByRole("button", { name: "Agent instructions" }).click();
  await expect(
    page.getByRole("heading", { name: "Claim agent" }),
  ).toBeVisible();
  await expect(page.getByRole("heading", { name: "Queue agent" })).toHaveCount(
    0,
  );
});

test("an over-limit claim resumes after handler sign-off and is paid", async ({
  page,
}) => {
  await page.goto("/");
  const claim = page.getByRole("button", { name: /CLM-1003/ });
  await claim.click();
  await expect(
    page.getByRole("heading", { name: /Hospital visit abroad/ }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Start agent review" }).click();

  await expect(
    page.getByText("Handler sign-off required before payment"),
  ).toBeVisible();
  await expect(claim).toContainText("waiting for sign-off");
  await expect(
    page.getByRole("button", { name: "Sign off payout" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Record simulated payment" }),
  ).toHaveCount(0);
  const unpaidClaim = await page.request.get("/api/claims/CLM-1003");
  expect(await unpaidClaim.json()).toHaveProperty("payment", null);

  await page.getByRole("button", { name: "Sign off payout" }).click();
  await expect(page.getByText(/Signed off by Dana Whitfield at/)).toBeVisible();
  expect(await resumedClaims(page.request)).toContain("CLM-1003");
  const payment = await page.request.post("/api/claims/CLM-1003/payment", {
    data: { amount_inr: 311250 },
  });
  expect(payment.ok()).toBeTruthy();
  await expect(
    page.getByText(/Paid ₹3,11,250\.00, signed off by Dana Whitfield at/),
  ).toBeVisible();
  await expect(claim).toContainText("decided");
});

test("the queue and scoreboard start dispatches and evaluations", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByLabel("Claim agents at once").fill("7");
  await page.getByRole("button", { name: "Start a dispatch" }).click();
  await expect
    .poll(() => startedRequests(page.request))
    .toEqual([{ path: "/api/dispatches", body: { agents: 7 } }]);

  await page.getByRole("button", { name: "Scoreboard" }).click();
  await page.getByLabel("Evaluation set").selectOption("held_out");
  await page.getByLabel("Claims").selectOption("tiny");
  await page.getByLabel("Claim agents at once").fill("5");
  await page.getByRole("button", { name: "Start an evaluation" }).click();
  await expect
    .poll(() => startedRequests(page.request))
    .toEqual([
      { path: "/api/dispatches", body: { agents: 7 } },
      {
        path: "/api/evaluations",
        body: { evaluation_set: "held_out", tiny: true, agents: 5 },
      },
    ]);
});
