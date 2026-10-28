import { describe, expect, it } from "vitest";
import { periodStart } from "./period";

// Must agree with services/insights/periods.py, which uses Python's Monday-based weekday().
describe("periodStart", () => {
  it.each([
    ["2026-10-02T04:00:00Z", "2026-09-28T00:00:00.000Z"], // Friday
    ["2026-09-28T00:00:00Z", "2026-09-28T00:00:00.000Z"], // Monday midnight
    ["2026-09-27T23:59:59Z", "2026-09-21T00:00:00.000Z"], // Sunday night
    ["2026-01-01T00:00:00Z", "2025-12-29T00:00:00.000Z"], // across a year
  ])("weekly from %s starts %s", (now, expected) => {
    expect(periodStart("weekly", new Date(now)).toISOString()).toBe(expected);
  });

  it.each([
    ["2026-10-02T04:00:00Z", "2026-10-01T00:00:00.000Z"],
    ["2026-03-01T00:00:00Z", "2026-03-01T00:00:00.000Z"],
    ["2026-12-31T23:59:59Z", "2026-12-01T00:00:00.000Z"],
  ])("monthly from %s starts %s", (now, expected) => {
    expect(periodStart("monthly", new Date(now)).toISOString()).toBe(expected);
  });
});
