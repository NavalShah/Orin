"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  api, errorText, type Asset, type ConversionOut, type ConvertRequest, type Job, type JobRef, type RerunRequest,
} from "@/lib/api";
import { pct, stageLabel } from "@/lib/format";
import { handoff, readHandoff } from "@/lib/handoff";
import { useLibrary } from "@/components/shell/Library";
import { useRegistry } from "@/components/shell/registry";
import { ContourPlot } from "./ContourPlot";
import { DetectionBanner } from "./DetectionBanner";
import { Downloads } from "./Downloads";
import { ExplanationPanel } from "./ExplanationPanel";
import { FormatSelects } from "./FormatSelects";
import { InputPanel, type InputMode, type NotesSyntax } from "./InputPanel";
import { IRSummaryPanel } from "./IRSummaryPanel";
import { LossPanel } from "./LossPanel";
import { NotationViewer } from "./NotationViewer";
import { ParseProblems } from "./ParseProblems";
import { ProcessingLog } from "./ProcessingLog";
import css from "./Studio.module.css";

type Problem = { text: string; retry?: () => void };

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));
const isAbort = (err: unknown) => (err as Error)?.name === "AbortError";

/** The Notation Studio (spec 11): input on the left, notation in the centre, loss on the right. */
export function StudioScreen() {
  const reg = useRegistry();
  const { registry, formatName } = reg;
  const library = useLibrary();
  const router = useRouter();
  const params = useSearchParams();
  const incoming = useMemo(() => readHandoff(params), [params]);

  const [inputFormat, setInputFormat] = useState("auto");
  const [outputFormat, setOutputFormat] = useState(() => registry.formats[0]?.id ?? "");
  const [mode, setMode] = useState<InputMode>("file");
  const [asset, setAsset] = useState<Asset | null>(null);
  const [notesText, setNotesText] = useState("");
  const [notesSyntax, setNotesSyntax] = useState<NotesSyntax>("auto");

  const [conversion, setConversion] = useState<ConversionOut | null>(null);
  const [revision, setRevision] = useState(0);
  const [job, setJob] = useState<Job | null>(null);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("");
  const [problem, setProblem] = useState<Problem | null>(null);
  const [selectedNoteId, setSelectedNoteId] = useState<string | null>(null);
  const [selectedIndex, setSelectedIndex] = useState<number | null>(null);
  const [tab, setTab] = useState<string | null>(null);

  const abortRef = useRef<AbortController | null>(null);
  const jobIdRef = useRef<string | null>(null);
  const shownRef = useRef<string | null>(null);

  const show = useCallback(
    (c: ConversionOut) => {
      shownRef.current = c.id;
      setConversion(c);
      setRevision((n) => n + 1);
      setSelectedNoteId(null);
      setSelectedIndex(null);
      if (c.status === "awaiting_style" || c.needs_style_choice) {
        // Spec 8.2: prefill the dropdown with the top candidate and ask.
        if (c.detected?.[0]) setInputFormat(c.detected[0].format_id);
        setStatus("Orin is not sure of the style. Please choose how to read this input.");
      } else if (c.status === "failed") {
        setStatus("The conversion failed. The details are below.");
      } else if (c.status === "done") {
        const n = c.ir?.note_count ?? c.score?.notes.length ?? 0;
        setStatus(`Done: ${n} ${n === 1 ? "note" : "notes"} written as ${formatName(c.output_format)}.`);
      } else {
        setStatus("The conversion is still running.");
      }
    },
    [formatName],
  );

  /** Follow a job to its end, then load and show its conversion. */
  const follow = useCallback(
    async (ref: JobRef, fallbackId: string | null, message: string) => {
      const ac = new AbortController();
      abortRef.current = ac;
      jobIdRef.current = ref.job_id;
      setBusy(true);
      setProblem(null);
      setJob(null);
      setStatus(message);
      try {
        const final = await api.watchJob(ref.job_id, setJob, ac.signal);
        if (final.status === "cancelled") {
          setStatus("Cancelled.");
          return;
        }
        const id = ref.conversion_id ?? (final.result?.kind === "conversion" ? final.result.id : null) ?? fallbackId;
        let shown: ConversionOut | null = null;
        if (id) {
          shown = await api.conversion(id);
          show(shown);
          router.replace(handoff.studioWithConversion(shown.id), { scroll: false });
          void library.refresh();
        }
        if (final.status === "failed" && !shown?.error) {
          const e = final.error;
          setProblem({ text: e ? (e.hint ? `${e.message} ${e.hint}` : e.message) : "The conversion failed." });
          if (!shown) setStatus("The conversion failed.");
        }
      } catch (err) {
        if (isAbort(err)) setStatus("Cancelled.");
        else setProblem({ text: errorText(err) });
      } finally {
        setBusy(false);
        setJob(null);
        abortRef.current = null;
        jobIdRef.current = null;
      }
    },
    [library, router, show],
  );

  const canConvert = !busy && Boolean(outputFormat) && (mode === "file" ? Boolean(asset) : notesText.trim().length > 0);
  const sameFormat = inputFormat !== "auto" && inputFormat === outputFormat;

  async function convert() {
    if (!canConvert) return;
    const req: ConvertRequest = {
      input_format: inputFormat,
      output_format: outputFormat,
      ...(mode === "file" ? { asset_id: asset?.id } : { notes_text: notesText, notes_syntax: notesSyntax }),
    };
    setBusy(true);
    setProblem(null);
    setStatus("Starting the conversion...");
    try {
      const ref = await api.convert(req);
      await follow(ref, null, "Starting the conversion...");
    } catch (err) {
      setBusy(false);
      setStatus("");
      setProblem({ text: errorText(err), retry: () => void convert() });
    }
  }

  async function rerun(req: RerunRequest, what: string) {
    if (!conversion || busy) return;
    const id = conversion.id;
    setBusy(true);
    setProblem(null);
    setStatus(`Re-running with ${what}...`);
    try {
      const ref = await api.rerun(id, req);
      await follow(ref, id, `Re-running with ${what}...`);
    } catch (err) {
      setBusy(false);
      setStatus("");
      setProblem({ text: errorText(err), retry: () => void rerun(req, what) });
    }
  }

  async function cancel() {
    const id = jobIdRef.current;
    abortRef.current?.abort();
    if (id) {
      try {
        await api.cancelJob(id);
      } catch {
        // The job may already have finished; the status line says what happened.
      }
    }
  }

  function startOver() {
    shownRef.current = null;
    setConversion(null);
    setSelectedNoteId(null);
    setSelectedIndex(null);
    setProblem(null);
    setStatus("Ready for a new conversion.");
    router.replace("/studio", { scroll: false });
  }

  // Handoff: /studio?conversion=ID opens a finished (or still running) conversion.
  useEffect(() => {
    const id = incoming.conversion;
    if (!id || id === shownRef.current) return;
    let live = true;
    setBusy(true);
    setStatus("Opening the conversion...");
    (async () => {
      try {
        let c = await api.conversion(id);
        while (live && c.status === "running") {
          await sleep(1500);
          if (live) c = await api.conversion(id);
        }
        if (!live) return;
        setInputFormat(c.input_format || "auto");
        if (registry.formats.some((f) => f.id === c.output_format)) setOutputFormat(c.output_format);
        if (c.source_asset) {
          setAsset(c.source_asset);
          setMode("file");
        } else if (c.notes_text != null) {
          setNotesText(c.notes_text);
          setMode("text");
        }
        show(c);
      } catch (err) {
        if (live) {
          setStatus("");
          setProblem({ text: `The conversion from the link could not be opened. ${errorText(err)}` });
        }
      } finally {
        if (live) setBusy(false);
      }
    })();
    return () => {
      live = false;
    };
  }, [incoming.conversion, registry.formats, show]);

  // Handoff: /studio?asset=ID brings a Library file in as the input.
  useEffect(() => {
    const id = incoming.asset;
    if (!id) return;
    let live = true;
    api
      .getAsset(id)
      .then((a) => {
        if (!live) return;
        setAsset(a);
        setMode("file");
        setStatus(`${a.filename} is ready to convert.`);
      })
      .catch((err) => {
        if (live) setProblem({ text: `The file from the link could not be opened. ${errorText(err)}` });
      });
    return () => {
      live = false;
    };
  }, [incoming.asset]);

  useEffect(() => () => abortRef.current?.abort(), []);

  const notes = useMemo(() => conversion?.score?.notes ?? [], [conversion]);
  const items = useMemo(() => conversion?.loss?.items ?? [], [conversion]);

  function selectItem(index: number, noteId: string | null) {
    setSelectedIndex(index);
    setSelectedNoteId(noteId);
  }

  function selectNote(noteId: string | null) {
    setSelectedNoteId(noteId);
    const eventId = noteId ? notes.find((n) => n.id === noteId)?.source_event_id : null;
    const index = eventId ? items.findIndex((item) => item.event_id === eventId || item.event_ids.includes(eventId)) : -1;
    setSelectedIndex(index >= 0 ? index : null);
  }

  const failed = conversion?.status === "failed";
  const awaiting = conversion ? conversion.status === "awaiting_style" || conversion.needs_style_choice : false;
  const hasResult = Boolean(conversion && conversion.score && !failed);
  const jobLine = job ? `${stageLabel(job.stage)}${job.message ? `: ${job.message}` : ""}` : status;

  return (
    <div className={css.studio}>
      <header className="o-stack" style={{ gap: 4 }} data-noprint>
        <h1>Notation Studio</h1>
        <p className="o-muted">
          Turn a recording or typed notes into another tradition&apos;s notation, and see what the new notation could not carry.{" "}
          {registry.monophonic_note}
        </p>
      </header>

      <section className={`o-panel o-stack ${css.topBar}`} aria-label="Conversion settings" data-noprint>
        <div className={css.topRow}>
          <FormatSelects
            registry={registry}
            inputFormat={inputFormat}
            outputFormat={outputFormat}
            onInputChange={setInputFormat}
            onOutputChange={setOutputFormat}
            disabled={busy}
          />
          <div className={css.actions}>
            <button type="button" className="o-btn o-btn-primary" onClick={() => void convert()} disabled={!canConvert}>
              {sameFormat ? "Clean up / quantize" : "Convert"}
            </button>
            {busy ? (
              <button type="button" className="o-btn" onClick={() => void cancel()}>
                Cancel
              </button>
            ) : null}
            {conversion && !busy ? (
              <button type="button" className="o-btn o-btn-quiet" onClick={startOver}>
                Start over
              </button>
            ) : null}
          </div>
        </div>
        {sameFormat ? (
          <p className="o-note">
            The same format is chosen on both sides, so this is clean-up / quantize mode: Orin tidies the notes onto that
            format&apos;s own grid. A loss report is still produced.
          </p>
        ) : null}
        {!busy && !canConvert ? (
          <p className="o-muted">{mode === "file" ? "Add a file below to convert." : "Type some notes below to convert."}</p>
        ) : null}
        {busy ? (
          <div className="o-row" style={{ flexWrap: "nowrap" }}>
            <progress
              className="o-progress"
              max={1}
              {...(job ? { value: Math.min(1, Math.max(0, job.progress)) } : {})}
              aria-label="Conversion progress"
            />
            <span className="o-mono" aria-hidden="true">
              {job ? pct(job.progress) : ""}
            </span>
          </div>
        ) : null}
        <p role="status" aria-live="polite" className={css.status}>
          {busy ? jobLine : status}
        </p>
        {problem ? (
          <div className="o-alert" role="alert">
            <p>{problem.text}</p>
            {problem.retry ? (
              <p style={{ marginTop: 8 }}>
                <button type="button" className="o-btn o-btn-small" onClick={problem.retry}>
                  Try again
                </button>
              </p>
            ) : null}
          </div>
        ) : null}
      </section>

      <div className={css.columns}>
        <div className={css.column} data-noprint>
          <InputPanel
            registry={registry}
            mode={mode}
            onMode={setMode}
            asset={asset}
            onAsset={setAsset}
            notesText={notesText}
            onNotesText={setNotesText}
            notesSyntax={notesSyntax}
            onNotesSyntax={setNotesSyntax}
            disabled={busy}
          />
          {conversion && hasResult ? <IRSummaryPanel conversion={conversion} registry={reg} busy={busy} onRerun={(req, what) => void rerun(req, what)} /> : null}
        </div>

        <div className={css.column}>
          {conversion && (conversion.detected?.length ?? 0) > 0 ? (
            <div data-noprint>
              <DetectionBanner
                detected={conversion.detected ?? []}
                needsChoice={awaiting}
                message={conversion.detect_message}
                sourceFormat={conversion.source_format}
                formatName={formatName}
                limits={registry.detect_limits}
                busy={busy}
                onChoose={(id) => {
                  setInputFormat(id);
                  void rerun({ input_format: id }, `the input read as ${formatName(id)}`);
                }}
              />
            </div>
          ) : null}

          {conversion && failed ? (
            <div className="o-alert" role="alert">
              <p>
                <strong>This conversion failed.</strong> {conversion.error?.message ?? "The server did not say why."}
              </p>
              {conversion.error?.hint ? <p>{conversion.error.hint}</p> : null}
            </div>
          ) : null}
          {conversion && (conversion.parse_problems?.length ?? 0) > 0 ? (
            <ParseProblems text={conversion.notes_text ?? notesText} problems={conversion.parse_problems ?? []} />
          ) : null}

          {conversion && hasResult ? (
            <NotationViewer
              conversion={conversion}
              registry={reg}
              selectedNoteId={selectedNoteId}
              onSelectNote={selectNote}
              tab={tab}
              onTab={setTab}
            />
          ) : (
            <section className="o-panel o-stack" aria-labelledby="orin-empty-title" data-noprint>
              <h2 id="orin-empty-title">Notation</h2>
              <p className="o-muted">
                {awaiting
                  ? "Choose a style above and the notation will be written here."
                  : failed
                    ? "Nothing was written. Fix the problem above and convert again."
                    : "The notation will appear here. Choose how to read the input and what to write, add a file or type notes, then press Convert."}
              </p>
            </section>
          )}

          {conversion && hasResult && conversion.ir ? (
            <ContourPlot ir={conversion.ir} notes={notes} items={items} selectedNoteId={selectedNoteId} onSelectNote={selectNote} />
          ) : null}
        </div>

        <div className={css.column} data-noprint>
          {conversion && hasResult && conversion.loss ? (
            <>
              <LossPanel
                loss={conversion.loss}
                notes={notes}
                howScored={conversion.how_scored}
                selectedIndex={selectedIndex}
                onSelect={selectItem}
                formatName={formatName}
              />
              <ExplanationPanel
                conversionId={conversion.id}
                revision={revision}
                selectedIndex={selectedIndex}
                selectedItem={selectedIndex != null ? (items[selectedIndex] ?? null) : null}
              />
            </>
          ) : (
            <section className="o-panel o-stack" aria-labelledby="orin-loss-empty">
              <h2 id="orin-loss-empty">Loss report</h2>
              <p className="o-muted">
                After a conversion, this lists everything the new notation could not carry, with a plain explanation of why.
              </p>
            </section>
          )}
          {conversion && hasResult && conversion.ir ? <ProcessingLog ir={conversion.ir} /> : null}
        </div>
      </div>

      {conversion && hasResult ? <Downloads conversion={conversion} registry={reg} selectedIndex={selectedIndex} /> : null}
    </div>
  );
}
