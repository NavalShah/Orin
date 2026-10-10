"use client";

import type { IRSummary } from "@/lib/api";
import { confidenceText, stageLabel } from "@/lib/format";
import css from "./Studio.module.css";

const time = (ms: number) => (ms >= 1000 ? `${(ms / 1000).toFixed(1)} s` : `${Math.round(ms)} ms`);

/** "How was this made?": the processing log and the confidences behind the result (spec 11.2). */
export function ProcessingLog({ ir }: { ir: IRSummary }) {
  const detected = ir.source.detected_format_confidence;
  return (
    <details className={`o-panel ${css.disclosure} ${css.logPanel}`}>
      <summary>
        <h2>How was this made?</h2>
      </summary>
      <div className="o-stack" style={{ marginTop: 10 }}>
        <h3>Confidences</h3>
        <dl className={css.facts}>
          <div>
            <dt>Tonic</dt>
            <dd>{confidenceText(ir.tonic.confidence)}</dd>
          </div>
          <div>
            <dt>Mode match</dt>
            <dd>{confidenceText(ir.mode.confidence)}</dd>
          </div>
          <div>
            <dt>Tempo</dt>
            <dd>{confidenceText(ir.tempo.confidence)}</dd>
          </div>
          <div>
            <dt>Cycle</dt>
            <dd>{confidenceText(ir.cycle.confidence)}</dd>
          </div>
          {detected != null ? (
            <div>
              <dt>Style detection</dt>
              <dd>{confidenceText(detected)}</dd>
            </div>
          ) : null}
          <div>
            <dt>Notes marked unsure</dt>
            <dd>
              {ir.low_confidence_event_ids.length} of {ir.note_count}
            </dd>
          </div>
        </dl>
        <p className="o-muted">
          {ir.analysis.trackers.length > 0 ? `Pitch tracking: ${ir.analysis.trackers.join(", ")}. ` : ""}
          {ir.analysis.profile_id ? `Analysis profile: ${ir.analysis.profile_id}. ` : ""}
          Sensitivity: {ir.analysis.sensitivity}.
        </p>
        <h3>Processing steps</h3>
        {ir.processing_log.length === 0 ? (
          <p>No steps were recorded.</p>
        ) : (
          <div className={css.tableWrap} tabIndex={0} role="group" aria-label="Processing steps table">
            <table className={css.table}>
              <thead>
                <tr>
                  <th scope="col">Stage</th>
                  <th scope="col">Time</th>
                  <th scope="col">Cached</th>
                  <th scope="col">Note</th>
                </tr>
              </thead>
              <tbody>
                {ir.processing_log.map((row, i) => (
                  <tr key={`${row.stage}-${i}`}>
                    <th scope="row">{stageLabel(row.stage)}</th>
                    <td className="o-mono">{time(row.duration_ms)}</td>
                    <td>{row.cache_hit ? "Yes, reused" : "No"}</td>
                    <td>{row.note ?? ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </details>
  );
}
