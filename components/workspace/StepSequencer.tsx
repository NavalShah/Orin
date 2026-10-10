"use client";

import type { KeyboardEvent } from "react";
import type { DrumVoice, Drums } from "@/lib/workspace/project";
import { panText, percent, Slider } from "./fields";
import s from "./workspace.module.css";

export const VOICE_LABEL: Record<DrumVoice, string> = { kick: "Kick", snare: "Snare", hat: "Hi-hat", clap: "Clap" };

type Props = {
  drums: Drums | null;
  onToggle: (voice: DrumVoice, step: number) => void;
  onGain: (voice: DrumVoice, gain: number) => void;
  onPan: (voice: DrumVoice, pan: number) => void;
  onAddDrums: () => void;
  /** The step sounding right now, or -1 when stopped. */
  currentStep?: number;
};

/** The channel rack: four drum voices on a 16-step grid that loops with the bar (spec 12.2). */
export function StepSequencer({ drums, onToggle, onGain, onPan, onAddDrums, currentStep = -1 }: Props) {
  if (!drums) {
    return (
      <div className={s.emptyPanel}>
        <p>The step sequencer plays a 16-step drum pattern that repeats every bar.</p>
        <button type="button" className="o-btn" onClick={onAddDrums}>
          Add drum track
        </button>
      </div>
    );
  }

  // Arrow keys move between steps; Space or Enter toggles the focused one.
  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const target = e.target as HTMLElement;
    if (target.dataset.step === undefined) return;
    let row = Number(target.dataset.row);
    let step = Number(target.dataset.step);
    if (e.key === "ArrowRight") step += 1;
    else if (e.key === "ArrowLeft") step -= 1;
    else if (e.key === "ArrowDown") row += 1;
    else if (e.key === "ArrowUp") row -= 1;
    else if (e.key === "Home") step = 0;
    else if (e.key === "End") step = drums.steps - 1;
    else return;
    e.preventDefault();
    e.currentTarget.querySelector<HTMLElement>(`[data-row="${row}"][data-step="${step}"]`)?.focus();
  };

  return (
    <div className={s.steps} role="group" aria-label="Step sequencer" onKeyDown={onKeyDown}>
      <p className={s.hint}>
        16 steps to the bar. Arrow keys move between steps; Space or Enter switches a step on or off.
      </p>
      {drums.rows.map((row, r) => {
        const name = VOICE_LABEL[row.voice];
        return (
          <div key={row.voice} className={s.stepRow} role="group" aria-label={`${name} steps`}>
            <span className={s.stepName}>{name}</span>
            <div className={s.stepCells}>
              {row.steps.map((on, i) => (
                <button
                  key={i}
                  type="button"
                  className={s.step}
                  data-row={r}
                  data-step={i}
                  data-beat={i % 4 === 0 ? "true" : undefined}
                  data-current={i === currentStep ? "true" : undefined}
                  aria-pressed={on}
                  aria-label={`${name} step ${i + 1}`}
                  onClick={() => onToggle(row.voice, i)}
                >
                  <span aria-hidden="true">{on ? "●" : ""}</span>
                </button>
              ))}
            </div>
            <Slider
              label={`${name} volume`}
              hideLabel
              min={0}
              max={1.5}
              step={0.01}
              value={row.gain}
              valueText={percent(row.gain)}
              onChange={(v) => onGain(row.voice, v)}
            />
            <Slider
              label={`${name} pan`}
              hideLabel
              min={-1}
              max={1}
              step={0.05}
              value={row.pan}
              valueText={panText(row.pan)}
              onChange={(v) => onPan(row.voice, v)}
            />
          </div>
        );
      })}
      <div className={s.stepRow} aria-hidden="true">
        <span className={s.stepName} />
        <div className={s.stepCells} />
        <span className={s.stepLegend}>Volume</span>
        <span className={s.stepLegend}>Pan</span>
      </div>
    </div>
  );
}
