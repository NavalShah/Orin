import { describe, expect, it } from "vitest";
import { effectiveGain, newProject, type AudioClip, type MidiClip, type Project } from "./project";
import { reduce, snapSec, soloClipProject, type Action } from "./reducer";
import { ProjectStore } from "./store";

const run = (p: Project, ...actions: Action[]) => actions.reduce(reduce, p);

const audioClip = (over: Partial<AudioClip> = {}): AudioClip => ({
  id: "c1", trackId: "a1", kind: "audio", assetId: "asset1", name: "Lead vocal",
  startSec: 4, offsetSec: 1, lengthSec: 6, gain: 1, fadeInSec: 0, fadeOutSec: 0, assetDurationSec: 10, ...over,
});
const midiClip = (over: Partial<MidiClip> = {}): MidiClip => ({
  id: "m1", trackId: "k1", kind: "midi", name: "Melody", startSec: 2, lengthSec: 4, tonicHz: 146.83,
  notes: [
    { id: "n1", midi: 60, detuneCents: 0, startSec: 0, durSec: 1, velocity: 0.7 },
    { id: "n2", midi: 62, detuneCents: 50, startSec: 1.5, durSec: 1, velocity: 0.7 },
    { id: "n3", midi: 64, detuneCents: 0, startSec: 3, durSec: 1, velocity: 0.7 },
  ],
  ...over,
});

function base(): Project {
  return run(
    newProject(),
    { type: "addTrack", kind: "audio", id: "a1" },
    { type: "addTrack", kind: "audio", id: "a2" },
    { type: "addTrack", kind: "midi", id: "k1" },
    { type: "addClip", clip: audioClip() },
    { type: "addClip", clip: midiClip() },
  );
}
const audio = (p: Project, id = "c1") => p.clips.find((c) => c.id === id) as AudioClip;
const midi = (p: Project, id = "m1") => p.clips.find((c) => c.id === id) as MidiClip;

describe("tracks", () => {
  it("adds, names, renames and removes tracks with their clips", () => {
    let p = base();
    expect(p.tracks.map((t) => t.name)).toEqual(["Audio 1", "Audio 2", "MIDI 1"]);
    p = reduce(p, { type: "renameTrack", trackId: "a1", name: "Voice" });
    expect(p.tracks[0].name).toBe("Voice");
    p = reduce(p, { type: "removeTrack", trackId: "a1" });
    expect(p.tracks.map((t) => t.id)).toEqual(["a2", "k1"]);
    expect(p.clips.map((c) => c.id)).toEqual(["m1"]);
  });

  it("allows one drum track, and removing it removes the pattern", () => {
    let p = run(newProject(), { type: "addTrack", kind: "drums", id: "d1" }, { type: "addTrack", kind: "drums", id: "d2" });
    expect(p.tracks).toHaveLength(1);
    expect(p.drums?.trackId).toBe("d1");
    p = reduce(p, { type: "removeTrack", trackId: "d1" });
    expect(p.drums).toBeNull();
  });

  it("sets gain and pan inside their ranges", () => {
    const p = run(base(), { type: "setTrackGain", trackId: "a1", gain: 9 }, { type: "setTrackPan", trackId: "a1", pan: -3 });
    expect(p.tracks[0].gain).toBe(1.5);
    expect(p.tracks[0].pan).toBe(-1);
  });

  it("mute silences a track and solo silences the others", () => {
    let p = reduce(base(), { type: "setTrackMute", trackId: "a1", mute: true });
    expect(p.tracks.map((t) => effectiveGain(t, p.tracks))).toEqual([0, 0.8, 0.8]);
    p = run(p, { type: "setTrackMute", trackId: "a1", mute: false }, { type: "setTrackSolo", trackId: "k1", solo: true });
    expect(p.tracks.map((t) => effectiveGain(t, p.tracks))).toEqual([0, 0, 0.8]);
    p = reduce(p, { type: "setTrackSolo", trackId: "a1", solo: true });
    expect(p.tracks.map((t) => effectiveGain(t, p.tracks))).toEqual([0.8, 0, 0.8]);
  });
});

