"use client";

import type { Transport } from "@/lib/audio/engine";
import type { Project } from "@/lib/workspace/project";
import type { Action } from "@/lib/workspace/reducer";
import type { DispatchOptions } from "@/lib/workspace/store";
import { formatBarsBeats, formatClock, secPerBar } from "@/lib/workspace/time";
import { NumberField, percent, Slider, TextField } from "./fields";
import { usePosition } from "./usePosition";
import s from "./workspace.module.css";

export type SaveState = "saved" | "saving" | "dirty" | "error";

const SAVE_TEXT: Record<SaveState, string> = {
  saved: "Saved",
  saving: "Saving...",
  dirty: "Unsaved changes",
  error: "Not saved",
};

type Props = {
  project: Project;
  transport: Transport;
  playing: boolean;
  dispatch: (action: Action, opts?: DispatchOptions) => void;
  onPlay: () => void;
  onPause: () => void;
  onStop: () => void;
  canUndo: boolean;
  canRedo: boolean;
  onUndo: () => void;
  onRedo: () => void;
  saveState: SaveState;
  /** False until the project exists on the server (Duplicate needs that). */
  stored: boolean;
  onSave: () => void;
  onSaveAs: () => void;
  onDuplicate: () => void;
  onNew: () => void;
  onExportWav: () => void;
  busy: boolean;
};

/** Bars:beats and mm:ss. A leaf of its own so only this text re-renders every frame. */
function Position({ transport, project }: { transport: Transport; project: Project }) {
  const sec = usePosition(transport);
  return (
    <div className={s.position} role="group" aria-label="Position">
      <span className={`o-mono ${s.positionMain}`} title="Bar : beat">
        <span className="o-sr-only">Bar and beat </span>
        {formatBarsBeats(sec, project.tempo, project.timeSignature)}
      </span>
      <span className="o-mono" title="Minutes : seconds">
        <span className="o-sr-only">Time </span>
        {formatClock(sec)}
      </span>
    </div>
  );
}

export function TransportBar(props: Props) {
  const { project, transport, playing, dispatch } = props;
  const bar = secPerBar(project.tempo, project.timeSignature);
  const loopStartBar = project.loop.startSec / bar + 1;
  const loopBars = (project.loop.endSec - project.loop.startSec) / bar;

  return (
    <div className={s.transport} role="group" aria-label="Transport">
      <div className={s.group} role="group" aria-label="Playback">
        <button type="button" className="o-btn o-btn-primary" onClick={props.onPlay} aria-pressed={playing}>
          Play
        </button>
        <button type="button" className="o-btn" onClick={props.onPause} disabled={!playing}>
          Pause
        </button>
        <button type="button" className="o-btn" onClick={props.onStop}>
          Stop
        </button>
        <Position transport={transport} project={project} />
      </div>

      <div className={s.group} role="group" aria-label="Loop">
        <button
          type="button"
          className="o-btn"
          aria-pressed={project.loop.enabled}
          onClick={() => dispatch({ type: "setLoop", loop: { enabled: !project.loop.enabled } })}
        >
          Loop {project.loop.enabled ? "on" : "off"}
        </button>
        <NumberField
          label="From bar"
          value={loopStartBar}
          min={1}
          step={1}
          onCommit={(v) =>
            dispatch({ type: "setLoop", loop: { startSec: (v - 1) * bar, endSec: (v - 1) * bar + Math.max(bar / 16, loopBars * bar) } })
          }
        />
        <NumberField
          label="Bars"
          value={loopBars}
          min={0.25}
          step={1}
          onCommit={(v) => dispatch({ type: "setLoop", loop: { endSec: project.loop.startSec + v * bar } })}
        />
      </div>

      <div className={s.group} role="group" aria-label="Tempo and metre">
        <NumberField
          label="BPM"
          value={project.tempo}
          min={20}
          max={300}
          step={1}
          digits={1}
          onCommit={(v) => dispatch({ type: "setTempo", tempo: v })}
        />
        <div className={s.field} role="group" aria-label="Time signature">
          <span className={s.fieldLabel} aria-hidden="true">
            Time signature
          </span>
          <span className={s.fieldRow}>
            <select
              className="o-select"
              aria-label="Beats per bar"
              value={project.timeSignature[0]}
              onChange={(e) => dispatch({ type: "setTimeSignature", timeSignature: [Number(e.target.value), project.timeSignature[1]] })}
            >
              {[2, 3, 4, 5, 6, 7, 8, 9, 12].map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </select>
            <span aria-hidden="true">/</span>
            <select
              className="o-select"
              aria-label="Beat unit"
              value={project.timeSignature[1]}
              onChange={(e) => dispatch({ type: "setTimeSignature", timeSignature: [project.timeSignature[0], Number(e.target.value)] })}
            >
              {[2, 4, 8, 16].map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </select>
          </span>
        </div>
        <button
          type="button"
          className="o-btn"
          aria-pressed={project.metronome}
          onClick={() => dispatch({ type: "setMetronome", on: !project.metronome })}
        >
          Metronome {project.metronome ? "on" : "off"}
        </button>
        <Slider
          label="Master volume"
          min={0}
          max={1.5}
          step={0.01}
          value={project.masterGain}
          valueText={percent(project.masterGain)}
          showValue
          onChange={(v) => dispatch({ type: "setMasterGain", gain: v }, { coalesce: "master" })}
        />
      </div>

      <div className={s.group} role="group" aria-label="History">
        <button type="button" className="o-btn" onClick={props.onUndo} disabled={!props.canUndo} title="Ctrl or Cmd + Z">
          Undo
        </button>
        <button type="button" className="o-btn" onClick={props.onRedo} disabled={!props.canRedo} title="Shift + Ctrl or Cmd + Z">
          Redo
        </button>
      </div>

      <div className={s.group} role="group" aria-label="Project">
        <TextField label="Project name" value={project.name} maxLength={120} onCommit={(name) => dispatch({ type: "setName", name })} />
        <button type="button" className="o-btn" onClick={props.onSave} disabled={props.busy || props.saveState === "saving"}>
          Save
        </button>
        <button type="button" className="o-btn" onClick={props.onSaveAs} disabled={props.busy}>
          Save as
        </button>
        <button
          type="button"
          className="o-btn"
          onClick={props.onDuplicate}
          disabled={props.busy || !props.stored}
          aria-label="Duplicate project"
          title={props.stored ? "Save a copy and keep working here" : "Save the project first"}
        >
          Duplicate
        </button>
        <button type="button" className="o-btn" onClick={props.onNew} disabled={props.busy} aria-label="New project">
          New
        </button>
        <button type="button" className="o-btn" onClick={props.onExportWav} disabled={props.busy}>
          Export WAV
        </button>
        <span
          className={`o-badge ${props.saveState === "saved" ? "o-badge-ok" : props.saveState === "error" ? "o-badge-danger" : "o-badge-warn"}`}
          role="status"
        >
          {SAVE_TEXT[props.saveState]}
        </span>
      </div>
    </div>
  );
}
