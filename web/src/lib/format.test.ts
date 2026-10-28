import { describe, expect, it } from "vitest";
import { dominantCurrency, formatChange, formatDay, formatMoney, greeting } from "./format";

describe("formatDay", () => {
  it("keeps date-only ledger dates in UTC regardless of browser timezone", () => {
    const expected = new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", timeZone: "UTC" })
      .format(new Date("2026-10-26T12:00:00Z"));
    expect(formatDay("2026-10-26T00:00:00Z")).toBe(expected);
    expect(formatDay("2026-10-26")).toBe(expected);
  });

  it("uses the same UTC date as server reporting for offset timestamps", () => {
    expect(formatDay("2026-10-25T23:30:00-07:00")).toBe(formatDay("2026-10-26T06:30:00Z"));
  });
});

describe("formatMoney", () => {
  it("formats in the record's own currency", () => {
    expect(formatMoney("-12.5", "EUR")).toContain("€");
    expect(formatMoney("-12.5", "EUR")).toContain("12.50");
    expect(formatMoney(1234, "USD")).toContain("1,234.00");
  });

  it("accepts lower-case codes", () => {
    expect(formatMoney(5, "gbp")).toContain("£");
  });
});

describe("formatChange", () => {
  it("signs the change and names the comparison", () => {
    expect(formatChange(8.24, "last month")).toBe("+8.2% vs last month");
    expect(formatChange(-12.44, "last week")).toBe("−12.4% vs last week");
    expect(formatChange(0, "last week")).toBe("0.0% vs last week");
  });

  it("returns null when there is nothing to compare with", () => {
    expect(formatChange(null, "last month")).toBeNull();
    expect(formatChange(undefined, "last month")).toBeNull();
  });
});

describe("greeting", () => {
  it("follows the local hour", () => {
    expect(greeting(new Date(2026, 0, 1, 9))).toBe("Good morning.");
    expect(greeting(new Date(2026, 0, 1, 13))).toBe("Good afternoon.");
    expect(greeting(new Date(2026, 0, 1, 20))).toBe("Good evening.");
  });

  it("uses the first name only, and nothing when there is no name", () => {
    expect(greeting(new Date(2026, 0, 1, 9), "  Ada  King ")).toBe("Good morning, Ada.");
    expect(greeting(new Date(2026, 0, 1, 9), "")).toBe("Good morning.");
    expect(greeting(new Date(2026, 0, 1, 9), null)).toBe("Good morning.");
  });
});

describe("dominantCurrency", () => {
  it("picks the most common currency, falling back to USD", () => {
    expect(dominantCurrency([])).toBe("USD");
    expect(dominantCurrency([{ currency: "EUR" }, { currency: "EUR" }, { currency: "USD" }])).toBe("EUR");
  });
});