describe("clips", () => {
  it("rejects a clip on a missing track or a track of another kind", () => {
    const p = base();
    expect(reduce(p, { type: "addClip", clip: audioClip({ id: "x", trackId: "nope" }) })).toBe(p);
    expect(reduce(p, { type: "addClip", clip: audioClip({ id: "x", trackId: "k1" }) })).toBe(p);
  });

  it("moves in time and between tracks of the same kind only", () => {
    let p = reduce(base(), { type: "moveClip", clipId: "c1", startSec: 7.5, trackId: "a2" });
    expect(audio(p)).toMatchObject({ startSec: 7.5, trackId: "a2", offsetSec: 1, lengthSec: 6 });
    p = reduce(p, { type: "moveClip", clipId: "c1", startSec: -3, trackId: "k1" });
    expect(audio(p)).toMatchObject({ startSec: 0, trackId: "a2" });
  });

  it("trims the left edge: the offset moves with the start and the end stays put", () => {
    const p = reduce(base(), { type: "trimClipStart", clipId: "c1", startSec: 5.5 });
    expect(audio(p)).toMatchObject({ startSec: 5.5, offsetSec: 2.5, lengthSec: 4.5 });
    expect(audio(p).startSec + audio(p).lengthSec).toBe(10);
    // The same audio is still under the same timeline position.
    expect(audio(p).startSec - audio(p).offsetSec).toBe(3);
  });

  it("cannot pull the left edge out past the start of the asset", () => {
    const p = reduce(base(), { type: "trimClipStart", clipId: "c1", startSec: 0 });
    expect(audio(p)).toMatchObject({ startSec: 3, offsetSec: 0, lengthSec: 7 });
  });

  it("cannot trim the left edge past the right edge", () => {
    const p = reduce(base(), { type: "trimClipStart", clipId: "c1", startSec: 99 });
    expect(audio(p).lengthSec).toBeGreaterThan(0);
    expect(audio(p).startSec + audio(p).lengthSec).toBeCloseTo(10);
    expect(audio(p).offsetSec - audio(p).startSec).toBeCloseTo(-3);
  });

  it("trims the right edge: start and offset stay, length changes, capped by the asset", () => {
    let p = reduce(base(), { type: "trimClipEnd", clipId: "c1", endSec: 7 });
    expect(audio(p)).toMatchObject({ startSec: 4, offsetSec: 1, lengthSec: 3 });
    p = reduce(p, { type: "trimClipEnd", clipId: "c1", endSec: 100 });
    expect(audio(p).lengthSec).toBe(9); // 10 s asset, 1 s offset
  });

  it("splits into two clips whose lengths sum and whose offsets line up", () => {
    const p = reduce(base(), { type: "splitClip", clipId: "c1", atSec: 6.5, newId: "c1b" });
    const left = audio(p, "c1");
    const right = audio(p, "c1b");
    expect(left.lengthSec + right.lengthSec).toBe(6);
    expect(left).toMatchObject({ startSec: 4, offsetSec: 1, lengthSec: 2.5 });
    expect(right).toMatchObject({ startSec: 6.5, offsetSec: 3.5, lengthSec: 3.5 });
    expect(right.offsetSec).toBe(left.offsetSec + left.lengthSec);
    expect(right.startSec).toBe(left.startSec + left.lengthSec);
    expect(p.clips).toHaveLength(3);
  });

  it("split keeps the outer fades and drops the inner ones", () => {
    let p = reduce(base(), { type: "setClipFades", clipId: "c1", fadeInSec: 1, fadeOutSec: 2 });
    p = reduce(p, { type: "splitClip", clipId: "c1", atSec: 7, newId: "c1b" });
    expect(audio(p, "c1")).toMatchObject({ fadeInSec: 1, fadeOutSec: 0 });
    expect(audio(p, "c1b")).toMatchObject({ fadeInSec: 0, fadeOutSec: 2 });
  });

  it("does not split outside the clip", () => {
    const p = base();
    expect(reduce(p, { type: "splitClip", clipId: "c1", atSec: 4 })).toBe(p);
    expect(reduce(p, { type: "splitClip", clipId: "c1", atSec: 10 })).toBe(p);
    expect(reduce(p, { type: "splitClip", clipId: "c1", atSec: 30 })).toBe(p);
  });

  it("splits a MIDI clip: notes go left or right, and a held note ends at the cut", () => {
    const p = reduce(base(), { type: "splitClip", clipId: "m1", atSec: 4, newId: "m1b" }); // 2 s into the clip
    const left = midi(p, "m1");
    const right = midi(p, "m1b");
    expect(left.lengthSec + right.lengthSec).toBe(4);
    expect(left.notes.map((n) => [n.midi, n.startSec, n.durSec])).toEqual([[60, 0, 1], [62, 1.5, 0.5]]);
    expect(right.notes.map((n) => [n.midi, n.startSec, n.durSec])).toEqual([[64, 1, 1]]);
    expect(right.startSec).toBe(4);
  });

  it("duplicates right after the original with a new id", () => {
    const p = reduce(base(), { type: "duplicateClip", clipId: "m1", newId: "m2" });
    const copy = midi(p, "m2");
    expect(copy).toMatchObject({ startSec: 6, lengthSec: 4, trackId: "k1", name: "Melody" });
    expect(copy.notes.map((n) => n.midi)).toEqual([60, 62, 64]);
    expect(copy.notes[0].id).not.toBe("n1");
    expect(midi(p, "m1").notes[0].id).toBe("n1");
  });

  it("deletes", () => {
    const p = reduce(base(), { type: "deleteClip", clipId: "c1" });
    expect(p.clips.map((c) => c.id)).toEqual(["m1"]);
    expect(reduce(p, { type: "deleteClip", clipId: "c1" })).toBe(p);
  });

  it("sets gain and keeps fades inside the clip", () => {
    let p = reduce(base(), { type: "setClipGain", clipId: "c1", gain: 0.5 });
    expect(audio(p).gain).toBe(0.5);
    p = reduce(p, { type: "setClipFades", clipId: "c1", fadeInSec: 4 });
    p = reduce(p, { type: "setClipFades", clipId: "c1", fadeOutSec: 5 });
    expect(audio(p).fadeOutSec).toBe(5);
    expect(audio(p).fadeInSec).toBe(1);
    p = reduce(p, { type: "trimClipEnd", clipId: "c1", endSec: 6 }); // 2 s long now
    expect(audio(p).fadeInSec + audio(p).fadeOutSec).toBeLessThanOrEqual(2);
  });

  it("keeps the clips array identical when only the mixer changes", () => {
    const p = base();
    expect(reduce(p, { type: "setTrackGain", trackId: "a1", gain: 0.3 }).clips).toBe(p.clips);
    expect(reduce(p, { type: "setMasterGain", gain: 0.3 }).clips).toBe(p.clips);
  });
});

