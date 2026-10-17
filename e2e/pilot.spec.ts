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
