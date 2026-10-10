import { describe, expect, it } from "vitest";
import { DRUM_VOICES, PROJECT_VERSION, effectiveGain, migrate, newProject, newTrack, projectEndSec } from "./project";

describe("newProject", () => {
  it("is a valid current project that survives a save and load", () => {
    const p = newProject("Demo");
    expect(p.version).toBe(PROJECT_VERSION);
    expect(p.name).toBe("Demo");
    expect(p.tempo).toBe(120);
    expect(p.timeSignature).toEqual([4, 4]);
    expect(p.tracks).toEqual([]);
    expect(p.clips).toEqual([]);
    expect(p.drums).toBeNull();
    expect(p.loop.endSec).toBeGreaterThan(p.loop.startSec);
    expect(migrate(JSON.parse(JSON.stringify(p)))).toEqual(p);
  });
});

describe("migrate", () => {
  it("returns a fresh project for junk", () => {
    expect(migrate(null).version).toBe(PROJECT_VERSION);
    expect(migrate("nope").tracks).toEqual([]);
    expect(migrate([1, 2]).clips).toEqual([]);
  });

  it("fills missing fields", () => {
    const p = migrate({ version: 1, name: "Half", tracks: [{ id: "t1", kind: "audio" }], clips: [{ id: "c1", trackId: "t1", kind: "audio", assetId: "a1" }] });
    expect(p.tempo).toBe(120);
    expect(p.timeSignature).toEqual([4, 4]);
    expect(p.masterGain).toBeCloseTo(0.8);
    expect(p.metronome).toBe(false);
    expect(p.loop).toEqual({ enabled: false, startSec: 0, endSec: 8 });
    expect(p.tracks[0]).toEqual({ id: "t1", name: "Audio", kind: "audio", gain: 0.8, pan: 0, mute: false, solo: false });
    expect(p.clips[0]).toMatchObject({ id: "c1", startSec: 0, offsetSec: 0, lengthSec: 1, gain: 1, fadeInSec: 0, fadeOutSec: 0 });
    expect(typeof p.createdAt).toBe("string");
  });

  it("upgrades a version-0 project", () => {
    const v0 = {
      name: "Old sketch",
      bpm: 96,
      tracks: [
        { id: "t1", name: "Voice", type: "audio", volume: 0.5 },
        { id: "t2", name: "Keys", type: "midi" },
      ],
      clips: [
        { id: "c1", track: "t1", asset: "a1", start: 2, offset: 0.5, length: 3, fadeIn: 0.1 },
        { id: "c2", track: "t2", type: "midi", start: 1, length: 2, notes: [{ pitch: 62, cents: 50, start: 0, duration: 0.5, velocity: 100 }] },
      ],
    };
    const p = migrate(v0);
    expect(p.version).toBe(PROJECT_VERSION);
    expect(p.tempo).toBe(96);
    expect(p.tracks.map((t) => [t.kind, t.gain])).toEqual([["audio", 0.5], ["midi", 0.8]]);
    expect(p.clips[0]).toMatchObject({ kind: "audio", trackId: "t1", assetId: "a1", startSec: 2, offsetSec: 0.5, lengthSec: 3, fadeInSec: 0.1 });
    const midi = p.clips[1];
    expect(midi.kind).toBe("midi");
    if (midi.kind !== "midi") throw new Error("expected a MIDI clip");
    expect(midi.notes[0]).toMatchObject({ midi: 62, detuneCents: 50, startSec: 0, durSec: 0.5 });
    expect(midi.notes[0].velocity).toBeCloseTo(100 / 127);
    expect(midi.notes[0].id).toBeTruthy();
  });

  it("drops clips that cannot play and repairs the drum pattern", () => {
    const p = migrate({
      version: 1,
      tracks: [{ id: "t1", kind: "audio" }, { id: "d1", kind: "drums" }, { id: "d2", kind: "drums" }],
      clips: [
        { id: "ok", trackId: "t1", kind: "audio", assetId: "a1", lengthSec: 2 },
        { id: "orphan", trackId: "gone", kind: "audio", assetId: "a1" },
        { id: "wrongkind", trackId: "t1", kind: "midi", notes: [] },
        { id: "noasset", trackId: "t1", kind: "audio" },
      ],
      drums: { trackId: "d1", rows: [{ voice: "kick", steps: [true, false, true], gain: 0.5 }] },
    });
    expect(p.clips.map((c) => c.id)).toEqual(["ok"]);
    expect(p.tracks.map((t) => t.id)).toEqual(["t1", "d1"]);
    expect(p.drums?.rows.map((r) => r.voice)).toEqual([...DRUM_VOICES]);
    const kick = p.drums!.rows[0];
    expect(kick.steps).toHaveLength(16);
    expect(kick.steps.slice(0, 3)).toEqual([true, false, true]);
    expect(kick.gain).toBe(0.5);
  });

  it("keeps a project from a newer version readable", () => {
    const p = migrate({ ...newProject("Future"), version: 7, somethingNew: true });
    expect(p.version).toBe(PROJECT_VERSION);
    expect(p.name).toBe("Future");
  });
});

describe("mute and solo", () => {
  const a = { ...newTrack("audio", "A", "a"), gain: 0.6 };
  const b = { ...newTrack("audio", "B", "b"), gain: 0.9 };
  it("plays every track at its gain by default", () => {
    expect(effectiveGain(a, [a, b])).toBe(0.6);
  });
  it("mute silences a track", () => {
    expect(effectiveGain({ ...a, mute: true }, [a, b])).toBe(0);
  });
  it("solo silences the others, and mute still wins on a soloed track", () => {
    const solo = { ...b, solo: true };
    expect(effectiveGain(a, [a, solo])).toBe(0);
    expect(effectiveGain(solo, [a, solo])).toBe(0.9);
    expect(effectiveGain({ ...solo, mute: true }, [a, solo])).toBe(0);
  });
});

describe("projectEndSec", () => {
  it("is the end of the last clip, or four bars for drums alone", () => {
    const p = newProject();
    expect(projectEndSec(p)).toBe(0);
    const withDrums = migrate({ ...p, tracks: [{ id: "d", kind: "drums" }], drums: { trackId: "d", rows: [{ voice: "hat", steps: [true] }] } });
    expect(projectEndSec(withDrums)).toBe(8);
  });
});
