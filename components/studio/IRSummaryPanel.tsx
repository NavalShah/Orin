"use client";

import { useEffect, useId, useState } from "react";
import type { ConversionOut, IRSummary, ModeCandidate, RerunRequest } from "@/lib/api";
import { clock, confidenceText, humanize, lookalikeLabel, nearestNote, pct, splitLookalikes, stageLabel } from "@/lib/format";
import { playTone } from "@/components/audio/engine";
import { Term } from "@/components/shell/Glossary";
import type { Registry } from "@/components/shell/registry";
import css from "./Studio.module.css";

type Rerun = (req: RerunRequest, what: string) => void;

const validHz = (hz: number) => Number.isFinite(hz) && hz >= 20 && hz <= 4000;

function SetTonic({ tonic, word, busy, onRerun }: { tonic: IRSummary["tonic"]; word: string; busy: boolean; onRerun: Rerun }) {
  const group = useId();
  const candidates = tonic.candidates.slice(0, 3);
  const [choice, setChoice] = useState("");
  const [custom, setCustom] = useState("");
  const customHz = Number(custom);
  const hz = choice === "custom" ? customHz : choice ? (candidates[Number(choice)]?.hz ?? NaN) : NaN;

  return (
    <fieldset className={css.fieldset}>
      <legend>Set tonic</legend>
      <p className="o-muted">Listen to each candidate against the recording, then apply the one that sounds like home.</p>
      {candidates.map((c, i) => (
        <div className="o-row" key={`${c.hz}-${i}`}>
          <label className={css.radio}>
            <input type="radio" name={group} checked={choice === String(i)} onChange={() => setChoice(String(i))} />
            <span>
              <span className="o-mono">{c.hz.toFixed(1)} Hz</span> ({nearestNote(c.hz)}), confidence {pct(c.confidence)}
              {Math.abs(c.hz - tonic.hz) < 0.05 ? " - in use" : ""}
            </span>
          </label>
          <button type="button" className="o-btn o-btn-small" onClick={() => playTone(c.hz)} aria-label={`Hear ${c.hz.toFixed(1)} hertz`}>
            <span aria-hidden="true">♪</span> Hear it
          </button>
        </div>
      ))}
      <div className="o-row">
        <label className={css.radio}>
          <input type="radio" name={group} checked={choice === "custom"} onChange={() => setChoice("custom")} />
          <span>Another pitch</span>
        </label>
        <input
          className="o-input"
          style={{ width: 110 }}
          type="number"
          inputMode="decimal"
          min={20}
          max={4000}
          step={0.1}
          value={custom}
          onChange={(e) => {
            setCustom(e.target.value);
            setChoice("custom");
          }}
          aria-label={`${word} in hertz`}
          placeholder="Hz"
        />
        <button type="button" className="o-btn o-btn-small" disabled={!validHz(customHz)} onClick={() => playTone(customHz)} aria-label="Hear the typed pitch">
          <span aria-hidden="true">♪</span> Hear it
        </button>
        {validHz(customHz) ? <span className="o-muted">{nearestNote(customHz)}</span> : null}
      </div>
      <div>
        <button
          type="button"
          className="o-btn"
          disabled={busy || !validHz(hz)}
          onClick={() => onRerun({ tonic_hz: hz }, `the tonic set to ${hz.toFixed(1)} Hz`)}
        >
          Apply and re-run
        </button>
      </div>
    </fieldset>
  );
}

