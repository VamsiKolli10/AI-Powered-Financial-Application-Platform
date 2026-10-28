import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { mockApi, noAlerts, summary, tx } from "../test/fixtures";
import { Dashboard } from "./Dashboard";

const allReady = {
  status: "ok",
  services: { gateway: "ok", transactions: "ok", assistant: "ok", insights: "ok", notifications: "ok" },
  checked_at: "2026-10-01T12:00:00Z",
};

function routes(overrides: Record<string, unknown> = {}) {
  return {
    "/auth/me": { body: { id: "usr_1", email: "ada@example.com", full_name: "Ada Lovelace" } },
    "/transactions": { body: { items: [tx()], next_cursor: null, limit: 10 } },
    "/notifications": { body: noAlerts },
    "/insights/summary": { body: summary() },
    "/status": { body: allReady },
    ...overrides,
  } as Parameters<typeof mockApi>[0];
}

describe("Dashboard", () => {
  it("greets the signed-in user by first name", async () => {
    vi.stubGlobal("fetch", mockApi(routes()));
    render(<Dashboard token="t" onLogout={vi.fn()} />);
    expect((await screen.findByRole("heading", { level: 1 })).textContent).toMatch(/, Ada\.$/);
  });

  it("reports readiness from the gateway's status endpoint", async () => {
    vi.stubGlobal(
      "fetch",
      mockApi(routes({ "/status": { body: { ...allReady, status: "degraded", services: { ...allReady.services, assistant: "unavailable" } } } })),
    );
    const { container } = render(<Dashboard token="t" onLogout={vi.fn()} />);
    await screen.findByRole("heading", { level: 1 }); // past the loading screen
    const card = within(container.querySelector(".sidebar__status") as HTMLElement);
    expect(await card.findByText(/1 of 5 services unavailable/)).toBeTruthy();
    expect(card.getByText("Assistant")).toBeTruthy();
  });

  it("falls back to its own requests when the status endpoint is unreachable", async () => {
    vi.stubGlobal("fetch", mockApi(routes({ "/status": { status: 502, body: { error: { message: "down" } } } })));
    render(<Dashboard token="t" onLogout={vi.fn()} />);
    expect(await screen.findByText(/All services responding/)).toBeTruthy();
    expect(screen.getByText("Based on this page's requests")).toBeTruthy();
  });

  it("keeps working when one service fails, and says which", async () => {
    vi.stubGlobal("fetch", mockApi(routes({ "/insights/summary": { status: 502, body: { error: { message: "down" } } } })));
    render(<Dashboard token="t" onLogout={vi.fn()} />);
    expect((await screen.findByRole("alert")).textContent).toContain("Insights");
    expect(screen.getByText(/Insights are unavailable right now/)).toBeTruthy();
    expect(screen.getByRole("table", { name: /Recent transactions/ })).toBeTruthy(); // still renders
  });

  it("notes currencies left out of the totals", async () => {
    vi.stubGlobal("fetch", mockApi(routes({ "/insights/summary": { body: summary({ other_currencies: ["EUR"] }) } })));
    render(<Dashboard token="t" onLogout={vi.fn()} />);
    expect(await screen.findByText(/Spending in EUR is not included/)).toBeTruthy();
  });

  it("refetches period data when switching to Week", async () => {
    const periods: string[] = [];
    vi.stubGlobal(
      "fetch",
      mockApi(
        routes({
          "/insights/summary": (url: URL) => {
            periods.push(url.searchParams.get("period") ?? "");
            return { body: summary() };
          },
        }),
      ),
    );
    render(<Dashboard token="t" onLogout={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: "Week" }));
    await vi.waitFor(() => expect(periods).toEqual(["monthly", "weekly"]));
  });

  it("keeps loaded monthly labels until the weekly figures arrive", async () => {
    let resolveWeekly: (response: Response) => void = () => {};
    const fallback = mockApi(routes({
      "/insights/summary": { body: summary({ change_vs_previous_pct: 20 }) },
    }));
    vi.stubGlobal("fetch", (input: RequestInfo | URL) => {
      const url = new URL(String(input), "http://localhost");
      if (url.searchParams.get("period") === "weekly") {
        return new Promise<Response>((resolve) => { resolveWeekly = resolve; });
      }
      return fallback(input);
    });
    render(<Dashboard token="t" onLogout={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: "Week" }));
    const moneyOut = within(screen.getByText("Money out").closest("article")!);
    expect(moneyOut.getByText("$1,700.00")).toBeTruthy();
    expect(moneyOut.getByText("+20.0% vs last month")).toBeTruthy();
    expect(moneyOut.queryByText(/vs last week/)).toBeNull();
    expect(screen.getByText("Updating this week…").getAttribute("role")).toBe("status");
    resolveWeekly(new Response(JSON.stringify(summary({ total_spend: "20.00", change_vs_previous_pct: 5 }))));
    await vi.waitFor(() => expect(moneyOut.getByText("$20.00")).toBeTruthy());
    expect(moneyOut.getByText("+5.0% vs last week")).toBeTruthy();
  });

  it("uses the server's reporting window even when the browser clock is ahead", async () => {
    const cutoff = "2026-09-30T23:59:00.123456Z";
    const start = "2026-09-01T00:00:00Z";
    const urls: URL[] = [];
    const fallback = mockApi(routes({
      "/insights/summary": { body: summary({ date_from: start, date_to: cutoff }) },
    }));
    vi.stubGlobal("fetch", (input: RequestInfo | URL) => {
      const url = new URL(String(input), "http://localhost");
      urls.push(url);
      return fallback(input);
    });
    render(<Dashboard token="t" onLogout={vi.fn()} />);
    await screen.findByRole("heading", { level: 1 });
    const summaryURL = urls.find((url) => url.pathname.endsWith("summary"))!;
    const periodURL = urls.find((url) => url.searchParams.has("from"))!;
    expect(summaryURL.searchParams.has("as_of")).toBe(false);
    expect(periodURL.searchParams.get("from")).toBe(start);
    expect(periodURL.searchParams.get("to")).toBe(cutoff);
  });

  it("signs out when the session has expired", async () => {
    vi.stubGlobal("fetch", mockApi(routes({ "/auth/me": { status: 401, body: { error: { message: "Token has expired." } } } })));
    const onLogout = vi.fn();
    render(<Dashboard token="t" onLogout={onLogout} />);
    await vi.waitFor(() => expect(onLogout).toHaveBeenCalled());
  });
});
