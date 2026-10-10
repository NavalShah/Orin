"use client";

import { useCallback, useEffect, useRef, useState, type DragEvent, type FormEvent } from "react";
import { api, errorText, type Asset, type ProjectSummary } from "@/lib/api";
import { computePeaks, drawPeaks, type WavePeaks } from "@/lib/audio/peaks";
import { AUDIO_ACCEPT, type BufferCache } from "@/lib/workspace/buffers";
import { generateHere } from "@/lib/workspace/generate";
import { importAudioFile } from "@/lib/workspace/importAudio";
import { migrate, uid, type Project } from "@/lib/workspace/project";
import { thumbnailRects } from "@/lib/workspace/thumbnail";
import { formatClock } from "@/lib/workspace/time";
import { ASSET_MIME } from "./Playlist";
import s from "./workspace.module.css";

type Props = {
  cache: BufferCache;
  currentProjectId: string | null;
  /** Bump to reload the Library and the list of sessions. */
  refreshKey: number;
  /** Put an audio asset on a new track at the playhead. */
  onAddAsset: (asset: Asset) => void;
  onOpenProject: (projectId: string) => void;
  busy: boolean;
};

type LocalImport = {
  key: string;
  name: string;
  status: "decoding" | "uploading" | "ready" | "error";
  error?: string;
  peaks?: WavePeaks;
  durationSec?: number;
  asset?: Asset;
};

const SOURCE_LABEL: Record<Asset["source"], string> = {
  upload: "Upload",
  generated: "Generated",
  bounce: "Bounce",
  conversion: "Conversion",
  narration: "Narration",
  standin: "Stand-in",
};

const isSound = (a: Asset) => a.kind === "audio" || a.kind === "stem";

function when(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

function MiniWave({ peaks }: { peaks: WavePeaks }) {
  const ref = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const canvas = ref.current;
    const g = canvas?.getContext("2d");
    if (!canvas || !g) return;
    g.clearRect(0, 0, canvas.width, canvas.height);
    g.fillStyle = getComputedStyle(canvas).color || "#20231f";
    drawPeaks(g, peaks, { x: 0, y: 0, width: canvas.width, height: canvas.height }, 0, peaks.durationSec);
  }, [peaks]);
  return <canvas ref={ref} className={s.miniWave} width={220} height={36} role="img" aria-label="Waveform" />;
}

/** A picture of a saved project's clip layout. Decorative: the text beside it says the same. */
function SessionThumb({ project }: { project: Project | undefined }) {
  const ref = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const canvas = ref.current;
    const g = canvas?.getContext("2d");
    if (!canvas || !g) return;
    g.clearRect(0, 0, canvas.width, canvas.height);
    if (!project) return;
    for (const r of thumbnailRects(project, canvas.width, canvas.height)) {
      g.fillStyle = r.kind === "audio" ? "#d47b53" : r.kind === "midi" ? "#f1efe9" : "#9aa093";
      g.fillRect(r.x, r.y, r.width, r.height);
    }
  }, [project]);
  return <canvas ref={ref} className={s.thumb} width={72} height={40} aria-hidden="true" />;
}

function GenerateBox({ onAsset, busy }: { onAsset: (asset: Asset) => void; busy: boolean }) {
  const [prompt, setPrompt] = useState("");
  const [status, setStatus] = useState<string | null>(null);
  const [reply, setReply] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const abort = useRef<AbortController | null>(null);
  const running = status !== null;

  useEffect(() => () => abort.current?.abort(), []);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const text = prompt.trim();
    if (!text || running) return;
    setError(null);
    setReply(null);
    abort.current = new AbortController();
    try {
      const result = await generateHere(text, setStatus, abort.current.signal);
      if (result.kind === "asset") {
        onAsset(result.asset);
        setPrompt("");
        // Say so when the track is not a real generation (spec D11: offline stand-ins).
        if (result.asset.standin || result.asset.source === "standin") {
          setReply("This is a synthesised stand-in clip, not a generated track: the music service is running offline.");
        }
      } else {
        setReply(result.text);
      }
    } catch (err) {
      if ((err as Error)?.name !== "AbortError") setError(errorText(err));
    } finally {
      setStatus(null);
    }
  };

  return (
    <form className={s.generate} onSubmit={submit}>
      <label className="o-label">
        Describe the music
        <textarea
          className={`o-textarea ${s.generateText}`}
          value={prompt}
          maxLength={600}
          placeholder="A slow tabla groove, 20 seconds"
          onChange={(e) => setPrompt(e.target.value)}
        />
      </label>
      <div className={s.rowButtons}>
        <button type="submit" className="o-btn o-btn-small" disabled={running || busy || !prompt.trim()}>
          Generate here
        </button>
        {running ? (
          <button type="button" className="o-btn o-btn-small" onClick={() => abort.current?.abort()}>
            Cancel
          </button>
        ) : null}
      </div>
      <p className={s.hint} role="status">
        {status ?? "The result lands on a new track at the playhead."}
      </p>
      {reply ? <p className="o-note">{reply}</p> : null}
      {error ? (
        <p className="o-alert" role="alert">
          {error}
        </p>
      ) : null}
    </form>
  );
}

