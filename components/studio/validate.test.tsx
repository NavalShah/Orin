import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ParseProblems } from "./ParseProblems";
import { durationProblem, validateFile } from "./validate";

const limits = { max_upload_mb: 1, max_audio_seconds: 60, max_generate_seconds: 30, daily_generations: 5 };

describe("file checks at the moment of choosing", () => {
  it("accepts audio and notes files by extension and MIME", () => {
    expect(validateFile({ name: "clip.WAV", type: "audio/wav", size: 1000 }, limits)).toEqual({ kind: "audio", problems: [] });
    expect(validateFile({ name: "clip.flac", type: "", size: 1000 }, limits)).toEqual({ kind: "audio", problems: [] });
    expect(validateFile({ name: "tune.abc", type: "", size: 10 }, limits)).toEqual({ kind: "notes", problems: [] });
    expect(validateFile({ name: "tune.musicxml", type: "application/xml", size: 10 }, limits)).toEqual({ kind: "notes", problems: [] });
  });

  it("rejects an unknown type, a mismatched MIME, an empty file and an oversize file", () => {
    expect(validateFile({ name: "photo.png", type: "image/png", size: 10 }, limits).problems[0]).toMatch(/not a type Orin can read/);
    expect(validateFile({ name: "clip.mp3", type: "image/png", size: 10 }, limits).problems[0]).toMatch(/reports it as image\/png/);
    expect(validateFile({ name: "clip.mp3", type: "audio/mpeg", size: 0 }, limits).problems).toContain("clip.mp3 is empty.");
    const big = validateFile({ name: "clip.mp3", type: "audio/mpeg", size: 3 * 1024 * 1024 }, limits);
    expect(big.kind).toBeNull();
    expect(big.problems).toEqual(["clip.mp3 is 3.0 MB. The limit is 1 MB."]);
  });

  it("checks the length of audio against the limit", () => {
    expect(durationProblem("clip.wav", 59, limits)).toBeNull();
    expect(durationProblem("clip.wav", null, limits)).toBeNull();
    expect(durationProblem("clip.wav", 125, limits)).toBe("clip.wav is 2:05 long. The limit is 1:00.");
  });
});

describe("typed-notes problems", () => {
  it("lists each problem by line and column and marks its place in the echo", () => {
    render(
      <ParseProblems
        text={"S R G\nM X P\nD"}
        problems={[
          { code: "bad_token", message: "X is not a note.", line: 2, column: 3, length: 1, severity: "error" },
          { code: "short_line", message: "This line ends early.", line: 3, column: 2, length: 1, severity: "warning" },
        ]}
      />,
    );
    expect(screen.getByRole("heading", { name: "Problems in the typed notes (2)" })).toBeInTheDocument();
    expect(screen.getByText("Error, line 2, column 3:")).toBeInTheDocument();
    expect(screen.getByText("Warning, line 3, column 2:")).toBeInTheDocument();
    const marks = Array.from(document.querySelectorAll("mark"));
    expect(marks).toHaveLength(2);
    expect(marks[0]).toHaveTextContent("X");
    expect(marks[0]).toHaveAttribute("title", "X is not a note.");
    // A problem past the end of its line still gets a visible mark.
    expect(marks[1].textContent).not.toBe("");
    expect(marks[1]).toHaveAttribute("data-severity", "warning");
  });

  it("renders nothing when there are no problems", () => {
    const { container } = render(<ParseProblems text="S R G" problems={[]} />);
    expect(container).toBeEmptyDOMElement();
  });
});
