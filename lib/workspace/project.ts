// The Workspace project: one versioned JSON document (spec 12.7). The server stores it
// as an opaque blob; ``migrate`` is the only way a stored project gets back into the app.
// Positions are in seconds. Assets are referenced by id only.
import { secPerBar, type TimeSignature } from "./time";

export const PROJECT_VERSION = 1;

export type TrackKind = "audio" | "midi" | "drums";
export type Track = {
  id: string;
  name: string;
  kind: TrackKind;
  gain: number; // 0..1.5
  pan: number; // -1..1
  mute: boolean;
  solo: boolean;
};

export type AudioClip = {
  id: string;
  trackId: string;
  kind: "audio";
  assetId: string;
  name: string;
  startSec: number; // where the clip sits on the timeline
  offsetSec: number; // where in the asset the clip begins
  lengthSec: number;
  gain: number;
  fadeInSec: number;
  fadeOutSec: number;
  /** Length of the whole asset when known. Limits how far the right edge can be pulled out. */
  assetDurationSec?: number;
};

export type MidiNote = {
  id: string;
  midi: number; // nearest semitone, A4 = 440
  detuneCents: number; // the microtone: what is left after rounding to the semitone
  startSec: number; // from the start of the clip
  durSec: number;
  velocity: number; // 0..1
};

export type MidiClip = {
  id: string;
  trackId: string;
  kind: "midi";
  name: string;
  startSec: number;
  lengthSec: number;
  tonicHz: number | null;
  notes: MidiNote[];
  conversionId?: string;
};

export type Clip = AudioClip | MidiClip;

export const DRUM_VOICES = ["kick", "snare", "hat", "clap"] as const;
export type DrumVoice = (typeof DRUM_VOICES)[number];
export type DrumRow = { voice: DrumVoice; steps: boolean[]; gain: number; pan: number };
export type Drums = { steps: number; rows: DrumRow[]; trackId: string };

export type Loop = { enabled: boolean; startSec: number; endSec: number };

export type Project = {
  version: number;
  name: string;
  tempo: number;
  timeSignature: TimeSignature;
  tonicHz: number | null;
  loop: Loop;
  metronome: boolean;
  masterGain: number;
  tracks: Track[];
  clips: Clip[];
  drums: Drums | null;
  createdAt: string;
  updatedAt: string;
};

export const MIN_CLIP_SEC = 0.02;
export const MIN_NOTE_SEC = 0.02;
export const DRUM_STEPS = 16;

let counter = 0;
/** A short unique id. Not stable across runs; only has to be unique inside one project. */
export function uid(prefix: string): string {
  counter = (counter + 1) % 1679616;
  const rand = Math.random().toString(36).slice(2, 7);
  return `${prefix}_${Date.now().toString(36)}${counter.toString(36)}${rand}`;
}

export function newProject(name = "Untitled project"): Project {
  const now = new Date().toISOString();
  return {
    version: PROJECT_VERSION,
    name,
    tempo: 120,
    timeSignature: [4, 4],
    tonicHz: null,
    loop: { enabled: false, startSec: 0, endSec: 8 },
    metronome: false,
    masterGain: 0.8,
    tracks: [],
    clips: [],
    drums: null,
    createdAt: now,
    updatedAt: now,
  };
}

export function newTrack(kind: TrackKind, name: string, id = uid("trk")): Track {
  return { id, name, kind, gain: 0.8, pan: 0, mute: false, solo: false };
}

export function newDrums(trackId: string): Drums {
  return {
    steps: DRUM_STEPS,
    trackId,
    rows: DRUM_VOICES.map((voice) => ({ voice, steps: Array<boolean>(DRUM_STEPS).fill(false), gain: 0.8, pan: 0 })),
  };
}

// --- selectors ---------------------------------------------------------------

export const clipEnd = (clip: Pick<Clip, "startSec" | "lengthSec">) => clip.startSec + clip.lengthSec;
export const findClip = (p: Project, clipId: string | null | undefined) =>
  clipId ? p.clips.find((c) => c.id === clipId) : undefined;
export const findTrack = (p: Project, trackId: string | null | undefined) =>
  trackId ? p.tracks.find((t) => t.id === trackId) : undefined;
export const drumsActive = (p: Project) => !!p.drums && p.drums.rows.some((r) => r.steps.some(Boolean));

/** The gain a track actually plays at: 0 when muted, and 0 when another track is soloed. */
export function effectiveGain(track: Track, tracks: readonly Track[]): number {
  if (track.mute) return 0;
  if (tracks.some((t) => t.solo) && !track.solo) return 0;
  return track.gain;
}

