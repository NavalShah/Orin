import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { lowConfidenceNoteIds, viewableOutputs } from "./NotationViewer";
import { TextNotation, splitTokens } from "./TextNotation";
import { makeConversion } from "./fixtures";

const text = "D E F#";
const tokens = [
  { note_id: "n1", start: 0, end: 1 },
  { note_id: "n2", start: 2, end: 3 },
  { note_id: "n3", start: 4, end: 6 },
];

describe("text notation", () => {
  it("wraps each note token and leaves the rest as plain text", () => {
    expect(splitTokens(text, tokens)).toEqual([
      { text: "D", noteId: "n1" },
      { text: " " },
      { text: "E", noteId: "n2" },
      { text: " " },
      { text: "F#", noteId: "n3" },
    ]);
    const { container } = render(<TextNotation text={text} tokens={tokens} lowIds={new Set()} label="Note names" />);
    const notes = Array.from(container.querySelectorAll("[data-note-id]"));
    expect(notes.map((n) => [n.getAttribute("data-note-id"), n.textContent])).toEqual([
      ["n1", "D"],
      ["n2", "E"],
      ["n3", "F#"],
    ]);
    expect(screen.getByLabelText("Note names").textContent).toBe(text);
  });

  it("ignores tokens that overlap or run past the text", () => {
    expect(splitTokens("abc", [{ note_id: "x", start: 0, end: 2 }, { note_id: "y", start: 1, end: 3 }, { note_id: "z", start: 2, end: 9 }])).toEqual([
      { text: "ab", noteId: "x" },
      { text: "c" },
    ]);
  });

  it("marks a low-confidence note with a marker and text, not colour alone", () => {
    const { container } = render(<TextNotation text={text} tokens={tokens} lowIds={new Set(["n2"])} label="Note names" />);
    const low = container.querySelector('[data-note-id="n2"]') as HTMLElement;
    expect(low).toHaveAttribute("data-low", "true");
    expect(low).toHaveTextContent("?");
    expect(low).toHaveTextContent("(low confidence)");
    expect(low.querySelector('[aria-hidden="true"]')).toHaveTextContent("?");

    const sure = container.querySelector('[data-note-id="n1"]') as HTMLElement;
    expect(sure).not.toHaveAttribute("data-low");
    expect(sure).not.toHaveTextContent("low confidence");
    expect(sure).not.toHaveTextContent("?");
  });

  it("marks the sounding and the selected note, and reports clicks", async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    const { container } = render(
      <TextNotation text={text} tokens={tokens} lowIds={new Set()} currentId="n3" selectedId="n1" onSelect={onSelect} label="Note names" />,
    );
    expect(container.querySelector('[data-note-id="n3"]')).toHaveAttribute("data-current", "true");
    expect(container.querySelector('[data-note-id="n1"]')).toHaveAttribute("data-selected", "true");
    await user.click(container.querySelector('[data-note-id="n2"]') as HTMLElement);
    expect(onSelect).toHaveBeenCalledWith("n2");
  });
});

describe("notation helpers", () => {
  it("maps low-confidence events to score notes", () => {
    expect([...lowConfidenceNoteIds(makeConversion())]).toEqual(["n2"]);
  });

  it("offers viewable outputs with the primary renderer first and keeps the description out of the tabs", () => {
    const outputs = [
      { kind: "midi", label: "MIDI", media_type: "audio/midi", ext: "mid", text: null, url: "/x", asset_id: "a" },
      { kind: "names", label: "Note names", media_type: "text/plain", ext: "txt", text: "D E", url: null, asset_id: null },
      { kind: "describe", label: "Description", media_type: "text/plain", ext: "txt", text: "Two notes.", url: null, asset_id: null },
      { kind: "svg", label: "Staff", media_type: "image/svg+xml", ext: "svg", text: null, pages: ["<svg/>"], url: null, asset_id: null },
      { kind: "musicxml", label: "MusicXML", media_type: "application/vnd.recordare.musicxml+xml", ext: "musicxml", text: "<score/>", url: null, asset_id: null },
    ];
    expect(viewableOutputs(outputs, "svg").map((o) => o.kind)).toEqual(["svg", "names"]);
    expect(viewableOutputs(outputs, "names").map((o) => o.kind)).toEqual(["names", "svg"]);
  });
});
