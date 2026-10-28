import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { noAlerts, summary, tx } from "../test/fixtures";
import { MetricsRow } from "./MetricsRow";

function metric(label: string) {
  const card = screen.getByText(label).closest("article");
  if (!card) throw new Error(`No metric ${label}`);
  return within(card);
}

describe("MetricsRow", () => {
  const periodTransactions = {
    items: [
      tx({ id: "a", amount: "2100.00" }),
      tx({ id: "b", amount: "40.00" }),
      tx({ id: "c", amount: "-1500.00" }),
    ],
    truncated: false,
  };

  it("computes cash flow as money in minus money out for the same period", () => {
    render(
      <MetricsRow
        period="monthly"
        currency="USD"
        periodTransactions={periodTransactions}
        summary={summary({ total_spend: "1700.00" })}
        notifications={noAlerts}
      />,
    );
    expect(metric("Money in").getByText("$2,140.00")).toBeTruthy();
    expect(metric("Money out").getByText("$1,700.00")).toBeTruthy();
    expect(metric("Cash flow").getByText("$440.00")).toBeTruthy();
    expect(metric("Money out").getByText("−12.4% vs last month")).toBeTruthy();
  });

  it("leaves other currencies out of the totals and says so", () => {
    render(
      <MetricsRow
        period="monthly"
        currency="USD"
        periodTransactions={{ items: [...periodTransactions.items, tx({ id: "d", amount: "999.00", currency: "EUR" })], truncated: false }}
        summary={summary({ other_currencies: ["EUR"] })}
        notifications={noAlerts}
      />,
    );
    expect(metric("Money in").getByText("$2,140.00")).toBeTruthy();
    expect(metric("Cash flow").getByText(/USD only/)).toBeTruthy();
  });

  it("shows an em dash, not zero, when data is unavailable", () => {
    render(<MetricsRow period="weekly" currency="USD" periodTransactions={null} summary={null} notifications={null} />);
    expect(metric("Money in").getByText("Unavailable")).toBeTruthy();
    expect(metric("Money in").getByText("Transactions unavailable")).toBeTruthy();
    expect(metric("Unread alerts").getByText("Alerts unavailable")).toBeTruthy();
    expect(screen.queryByText("$0.00")).toBeNull();
  });
});