/** Where the music ends. A drum pattern with nothing else gives four bars. */
export function projectEndSec(p: Project): number {
  const clipsEnd = p.clips.reduce((m, c) => Math.max(m, clipEnd(c)), 0);
  if (!drumsActive(p)) return clipsEnd;
  const bar = secPerBar(p.tempo, p.timeSignature);
  const bars = clipsEnd > 0 ? Math.ceil(clipsEnd / bar - 1e-6) : 4;
  return bars * bar;
}

// --- migration ---------------------------------------------------------------

type Raw = Record<string, unknown>;
const isObj = (v: unknown): v is Raw => typeof v === "object" && v !== null && !Array.isArray(v);
const num = (v: unknown, fallback: number, min = -Infinity, max = Infinity) =>
  typeof v === "number" && Number.isFinite(v) ? Math.min(max, Math.max(min, v)) : fallback;
const str = (v: unknown, fallback: string) => (typeof v === "string" && v.length > 0 ? v : fallback);
const bool = (v: unknown, fallback: boolean) => (typeof v === "boolean" ? v : fallback);
const arr = (v: unknown): unknown[] => (Array.isArray(v) ? v : []);

/** Copy ``from`` to ``to`` when only the old name is present. */
function rename(o: Raw, from: string, to: string) {
  if (o[to] === undefined && o[from] !== undefined) o[to] = o[from];
}

/** Version 0 (the first prototype) used shorter names: bpm, volume, start/offset/length, pitch. */
function upgradeV0(raw: Raw): Raw {
  const p: Raw = { ...raw };
  rename(p, "bpm", "tempo");
  rename(p, "signature", "timeSignature");
  rename(p, "tonic", "tonicHz");
  rename(p, "volume", "masterGain");
  p.tracks = arr(p.tracks).map((t) => {
    if (!isObj(t)) return t;
    const track: Raw = { ...t };
    rename(track, "volume", "gain");
    rename(track, "type", "kind");
    return track;
  });
  p.clips = arr(p.clips).map((c) => {
    if (!isObj(c)) return c;
    const clip: Raw = { ...c };
    rename(clip, "type", "kind");
    rename(clip, "track", "trackId");
    rename(clip, "asset", "assetId");
    rename(clip, "start", "startSec");
    rename(clip, "offset", "offsetSec");
    rename(clip, "length", "lengthSec");
    rename(clip, "fadeIn", "fadeInSec");
    rename(clip, "fadeOut", "fadeOutSec");
    if (clip.kind === undefined) clip.kind = clip.assetId !== undefined ? "audio" : "midi";
    clip.notes = arr(clip.notes).map((n) => {
      if (!isObj(n)) return n;
      const note: Raw = { ...n };
      rename(note, "pitch", "midi");
      rename(note, "cents", "detuneCents");
      rename(note, "start", "startSec");
      rename(note, "duration", "durSec");
      // v0 velocities were MIDI values (1..127)
      if (typeof note.velocity === "number" && note.velocity > 1) note.velocity = note.velocity / 127;
      return note;
    });
    return clip;
  });
  p.version = 1;
  return p;
}

function repairNote(raw: unknown, seen: Set<string>): MidiNote | null {
  if (!isObj(raw)) return null;
  const midi = Math.round(num(raw.midi, NaN, 0, 127));
  if (!Number.isFinite(midi)) return null;
  return {
    id: freshId(raw.id, "n", seen),
    midi,
    detuneCents: num(raw.detuneCents, 0, -1200, 1200),
    startSec: num(raw.startSec, 0, 0),
    durSec: num(raw.durSec, 0.25, MIN_NOTE_SEC),
    velocity: num(raw.velocity, 0.7, 0, 1),
  };
}

function freshId(v: unknown, prefix: string, seen: Set<string>): string {
  let id = typeof v === "string" && v ? v : uid(prefix);
  while (seen.has(id)) id = uid(prefix);
  seen.add(id);
  return id;
}

/**
 * Turn anything that was once saved as a project into a valid current one: upgrade old
 * versions, fill what is missing, and drop what cannot be repaired (a clip whose track
 * is gone). Never throws.
 */
