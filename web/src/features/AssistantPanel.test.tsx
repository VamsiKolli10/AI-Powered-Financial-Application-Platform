import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { mockApi } from "../test/fixtures";
import { AssistantPanel } from "./AssistantPanel";

describe("AssistantPanel", () => {
  it("discloses ledger mode before asking and shows why a reply used a fallback", async () => {
    vi.stubGlobal("fetch", mockApi({
      "/assistant/status": { body: { mode: "disabled", model: null, message: "Live AI is off. You can still ask about recorded transactions." } },
      "/assistant/chat": { body: { conversation_id: "c1", reply: "No transactions recorded.", reply_source: "draft", fallback_reason: "disabled" } },
    }));
    render(<AssistantPanel token="t" onUnauthorized={vi.fn()} />);
    expect(await screen.findByText("Ledger mode")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Ask Ledger AI" }));
    expect(await screen.findByText("Live AI is off. This answer comes directly from your ledger.")).toBeTruthy();
  });

  it("retains earlier turns and reuses the conversation for follow-up questions", async () => {
    let call = 0;
    const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).endsWith("/status")) return new Response(JSON.stringify({ mode: "llm", model: "test", message: "Live AI configured" }));
      call += 1;
      const payload = JSON.parse(String(init?.body));
      if (call === 2) expect(payload.conversation_id).toBe("c1");
      return new Response(JSON.stringify({ conversation_id: "c1", reply: call === 1 ? "First reply" : "Follow-up reply", reply_source: "llm" }));
    });
    vi.stubGlobal("fetch", fetcher);
    render(<AssistantPanel token="t" onUnauthorized={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Ask Ledger AI" }));
    await screen.findByText("First reply");
    fireEvent.change(screen.getByLabelText("Question for the assistant"), { target: { value: "And on groceries?" } });
    fireEvent.click(screen.getByRole("button", { name: "Ask Ledger AI" }));
    expect(await screen.findByText("Follow-up reply")).toBeTruthy();
    expect(screen.getByText("First reply")).toBeTruthy();
  });
  it("shows the answer with a label for how it was produced", async () => {
    vi.stubGlobal("fetch", mockApi({ "/assistant/chat": { body: { conversation_id: "c1", reply: "You spent $970.00.", reply_source: "draft" } } }));
    render(<AssistantPanel token="t" onUnauthorized={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Ask Ledger AI" }));
    expect(await screen.findByText("You spent $970.00.")).toBeTruthy();
    expect(screen.getByText("Direct answer")).toBeTruthy();
  });

  it("announces the API's refusal message", async () => {
    vi.stubGlobal(
      "fetch",
      mockApi({ "/assistant/chat": { status: 400, body: { error: { code: "READ_ONLY_ASSISTANT", message: "This assistant cannot move money." } } } }),
    );
    render(<AssistantPanel token="t" onUnauthorized={vi.fn()} />);
    fireEvent.change(screen.getByLabelText("Question for the assistant"), { target: { value: "Move $50 to savings" } });
    fireEvent.click(screen.getByRole("button", { name: "Ask Ledger AI" }));
    expect((await screen.findByRole("alert")).textContent).toBe("This assistant cannot move money.");
  });

  it("signs out on an expired session", async () => {
    vi.stubGlobal("fetch", mockApi({ "/assistant/chat": { status: 401, body: { error: { message: "Token has expired." } } } }));
    const onUnauthorized = vi.fn();
    render(<AssistantPanel token="t" onUnauthorized={onUnauthorized} />);
    fireEvent.click(screen.getByRole("button", { name: "Ask Ledger AI" }));
    await vi.waitFor(() => expect(onUnauthorized).toHaveBeenCalled());
  });
});
