// Small display helpers shared by the Orin screens. Pure functions: no React, no API calls.
import type { FormatInfo, LossItem, LossReport } from "./api";

export const pct = (x: number | null | undefined): string =>
  x == null || !Number.isFinite(x) ? "n/a" : `${Math.round(x * 100)}%`;

/** Seconds as m:ss. */
export function clock(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds)) return "-:--";
  const s = Math.max(0, Math.round(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

export function bytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${Math.round(n / 1024)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

const NOTE_NAMES = ["C", "C♯", "D", "E♭", "E", "F", "F♯", "G", "A♭", "A", "B♭", "B"];

/** The nearest equal-tempered note to a frequency: "D3", or "D3 +14 cents" when it sits off the note. */
export function nearestNote(hz: number, a4 = 440): string {
  if (!(hz > 0)) return "";
  const midi = 69 + 12 * Math.log2(hz / a4);
  const nearest = Math.round(midi);
  const cents = Math.round((midi - nearest) * 100);
  const name = `${NOTE_NAMES[((nearest % 12) + 12) % 12]}${Math.floor(nearest / 12) - 1}`;
  if (cents === 0) return name;
  return `${name} ${cents > 0 ? "+" : "-"}${Math.abs(cents)} cents`;
}

export function confidenceWord(c: number): "high" | "medium" | "low" {
  if (c >= 0.75) return "high";
  if (c >= 0.5) return "medium";
  return "low";
}

/** "72% (medium)": a confidence is always shown with a word, not a bare number. */
export const confidenceText = (c: number): string => `${pct(c)} (${confidenceWord(c)})`;

/** "MICROTONE_ROUNDED" -> "Microtone rounded". */
export function humanize(code: string): string {
  const words = code.replace(/[_.]+/g, " ").trim().toLowerCase();
  return words ? words[0].toUpperCase() + words.slice(1) : code;
}

const REVIEW_LABELS: Record<string, string> = {
  unreviewed: "Unreviewed draft",
  self_checked: "Self-checked draft",
  practitioner_reviewed: "Reviewed by a practitioner",
};
export const reviewLabel = (status: FormatInfo["review_status"] | string): string =>
  REVIEW_LABELS[status] ?? humanize(status);

export const SEVERITY: Record<LossItem["severity"], { label: string; icon: string }> = {
  info: { label: "For information", icon: "ⓘ" },
  minor: { label: "Minor loss", icon: "△" },
  major: { label: "Major loss", icon: "▲" },
};
export const severityLabel = (s: string): string => SEVERITY[s as LossItem["severity"]]?.label ?? humanize(s);
export const severityIcon = (s: string): string => SEVERITY[s as LossItem["severity"]]?.icon ?? "•";

const STAGE_LABELS: Record<string, string> = {
  queued: "Waiting to start",
  ingest: "Reading the file",
  parse: "Reading the notes",
  preprocess: "Cleaning up the audio",
  separate: "Separating the melody from the backing",
  melody: "Picking out the melody line",
  pitch: "Tracking the pitch",
  voicing: "Removing unvoiced sound",
  detect: "Guessing the style",
  tonic: "Finding the tonic",
  cents: "Measuring pitches against the tonic",
  segment: "Splitting the melody into notes",
  ornament: "Looking at the pitch movement inside notes",
  rhythm: "Finding the pulse and the cycle",
  mode: "Comparing with known modes",
  assemble: "Putting the analysis together",
  adapt: "Converting to the output format",
  convert: "Converting to the output format",
  notation: "Writing the notation",
  render: "Writing the notation",
  loss: "Building the loss report",
  done: "Done",
};
/** A job or log stage in words. Unknown stages fall back to their own name. */
export const stageLabel = (stage: string | null | undefined): string =>
  stage ? (STAGE_LABELS[stage] ?? humanize(stage)) : "Working";

/** The cost of one loss item, as points of a 0 to 100 fidelity score. */
export function costText(penalty: number): string {
  if (!(penalty > 0)) return "No cost to the score";
  const points = penalty * 100;
  return `Cost: ${points < 0.1 ? "under 0.1" : points.toFixed(1)} of 100 points`;
}

/** Mode lookalikes arrive as "format:mode"; a trailing "~" marks a rotation, not a match. */
export function splitLookalikes(refs: readonly string[]): { exact: string[]; rotations: string[] } {
  const exact: string[] = [];
  const rotations: string[] = [];
  for (const ref of refs) {
    if (ref.endsWith("~")) rotations.push(ref.slice(0, -1));
    else exact.push(ref);
  }
  return { exact, rotations };
}

/** "arabic_maqam:ajam" -> "Arabic Maqam: Ajam", using the registry; the raw reference if unknown. */
export function lookalikeLabel(ref: string, formats: readonly FormatInfo[]): string {
  const clean = ref.replace(/~$/, "");
  const cut = clean.indexOf(":");
  if (cut < 0) return clean;
  const format = formats.find((f) => f.id === clean.slice(0, cut));
  const mode = format?.modes.find((m) => m.id === clean.slice(cut + 1));
  if (!format) return clean;
  return `${format.display_name}: ${mode?.label ?? clean.slice(cut + 1)}`;
}

/** The readable export of a loss report (spec 9.3). Mirrors LossReport.to_text on the server. */
export function lossReportText(report: LossReport, name: (id: string | null | undefined) => string = (id) => id ?? "unknown"): string {
  const s = report.summary;
  const lines = [`Loss report: ${name(report.source_format)} -> ${name(report.target_format)}`];
  if (s) {
    lines.push(
      `Fidelity ${s.fidelity_score.toFixed(2)}  (pitch ${s.pitch_score.toFixed(2)}, ornament ${s.ornament_score.toFixed(2)}, timing ${s.timing_score.toFixed(2)})`,
      `Largest pitch change: ${Math.round(s.max_cents_delta)} cents`,
    );
  }
  lines.push("");
  if (report.items.length === 0) lines.push("Nothing was lost in this conversion.");
  for (const item of report.items) {
    const where = item.time_s != null ? ` at ${item.time_s.toFixed(2)} s` : "";
    lines.push(`[${severityLabel(item.severity)}] ${humanize(item.code)}${where}: ${item.human_text}`);
  }
  if (report.notices.length) {
    lines.push("", "About the analysis (not counted as loss):");
    for (const n of report.notices) {
      const where = n.time_s != null ? ` at ${n.time_s.toFixed(2)} s` : "";
      lines.push(`- ${humanize(n.code)}${where}: ${n.human_text}`);
    }
  }
  return lines.join("\n") + "\n";
}

/** A safe file name stem from free text. */
export const fileStem = (text: string): string =>
  text.toLowerCase().replace(/\.[a-z0-9]+$/, "").replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 40) || "orin";
