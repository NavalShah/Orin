import { describe, expect, it } from "vitest";
import { fileProblem, isSupportedAudioFile } from "./buffers";
import { migrate, newProject } from "./project";
import { thumbnailRects } from "./thumbnail";
import { barBeatAt, clipLabel, formatBarsBeats, formatClock, midiName, noteLabel } from "./time";

describe("file checks", () => {
  it("accepts wav, mp3, m4a, flac and ogg by name", () => {
    for (const name of ["a.wav", "B.MP3", "c.m4a", "d.flac", "e.ogg"]) expect(isSupportedAudioFile({ name })).toBe(true);
    for (const name of ["notes.txt", "song.aiff", "noextension"]) expect(isSupportedAudioFile({ name })).toBe(false);
  });
  it("explains what is wrong with a file", () => {
    expect(fileProblem({ name: "take.wav", size: 10 })).toBeNull();
    expect(fileProblem({ name: "notes.pdf", size: 10 })).toMatch(/\.pdf files are not supported/);
    expect(fileProblem({ name: "take.wav", size: 0 })).toMatch(/empty/);
  });
});

describe("time display", () => {
  it("shows bars and beats, 1-based", () => {
    expect(formatBarsBeats(0, 120, [4, 4])).toBe("1:1");
    expect(formatBarsBeats(2.5, 120, [4, 4])).toBe("2:2");
    expect(formatBarsBeats(4, 120, [4, 4])).toBe("3:1");
    expect(barBeatAt(1.5, 120, [3, 4])).toMatchObject({ bar: 2, beat: 1 });
  });
  it("shows minutes and seconds", () => {
    expect(formatClock(0)).toBe("00:00.0");
    expect(formatClock(83.46)).toBe("01:23.4");
  });
  it("names notes and describes clips and notes for screen readers", () => {
    expect(midiName(60)).toBe("C4");
    expect(midiName(53)).toBe("F3");
    expect(clipLabel({ kind: "audio", name: "Lead vocal", startSec: 4, lengthSec: 4.2 }, 120, [4, 4])).toBe(
      "Audio clip Lead vocal, starts at bar 3, 4.2 seconds",
    );
    expect(noteLabel({ midi: 53, detuneCents: 50, startSec: 1.25, durSec: 0.5 })).toBe(
      "Note F3 +50 cents, starts at 1.25 seconds, 0.50 seconds long",
    );
    expect(noteLabel({ midi: 60, detuneCents: 0, startSec: 0, durSec: 1 })).toBe("Note C4, starts at 0.00 seconds, 1.00 seconds long");
  });
});

describe("thumbnailRects", () => {
  it("lays clips out by track and time", () => {
    const p = migrate({
      ...newProject(),
      tracks: [{ id: "a", kind: "audio" }, { id: "m", kind: "midi" }],
      clips: [
        { id: "c1", trackId: "a", kind: "audio", assetId: "x", startSec: 0, lengthSec: 5 },
        { id: "c2", trackId: "m", kind: "midi", startSec: 5, lengthSec: 5, notes: [] },
      ],
    });
    const rects = thumbnailRects(p, 100, 40);
    expect(rects).toHaveLength(2);
    expect(rects[0]).toMatchObject({ x: 0, width: 50, kind: "audio" });
    expect(rects[1]).toMatchObject({ x: 50, width: 50, kind: "midi" });
    expect(rects[1].y).toBeGreaterThan(rects[0].y);
    expect(thumbnailRects(newProject(), 100, 40)).toEqual([]);
  });
});
