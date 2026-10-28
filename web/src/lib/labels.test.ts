import { describe, expect, it } from "vitest";
import { alertTitle, categorySource, replySource, summarySource } from "./labels";

describe("labels", () => {
  it("maps API codes to human labels", () => {
    expect(categorySource("llm").label).toBe("AI");
    expect(categorySource("rules").label).toBe("Rules");
    expect(categorySource("user").label).toBe("Edited");
    expect(summarySource("fallback").label).toBe("Template");
    expect(summarySource("cache").label).toBe("AI summary");
    expect(replySource("draft").label).toBe("Direct answer");
    expect(alertTitle("anomaly_flagged")).toBe("Unusual transaction");
  });

  it("treats a missing source as pending", () => {
    expect(categorySource(null).label).toBe("Pending");
  });

  it("never shows a raw code with underscores", () => {
    expect(categorySource("new_engine").label).toBe("new engine");
    expect(alertTitle("card_frozen")).toBe("card frozen");
  });
});
