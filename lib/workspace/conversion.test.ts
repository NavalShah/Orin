import { describe, expect, it } from "vitest";
import { conversionToClip, pitchFromCents, scoreToNotes } from "./conversion";

const score = (notes: Array<{ cents: number; onset_s: number; duration_s: number; velocity?: number }>, tonic = 146.83) =>
  ({ options: { tonic_hz: tonic }, notes }) as unknown as Parameters<typeof scoreToNotes>[0];

describe("conversion import", () => {
  it("keeps a neutral third as a semitone plus a detune", () => {
    // 350 cents above D3 (146.83 Hz) lies half-way between F3 (53) and F#3 (54).
    const { midi, detuneCents } = pitchFromCents(146.83, 350);
    const exact = 69 + 12 * Math.log2((146.83 * 2 ** (350 / 1200)) / 440);
    expect(midi).toBe(53);
    expect(Math.abs(detuneCents - (exact - 53) * 100)).toBeLessThan(1);
    expect(detuneCents).toBeGreaterThan(49);
    expect(detuneCents).toBeLessThan(51);
    // The semitone plus the detune sounds the original frequency.
    const hz = 440 * 2 ** ((midi - 69 + detuneCents / 100) / 12);
    expect(hz).toBeCloseTo(146.83 * 2 ** (350 / 1200), 1);
  });

  it("maps an in-tune note with no detune", () => {
    expect(pitchFromCents(440, 0)).toEqual({ midi: 69, detuneCents: 0 });
    expect(pitchFromCents(440, -1200)).toEqual({ midi: 57, detuneCents: 0 });
  });

  it("turns a score into clip notes with times and velocity", () => {
    const notes = scoreToNotes(score([{ cents: 0, onset_s: 0.5, duration_s: 0.25, velocity: 127 }, { cents: 700, onset_s: 1, duration_s: 1 }]));
    expect(notes).toHaveLength(2);
    expect(notes[0]).toMatchObject({ midi: 50, startSec: 0.5, durSec: 0.25, velocity: 1 });
    expect(Math.abs(notes[0].detuneCents)).toBeLessThan(1);
    expect(notes[1].midi).toBe(57);
    expect(notes[1].velocity).toBeCloseTo(80 / 127);
    expect(new Set(notes.map((n) => n.id)).size).toBe(2);
  });

  it("builds a MIDI clip that remembers its conversion", () => {
    const clip = conversionToClip(
      { id: "conv1", output_format: "western_classical", score: score([{ cents: 350, onset_s: 0, duration_s: 2 }]) as never, source_asset: { filename: "alapana.wav" } },
      { trackId: "t1", startSec: 4 },
    );
    expect(clip).toMatchObject({ kind: "midi", trackId: "t1", startSec: 4, lengthSec: 2, tonicHz: 146.83, conversionId: "conv1" });
    expect(clip?.name).toContain("alapana");
    expect(clip?.notes[0].midi).toBe(53);
  });

  it("returns null when there is nothing to import", () => {
    expect(conversionToClip({ id: "c", output_format: "x", score: null }, { trackId: "t", startSec: 0 })).toBeNull();
    expect(conversionToClip({ id: "c", output_format: "x", score: score([]) as never }, { trackId: "t", startSec: 0 })).toBeNull();
  });
});
