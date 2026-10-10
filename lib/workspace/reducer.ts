// Every edit the Workspace can make, as a pure function of (project, action). Untouched
// parts of the project keep their identity, so the audio engine can tell a mixer change
// (no rescheduling) from a clip change (reschedule) by comparing references.
import {
  DRUM_STEPS,
  MIN_CLIP_SEC,
  MIN_NOTE_SEC,
  newDrums,
  newTrack,
  uid,
  type AudioClip,
  type Clip,
  type DrumRow,
  type DrumVoice,
  type Loop,
  type MidiClip,
  type MidiNote,
  type Project,
  type TrackKind,
} from "./project";
import type { TimeSignature } from "./time";

export { snapSec, gridStepSec } from "./time";

export type Action =
  | { type: "batch"; actions: Action[] }
  | { type: "setName"; name: string }
  | { type: "setTempo"; tempo: number }
  | { type: "setTimeSignature"; timeSignature: TimeSignature }
  | { type: "setLoop"; loop: Partial<Loop> }
  | { type: "setMetronome"; on: boolean }
  | { type: "setMasterGain"; gain: number }
  | { type: "setTonic"; tonicHz: number | null }
  | { type: "addTrack"; kind: TrackKind; id?: string; name?: string }
  | { type: "removeTrack"; trackId: string }
  | { type: "renameTrack"; trackId: string; name: string }
  | { type: "setTrackGain"; trackId: string; gain: number }
  | { type: "setTrackPan"; trackId: string; pan: number }
  | { type: "setTrackMute"; trackId: string; mute: boolean }
  | { type: "setTrackSolo"; trackId: string; solo: boolean }
  | { type: "addClip"; clip: Clip }
  | { type: "moveClip"; clipId: string; startSec: number; trackId?: string }
  | { type: "trimClipStart"; clipId: string; startSec: number }
  | { type: "trimClipEnd"; clipId: string; endSec: number }
  | { type: "splitClip"; clipId: string; atSec: number; newId?: string }
  | { type: "duplicateClip"; clipId: string; newId?: string; startSec?: number }
  | { type: "deleteClip"; clipId: string }
  | { type: "renameClip"; clipId: string; name: string }
  | { type: "setClipGain"; clipId: string; gain: number }
  | { type: "setClipFades"; clipId: string; fadeInSec?: number; fadeOutSec?: number }
  | { type: "addNote"; clipId: string; note: MidiNote }
  | { type: "moveNote"; clipId: string; noteId: string; startSec: number; midi: number }
  | { type: "resizeNote"; clipId: string; noteId: string; durSec: number }
  | { type: "deleteNote"; clipId: string; noteId: string }
  | { type: "setNoteDetune"; clipId: string; noteId: string; detuneCents: number }
  | { type: "setNoteVelocity"; clipId: string; noteId: string; velocity: number }
  | { type: "toggleStep"; voice: DrumVoice; step: number }
  | { type: "setDrumRowGain"; voice: DrumVoice; gain: number }
  | { type: "setDrumRowPan"; voice: DrumVoice; pan: number };

export type ActionType = Action["type"];

const clamp = (v: number, min: number, max: number) => Math.min(max, Math.max(min, v));
const finite = (v: number, fallback: number) => (Number.isFinite(v) ? v : fallback);

function mapTrack(p: Project, trackId: string, patch: Partial<Project["tracks"][number]>): Project {
  if (!p.tracks.some((t) => t.id === trackId)) return p;
  return { ...p, tracks: p.tracks.map((t) => (t.id === trackId ? { ...t, ...patch } : t)) };
}

function mapClip(p: Project, clipId: string, fn: (clip: Clip) => Clip): Project {
  let changed = false;
  const clips = p.clips.map((c) => {
    if (c.id !== clipId) return c;
    const next = fn(c);
    if (next !== c) changed = true;
    return next;
  });
  return changed ? { ...p, clips } : p;
}

function mapNotes(p: Project, clipId: string, fn: (notes: MidiNote[], clip: MidiClip) => MidiNote[]): Project {
  return mapClip(p, clipId, (c) => {
    if (c.kind !== "midi") return c;
    const notes = fn(c.notes, c);
    if (notes === c.notes) return c;
    // A note drawn past the end makes the clip longer; it never makes it shorter.
    const end = notes.reduce((m, n) => Math.max(m, n.startSec + n.durSec), 0);
    return { ...c, notes, lengthSec: Math.max(c.lengthSec, end) };
  });
}

function mapNote(p: Project, clipId: string, noteId: string, fn: (n: MidiNote) => MidiNote): Project {
  return mapNotes(p, clipId, (notes) => {
    if (!notes.some((n) => n.id === noteId)) return notes;
    return notes.map((n) => (n.id === noteId ? fn(n) : n));
  });
}

