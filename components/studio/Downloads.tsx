"use client";

import Link from "next/link";
import { useState } from "react";
import { api, errorText, type ConversionOut, type OutputFile } from "@/lib/api";
import { fileStem, lossReportText } from "@/lib/format";
import { handoff } from "@/lib/handoff";
import { saveBlob, saveText, saveUrl } from "@/components/shell/download";
import type { Registry } from "@/components/shell/registry";
import { svgPagesToPng } from "./png";
import css from "./Studio.module.css";

async function saveOutput(o: OutputFile, stem: string): Promise<void> {
  const name = `${stem}-${o.kind}.${o.ext}`;
  if (o.url) return saveUrl(o.url, name, o.media_type);
  if (o.text != null) return saveText(o.text, name, o.media_type);
  const pages = o.pages ?? [];
  if (pages.length === 0) throw new Error("This output has no file.");
  pages.forEach((page, i) => saveText(page, pages.length === 1 ? name : `${stem}-${o.kind}-page-${i + 1}.${o.ext}`, o.media_type));
}

/** The bottom of the Studio: every output as a file, then the handoffs (spec 11.2, 3.5). */
export function Downloads({
  conversion,
  registry,
  selectedIndex,
}: {
  conversion: ConversionOut;
  registry: Registry;
  selectedIndex: number | null;
}) {
  const [status, setStatus] = useState("");
  const [error, setError] = useState<string | null>(null);
  const outputs = conversion.outputs ?? [];
  const staff = outputs.find((o) => (o.pages?.length ?? 0) > 0);
  const stem = `orin-${fileStem(conversion.source_asset?.filename ?? "notes")}-${fileStem(conversion.output_format)}`;
  const loss = conversion.loss;

  async function run(label: string, save: () => Promise<void> | void) {
    setError(null);
    setStatus(`Preparing ${label}...`);
    try {
      await save();
      setStatus(`${label} saved.`);
    } catch (err) {
      setStatus("");
      setError(`${label} could not be saved. ${errorText(err)}`);
    }
  }

  return (
    <section className={`o-panel o-stack ${css.downloads}`} aria-labelledby="orin-downloads-title" data-noprint>
      <h2 id="orin-downloads-title">Downloads</h2>
      <div className="o-row">
        {outputs.map((o) => (
          <button key={o.kind} type="button" className="o-btn o-btn-small" onClick={() => void run(o.label, () => saveOutput(o, stem))}>
            {o.label} <span className="o-mono">.{o.ext}</span>
          </button>
        ))}
        {staff ? (
          <button
            type="button"
            className="o-btn o-btn-small"
            onClick={() => void run("Staff image", async () => saveBlob(await svgPagesToPng(staff.pages ?? []), `${stem}-staff.png`))}
          >
            Staff image <span className="o-mono">.png</span>
          </button>
        ) : null}
        <button type="button" className="o-btn o-btn-small" onClick={() => window.print()}>
          Print or save as PDF
        </button>
        <button
          type="button"
          className="o-btn o-btn-small"
          onClick={() => void run("Analysis data", async () => saveBlob(await api.conversionIr(conversion.id), `${stem}-ir.json`))}
        >
          Analysis data (IR) <span className="o-mono">.json</span>
        </button>
        {loss ? (
          <>
            <button
              type="button"
              className="o-btn o-btn-small"
              onClick={() => void run("Loss report", () => saveText(JSON.stringify(loss, null, 2), `${stem}-loss-report.json`, "application/json"))}
            >
              Loss report <span className="o-mono">.json</span>
            </button>
            <button
              type="button"
              className="o-btn o-btn-small"
              onClick={() => void run("Loss report", () => saveText(lossReportText(loss, registry.formatName), `${stem}-loss-report.txt`))}
            >
              Loss report <span className="o-mono">.txt</span>
            </button>
          </>
        ) : null}
      </div>
      <p className="o-muted">
        Printing shows only the notation and its description; choose "Save as PDF" in the print dialog to keep a file.
      </p>
      <p role="status" aria-live="polite" className={status ? undefined : "o-sr-only"}>
        {status}
      </p>
      {error ? (
        <p className="o-alert" role="alert">
          {error}
        </p>
      ) : null}
      <div className="o-row">
        <Link className={`o-btn o-btn-primary ${css.linkButton}`} href={handoff.workspaceWithConversion(conversion.id)}>
          Open in Workspace
        </Link>
        <Link
          className={`o-btn ${css.linkButton}`}
          href={handoff.chatAboutLoss(conversion.id, selectedIndex != null ? [selectedIndex] : [])}
        >
          Ask Orin about this loss report
        </Link>
      </div>
    </section>
  );
}