/** The left panel: the Library, saved sessions, files from this computer, and Generate here (spec 12.2, 12.3). */
export function BrowserPanel({ cache, currentProjectId, refreshKey, onAddAsset, onOpenProject, busy }: Props) {
  const [assets, setAssets] = useState<Asset[] | null>(null);
  const [assetsError, setAssetsError] = useState<string | null>(null);
  const [projects, setProjects] = useState<ProjectSummary[] | null>(null);
  const [projectsError, setProjectsError] = useState<string | null>(null);
  const [layouts, setLayouts] = useState<Record<string, Project>>({});
  const [imports, setImports] = useState<LocalImport[]>([]);
  const [over, setOver] = useState(false);
  const [reload, setReload] = useState(0);
  const fileInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    let live = true;
    api
      .listAssets()
      .then((list) => {
        if (!live) return;
        setAssets(list);
        setAssetsError(null);
      })
      .catch((err) => live && setAssetsError(errorText(err)));
    api
      .projects()
      .then((list) => {
        if (!live) return;
        setProjects(list);
        setProjectsError(null);
      })
      .catch((err) => live && setProjectsError(errorText(err)));
    return () => {
      live = false;
    };
  }, [refreshKey, reload]);

  // The list of sessions carries counts but not the layout, so the thumbnails read the
  // newest few projects themselves, one at a time.
  const layoutStamp = useRef(new Map<string, string>());
  useEffect(() => {
    if (!projects) return;
    let live = true;
    void (async () => {
      for (const summary of projects.slice(0, 12)) {
        if (!live) return;
        if (layoutStamp.current.get(summary.id) === summary.updated_at) continue;
        try {
          const out = await api.getProject(summary.id);
          if (!live) return;
          layoutStamp.current.set(summary.id, summary.updated_at);
          setLayouts((prev) => ({ ...prev, [summary.id]: migrate(out.project) }));
        } catch {
          // no thumbnail; the text still describes the session
        }
      }
    })();
    return () => {
      live = false;
    };
  }, [projects]);

  const importFiles = useCallback(
    async (files: File[]) => {
      for (const file of files) {
        const key = uid("imp");
        const patch = (change: Partial<LocalImport>) =>
          setImports((list) => list.map((item) => (item.key === key ? { ...item, ...change } : item)));
        setImports((list) => [{ key, name: file.name, status: "decoding" }, ...list]);
        try {
          const { asset } = await importAudioFile(file, cache, (stage, buffer) =>
            patch(buffer ? { status: stage, peaks: computePeaks(buffer, 220), durationSec: buffer.duration } : { status: stage }),
          );
          patch({ status: "ready", asset });
          setAssets((list) => (list ? [asset, ...list.filter((a) => a.id !== asset.id)] : list));
        } catch (err) {
          patch({ status: "error", error: err instanceof Error ? err.message : "That file could not be imported." });
        }
      }
    },
    [cache],
  );

  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setOver(false);
    if (e.dataTransfer.files.length) void importFiles(Array.from(e.dataTransfer.files));
  };

  const sounds = assets?.filter(isSound) ?? [];

  return (
    <aside className={s.browser} aria-label="Browser">
      <section aria-labelledby="ws-library">
        <div className={s.sectionHead}>
          <h2 id="ws-library">Library</h2>
          <button type="button" className="o-btn o-btn-small o-btn-quiet" onClick={() => setReload((n) => n + 1)}>
            Refresh
          </button>
        </div>
        {assetsError ? (
          <p className="o-alert" role="alert">
            {assetsError}
          </p>
        ) : assets === null ? (
          <p className={s.hint}>Loading the Library...</p>
        ) : sounds.length === 0 ? (
          <p className={s.hint}>No audio yet. Generate a track in Chat, or bring in a file below.</p>
        ) : (
          <ul className={s.list}>
            {sounds.map((asset) => (
              <li
                key={asset.id}
                className={s.item}
                draggable
                onDragStart={(e) => {
                  e.dataTransfer.setData(ASSET_MIME, asset.id);
                  e.dataTransfer.setData("text/plain", asset.filename);
                  e.dataTransfer.effectAllowed = "copy";
                }}
                title="Drag onto the timeline"
              >
                <span className={s.itemName}>{asset.filename}</span>
                <span className={s.itemMeta}>
                  {SOURCE_LABEL[asset.source] ?? asset.source}
                  {asset.duration_s ? ` · ${formatClock(asset.duration_s)}` : ""}
                  {asset.standin ? " · offline stand-in" : ""}
                </span>
                <button
                  type="button"
                  className="o-btn o-btn-small"
                  aria-label={`Add ${asset.filename} to the timeline`}
                  disabled={busy}
                  onClick={() => onAddAsset(asset)}
                >
                  Add to timeline
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section aria-labelledby="ws-sessions">
        <div className={s.sectionHead}>
          <h2 id="ws-sessions">Previous sessions</h2>
        </div>
        {projectsError ? (
          <p className="o-alert" role="alert">
            {projectsError}
          </p>
        ) : projects === null ? (
          <p className={s.hint}>Loading your sessions...</p>
        ) : projects.length === 0 ? (
          <p className={s.hint}>Nothing saved yet. A project is saved as soon as you change something.</p>
        ) : (
          <ul className={s.list}>
            {projects.map((p) => {
              const current = p.id === currentProjectId;
              return (
                <li key={p.id} className={s.item} data-current={current ? "true" : undefined}>
                  <div className={s.session}>
                    <SessionThumb project={layouts[p.id]} />
                    <div className={s.sessionText}>
                      <span className={s.itemName}>{p.name}</span>
                      <span className={s.itemMeta}>{when(p.updated_at)}</span>
                      <span className={s.itemMeta}>
                        {p.track_count} {p.track_count === 1 ? "track" : "tracks"} {"·"} {p.clip_count}{" "}
                        {p.clip_count === 1 ? "clip" : "clips"}
                        {p.tempo ? ` · ${Math.round(p.tempo)} BPM` : ""}
                      </span>
                    </div>
                  </div>
                  {current ? (
                    <span className="o-badge o-badge-ok">Open now</span>
                  ) : (
                    <button
                      type="button"
                      className="o-btn o-btn-small"
                      aria-label={`Open ${p.name}`}
                      disabled={busy}
                      onClick={() => onOpenProject(p.id)}
                    >
                      Open
                    </button>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </section>

      <section aria-labelledby="ws-computer">
        <div className={s.sectionHead}>
          <h2 id="ws-computer">From my computer</h2>
        </div>
        <div
          className={s.dropZone}
          data-over={over ? "true" : undefined}
          onDragOver={(e) => {
            if (!e.dataTransfer.types.includes("Files")) return;
            e.preventDefault();
            setOver(true);
          }}
          onDragLeave={() => setOver(false)}
          onDrop={onDrop}
        >
          <p>Drop audio files here: wav, mp3, m4a, flac or ogg.</p>
          <button type="button" className="o-btn o-btn-small" onClick={() => fileInput.current?.click()}>
            Choose files
          </button>
          <input
            ref={fileInput}
            hidden
            type="file"
            accept={AUDIO_ACCEPT}
            multiple
            onChange={(e) => {
              const files = Array.from(e.target.files ?? []);
              e.target.value = "";
              if (files.length) void importFiles(files);
            }}
          />
          <p className={s.hint}>Only bring in audio you have the right to use. Files are saved to your Library.</p>
        </div>
        {imports.length > 0 ? (
          <ul className={s.list} aria-label="Files brought in this session">
            {imports.map((item) => (
              <li key={item.key} className={s.item}>
                <span className={s.itemName}>{item.name}</span>
                {item.peaks ? <MiniWave peaks={item.peaks} /> : null}
                {item.status === "error" ? (
                  <p className="o-alert" role="alert">
                    {item.error}
                  </p>
                ) : (
                  <span className={s.itemMeta} role="status">
                    {item.status === "decoding" ? "Reading the file..." : item.status === "uploading" ? "Saving to the Library..." : "Saved to the Library"}
                    {item.durationSec ? ` · ${formatClock(item.durationSec)}` : ""}
                  </span>
                )}
                {item.asset ? (
                  <button
                    type="button"
                    className="o-btn o-btn-small"
                    aria-label={`Add ${item.name} to the timeline`}
                    disabled={busy}
                    onClick={() => onAddAsset(item.asset!)}
                  >
                    Add to timeline
                  </button>
                ) : null}
              </li>
            ))}
          </ul>
        ) : null}
      </section>

      <section aria-labelledby="ws-generate">
        <div className={s.sectionHead}>
          <h2 id="ws-generate">Generate here</h2>
        </div>
        <GenerateBox
          busy={busy}
          onAsset={(asset) => {
            setAssets((list) => (list ? [asset, ...list.filter((a) => a.id !== asset.id)] : list));
            onAddAsset(asset);
          }}
        />
      </section>
    </aside>
  );
}