describe("MIDI notes", () => {
  it("adds, moves, resizes, detunes and deletes", () => {
    let p = reduce(base(), { type: "addNote", clipId: "m1", note: { id: "n4", midi: 67, detuneCents: 0, startSec: 2.5, durSec: 0.5, velocity: 0.8 } });
    expect(midi(p).notes).toHaveLength(4);
    p = reduce(p, { type: "moveNote", clipId: "m1", noteId: "n4", startSec: 0.25, midi: 70 });
    expect(midi(p).notes[3]).toMatchObject({ startSec: 0.25, midi: 70, durSec: 0.5 });
    p = reduce(p, { type: "resizeNote", clipId: "m1", noteId: "n4", durSec: 1.25 });
    expect(midi(p).notes[3].durSec).toBe(1.25);
    p = reduce(p, { type: "setNoteDetune", clipId: "m1", noteId: "n4", detuneCents: -35 });
    expect(midi(p).notes[3].detuneCents).toBe(-35);
    p = reduce(p, { type: "deleteNote", clipId: "m1", noteId: "n4" });
    expect(midi(p).notes.map((n) => n.id)).toEqual(["n1", "n2", "n3"]);
  });

  it("keeps notes on the keyboard and never shorter than a sliver", () => {
    let p = reduce(base(), { type: "moveNote", clipId: "m1", noteId: "n1", startSec: -2, midi: 400 });
    expect(midi(p).notes[0]).toMatchObject({ startSec: 0, midi: 127 });
    p = reduce(p, { type: "resizeNote", clipId: "m1", noteId: "n1", durSec: -1 });
    expect(midi(p).notes[0].durSec).toBeGreaterThan(0);
  });

  it("grows the clip when a note runs past its end", () => {
    const p = reduce(base(), { type: "resizeNote", clipId: "m1", noteId: "n3", durSec: 3 });
    expect(midi(p).lengthSec).toBe(6);
  });

  it("trimming the left edge keeps notes at their timeline position", () => {
    const p = reduce(base(), { type: "trimClipStart", clipId: "m1", startSec: 2.5 });
    const clip = midi(p);
    expect(clip).toMatchObject({ startSec: 2.5, lengthSec: 3.5 });
    // n1 (0..1) loses its first half second; n2 moves from 1.5 to 1.0 inside the clip.
    expect(clip.notes.map((n) => [n.startSec, n.durSec])).toEqual([[0, 0.5], [1, 1], [2.5, 1]]);
  });

  it("ignores note edits on an audio clip", () => {
    const p = base();
    expect(reduce(p, { type: "deleteNote", clipId: "c1", noteId: "n1" })).toBe(p);
  });
});

