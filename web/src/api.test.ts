import { afterEach, describe, expect, it, vi } from "vitest";
import { getPeriodTransactions, getSummary } from "./api";
import { summary, tx } from "./test/fixtures";

afterEach(() => vi.useRealTimers());

describe("reporting bounds", () => {
  it("preserves server timestamps with microsecond precision", async () => {
    const end = "2026-10-15T12:00:00.123456Z";
    const start = "2026-10-01T00:00:00Z";
    let requested: URL | undefined;
    vi.stubGlobal("fetch", async (input: RequestInfo | URL) => {
      requested = new URL(String(input), "http://localhost");
      return new Response(JSON.stringify({ items: [], next_cursor: null, limit: 100 }));
    });
    await getPeriodTransactions("t", "monthly", end, start);
    expect(requested!.searchParams.get("from")).toBe(start);
    expect(requested!.searchParams.get("to")).toBe(end);
  });

  it("holds both bounds fixed across pages and excludes future transactions", async () => {
    const asOf = new Date("2026-10-15T12:00:00Z");
    const records = [
      tx({ id: "posted", amount: "100.00", occurred_at: "2026-10-14T12:00:00Z" }),
      tx({ id: "future", amount: "5000.00", occurred_at: "2026-10-16T12:00:00Z" }),
    ];
    const urls: URL[] = [];
    vi.stubGlobal("fetch", async (input: RequestInfo | URL) => {
      const url = new URL(String(input), "http://localhost");
      urls.push(url);
      const end = url.searchParams.get("to");
      const eligible = records.filter((record) => !end || new Date(record.occurred_at) <= new Date(end));
      return new Response(JSON.stringify({
        items: urls.length === 1 ? eligible : [],
        next_cursor: urls.length === 1 ? "cursor_1" : null,
        limit: 100,
      }));
    });
    const result = await getPeriodTransactions("token", "monthly", asOf);
    expect(result.items.map((record) => record.id)).toEqual(["posted"]);
    expect(urls).toHaveLength(2);
    for (const url of urls) {
      expect(url.searchParams.get("from")).toBe("2026-10-01T00:00:00.000Z");
      expect(url.searchParams.get("to")).toBe(asOf.toISOString());
    }
    expect(urls[1].searchParams.get("cursor")).toBe("cursor_1");
  });

  it("sends the same end time to Insights as to transaction listing", async () => {
    const asOf = new Date("2026-10-15T12:00:00Z");
    const urls: URL[] = [];
    vi.stubGlobal("fetch", async (input: RequestInfo | URL) => {
      const url = new URL(String(input), "http://localhost");
      urls.push(url);
      return new Response(JSON.stringify(url.pathname.endsWith("summary")
        ? summary()
        : { items: [], next_cursor: null, limit: 100 }));
    });
    await Promise.all([getSummary("t", "weekly", asOf), getPeriodTransactions("t", "weekly", asOf)]);
    expect(urls[0].searchParams.get("as_of")).toBe(urls[1].searchParams.get("to"));
    expect(urls[1].searchParams.get("from")).toBe("2026-10-12T00:00:00.000Z");
  });
});
