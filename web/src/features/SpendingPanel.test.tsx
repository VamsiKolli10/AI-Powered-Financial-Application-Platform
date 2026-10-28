import { render, screen } from "@testing-library/react";
import { expect, it } from "vitest";
import { summary } from "../test/fixtures";
import { SpendingPanel } from "./SpendingPanel";

it("discloses excluded spending even when the reporting currency has no rows", () => {
  render(<SpendingPanel period="monthly" currency="USD" busy={false}
    summary={summary({ total_spend: "0.00", by_category: [], other_currencies: ["EUR"] })} />);
  expect(screen.getByText("No USD spending recorded this month yet.")).toBeTruthy();
  expect(screen.getByText(/Spending in EUR is not included/)).toBeTruthy();
  expect(screen.queryByText("No spending recorded this month yet.")).toBeNull();
});
