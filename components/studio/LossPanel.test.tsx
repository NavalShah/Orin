import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { LossPanel, groupByCode } from "./LossPanel";
import { FIXTURE_LOSS, FIXTURE_NOTES } from "./fixtures";

const groupNamed = (name: string) => screen.getByText(name).closest("details") as HTMLElement;

describe("loss panel", () => {
  it("groups items by code and keeps each item's index", () => {
    const groups = groupByCode(FIXTURE_LOSS.items);
    expect(groups.map((g) => [g.code, g.items.map((i) => i.index)])).toEqual([
      ["MICROTONE_ROUNDED", [0, 2]],
      ["ORNAMENT_DROPPED", [1]],
    ]);
  });

  it("shows each group with its count and each item's severity in words", () => {
    render(<LossPanel loss={FIXTURE_LOSS} notes={FIXTURE_NOTES} howScored="Three parts, weighted." />);
    const micro = groupNamed("Microtone rounded");
    expect(within(micro).getByText("(2)")).toBeInTheDocument();
    expect(within(micro).getAllByRole("button")).toHaveLength(2);
    expect(within(micro).getByText("Major loss")).toBeInTheDocument();
    expect(within(micro).getByText("Minor loss")).toBeInTheDocument();
    expect(within(micro).getByText("Cost: 4.0 of 100 points")).toBeInTheDocument();
    expect(within(micro).getByText("No cost to the score")).toBeInTheDocument();

    const ornament = groupNamed("Ornament dropped");
    expect(within(ornament).getByText("(1)")).toBeInTheDocument();
    expect(within(ornament).getAllByRole("button")).toHaveLength(1);
  });

  it("shows the fidelity score, its three parts and how it is scored", () => {
    render(<LossPanel loss={FIXTURE_LOSS} notes={FIXTURE_NOTES} howScored="Three parts, weighted." />);
    expect(screen.getByText("0.82")).toBeInTheDocument();
    for (const name of ["Pitch score", "Ornament score", "Timing score"]) {
      expect(screen.getByLabelText(name)).toBeInTheDocument();
    }
    expect(screen.getByText("How is this scored?")).toBeInTheDocument();
    expect(screen.getByText("Three parts, weighted.")).toBeInTheDocument();
  });

  it("lists notices separately from loss", () => {
    render(<LossPanel loss={FIXTURE_LOSS} notes={FIXTURE_NOTES} />);
    const heading = screen.getByRole("heading", { name: "About the analysis (not counted as loss)" });
    const section = heading.closest("section") as HTMLElement;
    expect(within(section).getByText(/No tonic could be found with confidence/)).toBeInTheDocument();
    expect(within(section).queryByRole("button")).toBeNull();
    // A notice is never one of the loss buttons.
    for (const button of screen.getAllByRole("button")) {
      expect(button).not.toHaveTextContent("No tonic could be found");
    }
  });

  it("calls back with the item index and its note id when activated by keyboard", async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    render(<LossPanel loss={FIXTURE_LOSS} notes={FIXTURE_NOTES} onSelect={onSelect} />);

    screen.getByRole("button", { name: /rounded by 31 cents/ }).focus();
    await user.keyboard("{Enter}");
    expect(onSelect).toHaveBeenLastCalledWith(0, "n2");

    screen.getByRole("button", { name: /shown as a plain note/ }).focus();
    await user.keyboard(" ");
    expect(onSelect).toHaveBeenLastCalledWith(1, "n3");
  });

  it("marks the selected item as pressed", () => {
    render(<LossPanel loss={FIXTURE_LOSS} notes={FIXTURE_NOTES} selectedIndex={1} />);
    expect(screen.getByRole("button", { name: /shown as a plain note/ })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: /rounded by 31 cents/ })).toHaveAttribute("aria-pressed", "false");
  });

  it("says so when nothing was lost", () => {
    render(<LossPanel loss={{ ...FIXTURE_LOSS, items: [], notices: [] }} />);
    expect(screen.getByText("Nothing was lost in this conversion.")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: /About the analysis/ })).toBeNull();
  });
});
