"use client";

import { useMemo } from "react";
import type { LossItem, LossReport, ScoreNote } from "@/lib/api";
import { clock, costText, humanize, lossReportText, pct, severityIcon, severityLabel } from "@/lib/format";
import { saveText } from "@/components/shell/download";
import { Term } from "@/components/shell/Glossary";
import css from "./Studio.module.css";

type Group = { code: string; items: Array<{ item: LossItem; index: number }> };

/** Group loss items by code, keeping each item's index into ``loss.items``. */
export function groupByCode(items: readonly LossItem[]): Group[] {
  const groups: Group[] = [];
  items.forEach((item, index) => {
    let group = groups.find((g) => g.code === item.code);
    if (!group) {
      group = { code: item.code, items: [] };
      groups.push(group);
    }
    group.items.push({ item, index });
  });
  return groups;
}

/**
 * The loss report (spec 9.3): what the target notation could not carry. Items are grouped by
 * code and each one can be activated to highlight its note in the score. Notices (Orin's own
 * assumptions and doubts) sit in a separate group and cost nothing.
 */
export function LossPanel({
  loss,
  notes = [],
  howScored = "",
  selectedIndex = null,
  onSelect,
  formatName = (id) => id ?? "unknown",
  fileStem = "orin",
}: {
  loss: LossReport;
  notes?: ReadonlyArray<Pick<ScoreNote, "id" | "source_event_id">>;
  howScored?: string;
  selectedIndex?: number | null;
  /** Called with the item's index and the id of its note in the score (null if it has none). */
  onSelect?: (index: number, noteId: string | null) => void;
  formatName?: (id: string | null | undefined) => string;
  fileStem?: string;
}) {
  const groups = useMemo(() => groupByCode(loss.items), [loss.items]);
  const noteByEvent = useMemo(() => {
    const map = new Map<string, string>();
    for (const n of notes) if (n.source_event_id && !map.has(n.source_event_id)) map.set(n.source_event_id, n.id);
    return map;
  }, [notes]);
  const noteIdOf = (item: LossItem): string | null => {
    const eventId = item.event_id ?? item.event_ids[0] ?? null;
    return eventId ? (noteByEvent.get(eventId) ?? null) : null;
  };
  const s = loss.summary;

  return (
    <section className="o-panel o-stack" aria-labelledby="orin-loss-title">
      <h2 id="orin-loss-title">Loss report</h2>
      <p className="o-muted">
        What {formatName(loss.target_format)} could not carry over from {formatName(loss.source_format)}.
      </p>

      {s ? (
        <div className="o-stack" style={{ gap: 6 }}>
          <p className={css.fidelity}>
            <Term name="fidelity">Fidelity</Term> <strong>{s.fidelity_score.toFixed(2)}</strong>{" "}
            <span className="o-muted">out of 1 ({pct(s.fidelity_score)})</span>
          </p>
          <dl className={css.subScores}>
            {(
              [
                ["Pitch", s.pitch_score],
                ["Ornament", s.ornament_score],
                ["Timing", s.timing_score],
              ] as const
            ).map(([name, value]) => (
              <div key={name}>
                <dt>{name}</dt>
                <dd>
                  <meter min={0} max={1} value={value} aria-label={`${name} score`} />
                  <span className="o-mono"> {value.toFixed(2)}</span>
                </dd>
              </div>
            ))}
          </dl>
          <p className="o-muted">
            Largest pitch change: {Math.round(s.max_cents_delta)} <Term name="cents">cents</Term>.
          </p>
        </div>
      ) : null}
      <details className={css.disclosure}>
        <summary>How is this scored?</summary>
        <p>{howScored || "The scoring note is not available for this conversion."}</p>
      </details>

      <h3>
        What was lost <span className="o-muted">({loss.items.length})</span>
      </h3>
      {loss.items.length === 0 ? (
        <p className="o-note">Nothing was lost in this conversion.</p>
      ) : (
        <>
          <p className="o-muted">Choose an item to highlight its note in the score.</p>
          {groups.map((group) => (
            <details key={group.code} className={css.lossGroup} open>
              <summary>
                {humanize(group.code)} <span className="o-muted">({group.items.length})</span>
              </summary>
              <ul className={css.lossList}>
                {group.items.map(({ item, index }) => (
                  <li key={index}>
                    <button
                      type="button"
                      className={css.lossItem}
                      aria-pressed={selectedIndex === index}
                      onClick={() => onSelect?.(index, noteIdOf(item))}
                    >
                      <span className={css.severity} data-severity={item.severity}>
                        <span aria-hidden="true">{severityIcon(item.severity)}</span> {severityLabel(item.severity)}
                        {item.time_s != null ? <span className="o-muted"> at {clock(item.time_s)}</span> : null}
                      </span>
                      <span>{item.human_text}</span>
                      <span className="o-muted">{costText(item.penalty)}</span>
                    </button>
                  </li>
                ))}
              </ul>
            </details>
          ))}
        </>
      )}

      {loss.notices.length > 0 && (
        <section className="o-stack" aria-labelledby="orin-notices-title">
          <h3 id="orin-notices-title">About the analysis (not counted as loss)</h3>
          <p className="o-muted">These are Orin&apos;s own assumptions and doubts. They do not lower the score.</p>
          <ul className={css.noticeList}>
            {loss.notices.map((n, i) => (
              <li key={i}>
                <strong>{humanize(n.code)}</strong>
                {n.time_s != null ? <span className="o-muted"> at {clock(n.time_s)}</span> : null}: {n.human_text}
              </li>
            ))}
          </ul>
        </section>
      )}

      <div className="o-row">
        <button
          type="button"
          className="o-btn o-btn-small"
          onClick={() => saveText(JSON.stringify(loss, null, 2), `${fileStem}-loss-report.json`, "application/json")}
        >
          Export loss report (JSON)
        </button>
        <button
          type="button"
          className="o-btn o-btn-small"
          onClick={() => saveText(lossReportText(loss, formatName), `${fileStem}-loss-report.txt`)}
        >
          Export loss report (text)
        </button>
      </div>
    </section>
  );
}