describe("drums and project settings", () => {
  it("toggles steps and sets row volume", () => {
    let p = run(newProject(), { type: "addTrack", kind: "drums", id: "d1" }, { type: "toggleStep", voice: "kick", step: 0 }, { type: "toggleStep", voice: "kick", step: 4 }, { type: "toggleStep", voice: "kick", step: 4 });
    const kick = p.drums!.rows.find((r) => r.voice === "kick")!;
    expect(kick.steps.filter(Boolean)).toHaveLength(1);
    expect(kick.steps[0]).toBe(true);
    p = reduce(p, { type: "setDrumRowGain", voice: "hat", gain: 0.25 });
    expect(p.drums!.rows.find((r) => r.voice === "hat")!.gain).toBe(0.25);
    expect(reduce(p, { type: "toggleStep", voice: "kick", step: 16 })).toBe(p);
  });

  it("sets tempo, time signature, loop, metronome, master gain, name and tonic", () => {
    const p = run(
      newProject(),
      { type: "setTempo", tempo: 96 },
      { type: "setTimeSignature", timeSignature: [7, 8] },
      { type: "setLoop", loop: { enabled: true, startSec: 6, endSec: 2 } },
      { type: "setMetronome", on: true },
      { type: "setMasterGain", gain: 0.5 },
      { type: "setName", name: "  Alapana remix " },
      { type: "setTonic", tonicHz: 146.83 },
    );
    expect(p).toMatchObject({ tempo: 96, timeSignature: [7, 8], metronome: true, masterGain: 0.5, name: "Alapana remix", tonicHz: 146.83 });
    expect(p.loop).toEqual({ enabled: true, startSec: 2, endSec: 6 });
    expect(reduce(p, { type: "setTempo", tempo: 5000 }).tempo).toBe(300);
    expect(reduce(p, { type: "setLoop", loop: { startSec: 3, endSec: 3 } }).loop.enabled).toBe(false);
  });

  it("applies a batch as one change", () => {
    const p = reduce(newProject(), { type: "batch", actions: [{ type: "addTrack", kind: "audio", id: "a1" }, { type: "addClip", clip: audioClip() }] });
    expect(p.tracks).toHaveLength(1);
    expect(p.clips).toHaveLength(1);
  });
});

describe("snapping", () => {
  it("snaps to the grid at 120 BPM", () => {
    // 120 BPM: a beat is 0.5 s, a sixteenth is 0.125 s.
    expect(snapSec(1.03, 120, 4)).toBe(1);
    expect(snapSec(1.07, 120, 4)).toBe(1.125);
    expect(snapSec(0.74, 120, 1)).toBe(0.5);
    expect(snapSec(0.76, 120, 1)).toBe(1);
    expect(snapSec(2.9, 120, 0.25)).toBe(2); // whole bars in 4/4
    expect(snapSec(-0.4, 120, 4)).toBe(0);
    expect(snapSec(0.3, 120, 8)).toBe(0.3125);
  });

  it("follows the tempo", () => {
    expect(snapSec(0.7, 90, 1)).toBeCloseTo(2 / 3, 5);
  });
});

describe("soloClipProject", () => {
  it("keeps one clip on an audible track", () => {
    const p = reduce(base(), { type: "setTrackMute", trackId: "k1", mute: true });
    const solo = soloClipProject(p, "m1")!;
    expect(solo.clips.map((c) => c.id)).toEqual(["m1"]);
    expect(solo.tracks).toHaveLength(1);
    expect(effectiveGain(solo.tracks[0], solo.tracks)).toBe(1);
    expect(solo.masterGain).toBe(1);
    expect(solo.drums).toBeNull();
    expect(soloClipProject(p, "missing")).toBeNull();
  });
});

describe("ProjectStore", () => {
  it("previews a drag from where it began and records it once", () => {
    const store = new ProjectStore(base());
    const seen: string[] = [];
    store.onAction((a) => seen.push(a.type));
    store.begin();
    // Each preview is computed from the state at the start of the drag, so dragging a
    // MIDI clip's left edge in and back out again loses nothing.
    store.dispatch({ type: "trimClipStart", clipId: "m1", startSec: 4 }, { transient: true });
    expect(midi(store.project).notes).toHaveLength(2);
    store.dispatch({ type: "trimClipStart", clipId: "m1", startSec: 2.5 }, { transient: true });
    expect(midi(store.project).notes).toHaveLength(3);
    expect(store.getSnapshot().past).toHaveLength(0);
    expect(store.gesture).toBe(true);
    store.commit({ type: "trimClipStart", clipId: "m1", startSec: 2.5 });
    expect(store.getSnapshot().past).toHaveLength(1);
    expect(seen).toEqual(["trimClipStart"]);
    store.undo();
    expect(midi(store.project).startSec).toBe(2);
    store.redo();
    expect(midi(store.project).startSec).toBe(2.5);
  });

  it("notifies subscribers and resets history on load", () => {
    const store = new ProjectStore();
    let calls = 0;
    const off = store.subscribe(() => calls++);
    store.dispatch({ type: "setTempo", tempo: 100 });
    store.dispatch({ type: "setTempo", tempo: 100 }); // no change, no call
    expect(calls).toBe(1);
    store.load(newProject("Other"));
    expect(store.getSnapshot().past).toEqual([]);
    expect(store.project.name).toBe("Other");
    off();
    store.dispatch({ type: "setTempo", tempo: 90 });
    expect(calls).toBe(2);
  });
});
