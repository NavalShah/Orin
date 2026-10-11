import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { PHRASE, lostItems, performedCents, pitchLine } from "./melody";

vi.mock("../../lib/api", () => ({ api: { formats: vi.fn(() => Promise.reject(new Error("offline"))) } }));

import { Home } from "./Home";

describe("home page", () => {
  it("leads to the three parts", () => {
    render(<Home />);
    const parts = screen.getByRole("region", { name: "Ask, write, make" });
    expect(within(parts).getByRole("link", { name: /Chat/ })).toHaveAttribute("href", "/chat");
    expect(within(parts).getByRole("link", { name: /Notation Studio/ })).toHaveAttribute("href", "/studio");
    expect(within(parts).getByRole("link", { name: /Workspace/ })).toHaveAttribute("href", "/workspace");
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("One melody.");
  });

  it("shows what the piano loses when the switch is flipped, and renames the notes", async () => {
    const user = userEvent.setup();
    render(<Home />);
    expect(screen.getByText("This is the performance.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Hear the note Ga, with its movement" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "On piano keys" }));
    expect(screen.getByRole("button", { name: "On piano keys" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText("2 things did not make it onto the keys.")).toBeInTheDocument();
    for (const item of lostItems()) expect(screen.getByText(item)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Hear the note E" })).toBeInTheDocument();
  });

  it("lists six traditions without the server and marks them as drafts", async () => {
    const user = userEvent.setup();
    render(<Home />);
    const chips = within(screen.getByRole("group", { name: "Traditions" })).getAllByRole("button");
    expect(chips).toHaveLength(6);
    await user.click(screen.getByRole("button", { name: "Persian (Dastgah)" }));
    expect(screen.getByText(/koron and sori/)).toBeInTheDocument();
    expect(screen.getByText("Unreviewed draft")).toBeInTheDocument();
  });
});

describe("the phrase", () => {
  it("wavers and slides when performed, and is flat on the keys", () => {
    const ga = PHRASE[2];
    const pa = PHRASE[3];
    const performed = pitchLine(ga, 0).map((p) => p.cents);
    expect(Math.max(...performed) - Math.min(...performed)).toBeGreaterThan(100);
    expect(new Set(pitchLine(ga, 1).map((p) => p.cents))).toEqual(new Set([ga.written]));
    expect(performedCents(pa, 0)).toBe(pa.slide!.from);
    expect(performedCents(pa, pa.dur)).toBe(pa.cents);
  });
});
