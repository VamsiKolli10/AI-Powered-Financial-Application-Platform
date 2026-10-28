import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { TransactionEntry } from "./TransactionEntry";

describe("TransactionEntry", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("saves on HTTP, retains IDs on retries, and gives the next entry a new ID", async () => {
    // HTTP provides getRandomValues but does not expose randomUUID.
    vi.stubGlobal("crypto", { getRandomValues: crypto.getRandomValues.bind(crypto) });
    const bodies: Record<string, string>[] = [];
    const onChanged = vi.fn();
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      if (url.endsWith("/accounts")) return new Response(JSON.stringify([{ id: "a", name: "Checking", currency: "EUR" }]));
      bodies.push(JSON.parse(String(init?.body)));
      if (bodies.length === 1) return new Response(JSON.stringify({ error: { message: "Try again" } }), { status: 503 });
      return new Response(JSON.stringify({ id: "tx" }));
    }));
    render(<TransactionEntry token="t" onChanged={onChanged} onUnauthorized={vi.fn()} />);
    await screen.findByText("Checking · EUR");
    fireEvent.change(screen.getByLabelText("Description"), { target: { value: "Market" } });
    fireEvent.change(screen.getByLabelText("Amount (EUR)"), { target: { value: "12.34" } });
    fireEvent.change(screen.getByLabelText("Date (UTC)"), { target: { value: "2026-10-26" } });
    fireEvent.click(screen.getByRole("button", { name: "Save transaction" }));
    await screen.findByText("Try again");
    expect(onChanged).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Save transaction" }));
    await screen.findByText(/Transaction saved/);
    expect(bodies[0]).toEqual(bodies[1]);
    expect(bodies[1].amount).toBe("-12.34");
    expect(bodies[1].currency).toBe("EUR");
    expect(bodies[1].occurred_at).toBe("2026-10-26T00:00:00.000Z");
    expect(onChanged).toHaveBeenCalledOnce();

    fireEvent.change(screen.getByLabelText("Description"), { target: { value: "Market" } });
    fireEvent.change(screen.getByLabelText("Amount (EUR)"), { target: { value: "12.34" } });
    fireEvent.click(screen.getByRole("button", { name: "Save transaction" }));
    await vi.waitFor(() => expect(onChanged).toHaveBeenCalledTimes(2));
    expect(bodies[2].external_tx_id).not.toBe(bodies[1].external_tx_id);
  });

  it("handles an expired account lookup session", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 401 })));
    const onUnauthorized = vi.fn();
    render(<TransactionEntry token="t" onChanged={vi.fn()} onUnauthorized={onUnauthorized} />);
    await vi.waitFor(() => expect(onUnauthorized).toHaveBeenCalledOnce());
  });
});
