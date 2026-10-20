import { expect, test } from "@playwright/test";

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
  await expect(
    page.getByText("Simulated payment recorded: ₹34,860.00"),
  ).toBeVisible();
  await expect(claim).toContainText("decided");
});

test("an over-limit claim needs handler sign-off before simulated payment", async ({
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
  await page.getByRole("button", { name: "Record simulated payment" }).click();
  await expect(
    page.getByText("Simulated payment recorded: ₹3,11,250.00"),
  ).toBeVisible();
  await expect(claim).toContainText("decided");
});

test("a capped queue run retries, routes a second failure and leaves other claims waiting", async ({
  page,
}) => {
  const browserErrors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") browserErrors.push(message.text());
  });
  page.on("pageerror", (error) => browserErrors.push(error.message));

  await page.goto("/");
  await page.getByLabel("Claims per run").fill("5");
  await page.getByRole("button", { name: "Run queue" }).click();

  await expect(
    page.getByText(
      "Reviewed 5 claims: 0 approved, 0 declined, 4 referred, 1 need handler attention.",
    ),
  ).toBeVisible({ timeout: 30000 });
  const runPanel = page.getByRole("region", { name: "Agent run" });
  await expect(runPanel.getByRole("listitem")).toHaveCount(5);

  const retriedTodo = runPanel
    .getByRole("listitem")
    .filter({ hasText: "CLM-1007" });
  await expect(retriedTodo).toContainText("Attempts: 2");
  await expect(retriedTodo).toContainText("done");
  const handlerTodo = runPanel
    .getByRole("listitem")
    .filter({ hasText: "CLM-1006" });
  await expect(handlerTodo).toContainText("left for handler");
  await expect(
    runPanel.getByRole("listitem").filter({ hasText: "CLM-1002" }),
  ).toContainText("done");
  await expect(
    runPanel.getByRole("listitem").filter({ hasText: "CLM-1002" }),
  ).toContainText("Attempts: 2");
  await expect(page.getByRole("button", { name: /CLM-1002/ })).toContainText(
    "decided",
  );
  await expect(page.getByRole("button", { name: /CLM-1008/ })).toContainText(
    "waiting",
  );

  await page.getByRole("button", { name: /CLM-1007/ }).click();
  await expect(
    page.getByRole("heading", { name: "Policy and manual sections read" }),
  ).toBeVisible();
  await expect(
    page.getByText("Section 5: Baggage and Personal Effects"),
  ).toBeVisible();
  await expect(page.getByText("claims_manual:05")).toBeVisible();

  await page.getByRole("button", { name: "Agent instructions" }).click();
  await expect(
    page.getByRole("heading", { name: "Queue agent" }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Claim agent" }),
  ).toBeVisible();
  expect(browserErrors).toEqual([]);
});
