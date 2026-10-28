import { expect, test } from "@playwright/test";

test("clean workspace → entry → import → correction → grounded chat → read alert", async ({ page, request }) => {
  const email = `browser-${crypto.randomUUID()}@example.com`;
  const password = "isolated-browser-password";
  const registration = await request.post("/api/v1/auth/register", { data: { email, password, full_name: "Browser Test" } });
  expect(registration.status()).toBe(201);
  const { access_token: token } = await registration.json();
  const headers = { Authorization: `Bearer ${token}` };
  const accounts = await request.get("/api/v1/transactions/accounts", { headers });
  const account = (await accounts.json())[0];
  await page.goto("/");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Open dashboard" }).click();
  await expect(page.getByRole("heading", { level: 1 })).toContainText("Browser");
  await expect(page.getByText("No transactions yet.", { exact: false })).toBeVisible();
  await expect(page.getByLabel("Account")).toHaveValue(account.id);

  await page.getByLabel("Description", { exact: true }).fill("Garden produce market");
  await page.getByLabel("Amount (USD)").fill("23.45");
  await page.getByRole("button", { name: "Save transaction", exact: true }).click();
  await expect(page.getByRole("status").filter({ hasText: "Transaction saved" })).toBeVisible();
  await expect(page.getByRole("table").getByText("Garden produce market", { exact: false })).toBeVisible();
  const manualRow = page.getByRole("row").filter({ hasText: /garden produce market/i });
  await manualRow.getByRole("button", { name: "Edit category" }).click();
  await manualRow.getByLabel("New category").selectOption("groceries");
  await manualRow.getByRole("button", { name: "Save category" }).click();
  await expect(manualRow).toContainText("Groceries");
  await expect(manualRow).toContainText("Edited");

  const today = new Date().toISOString().slice(0, 10);
  const csv = `occurred_at,description,amount,external_tx_id\n${today},Laptop purchase,-900.00,browser-laptop\n${today},Salary,2000.00,browser-salary\n`;
  await page.getByText("Import a CSV", { exact: true }).click();
  await page.getByLabel("CSV file").setInputFiles({ name: "bank.csv", mimeType: "text/csv", buffer: Buffer.from(csv) });
  await page.getByRole("button", { name: "Import transactions", exact: true }).click();
  await expect(page.getByText("Imported 2 transactions; skipped 0 existing rows.")).toBeVisible();
  await page.getByRole("button", { name: "Import transactions", exact: true }).click();
  await expect(page.getByText("Imported 0 transactions; skipped 2 existing rows.")).toBeVisible();
  await expect(page.getByRole("table").getByRole("row")).toHaveCount(4);

  await page.getByLabel("Question for the assistant").fill("How much did I spend this month?");
  await page.getByRole("button", { name: "Ask Ledger AI" }).click();
  await expect(page.getByRole("log")).toContainText("$923.45");
  await expect(page.getByRole("log")).toContainText("Direct answer");
  // Poll the real broker consumer through the page's explicit refresh action.
  await expect.poll(async () => {
    await page.getByRole("button", { name: "Refresh data" }).click();
    return page.getByRole("button", { name: "Mark as read" }).count();
  }).toBeGreaterThan(0);
  const alerts = page.locator("#alerts");
  const unread = await alerts.getByRole("button", { name: "Mark as read" }).count();
  await alerts.getByRole("button", { name: "Mark as read" }).first().click();
  await expect(alerts.getByRole("button", { name: "Mark as read" })).toHaveCount(unread - 1);

  // Invalid import fails atomically and leaves existing activity visible.
  await page.getByLabel("CSV file").setInputFiles({ name: "invalid.csv", mimeType: "text/csv", buffer: Buffer.from(`occurred_at,description,amount\n${today},Bad,0\n`) });
  await page.getByRole("button", { name: "Import transactions", exact: true }).click();
  await expect(page.getByRole("alert").filter({ hasText: "row 2" })).toBeVisible();
  await expect(page.getByRole("table").getByRole("row")).toHaveCount(4);

  const other = await request.post("/api/v1/auth/register", { data: { email: `other-${crypto.randomUUID()}@example.com`, password } });
  const otherHeaders = { Authorization: `Bearer ${(await other.json()).access_token}` };
  const ownRows = await request.get("/api/v1/transactions", { headers });
  const id = (await ownRows.json()).items[0].id;
  const forbidden = await request.patch(`/api/v1/transactions/${id}`, { headers: otherHeaders, data: { category: "travel" } });
  expect(forbidden.status()).toBe(404);
});
