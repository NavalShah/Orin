import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { OutputFile } from "../../lib/api";
import { makeRegistry } from "../shell/registry";
import { NotationViewer } from "./NotationViewer";
import { makeConversion, makeRegistryFixture } from "./fixtures";

const registry = makeRegistry(makeRegistryFixture());

const svg =
  '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 40">' +
  '<g class="note" id="n1"><ellipse cx="10" cy="20" rx="4" ry="3"/></g>' +
  '<g class="note" id="n2"><ellipse cx="30" cy="20" rx="4" ry="3"/></g>' +
  '<g class="note" id="n2t2"><ellipse cx="50" cy="20" rx="4" ry="3"/></g>' +
  '<g class="note" id="n3"><ellipse cx="70" cy="20" rx="4" ry="3"/></g>' +
  "</svg>";

const staff: OutputFile = {
  kind: "svg", label: "Staff", media_type: "image/svg+xml", ext: "svg", text: null, pages: [svg], tokens: [], note_ids: ["n1", "n2", "n3"],
  asset_id: null, url: null, warnings: [],
};

function setup(selectedNoteId: string | null = null, tab: string | null = null) {
  const base = makeConversion();
  const conversion = makeConversion({ outputs: [...(base.outputs ?? []), staff] });
  const onSelectNote = vi.fn();
  const onTab = vi.fn();
  render(
    <NotationViewer conversion={conversion} registry={registry} selectedNoteId={selectedNoteId} onSelectNote={onSelectNote} tab={tab} onTab={onTab} />,
  );
  return { onSelectNote, onTab };
}

describe("notation viewer", () => {
  it("puts the format's primary renderer first and keeps the description out of the tabs", () => {
    setup();
    const tabs = within(screen.getByRole("tablist", { name: "Notation views" })).getAllByRole("tab");
    expect(tabs.map((t) => t.textContent)).toEqual(["Staff", "Note names"]);
    expect(tabs[0]).toHaveAttribute("aria-selected", "true");
    // The description is always on the page, as the text alternative of the score.
    expect(screen.getByText("Three notes rising from D.")).toBeInTheDocument();
    const staffImage = screen.getByRole("img", { name: /Staff for Staff Notation/ });
    expect(document.getElementById(staffImage.getAttribute("aria-describedby") ?? "")).toHaveTextContent("Three notes rising from D.");
  });

  it("marks low-confidence and selected notes inside the staff, including tied parts, and explains the marks", () => {
    setup("n3");
    expect(document.getElementById("n2")).toHaveAttribute("data-orin-low");
    expect(document.getElementById("n2t2")).toHaveAttribute("data-orin-low");
    expect(document.getElementById("n1")).not.toHaveAttribute("data-orin-low");
    expect(document.getElementById("n3")).toHaveAttribute("data-orin-selected");
    expect(screen.getByText(/A dashed outline with a question mark: Orin was unsure of this note/)).toHaveTextContent("1 of 3 notes are marked.");
    expect(screen.getByText(/Selected note 3 of 3: G/)).toBeInTheDocument();
  });

  it("selects a note by click, and by the keyboard stepper", async () => {
    const user = userEvent.setup();
    const { onSelectNote } = setup("n1");
    fireEvent.click(document.querySelector("#n2t2 ellipse") as Element);
    expect(onSelectNote).toHaveBeenLastCalledWith("n2");
    await user.click(screen.getByRole("button", { name: "Next note" }));
    expect(onSelectNote).toHaveBeenLastCalledWith("n2");
    await user.click(screen.getByRole("button", { name: "Clear selection" }));
    expect(onSelectNote).toHaveBeenLastCalledWith(null);
  });

  it("moves between tabs with the arrow keys and shows text notation with tokens", async () => {
    const user = userEvent.setup();
    const { onTab } = setup(null, "names");
    const tabs = screen.getAllByRole("tab");
    expect(tabs[1]).toHaveAttribute("aria-selected", "true");
    expect(document.querySelectorAll("[data-note-id]")).toHaveLength(3);
    tabs[1].focus();
    await user.keyboard("{ArrowLeft}");
    expect(onTab).toHaveBeenLastCalledWith("svg");
  });

  it("offers Original only when there is a recording, and says so when sound cannot be made", async () => {
    const user = userEvent.setup();
    setup();
    expect(screen.getByRole("button", { name: "Original" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Notation" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText(/There is no original recording for typed notes\./)).toBeInTheDocument();
    // jsdom has no Web Audio: the viewer says so instead of failing.
    await user.click(screen.getByRole("button", { name: "Play notation" }));
    expect(screen.getByText(/This browser cannot play synthesised sound\./)).toBeInTheDocument();
  });
});
