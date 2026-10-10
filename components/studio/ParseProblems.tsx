"use client";

import type { ReactNode } from "react";
import type { ParseProblem } from "@/lib/api";
import css from "./Studio.module.css";

/** One line of the echo, with each problem's span wrapped in <mark>. Columns are 1-based. */
function markLine(line: string, problems: ParseProblem[], lineNo: number): ReactNode[] {
  const out: ReactNode[] = [];
  let pos = 0;
  const sorted = [...problems].sort((a, b) => a.column - b.column);
  sorted.forEach((p, i) => {
    const start = Math.max(pos, Math.min(line.length, p.column - 1));
    const end = Math.min(line.length, Math.max(start, p.column - 1 + Math.max(1, p.length)));
    if (start > pos) out.push(line.slice(pos, start));
    out.push(
      <mark key={`${lineNo}-${i}`} className={css.problemMark} title={p.message} data-severity={p.severity}>
        {/* A problem at the end of the line still gets a visible place to point at. */}
        {end > start ? line.slice(start, end) : "␣"}
      </mark>,
    );
    pos = end;
  });
  if (pos < line.length) out.push(line.slice(pos));
  return out;
}

/** Typed-notes problems by line and column, and an echo of the text with those places marked (spec 7.14). */
export function ParseProblems({ text, problems }: { text: string; problems: ParseProblem[] }) {
  if (problems.length === 0) return null;
  const lines = text.split("\n");
  return (
    <section className="o-panel o-stack" aria-labelledby="orin-parse-title">
      <h2 id="orin-parse-title">Problems in the typed notes ({problems.length})</h2>
      <ul className={css.plainList}>
        {problems.map((p, i) => (
          <li key={i}>
            <strong>
              {p.severity === "warning" ? "Warning" : "Error"}, line {p.line}, column {p.column}:
            </strong>{" "}
            {p.message}
          </li>
        ))}
      </ul>
      {text ? (
        <>
          <h3>Your notes, with the problem places marked</h3>
          <pre className={css.echo} tabIndex={0} aria-label="Your notes, with the problem places marked">
            {lines.map((line, i) => (
              <span key={i} className={css.echoLine}>
                <span className={css.lineNo} aria-hidden="true">
                  {i + 1}
                </span>
                <span>
                  {markLine(
                    line,
                    problems.filter((p) => p.line === i + 1),
                    i + 1,
                  )}
                  {line.length === 0 && !problems.some((p) => p.line === i + 1) ? "\u00a0" : null}
                </span>
              </span>
            ))}
          </pre>
          <p className="o-muted">Marked places are underlined and boxed. Fix them in the notes box, then convert again.</p>
        </>
      ) : null}
    </section>
  );
}
