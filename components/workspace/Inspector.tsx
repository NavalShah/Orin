"use client";

import type { Clip, MidiNote, Project } from "@/lib/workspace/project";
import type { Action } from "@/lib/workspace/reducer";
import type { DispatchOptions } from "@/lib/workspace/store";
import { formatBarsBeats, midiName, signedCents } from "@/lib/workspace/time";
import { NumberField, percent, Slider, TextField } from "./fields";
import s from "./workspace.module.css";

type Props = {
  project: Project;
  clip: Clip | undefined;
  note: MidiNote | undefined;
  dispatch: (action: Action, opts?: DispatchOptions) => void;
  onTranscribe: () => void;
  onExportMidi: () => void;
  onOpenPianoRoll: () => void;
  busy: boolean;
};

/** Properties of the selected clip, and of the selected note in a MIDI clip (spec 12.2). */
export function Inspector({ project, clip, note, dispatch, busy, ...on }: Props) {
  if (!clip) {
    return (
      <aside className={s.inspector} aria-label="Inspector">
        <h2>Inspector</h2>
        <p className={s.hint}>Select a clip to see and change its name, position, gain and fades.</p>
        <div className={s.inspectorActions}>
          <button
            type="button"
            className="o-btn"
            onClick={on.onTranscribe}
            disabled={busy || !project.loop.enabled}
            title={project.loop.enabled ? undefined : "Select a clip, or switch the loop on to transcribe the loop region"}
          >
            Transcribe loop region
          </button>
        </div>
      </aside>
    );
  }

  const track = project.tracks.find((t) => t.id === clip.trackId);
  return (
    <aside className={s.inspector} aria-label="Inspector">
      <h2>Inspector</h2>
      <p className={s.inspectorKind}>
        <span className="o-badge">{clip.kind === "audio" ? "Audio clip" : "MIDI clip"}</span>
        <span className="o-muted"> on {track?.name ?? "a missing track"}</span>
      </p>
      <TextField label="Name" value={clip.name} onCommit={(name) => dispatch({ type: "renameClip", clipId: clip.id, name })} />
      <NumberField
        label="Start (seconds)"
        value={clip.startSec}
        min={0}
        step={0.1}
        digits={3}
        suffix={`bar ${formatBarsBeats(clip.startSec, project.tempo, project.timeSignature)}`}
        onCommit={(v) => dispatch({ type: "moveClip", clipId: clip.id, startSec: v })}
      />
      <NumberField
        label="Length (seconds)"
        value={clip.lengthSec}
        min={0.02}
        step={0.1}
        digits={3}
        onCommit={(v) => dispatch({ type: "trimClipEnd", clipId: clip.id, endSec: clip.startSec + v })}
      />

      {clip.kind === "audio" ? (
        <>
          <Slider
            label="Gain"
            min={0}
            max={2}
            step={0.01}
            value={clip.gain}
            valueText={percent(clip.gain)}
            showValue
            onChange={(v) => dispatch({ type: "setClipGain", clipId: clip.id, gain: v }, { coalesce: `clipgain:${clip.id}` })}
          />
          <NumberField
            label="Fade in (seconds)"
            value={clip.fadeInSec}
            min={0}
            max={clip.lengthSec}
            step={0.05}
            onCommit={(v) => dispatch({ type: "setClipFades", clipId: clip.id, fadeInSec: v })}
          />
          <NumberField
            label="Fade out (seconds)"
            value={clip.fadeOutSec}
            min={0}
            max={clip.lengthSec}
            step={0.05}
            onCommit={(v) => dispatch({ type: "setClipFades", clipId: clip.id, fadeOutSec: v })}
          />
        </>
      ) : (
        <p className={s.hint}>
          {clip.notes.length} {clip.notes.length === 1 ? "note" : "notes"}
          {clip.tonicHz ? `, tonic ${clip.tonicHz.toFixed(1)} Hz` : ""}.
          {clip.conversionId ? " Imported from the Notation Studio; microtones are kept as a detune on each note." : ""}
        </p>
      )}

      <div className={s.inspectorActions}>
        {clip.kind === "midi" ? (
          <button type="button" className="o-btn" onClick={on.onOpenPianoRoll}>
            Open piano roll
          </button>
        ) : null}
        <button type="button" className="o-btn" onClick={on.onTranscribe} disabled={busy}>
          Transcribe selection
        </button>
        {clip.kind === "midi" ? (
          <button type="button" className="o-btn" onClick={on.onExportMidi} disabled={clip.notes.length === 0}>
            Export .mid
          </button>
        ) : null}
      </div>

      {clip.kind === "midi" && note ? (
        <section className={s.noteProps} aria-label="Selected note">
          <h3>Note {midiName(note.midi)}{Math.abs(note.detuneCents) >= 0.05 ? ` ${signedCents(note.detuneCents)} cents` : ""}</h3>
          <NumberField
            label="Pitch (MIDI number)"
            value={note.midi}
            min={0}
            max={127}
            step={1}
            digits={0}
            suffix={midiName(note.midi)}
            onCommit={(v) => dispatch({ type: "moveNote", clipId: clip.id, noteId: note.id, startSec: note.startSec, midi: v })}
          />
          <NumberField
            label="Detune (cents)"
            value={note.detuneCents}
            min={-100}
            max={100}
            step={1}
            digits={1}
            onCommit={(v) => dispatch({ type: "setNoteDetune", clipId: clip.id, noteId: note.id, detuneCents: v })}
          />
          <NumberField
            label="Start in clip (seconds)"
            value={note.startSec}
            min={0}
            step={0.05}
            digits={3}
            onCommit={(v) => dispatch({ type: "moveNote", clipId: clip.id, noteId: note.id, startSec: v, midi: note.midi })}
          />
          <NumberField
            label="Length (seconds)"
            value={note.durSec}
            min={0.02}
            step={0.05}
            digits={3}
            onCommit={(v) => dispatch({ type: "resizeNote", clipId: clip.id, noteId: note.id, durSec: v })}
          />
          <Slider
            label="Velocity"
            min={0.05}
            max={1}
            step={0.01}
            value={note.velocity}
            valueText={percent(note.velocity)}
            showValue
            onChange={(v) => dispatch({ type: "setNoteVelocity", clipId: clip.id, noteId: note.id, velocity: v }, { coalesce: `vel:${note.id}` })}
          />
        </section>
      ) : clip.kind === "midi" ? (
        <p className={s.hint}>Select a note in the piano roll to change its pitch and its detune in cents.</p>
      ) : null}
    </aside>
  );
}
