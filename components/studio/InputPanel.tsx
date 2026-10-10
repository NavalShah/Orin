"use client";

import { useState, type ChangeEvent, type DragEvent } from "react";
import { errorText, type Asset, type ConvertRequest, type FormatsResponse } from "@/lib/api";
import { bytes, clock } from "@/lib/format";
import { Waveform } from "@/components/audio/Waveform";
import { LibraryPicker, RIGHTS_NOTICE, useLibrary } from "@/components/shell/Library";
import { ACCEPT, AUDIO_EXT, NOTES_EXT, audioDuration, durationProblem, validateFile } from "./validate";
import css from "./Studio.module.css";

export type NotesSyntax = NonNullable<ConvertRequest["notes_syntax"]>;
export type InputMode = "file" | "text";

const SYNTAXES: Array<{ value: NotesSyntax; label: string }> = [
  { value: "auto", label: "Work it out for me" },
  { value: "sargam", label: "Sargam (Sa Ri Ga...)" },
  { value: "note_names", label: "Note names (C D E...)" },
  { value: "abc", label: "ABC" },
  { value: "musicxml", label: "MusicXML" },
];

const isSound = (a: Asset) => a.kind === "audio" || a.kind === "stem";

/** The left of the Studio: a file (dropped, chosen or from the Library) or typed notes (spec 11.2). */
export function InputPanel({
  registry,
  mode,
  onMode,
  asset,
  onAsset,
  notesText,
  onNotesText,
  notesSyntax,
  onNotesSyntax,
  disabled = false,
}: {
  registry: FormatsResponse;
  mode: InputMode;
  onMode: (mode: InputMode) => void;
  asset: Asset | null;
  onAsset: (asset: Asset | null) => void;
  notesText: string;
  onNotesText: (text: string) => void;
  notesSyntax: NotesSyntax;
  onNotesSyntax: (syntax: NotesSyntax) => void;
  disabled?: boolean;
}) {
  const { upload } = useLibrary();
  const limits = registry.limits;
  const [dragging, setDragging] = useState(false);
  const [working, setWorking] = useState(false);
  const [status, setStatus] = useState("");
  const [problems, setProblems] = useState<string[]>([]);

  async function accept(file: File) {
    setProblems([]);
    setStatus("");
    const check = validateFile(file, limits);
    if (check.problems.length > 0) {
      setProblems(check.problems);
      return;
    }
    setWorking(true);
    try {
      let lengthNote = "";
      if (check.kind === "audio") {
        setStatus(`Checking ${file.name}...`);
        const seconds = await audioDuration(file);
        const tooLong = durationProblem(file.name, seconds, limits);
        if (tooLong) {
          setProblems([tooLong]);
          setStatus("");
          return;
        }
        if (seconds == null) lengthNote = " This browser could not read its length, so the server will check it.";
      }
      setStatus(`Uploading ${file.name}...`);
      const uploaded = await upload(file);
      onAsset(uploaded);
      setStatus(`${uploaded.filename} is ready.${lengthNote}`);
    } catch (err) {
      setStatus("");
      setProblems([errorText(err)]);
    } finally {
      setWorking(false);
    }
  }

  function onChoose(e: ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (file) void accept(file);
  }

  function onDrop(e: DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setDragging(false);
    if (disabled || working) return;
    const file = e.dataTransfer.files?.[0];
    if (file) void accept(file);
  }

  function pick(picked: Asset) {
    const tooLong = durationProblem(picked.filename, picked.duration_s, limits);
    if (tooLong) {
      setProblems([tooLong]);
      return;
    }
    setProblems([]);
    setStatus(`${picked.filename} is ready.`);
    onAsset(picked);
  }

  return (
    <section className="o-panel o-stack" aria-labelledby="orin-input-title">
      <h2 id="orin-input-title">Input</h2>
      <div className="o-row" role="group" aria-label="Kind of input" style={{ gap: 0 }}>
        <button type="button" className={`o-btn ${css.segLeft}`} aria-pressed={mode === "file"} onClick={() => onMode("file")}>
          A file
        </button>
        <button type="button" className={`o-btn ${css.segRight}`} aria-pressed={mode === "text"} onClick={() => onMode("text")}>
          Typed notes
        </button>
      </div>

      {mode === "file" ? (
        <>
          <div
            className={`${css.drop} ${dragging ? css.dropActive : ""}`}
            onDragOver={(e) => {
              e.preventDefault();
              setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={onDrop}
          >
            <p>
              <strong>Drop an audio file or a notes file here</strong>
              {dragging ? " - let go to add it" : ", or choose one:"}
            </p>
            <label className="o-label">
              Choose a file
              <input id="orin-file-input" type="file" accept={ACCEPT} onChange={onChoose} disabled={disabled || working} />
            </label>
            <p>{RIGHTS_NOTICE}</p>
            <p className="o-muted">
              Up to {limits.max_upload_mb} MB and {clock(limits.max_audio_seconds)} of audio. Audio: {AUDIO_EXT.join(", ")}. Notes:{" "}
              {NOTES_EXT.join(", ")}.
            </p>
          </div>
          <LibraryPicker
            label="Or pick from your Library"
            kinds={["audio", "stem", "midi", "notation"]}
            onPick={pick}
            disabled={disabled || working}
          />
          <p role="status" aria-live="polite" className={status ? undefined : "o-sr-only"}>
            {status}
          </p>
          {problems.length > 0 ? (
            <div className="o-alert" role="alert">
              <ul className={css.plainList}>
                {problems.map((p) => (
                  <li key={p}>{p}</li>
                ))}
              </ul>
            </div>
          ) : null}
          {asset ? (
            <div className={`o-stack ${css.chosen}`}>
              <p>
                <strong>{asset.filename}</strong>{" "}
                <span className="o-muted">
                  {asset.kind}
                  {asset.duration_s != null ? `, ${clock(asset.duration_s)}` : ""}, {bytes(asset.size_bytes)}
                </span>
                {asset.standin ? <span className="o-badge o-badge-warn"> Offline stand-in</span> : null}
              </p>
              {isSound(asset) ? <Waveform asset={asset} /> : <p className="o-muted">This is a notes file, so there is no waveform.</p>}
              <div>
                <button
                  type="button"
                  className="o-btn o-btn-small"
                  onClick={() => {
                    // This button is about to go; put focus on the file chooser.
                    document.getElementById("orin-file-input")?.focus();
                    setStatus("The file was removed.");
                    onAsset(null);
                  }}
                  disabled={disabled}
                >
                  Remove
                </button>
              </div>
            </div>
          ) : (
            <p className="o-muted">No file chosen yet.</p>
          )}
        </>
      ) : (
        <>
          <label className="o-label">
            Notes
            <textarea
              className="o-textarea o-mono"
              rows={8}
              value={notesText}
              onChange={(e) => onNotesText(e.target.value)}
              spellCheck={false}
              autoCapitalize="off"
              autoCorrect="off"
              placeholder="Type or paste notes here"
            />
          </label>
          <label className="o-label">
            Notes are written in
            <select className="o-select" value={notesSyntax} onChange={(e) => onNotesSyntax(e.target.value as NotesSyntax)}>
              {SYNTAXES.map((s) => (
                <option key={s.value} value={s.value}>
                  {s.label}
                </option>
              ))}
            </select>
          </label>
          <details className={css.disclosure}>
            <summary>How to type sargam</summary>
            <pre className={css.help}>{registry.sargam_help}</pre>
          </details>
        </>
      )}
    </section>
  );
}