export function migrate(json: unknown): Project {
  const base = newProject();
  if (!isObj(json)) return base;
  let raw: Raw = json;
  const version = num(raw.version, 0);
  if (version < 1) raw = upgradeV0(raw);
  // A newer version than this build knows: keep every field we understand.

  const seen = new Set<string>();
  const tracks: Track[] = [];
  for (const t of arr(raw.tracks)) {
    if (!isObj(t)) continue;
    const kind: TrackKind = t.kind === "midi" || t.kind === "drums" ? t.kind : "audio";
    tracks.push({
      id: freshId(t.id, "trk", seen),
      name: str(t.name, kind === "audio" ? "Audio" : kind === "midi" ? "MIDI" : "Drums"),
      kind,
      gain: num(t.gain, 0.8, 0, 2),
      pan: num(t.pan, 0, -1, 1),
      mute: bool(t.mute, false),
      solo: bool(t.solo, false),
    });
  }
  const trackKind = new Map(tracks.map((t) => [t.id, t.kind]));

  const clips: Clip[] = [];
  for (const c of arr(raw.clips)) {
    if (!isObj(c) || typeof c.trackId !== "string") continue;
    const kind = c.kind === "midi" ? "midi" : c.kind === "audio" ? "audio" : null;
    if (!kind || trackKind.get(c.trackId) !== kind) continue;
    const startSec = num(c.startSec, 0, 0);
    if (kind === "audio") {
      if (typeof c.assetId !== "string" || !c.assetId) continue;
      const lengthSec = num(c.lengthSec, 1, MIN_CLIP_SEC);
      const clip: AudioClip = {
        id: freshId(c.id, "clip", seen),
        trackId: c.trackId,
        kind: "audio",
        assetId: c.assetId,
        name: str(c.name, "Audio"),
        startSec,
        offsetSec: num(c.offsetSec, 0, 0),
        lengthSec,
        gain: num(c.gain, 1, 0, 4),
        fadeInSec: num(c.fadeInSec, 0, 0, lengthSec),
        fadeOutSec: num(c.fadeOutSec, 0, 0, lengthSec),
      };
      if (typeof c.assetDurationSec === "number" && c.assetDurationSec > 0) clip.assetDurationSec = c.assetDurationSec;
      clips.push(clip);
    } else {
      const noteIds = new Set<string>();
      const notes = arr(c.notes)
        .map((n) => repairNote(n, noteIds))
        .filter((n): n is MidiNote => n !== null);
      const notesEnd = notes.reduce((m, n) => Math.max(m, n.startSec + n.durSec), 0);
      const clip: MidiClip = {
        id: freshId(c.id, "clip", seen),
        trackId: c.trackId,
        kind: "midi",
        name: str(c.name, "MIDI"),
        startSec,
        lengthSec: num(c.lengthSec, Math.max(1, notesEnd), MIN_CLIP_SEC),
        tonicHz: typeof c.tonicHz === "number" && c.tonicHz > 0 ? c.tonicHz : null,
        notes,
      };
      if (typeof c.conversionId === "string" && c.conversionId) clip.conversionId = c.conversionId;
      clips.push(clip);
    }
  }

  let drums: Drums | null = null;
  if (isObj(raw.drums)) {
    const drumTrack = tracks.find((t) => t.id === (raw.drums as Raw).trackId && t.kind === "drums") ??
      tracks.find((t) => t.kind === "drums");
    if (drumTrack) {
      const rawRows = arr((raw.drums as Raw).rows).filter(isObj);
      drums = newDrums(drumTrack.id);
      drums.rows = drums.rows.map((row) => {
        const saved = rawRows.find((r) => r.voice === row.voice);
        if (!saved) return row;
        const steps = arr(saved.steps);
        return {
          voice: row.voice,
          steps: row.steps.map((_, i) => steps[i] === true),
          gain: num(saved.gain, 0.8, 0, 2),
          pan: num(saved.pan, 0, -1, 1),
        };
      });
    }
  }
  // One drum pattern means one drum track; a drum track without a pattern gets an empty one.
  const firstDrumTrack = tracks.find((t) => t.kind === "drums");
  if (!drums && firstDrumTrack) drums = newDrums(firstDrumTrack.id);
  const keptTracks = tracks.filter((t) => t.kind !== "drums" || t.id === drums?.trackId);

  const ts = arr(raw.timeSignature);
  const numerator = Math.round(num(ts[0], 4, 1, 16));
  const denominator = [2, 4, 8, 16].includes(ts[1] as number) ? (ts[1] as number) : 4;
  const loopRaw = isObj(raw.loop) ? raw.loop : {};
  const loopStart = num(loopRaw.startSec, 0, 0);
  const loopEnd = num(loopRaw.endSec, loopStart + 8, 0);

  return {
    version: PROJECT_VERSION,
    name: str(raw.name, base.name),
    tempo: num(raw.tempo, 120, 20, 300),
    timeSignature: [numerator, denominator],
    tonicHz: typeof raw.tonicHz === "number" && raw.tonicHz > 0 ? raw.tonicHz : null,
    loop: {
      enabled: bool(loopRaw.enabled, false) && loopEnd > loopStart,
      startSec: loopStart,
      endSec: loopEnd > loopStart ? loopEnd : loopStart + 8,
    },
    metronome: bool(raw.metronome, false),
    masterGain: num(raw.masterGain, 0.8, 0, 1.5),
    tracks: keptTracks,
    clips,
    drums,
    createdAt: str(raw.createdAt, base.createdAt),
    updatedAt: str(raw.updatedAt, base.updatedAt),
  };
}
