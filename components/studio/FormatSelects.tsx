"use client";

import { useId } from "react";
import type { FormatInfo, FormatsResponse } from "@/lib/api";
import { reviewLabel } from "@/lib/format";
import css from "./Studio.module.css";

const BADGE: Record<string, string> = {
  unreviewed: "o-badge-warn",
  self_checked: "o-badge-info",
  practitioner_reviewed: "o-badge-ok",
};

/**
 * One format dropdown, built from the registry and grouped by family (spec 4.5). Beside it:
 * the chosen format's review status in words and its caveats in a disclosure. Nothing here
 * names a format, so a format added on the server shows up with no UI change.
 */
export function FormatSelect({
  label,
  value,
  onChange,
  registry,
  allowAuto = false,
  disabled = false,
}: {
  label: string;
  value: string;
  onChange: (id: string) => void;
  registry: FormatsResponse;
  allowAuto?: boolean;
  disabled?: boolean;
}) {
  const id = useId();
  const byId = new Map(registry.formats.map((f) => [f.id, f]));
  const grouped = new Set<string>();
  const groups = registry.families
    .map((family) => ({
      family,
      formats: family.format_ids.map((fid) => byId.get(fid)).filter((f): f is FormatInfo => Boolean(f)),
    }))
    .filter((g) => g.formats.length > 0);
  for (const g of groups) for (const f of g.formats) grouped.add(f.id);
  // A format whose family is not listed is still offered.
  const loose = registry.formats.filter((f) => !grouped.has(f.id));
  const chosen = byId.get(value);

  return (
    <div className={css.formatField}>
      <label className="o-label" htmlFor={id}>
        {label}
      </label>
      <select
        id={id}
        className="o-select"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        disabled={disabled}
        aria-describedby={`${id}-status`}
      >
        {allowAuto && <option value="auto">Auto-detect</option>}
        {groups.map((g) => (
          <optgroup key={g.family.id} label={g.family.label}>
            {g.formats.map((f) => (
              <option key={f.id} value={f.id}>
                {f.display_name}
              </option>
            ))}
          </optgroup>
        ))}
        {loose.length > 0 && (
          <optgroup label="Other">
            {loose.map((f) => (
              <option key={f.id} value={f.id}>
                {f.display_name}
              </option>
            ))}
          </optgroup>
        )}
      </select>
      <div className={css.formatMeta}>
        {chosen ? (
          <>
            <span id={`${id}-status`} className={`o-badge ${BADGE[chosen.review_status] ?? "o-badge-warn"}`}>
              {reviewLabel(chosen.review_status)}
            </span>
            <details className={css.disclosure}>
              <summary>
                Caveats for {chosen.display_name} ({chosen.caveats.length})
              </summary>
              {chosen.description ? <p>{chosen.description}</p> : null}
              {chosen.reviewer_note ? <p>Review note: {chosen.reviewer_note}</p> : null}
              {chosen.caveats.length > 0 ? (
                <ul>
                  {chosen.caveats.map((c) => (
                    <li key={c}>{c}</li>
                  ))}
                </ul>
              ) : (
                <p>No caveats are recorded for this format.</p>
              )}
            </details>
          </>
        ) : (
          <>
            <span id={`${id}-status`} className="o-muted">
              {allowAuto && value === "auto"
                ? "Orin will suggest the three closest styles and ask if it is not sure."
                : "Choose a format."}
            </span>
            {allowAuto && value === "auto" && registry.detect_limits.length > 0 ? (
              <details className={css.disclosure}>
                <summary>What auto-detect cannot tell apart</summary>
                <ul>
                  {registry.detect_limits.map((limit) => (
                    <li key={limit}>{limit}</li>
                  ))}
                </ul>
              </details>
            ) : null}
          </>
        )}
      </div>
    </div>
  );
}

/** The two dropdowns of the top bar: how to read the input, and what to write. */
export function FormatSelects({
  registry,
  inputFormat,
  outputFormat,
  onInputChange,
  onOutputChange,
  disabled = false,
}: {
  registry: FormatsResponse;
  inputFormat: string;
  outputFormat: string;
  onInputChange: (id: string) => void;
  onOutputChange: (id: string) => void;
  disabled?: boolean;
}) {
  return (
    <div className={css.selects}>
      <FormatSelect
        label="Read the input as"
        value={inputFormat}
        onChange={onInputChange}
        registry={registry}
        allowAuto
        disabled={disabled}
      />
      <span className={css.arrow} aria-hidden="true">
        →
      </span>
      <FormatSelect
        label="Write the notation as"
        value={outputFormat}
        onChange={onOutputChange}
        registry={registry}
        disabled={disabled}
      />
    </div>
  );
}