function mapDrumRow(p: Project, voice: DrumVoice, fn: (row: DrumRow) => DrumRow): Project {
  if (!p.drums) return p;
  return { ...p, drums: { ...p.drums, rows: p.drums.rows.map((r) => (r.voice === voice ? fn(r) : r)) } };
}

function clampFades<T extends AudioClip>(clip: T): T {
  const fadeInSec = clamp(clip.fadeInSec, 0, clip.lengthSec);
  const fadeOutSec = clamp(clip.fadeOutSec, 0, clip.lengthSec - fadeInSec);
  return fadeInSec === clip.fadeInSec && fadeOutSec === clip.fadeOutSec ? clip : { ...clip, fadeInSec, fadeOutSec };
}

/** Move the left edge to ``startSec``. The audio under the clip stays where it is on the timeline. */
function trimStart(clip: Clip, startSec: number): Clip {
  const maxDelta = clip.lengthSec - MIN_CLIP_SEC;
  if (clip.kind === "audio") {
    // Can't reveal audio before the start of the asset, and can't cross the right edge.
    const delta = clamp(startSec - clip.startSec, -Math.min(clip.offsetSec, clip.startSec), maxDelta);
    if (delta === 0) return clip;
    return clampFades({
      ...clip,
      startSec: clip.startSec + delta,
      offsetSec: clip.offsetSec + delta,
      lengthSec: clip.lengthSec - delta,
    });
  }
  const delta = clamp(startSec - clip.startSec, -clip.startSec, maxDelta);
  if (delta === 0) return clip;
  // Notes keep their place on the timeline. Trimming in drops what falls off the left edge.
  const notes: MidiNote[] = [];
  for (const n of clip.notes) {
    const start = n.startSec - delta;
    const end = start + n.durSec;
    if (end <= MIN_NOTE_SEC) continue;
    notes.push(start < 0 ? { ...n, startSec: 0, durSec: end } : { ...n, startSec: start });
  }
  return { ...clip, startSec: clip.startSec + delta, lengthSec: clip.lengthSec - delta, notes };
}

/** Move the right edge to ``endSec``. MIDI notes past the edge are kept but not played. */
function trimEnd(clip: Clip, endSec: number): Clip {
  let lengthSec = Math.max(MIN_CLIP_SEC, endSec - clip.startSec);
  if (clip.kind === "audio" && clip.assetDurationSec !== undefined) {
    lengthSec = Math.min(lengthSec, Math.max(MIN_CLIP_SEC, clip.assetDurationSec - clip.offsetSec));
  }
  if (lengthSec === clip.lengthSec) return clip;
  return clip.kind === "audio" ? clampFades({ ...clip, lengthSec }) : { ...clip, lengthSec };
}

/** Cut one clip into two at a timeline position. Returns null when the cut misses the clip. */
export function splitClipAt(clip: Clip, atSec: number, newId = uid("clip")): [Clip, Clip] | null {
  const local = atSec - clip.startSec;
  if (local < MIN_CLIP_SEC || local > clip.lengthSec - MIN_CLIP_SEC) return null;
  if (clip.kind === "audio") {
    const left = clampFades({ ...clip, lengthSec: local, fadeOutSec: 0 });
    const right = clampFades({
      ...clip,
      id: newId,
      startSec: atSec,
      offsetSec: clip.offsetSec + local,
      lengthSec: clip.lengthSec - local,
      fadeInSec: 0,
    });
    return [left, right];
  }
  const leftNotes: MidiNote[] = [];
  const rightNotes: MidiNote[] = [];
  for (const n of clip.notes) {
    if (n.startSec < local) {
      // A note held across the cut ends at the cut.
      leftNotes.push(n.startSec + n.durSec > local ? { ...n, durSec: local - n.startSec } : n);
    } else {
      rightNotes.push({ ...n, id: uid("n"), startSec: n.startSec - local });
    }
  }
  return [
    { ...clip, lengthSec: local, notes: leftNotes },
    { ...clip, id: newId, startSec: atSec, lengthSec: clip.lengthSec - local, notes: rightNotes },
  ];
}

const DEFAULT_NAMES: Record<TrackKind, string> = { audio: "Audio", midi: "MIDI", drums: "Drums" };

