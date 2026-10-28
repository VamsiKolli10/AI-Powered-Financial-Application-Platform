import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Button, Field, Metric, SegmentedControl } from ".";

describe("Field", () => {
  it("associates the label with the control, even when visually hidden", () => {
    render(<Field multiline label="Question for the assistant" hideLabel defaultValue="hi" />);
    expect(screen.getByLabelText("Question for the assistant").tagName).toBe("TEXTAREA");
  });
});

describe("Button", () => {
  it("disables itself and swaps its label while loading", () => {
    render(<Button loading loadingLabel="Thinking…">Ask</Button>);
    const button = screen.getByRole("button", { name: "Thinking…" }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    expect(button.getAttribute("aria-busy")).toBe("true");
  });
});

describe("SegmentedControl", () => {
  it("exposes the selection with aria-pressed and reports changes", () => {
    const onChange = vi.fn();
    render(
      <SegmentedControl
        label="Reporting period"
        value="monthly"
        onChange={onChange}
        options={[{ value: "monthly", label: "Month" }, { value: "weekly", label: "Week" }]}
      />,
    );
    expect(screen.getByRole("button", { name: "Month" }).getAttribute("aria-pressed")).toBe("true");
    fireEvent.click(screen.getByRole("button", { name: "Week" }));
    expect(onChange).toHaveBeenCalledWith("weekly");
  });
});

describe("Metric", () => {
  it("announces unavailable values instead of an empty dash", () => {
    render(<Metric label="Money in" value={null} caption="—" />);
    expect(screen.getByText("Unavailable")).toBeTruthy();
  });
});