function ModeList({
  title,
  intro,
  candidates,
  currentId,
  userSelected,
  declaredLabel,
  registry,
  busy,
  onUse,
}: {
  title: string;
  intro: string;
  candidates: ModeCandidate[];
  currentId: string | null;
  userSelected: boolean;
  /** A mode named in the input itself (a raga header, a key signature), not matched by Orin. */
  declaredLabel?: string | null;
  registry: Registry;
  busy: boolean;
  onUse: (c: ModeCandidate) => void;
}) {
  return (
    <div className="o-stack" style={{ gap: 8 }}>
      <h3>{title}</h3>
      <p className="o-muted">{intro}</p>
      {candidates.length === 0 ? (
        declaredLabel ? (
          <p>
            <strong>{declaredLabel}</strong>, as stated in the input. Orin did not match it from the pitches.
          </p>
        ) : (
          <p>No close match was found.</p>
        )
      ) : (
        <ul className={css.modeList}>
          {candidates.map((c) => {
            const { exact, rotations } = splitLookalikes(c.lookalikes ?? []);
            const current = c.id === currentId;
            return (
              <li key={c.id} className={css.modeItem}>
                <div className="o-row" style={{ justifyContent: "space-between" }}>
                  <span>
                    <strong>{c.label}</strong>{" "}
                    <span className="o-muted">
                      <Term name={c.label_system}>{c.label_system}</Term>
                    </span>{" "}
                    <span className="o-mono">match {pct(c.score)}</span>
                  </span>
                  <button type="button" className="o-btn o-btn-small" aria-pressed={current} disabled={busy} onClick={() => onUse(c)}>
                    {current ? (userSelected ? "In use (your choice)" : "In use") : "Use this"}
                  </button>
                </div>
                {exact.length > 0 ? (
                  <p>Same pitches as: {exact.map((ref) => lookalikeLabel(ref, registry.registry.formats)).join("; ")}.</p>
                ) : null}
                {rotations.length > 0 ? (
                  <details className={css.disclosure}>
                    <summary>
                      also a rotation of {rotations.length} {rotations.length === 1 ? "other" : "others"}
                    </summary>
                    <ul>
                      {rotations.map((ref) => (
                        <li key={ref}>{lookalikeLabel(ref, registry.registry.formats)}</li>
                      ))}
                    </ul>
                  </details>
                ) : null}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

/**
 * What the analysis found (spec 11.3 step 5), with the controls to correct it. The tonic
 * comes first and largest: a wrong tonic renames every note. Each change re-runs the
 * affected stages on the server and refreshes the notation and the loss report.
 */
export function IRSummaryPanel({
  conversion,
  registry,
  busy,
  onRerun,
}: {
  conversion: ConversionOut;
  registry: Registry;
  busy: boolean;
  onRerun: Rerun;
}) {
  const ir = conversion.ir;
  const source = registry.format(conversion.source_format);
  const word = source?.tonic_word ?? "tonic";
  const templates = source?.rhythm_templates ?? [];
  const [cycle, setCycle] = useState(ir?.cycle.template_id ?? "");
  const [tempo, setTempo] = useState("");
  const [sensitivity, setSensitivity] = useState(ir?.analysis.sensitivity ?? 1);

  useEffect(() => {
    setCycle(ir?.cycle.template_id ?? "");
    setSensitivity(ir?.analysis.sensitivity ?? 1);
    setTempo("");
  }, [ir]);

  if (!ir) return null;
  const tempoBpm = Number(tempo);
  const lowCount = ir.low_confidence_event_ids.length;

  return (
    <section className="o-panel o-stack" aria-labelledby="orin-ir-title">
      <h2 id="orin-ir-title">What Orin heard</h2>

      <div className={`o-stack ${css.tonic}`}>
        <h3>
          <Term name={word}>{word === "tonic" ? "Tonic" : word}</Term>
          {word !== "tonic" ? " (the tonic)" : ""}
        </h3>
        <p className={css.tonicValue}>
          <strong>{ir.tonic.hz.toFixed(1)} Hz</strong>
          <span> nearest note {ir.tonic.nearest_note ?? nearestNote(ir.tonic.hz)}</span>
        </p>
        <p>
          Found by: {humanize(ir.tonic.method)}. Confidence {confidenceText(ir.tonic.confidence)}.
          {Math.abs(ir.tonic.tuning_offset_cents) >= 1
            ? ` The recording sits ${Math.round(ir.tonic.tuning_offset_cents)} cents from standard pitch.`
            : ""}
        </p>
        <p className="o-note">
          <span aria-hidden="true">! </span>
          Check this first. Every note is named by its distance from the tonic, so a wrong tonic renames the whole melody.
        </p>
        <button type="button" className="o-btn o-btn-small" style={{ alignSelf: "flex-start" }} onClick={() => playTone(ir.tonic.hz)}>
          <span aria-hidden="true">♪</span> Hear the tonic in use
        </button>
        <SetTonic tonic={ir.tonic} word={word} busy={busy} onRerun={onRerun} />
      </div>

      <ModeList
        title="Mode: closest matches"
        intro="Matched by which pitches are used. Phrases, emphasis and ornaments also define a mode, so treat these as candidates, not an identification."
        candidates={ir.mode.candidates}
        currentId={ir.mode.mode_id}
        userSelected={ir.mode.user_selected}
        declaredLabel={ir.mode.label}
        registry={registry}
        busy={busy}
        onUse={(c) => onRerun({ mode_id: c.id }, `the mode set to ${c.label}`)}
      />

      {(conversion.suggestions ?? []).length > 0 ? (
        <ModeList
          title={`Closest modes in ${registry.formatName(conversion.output_format)}`}
          intro="None of these is applied unless you choose it. Sharing pitches does not make two modes the same thing."
          candidates={conversion.suggestions ?? []}
          currentId={conversion.score?.options.mode_id ?? null}
          userSelected
          registry={registry}
          busy={busy}
          onUse={(c) => onRerun({ target_mode_id: c.id }, `the output mode set to ${c.label}`)}
        />
      ) : null}

      <div className="o-stack" style={{ gap: 8 }}>
        <h3>Tempo and cycle</h3>
        <p>
          Tempo:{" "}
          {ir.tempo.free_time
            ? "free time (no steady pulse was found)"
            : ir.tempo.bpm != null
              ? `${Math.round(ir.tempo.bpm)} beats a minute`
              : "not found"}
          . Confidence {confidenceText(ir.tempo.confidence)}.
        </p>
        <div className="o-row" style={{ alignItems: "flex-end" }}>
          <label className="o-label">
            Set the tempo (beats a minute)
            <input
              className="o-input"
              style={{ width: 120 }}
              type="number"
              min={20}
              max={400}
              step={1}
              value={tempo}
              onChange={(e) => setTempo(e.target.value)}
            />
          </label>
          <button
            type="button"
            className="o-btn o-btn-small"
            disabled={busy || !(tempoBpm >= 20 && tempoBpm <= 400)}
            onClick={() => onRerun({ tempo_bpm: tempoBpm }, `the tempo set to ${tempoBpm} beats a minute`)}
          >
            Apply tempo
          </button>
        </div>
        <p>
          Suggested <Term name="cycle">cycle</Term>:{" "}
          <strong>{ir.cycle.label ?? (ir.cycle.length_pulses ? `${ir.cycle.length_pulses} pulses` : "none found")}</strong>. Confidence{" "}
          {confidenceText(ir.cycle.confidence)}. This is a suggestion from the accents Orin heard, not a statement about the piece.
        </p>
        {templates.length > 0 ? (
          <div className="o-row" style={{ alignItems: "flex-end" }}>
            <label className="o-label">
              Choose a cycle from {source?.display_name}
              <select className="o-select" value={cycle} onChange={(e) => setCycle(e.target.value)}>
                <option value="">Choose...</option>
                {templates.map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.label} ({t.cycle_beats} beats)
                  </option>
                ))}
              </select>
            </label>
            <button
              type="button"
              className="o-btn o-btn-small"
              disabled={busy || !cycle || cycle === ir.cycle.template_id}
              onClick={() => onRerun({ cycle_template_id: cycle }, "the cycle changed")}
            >
              Apply cycle
            </button>
          </div>
        ) : null}
      </div>

      <p>
        <strong>{ir.note_count}</strong> {ir.note_count === 1 ? "note" : "notes"} in {clock(ir.duration_s)}.{" "}
        {lowCount > 0 ? `${lowCount} ${lowCount === 1 ? "is" : "are"} marked as unsure in the notation.` : "None are marked as unsure."}
      </p>

      {ir.warnings.length > 0 ? (
        <div className="o-stack" style={{ gap: 6 }}>
          <h3>Warnings</h3>
          <ul className={css.plainList}>
            {ir.warnings.map((w, i) => (
              <li key={`${w.code}-${i}`}>
                <span aria-hidden="true">△ </span>
                {w.message}
                {w.stage ? <span className="o-muted"> ({stageLabel(w.stage)})</span> : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <details className={css.disclosure}>
        <summary>Advanced</summary>
        <div className="o-stack" style={{ gap: 8, marginTop: 8 }}>
          <label className="o-label">
            Note sensitivity: {sensitivity.toFixed(1)}
            <input
              type="range"
              min={0.5}
              max={2}
              step={0.1}
              value={sensitivity}
              onChange={(e) => setSensitivity(Number(e.target.value))}
              aria-valuetext={`${sensitivity.toFixed(1)}, where 1 is the default`}
            />
          </label>
          <p className="o-muted">Above 1 splits the melody into more, shorter notes. Below 1 merges more of it into longer notes.</p>
          <div>
            <button
              type="button"
              className="o-btn o-btn-small"
              disabled={busy || Math.abs(sensitivity - ir.analysis.sensitivity) < 0.05}
              onClick={() => onRerun({ sensitivity }, `the sensitivity set to ${sensitivity.toFixed(1)}`)}
            >
              Apply sensitivity
            </button>
          </div>
        </div>
      </details>
    </section>
  );
}
