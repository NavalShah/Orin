"use client";

import { useId } from "react";
import type { FormatCandidate } from "@/lib/api";
import { pct } from "@/lib/format";
import css from "./Studio.module.css";

/**
 * The auto-detect result (spec 8.2): always the top three with percentages, never a bare
 * label. When Orin is not sure it says so and asks; either way the user's choice wins.
 */
export function DetectionBanner({
  detected,
  needsChoice,
  message,
  sourceFormat,
  formatName,
  limits,
  onChoose,
  busy = false,
}: {
  detected: FormatCandidate[];
  needsChoice: boolean;
  message?: string | null;
  sourceFormat?: string | null;
  formatName: (id: string) => string;
  limits: string[];
  onChoose: (formatId: string) => void;
  busy?: boolean;
}) {
  const titleId = useId();
  const top = detected.slice(0, 3);
  if (top.length === 0) return null;
  const summary = top.map((c) => `${formatName(c.format_id)} (${pct(c.score)})`).join(", ");

  return (
    <section className={`${css.detect} ${needsChoice ? css.detectUnsure : ""}`} aria-labelledby={titleId}>
      <h3 id={titleId}>
        <span aria-hidden="true">{needsChoice ? "? " : ""}</span>
        {needsChoice ? "Not sure - please choose" : "Detected style"}
      </h3>
      {/* The server's sentence already names the three candidates; show one or the other. */}
      {message ? (
        <p>{message}</p>
      ) : (
        <p>
          {needsChoice ? "Closest styles: " : "Detected: "}
          {summary}.
        </p>
      )}
      <p className="o-muted">
        {needsChoice
          ? "Pick how to read this input and the analysis re-runs with your choice."
          : "These are the closest styles, not a certain answer. Pick another to re-run the steps that depend on style."}
      </p>
      <div className="o-row" role="group" aria-label="Detected style candidates">
        {top.map((c) => (
          <button
            key={c.format_id}
            type="button"
            className="o-btn"
            aria-pressed={!needsChoice && c.format_id === sourceFormat}
            disabled={busy}
            onClick={() => onChoose(c.format_id)}
          >
            {formatName(c.format_id)} <span className="o-mono">{pct(c.score)}</span>
          </button>
        ))}
      </div>
      <details className={css.disclosure}>
        <summary>What auto-detect cannot tell apart</summary>
        {limits.length > 0 ? (
          <ul>
            {limits.map((l) => (
              <li key={l}>{l}</li>
            ))}
          </ul>
        ) : (
          <p>Auto-detect is a helper, not an oracle.</p>
        )}
      </details>
    </section>
  );
}
