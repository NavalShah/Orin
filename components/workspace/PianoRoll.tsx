"use client";

import { useLayoutEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import type { Transport } from "@/lib/audio/engine";
import { MIN_NOTE_SEC, uid, type MidiClip, type MidiNote } from "@/lib/workspace/project";
import type { ProjectStore } from "@/lib/workspace/store";
import {
  gridStepSec, isBlackKey, midiName, noteLabel, secPerBar, secPerBeat, signedCents, snapSec, type TimeSignature,
} from "@/lib/workspace/time";
import { usePosition } from "./usePosition";
import s from "./workspace.module.css";

const ROW_H = 16;
const KEYS_W = 56;
const EDGE_PX = 7;

type Props = {
  clip: MidiClip | undefined;
  store: ProjectStore;
  transport: Transport;
  tempo: number;
  timeSignature: TimeSignature;
  snap: boolean;
  division: number;
  selectedNoteId: string | null;
  onSelectNote: (noteId: string | null) => void;
  /** Offered when no MIDI clip is selected. */
  onNewClip: () => void;
};

type Drag =
  | { mode: "draw"; noteId: string; midi: number; startSec: number; startX: number }
  | { mode: "move" | "resize"; note: MidiNote; startX: number; startY: number; moved: boolean };

function Playhead({ transport, clipStart, pxPerSec, height }: { transport: Transport; clipStart: number; pxPerSec: number; height: number }) {
  const x = (usePosition(transport) - clipStart) * pxPerSec;
  if (x < 0) return null;
  return <div className={s.prPlayhead} style={{ transform: `translateX(${x}px)`, height }} aria-hidden="true" />;
}

/** Draw and edit the notes of one MIDI clip. A detuned note shows a marker and its cents (spec 12.2, 12.5). */
export function PianoRoll(props: Props) {
  const { clip, store, transport, tempo, timeSignature, snap, division, selectedNoteId, onSelectNote } = props;
  const [pxPerSec, setPxPerSec] = useState(140);
  const scroller = useRef<HTMLDivElement>(null);
  const grid = useRef<HTMLDivElement>(null);
  const drag = useRef<Drag | null>(null);

  // The pitch range is fixed while a clip is open, so rows don't shift under a drag.
  const clipId = clip?.id;
  const range = useMemo(() => {
    const pitches = clip?.notes.map((n) => n.midi) ?? [];
    return {
      lo: Math.max(0, Math.min(36, ...pitches.map((p) => p - 3))),
      hi: Math.min(127, Math.max(96, ...pitches.map((p) => p + 3))),
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [clipId]);

  // Open centred on the notes (or around middle C for an empty clip).
  useLayoutEffect(() => {
    const el = scroller.current;
    if (!el || !clip) return;
    const pitches = clip.notes.map((n) => n.midi);
    const centre = pitches.length ? (Math.min(...pitches) + Math.max(...pitches)) / 2 : 62;
    el.scrollTop = Math.max(0, (range.hi - centre) * ROW_H - el.clientHeight / 2);
    el.scrollLeft = 0;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [clipId]);

  if (!clip) {
    return (
      <div className={s.emptyPanel}>
        <p>Select a MIDI clip to edit its notes here, or start a new one.</p>
        <button type="button" className="o-btn" onClick={props.onNewClip}>
          New MIDI clip
        </button>
      </div>
    );
  }

  const step = gridStepSec(tempo, division);
  const rows = range.hi - range.lo + 1;
  const height = rows * ROW_H;
  const bar = secPerBar(tempo, timeSignature);
  const beat = secPerBeat(tempo);
  const lengthSec = Math.max(clip.lengthSec + bar, 4 * bar);
  const width = lengthSec * pxPerSec;
  // Grid lines follow the project's bars, wherever in the bar the clip starts.
  const gridShift = -(clip.startSec % bar) * pxPerSec;

  /** Snap a clip-relative time to the project grid. */
  const quantise = (local: number, mode: "nearest" | "floor" = "nearest") => {
    if (!snap) return Math.max(0, local);
    const abs = clip.startSec + local;
    const snapped = mode === "floor" ? Math.floor(abs / step + 1e-6) * step : snapSec(abs, tempo, division);
    return Math.max(0, snapped - clip.startSec);
  };
  const minDur = snap ? step : MIN_NOTE_SEC * 2;

  const point = (e: ReactPointerEvent) => {
    const rect = grid.current!.getBoundingClientRect();
    return { x: e.clientX - rect.left, y: e.clientY - rect.top };
  };

  const onGridDown = (e: ReactPointerEvent<HTMLDivElement>) => {
    if (e.button !== 0 || e.target !== e.currentTarget) return;
    const { x, y } = point(e);
    const midi = Math.min(range.hi, Math.max(range.lo, range.hi - Math.floor(y / ROW_H)));
    const startSec = quantise(x / pxPerSec, "floor");
    const noteId = uid("n");
    e.currentTarget.setPointerCapture(e.pointerId);
    drag.current = { mode: "draw", noteId, midi, startSec, startX: e.clientX };
    store.begin();
    store.dispatch(
      { type: "addNote", clipId: clip.id, note: { id: noteId, midi, detuneCents: 0, startSec, durSec: minDur, velocity: 0.7 } },
      { transient: true },
    );
    onSelectNote(noteId);
    if (!transport.playing) transport.auditionNote(clip.trackId, midi);
  };

  const onNoteDown = (e: ReactPointerEvent<HTMLDivElement>, note: MidiNote) => {
    if (e.button !== 0) return;
    e.stopPropagation();
    const rect = e.currentTarget.getBoundingClientRect();
    const mode = rect.right - e.clientX <= EDGE_PX ? "resize" : "move";
    e.currentTarget.setPointerCapture(e.pointerId);
    drag.current = { mode, note, startX: e.clientX, startY: e.clientY, moved: false };
    onSelectNote(note.id);
    // Hear the note as written, detune included.
    if (!transport.playing) transport.auditionNote(clip.trackId, note.midi, note.detuneCents, note.velocity);
  };

  const onMove = (e: ReactPointerEvent<HTMLDivElement>) => {
    const d = drag.current;
    if (!d) return;
    if (d.mode === "draw") {
      const end = quantise(point(e).x / pxPerSec);
      store.dispatch(
        {
          type: "addNote",
          clipId: clip.id,
          note: { id: d.noteId, midi: d.midi, detuneCents: 0, startSec: d.startSec, durSec: Math.max(minDur, end - d.startSec), velocity: 0.7 },
        },
        { transient: true },
      );
      return;
    }
    const dx = e.clientX - d.startX;
    const dy = e.clientY - d.startY;
    if (!d.moved) {
      if (Math.abs(dx) < 3 && Math.abs(dy) < 3) return;
      d.moved = true;
      store.begin();
    }
    if (d.mode === "move") {
      const midi = Math.min(range.hi, Math.max(range.lo, d.note.midi - Math.round(dy / ROW_H)));
      store.dispatch(
        { type: "moveNote", clipId: clip.id, noteId: d.note.id, startSec: quantise(d.note.startSec + dx / pxPerSec), midi },
        { transient: true },
      );
    } else {
      const end = quantise(d.note.startSec + d.note.durSec + dx / pxPerSec);
      store.dispatch(
        { type: "resizeNote", clipId: clip.id, noteId: d.note.id, durSec: Math.max(minDur, end - d.note.startSec) },
        { transient: true },
      );
    }
  };

  const onUp = () => {
    const d = drag.current;
    drag.current = null;
    if (!d) return;
    if (d.mode === "draw") store.commit({ type: "addNote", clipId: clip.id, note: { id: d.noteId, midi: d.midi, detuneCents: 0, startSec: d.startSec, durSec: minDur, velocity: 0.7 } });
    else if (d.moved) store.commit({ type: "moveNote", clipId: clip.id, noteId: d.note.id, startSec: d.note.startSec, midi: d.note.midi });
  };

  const onCancel = () => {
    if (drag.current) store.cancel();
    drag.current = null;
  };

  const keys = [];
  for (let midi = range.hi; midi >= range.lo; midi--) keys.push(midi);
  const beatPx = beat * pxPerSec;
  const barPx = bar * pxPerSec;

  return (
    <div className={s.pianoRoll}>
      <div className={s.panelTools}>
        <strong className={s.panelTitle}>{clip.name}</strong>
        <span className={s.hint}>
          Drag on the grid to draw a note. Drag a note to move it, or its right edge to resize. Arrow keys nudge the
          selected note; Delete removes it. Detune is set in the inspector.
        </span>
        <span className={s.spacer} />
        <button type="button" className="o-btn o-btn-small" onClick={() => setPxPerSec((v) => Math.max(40, v / 1.4))} aria-label="Zoom out piano roll">
          Zoom out
        </button>
        <button type="button" className="o-btn o-btn-small" onClick={() => setPxPerSec((v) => Math.min(800, v * 1.4))} aria-label="Zoom in piano roll">
          Zoom in
        </button>
      </div>
      <div className={s.prScroll} ref={scroller}>
        <div className={s.prInner} style={{ width: KEYS_W + width, height }}>
          <div className={s.prKeys} style={{ width: KEYS_W, height }} aria-hidden="true">
            {keys.map((midi) => (
              <div key={midi} className={s.prKey} data-black={isBlackKey(midi) ? "true" : undefined} style={{ height: ROW_H }}>
                {midiName(midi)}
              </div>
            ))}
          </div>
          <div
            ref={grid}
            className={s.prGrid}
            role="group"
            aria-label={`Notes of ${clip.name}`}
            style={{
              left: KEYS_W,
              width,
              height,
              backgroundImage: [
                `linear-gradient(to right, rgba(241, 239, 233, 0.4) 1px, transparent 1px)`,
                beatPx >= 8 ? `linear-gradient(to right, var(--o-daw-grid) 1px, transparent 1px)` : null,
                `repeating-linear-gradient(to bottom, transparent 0, transparent ${ROW_H - 1}px, var(--o-daw-grid) ${ROW_H - 1}px, var(--o-daw-grid) ${ROW_H}px)`,
              ]
                .filter(Boolean)
                .join(", "),
              backgroundSize: [`${barPx}px 100%`, beatPx >= 8 ? `${beatPx}px 100%` : null, `100% ${ROW_H}px`].filter(Boolean).join(", "),
              backgroundPosition: [`${gridShift}px 0`, beatPx >= 8 ? `${gridShift}px 0` : null, "0 0"].filter(Boolean).join(", "),
            }}
            onPointerDown={onGridDown}
            onPointerMove={onMove}
            onPointerUp={onUp}
            onPointerCancel={onCancel}
          >
            {keys.filter(isBlackKey).map((midi) => (
              <div key={midi} className={s.prBlackRow} style={{ top: (range.hi - midi) * ROW_H, height: ROW_H }} />
            ))}
            <div className={s.prClipEnd} style={{ left: clip.lengthSec * pxPerSec }} title="End of the clip" />
            {clip.notes.map((note) => {
              const detuned = Math.abs(note.detuneCents) >= 0.05;
              const hidden = note.startSec >= clip.lengthSec;
              return (
                <div
                  key={note.id}
                  role="button"
                  tabIndex={0}
                  className={s.prNote}
                  data-selected={note.id === selectedNoteId ? "true" : undefined}
                  data-muted={hidden ? "true" : undefined}
                  aria-pressed={note.id === selectedNoteId}
                  aria-label={noteLabel(note) + (hidden ? ", past the end of the clip, not played" : "")}
                  style={{
                    left: note.startSec * pxPerSec,
                    top: (range.hi - note.midi) * ROW_H,
                    width: Math.max(4, note.durSec * pxPerSec),
                    height: ROW_H - 1,
                  }}
                  onPointerDown={(e) => onNoteDown(e, note)}
                  onFocus={() => onSelectNote(note.id)}
                >
                  {detuned ? (
                    <span className={s.prDetune} aria-hidden="true">
                      <span className={s.prDetuneMark}>{note.detuneCents > 0 ? "▴" : "▾"}</span>
                      {signedCents(note.detuneCents)}c
                    </span>
                  ) : null}
                  <span className={s.prNoteEdge} aria-hidden="true" />
                </div>
              );
            })}
            <Playhead transport={transport} clipStart={clip.startSec} pxPerSec={pxPerSec} height={height} />
          </div>
        </div>
      </div>
    </div>
  );
}
