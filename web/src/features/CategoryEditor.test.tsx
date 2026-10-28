import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { CategoryEditor } from "./CategoryEditor";

it("retains the editor and reports failed corrections without refreshing", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ error: { message: "Not found" } }), { status: 404 })));
  const changed = vi.fn();
  render(<CategoryEditor token="t" id="tx" category="Other" categories={{ other: "Other", groceries: "Groceries" }} onChanged={changed} onUnauthorized={vi.fn()} />);
  fireEvent.click(screen.getByText("Edit category"));
  fireEvent.change(screen.getByLabelText("New category"), { target: { value: "groceries" } });
  fireEvent.click(screen.getByText("Save category"));
  expect((await screen.findByRole("alert")).textContent).toBe("Not found");
  expect(changed).not.toHaveBeenCalled();
  expect((screen.getByLabelText("New category") as HTMLSelectElement).value).toBe("groceries");
});
