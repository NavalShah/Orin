import { describe, expect, it } from "vitest";
import type { FormatInfo, LossReport } from "./api";
import {
  clock, confidenceText, costText, humanize, lookalikeLabel, lossReportText, nearestNote, pct, reviewLabel,
  severityLabel, splitLookalikes, stageLabel,
} from "./format";

describe("display helpers", () => {
  it("formats percentages, clocks and confidences", () => {
    expect(pct(0.716)).toBe("72%");
    expect(pct(null)).toBe("n/a");
    expect(clock(65.2)).toBe("1:05");
    expect(confidenceText(0.4)).toBe("40% (low)");
  });

  it("names the nearest note to a frequency", () => {
    expect(nearestNote(440)).toBe("A4");
    expect(nearestNote(146.83)).toBe("D3");
    expect(nearestNote(445)).toBe("A4 +20 cents");
    expect(nearestNote(0)).toBe("");
  });

  it("puts codes, statuses and stages into words", () => {
    expect(humanize("MICROTONE_ROUNDED")).toBe("Microtone rounded");
    expect(reviewLabel("unreviewed")).toBe("Unreviewed draft");
    expect(severityLabel("major")).toBe("Major loss");
    expect(stageLabel("tonic")).toBe("Finding the tonic");
    expect(stageLabel("some_new_stage")).toBe("Some new stage");
    expect(costText(0)).toBe("No cost to the score");
    expect(costText(0.012)).toBe("Cost: 1.2 of 100 points");
  });

  it("separates exact lookalikes from rotations and labels them from the registry", () => {
    expect(splitLookalikes(["a:x", "b:y~", "c:z~"])).toEqual({ exact: ["a:x"], rotations: ["b:y", "c:z"] });
    const formats = [{ id: "a", display_name: "Alpha", modes: [{ id: "x", label: "Ex" }] }] as unknown as FormatInfo[];
    expect(lookalikeLabel("a:x", formats)).toBe("Alpha: Ex");
    expect(lookalikeLabel("q:r~", formats)).toBe("q:r");
  });

  it("writes a readable loss report", () => {
    const report = {
      items: [{ code: "ORNAMENT_DROPPED", severity: "major", time_s: 1.5, human_text: "Movement dropped." }],
      notices: [{ code: "TONIC_ASSUMED", time_s: null, human_text: "Tonic assumed." }],
      summary: { fidelity_score: 0.8, pitch_score: 0.9, ornament_score: 0.6, timing_score: 1, max_cents_delta: 31.4 },
      source_format: "a",
      target_format: "b",
    } as unknown as LossReport;
    const text = lossReportText(report);
    expect(text).toContain("Loss report: a -> b");
    expect(text).toContain("Fidelity 0.80");
    expect(text).toContain("[Major loss] Ornament dropped at 1.50 s: Movement dropped.");
    expect(text).toContain("About the analysis (not counted as loss):");
    expect(lossReportText({ ...report, items: [] })).toContain("Nothing was lost in this conversion.");
  });
});