export function reduce(p: Project, a: Action): Project {
  switch (a.type) {
    case "batch":
      return a.actions.reduce(reduce, p);

    case "setName": {
      const name = a.name.trim().slice(0, 120);
      return name && name !== p.name ? { ...p, name } : p;
    }
    case "setTempo": {
      const tempo = clamp(finite(a.tempo, p.tempo), 20, 300);
      return tempo === p.tempo ? p : { ...p, tempo };
    }
    case "setTimeSignature": {
      const n = clamp(Math.round(finite(a.timeSignature[0], 4)), 1, 16);
      const d = [2, 4, 8, 16].includes(a.timeSignature[1]) ? a.timeSignature[1] : 4;
      return n === p.timeSignature[0] && d === p.timeSignature[1] ? p : { ...p, timeSignature: [n, d] };
    }
    case "setLoop": {
      const next = { ...p.loop, ...a.loop };
      next.startSec = Math.max(0, finite(next.startSec, 0));
      next.endSec = Math.max(0, finite(next.endSec, next.startSec));
      if (next.endSec < next.startSec) [next.startSec, next.endSec] = [next.endSec, next.startSec];
      if (next.endSec - next.startSec < MIN_CLIP_SEC) next.enabled = false;
      if (next.enabled === p.loop.enabled && next.startSec === p.loop.startSec && next.endSec === p.loop.endSec) return p;
      return { ...p, loop: next };
    }
    case "setMetronome":
      return a.on === p.metronome ? p : { ...p, metronome: a.on };
    case "setMasterGain": {
      const masterGain = clamp(finite(a.gain, p.masterGain), 0, 1.5);
      return masterGain === p.masterGain ? p : { ...p, masterGain };
    }
    case "setTonic": {
      const tonicHz = a.tonicHz !== null && Number.isFinite(a.tonicHz) && a.tonicHz > 0 ? a.tonicHz : null;
      return tonicHz === p.tonicHz ? p : { ...p, tonicHz };
    }

    case "addTrack": {
      // One drum pattern, so one drum track.
      if (a.kind === "drums" && p.tracks.some((t) => t.kind === "drums")) return p;
      if (a.id && p.tracks.some((t) => t.id === a.id)) return p;
      const count = p.tracks.filter((t) => t.kind === a.kind).length;
      const name = a.name?.trim() || (a.kind === "drums" ? "Drums" : `${DEFAULT_NAMES[a.kind]} ${count + 1}`);
      const track = newTrack(a.kind, name, a.id);
      return {
        ...p,
        tracks: [...p.tracks, track],
        drums: a.kind === "drums" ? newDrums(track.id) : p.drums,
      };
    }
    case "removeTrack": {
      if (!p.tracks.some((t) => t.id === a.trackId)) return p;
      const hasClips = p.clips.some((c) => c.trackId === a.trackId);
      return {
        ...p,
        tracks: p.tracks.filter((t) => t.id !== a.trackId),
        clips: hasClips ? p.clips.filter((c) => c.trackId !== a.trackId) : p.clips,
        drums: p.drums?.trackId === a.trackId ? null : p.drums,
      };
    }
    case "renameTrack": {
      const name = a.name.slice(0, 60);
      return name ? mapTrack(p, a.trackId, { name }) : p;
    }
    case "setTrackGain":
      return mapTrack(p, a.trackId, { gain: clamp(finite(a.gain, 0.8), 0, 1.5) });
    case "setTrackPan":
      return mapTrack(p, a.trackId, { pan: clamp(finite(a.pan, 0), -1, 1) });
    case "setTrackMute":
      return mapTrack(p, a.trackId, { mute: a.mute });
    case "setTrackSolo":
      return mapTrack(p, a.trackId, { solo: a.solo });

    case "addClip": {
      const track = p.tracks.find((t) => t.id === a.clip.trackId);
      if (!track || track.kind !== a.clip.kind || p.clips.some((c) => c.id === a.clip.id)) return p;
      const clip = { ...a.clip, startSec: Math.max(0, a.clip.startSec), lengthSec: Math.max(MIN_CLIP_SEC, a.clip.lengthSec) };
      return { ...p, clips: [...p.clips, clip] };
    }
    case "moveClip":
      return mapClip(p, a.clipId, (c) => {
        const startSec = Math.max(0, finite(a.startSec, c.startSec));
        const target = a.trackId ? p.tracks.find((t) => t.id === a.trackId) : undefined;
        // A clip only moves to a track of its own kind.
        const trackId = target && target.kind === c.kind ? target.id : c.trackId;
        return startSec === c.startSec && trackId === c.trackId ? c : { ...c, startSec, trackId };
      });
    case "trimClipStart":
      return mapClip(p, a.clipId, (c) => trimStart(c, a.startSec));
    case "trimClipEnd":
      return mapClip(p, a.clipId, (c) => trimEnd(c, a.endSec));
    case "splitClip": {
      const index = p.clips.findIndex((c) => c.id === a.clipId);
      if (index < 0) return p;
      const parts = splitClipAt(p.clips[index], a.atSec, a.newId);
      if (!parts) return p;
      const clips = p.clips.slice();
      clips.splice(index, 1, ...parts);
      return { ...p, clips };
    }
    case "duplicateClip": {
      const clip = p.clips.find((c) => c.id === a.clipId);
      if (!clip) return p;
      const copy: Clip = { ...clip, id: a.newId ?? uid("clip"), startSec: Math.max(0, a.startSec ?? clip.startSec + clip.lengthSec) };
      if (copy.kind === "midi") copy.notes = copy.notes.map((n) => ({ ...n, id: uid("n") }));
      return { ...p, clips: [...p.clips, copy] };
    }
    case "deleteClip":
      return p.clips.some((c) => c.id === a.clipId) ? { ...p, clips: p.clips.filter((c) => c.id !== a.clipId) } : p;
    case "renameClip": {
      const name = a.name.slice(0, 80);
      return name ? mapClip(p, a.clipId, (c) => (c.name === name ? c : { ...c, name })) : p;
    }
    case "setClipGain":
      return mapClip(p, a.clipId, (c) => (c.kind === "audio" ? { ...c, gain: clamp(finite(a.gain, 1), 0, 4) } : c));
    case "setClipFades":
      return mapClip(p, a.clipId, (c) => {
        if (c.kind !== "audio") return c;
        // The fade being edited wins; the other one gives way if they would overlap.
        const fadeInSec = clamp(finite(a.fadeInSec ?? c.fadeInSec, 0), 0, c.lengthSec);
        const fadeOutSec = clamp(finite(a.fadeOutSec ?? c.fadeOutSec, 0), 0, c.lengthSec);
        if (a.fadeOutSec !== undefined && a.fadeInSec === undefined) {
          return { ...c, fadeOutSec, fadeInSec: Math.min(fadeInSec, c.lengthSec - fadeOutSec) };
        }
        return { ...c, fadeInSec, fadeOutSec: Math.min(fadeOutSec, c.lengthSec - fadeInSec) };
      });

    case "addNote":
      return mapNotes(p, a.clipId, (notes) => {
        if (notes.some((n) => n.id === a.note.id)) return notes;
        return [
          ...notes,
          {
            ...a.note,
            midi: clamp(Math.round(a.note.midi), 0, 127),
            startSec: Math.max(0, a.note.startSec),
            durSec: Math.max(MIN_NOTE_SEC, a.note.durSec),
            velocity: clamp(a.note.velocity, 0, 1),
          },
        ];
      });
    case "moveNote":
      return mapNote(p, a.clipId, a.noteId, (n) => ({
        ...n,
        startSec: Math.max(0, finite(a.startSec, n.startSec)),
        midi: clamp(Math.round(finite(a.midi, n.midi)), 0, 127),
      }));
    case "resizeNote":
      return mapNote(p, a.clipId, a.noteId, (n) => ({ ...n, durSec: Math.max(MIN_NOTE_SEC, finite(a.durSec, n.durSec)) }));
    case "deleteNote":
      return mapNotes(p, a.clipId, (notes) =>
        notes.some((n) => n.id === a.noteId) ? notes.filter((n) => n.id !== a.noteId) : notes,
      );
    case "setNoteDetune":
      return mapNote(p, a.clipId, a.noteId, (n) => ({ ...n, detuneCents: clamp(finite(a.detuneCents, 0), -100, 100) }));
    case "setNoteVelocity":
      return mapNote(p, a.clipId, a.noteId, (n) => ({ ...n, velocity: clamp(finite(a.velocity, 0.7), 0.05, 1) }));

    case "toggleStep":
      if (a.step < 0 || a.step >= (p.drums?.steps ?? DRUM_STEPS)) return p;
      return mapDrumRow(p, a.voice, (r) => ({ ...r, steps: r.steps.map((on, i) => (i === a.step ? !on : on)) }));
    case "setDrumRowGain":
      return mapDrumRow(p, a.voice, (r) => ({ ...r, gain: clamp(finite(a.gain, 0.8), 0, 1.5) }));
    case "setDrumRowPan":
      return mapDrumRow(p, a.voice, (r) => ({ ...r, pan: clamp(finite(a.pan, 0), -1, 1) }));
  }
}

/**
 * A project that plays one clip alone, for bouncing a selection (spec 12.5). The mixer is
 * set flat (full level, centred, not muted), so the bounce is the clip itself with its own
 * gain and fades, whatever the track was doing.
 */
export function soloClipProject(p: Project, clipId: string): Project | null {
  const clip = p.clips.find((c) => c.id === clipId);
  const track = clip && p.tracks.find((t) => t.id === clip.trackId);
  if (!clip || !track) return null;
  return {
    ...p,
    masterGain: 1,
    tracks: [{ ...track, gain: 1, pan: 0, mute: false, solo: false }],
    clips: [clip],
    drums: null,
    metronome: false,
  };
}
