"use client";

import Link from "next/link";
import {
  createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ChangeEvent, type ReactNode,
} from "react";
import { api, errorText, type Asset, type ProjectSummary } from "@/lib/api";
import { bytes, clock } from "@/lib/format";
import { handoff } from "@/lib/handoff";
import { Dialog } from "./Dialog";
import { saveAsset } from "./download";
import { useRegistry } from "./registry";
import { useFocusAfter } from "./useFocusAfter";
import css from "./Shell.module.css";

/** Saved Workspace projects: listed with the user's other files, opened in the Workspace. */
function SavedProjects({ onClose }: { onClose: () => void }) {
  const [projects, setProjects] = useState<ProjectSummary[] | null>(null);
  const [problem, setProblem] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    api.projects().then(
      (list) => alive && setProjects(list),
      (err) => alive && setProblem(errorText(err)),
    );
    return () => {
      alive = false;
    };
  }, []);

  return (
    <section aria-labelledby="orin-lib-projects" className="o-stack">
      <h3 id="orin-lib-projects">
        Saved projects <span className="o-muted">({projects?.length ?? 0})</span>
      </h3>
      {problem ? (
        <p className="o-alert" role="alert">
          {problem}
        </p>
      ) : projects === null ? (
        <p className="o-muted">Loading...</p>
      ) : projects.length === 0 ? (
        <p className="o-muted">Nothing here yet. A project is saved as soon as you change something in the Workspace.</p>
      ) : (
        <ul className={css.assetList}>
          {projects.map((project) => (
            <li key={project.id} className={css.asset}>
              <div className={css.assetName}>
                <strong>{project.name}</strong>
                <span className="o-muted">
                  {project.track_count} {project.track_count === 1 ? "track" : "tracks"}, {project.clip_count}{" "}
                  {project.clip_count === 1 ? "clip" : "clips"}, saved {new Date(project.updated_at).toLocaleString()}
                </span>
              </div>
              <div className="o-row" style={{ gap: 6 }}>
                <Link className={css.sendLink} href={`/workspace?project=${encodeURIComponent(project.id)}`} onClick={onClose}>
                  Open in Workspace
                </Link>
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

export const RIGHTS_NOTICE = "Only upload audio you have the right to use.";

type LibraryValue = {
  assets: Asset[];
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
  /** Upload a file into the Library. Throws ApiError on failure. */
  upload: (file: File) => Promise<Asset>;
  /** Open the Library panel. */
  open: () => void;
};

const LibraryContext = createContext<LibraryValue>({
  assets: [],
  loading: false,
  error: null,
  refresh: async () => undefined,
  upload: (file) => api.uploadAsset(file),
  open: () => undefined,
});

export const useLibrary = (): LibraryValue => useContext(LibraryContext);

export function LibraryProvider({ children }: { children: ReactNode }) {
  const [assets, setAssets] = useState<Asset[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [isOpen, setOpen] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      setAssets(await api.listAssets());
      setError(null);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const upload = useCallback(async (file: File) => {
    const asset = await api.uploadAsset(file);
    setAssets((list) => [asset, ...list.filter((a) => a.id !== asset.id)]);
    return asset;
  }, []);

  const open = useCallback(() => {
    setOpen(true);
    void refresh();
  }, [refresh]);

  const value = useMemo(() => ({ assets, loading, error, refresh, upload, open }), [assets, loading, error, refresh, upload, open]);

  return (
    <LibraryContext.Provider value={value}>
      {children}
      {isOpen && <LibraryPanel onClose={() => setOpen(false)} />}
    </LibraryContext.Provider>
  );
}

const GROUPS: Array<{ key: string; label: string; sources: Asset["source"][]; always: boolean }> = [
  { key: "uploads", label: "Uploads", sources: ["upload"], always: true },
  { key: "generated", label: "Generated", sources: ["generated", "standin"], always: true },
  { key: "bounces", label: "Bounces", sources: ["bounce"], always: true },
  { key: "conversions", label: "Conversions", sources: ["conversion"], always: true },
  { key: "narration", label: "Narration", sources: ["narration"], always: false },
];

const isSound = (a: Asset) => a.kind === "audio" || a.kind === "stem";

function LibraryPanel({ onClose }: { onClose: () => void }) {
  const { assets, loading, error, refresh, upload } = useLibrary();
  const { registry } = useRegistry();
  const audioRef = useRef<HTMLAudioElement>(null);
  const [playingId, setPlayingId] = useState<string | null>(null);
  const [confirmId, setConfirmId] = useState<string | null>(null);
  const [status, setStatus] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const focusAfter = useFocusAfter();

  async function onFile(e: ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    setProblem(null);
    const maxMb = registry.limits.max_upload_mb;
    if (file.size > maxMb * 1024 * 1024) {
      setProblem(`${file.name} is ${bytes(file.size)}. The limit is ${maxMb} MB.`);
      return;
    }
    setBusy(true);
    setStatus(`Uploading ${file.name}...`);
    try {
      const asset = await upload(file);
      setStatus(`${asset.filename} was added to your Library.`);
    } catch (err) {
      setStatus("");
      setProblem(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  function togglePlay(asset: Asset) {
    const audio = audioRef.current;
    if (!audio) return;
    if (playingId === asset.id) {
      audio.pause();
      setPlayingId(null);
      return;
    }
    audio.src = api.assetUrl(asset);
    setPlayingId(asset.id);
    audio.play().catch(() => {
      setPlayingId(null);
      setProblem(`${asset.filename} could not be played in the browser.`);
    });
  }

  async function download(asset: Asset) {
    setProblem(null);
    try {
      await saveAsset(asset);
    } catch (err) {
      setProblem(errorText(err));
    }
  }

  async function remove(asset: Asset) {
    setConfirmId(null);
    setProblem(null);
    if (playingId === asset.id) {
      audioRef.current?.pause();
      setPlayingId(null);
    }
    try {
      await api.deleteAsset(asset.id);
      // The row is about to go; move focus to the message that says so.
      focusAfter("orin-lib-status");
      setStatus(`${asset.filename} was deleted.`);
      await refresh();
    } catch (err) {
      setProblem(errorText(err));
    }
  }

  return (
    <Dialog title="Library" onClose={onClose} variant="drawer">
      <div className="o-stack">
        <p className="o-muted">
          Your uploads, generated tracks, bounces and conversions. Anything here can be sent to the Notation Studio, the
          Workspace or the Chat.
        </p>
        <div className="o-note o-stack">
          <label className="o-label">
            Upload a file
            <input type="file" accept="audio/*,.mid,.midi,.abc,.musicxml,.xml,.txt" onChange={onFile} disabled={busy} />
          </label>
          <p>{RIGHTS_NOTICE}</p>
        </div>
        <p id="orin-lib-status" tabIndex={-1} role="status" aria-live="polite" className={status ? undefined : "o-sr-only"}>
          {loading && !status ? "Loading your Library..." : status}
        </p>
        {(problem || error) && (
          <div className="o-alert" role="alert">
            <p>{problem ?? error}</p>
            {error && !problem ? (
              <p style={{ marginTop: 8 }}>
                <button type="button" className="o-btn o-btn-small" onClick={() => void refresh()}>
                  Try again
                </button>
              </p>
            ) : null}
          </div>
        )}
        <audio ref={audioRef} onEnded={() => setPlayingId(null)} />
        {GROUPS.map((group) => {
          const items = assets.filter((a) => group.sources.includes(a.source));
          if (items.length === 0 && !group.always) return null;
          return (
            <section key={group.key} aria-labelledby={`orin-lib-${group.key}`} className="o-stack">
              <h3 id={`orin-lib-${group.key}`}>
                {group.label} <span className="o-muted">({items.length})</span>
              </h3>
              {items.length === 0 ? (
                <p className="o-muted">Nothing here yet.</p>
              ) : (
                <ul className={css.assetList}>
                  {items.map((asset) => (
                    <li key={asset.id} className={css.asset}>
                      <div className={css.assetName}>
                        <strong>{asset.filename}</strong>
                        <span className="o-muted">
                          {asset.kind}
                          {asset.duration_s != null ? `, ${clock(asset.duration_s)}` : ""}, {bytes(asset.size_bytes)}
                        </span>
                        {asset.standin ? <span className="o-badge o-badge-warn">Offline stand-in</span> : null}
                      </div>
                      <div className="o-row" style={{ gap: 6 }}>
                        {isSound(asset) && (
                          <button
                            type="button"
                            className="o-btn o-btn-small"
                            aria-pressed={playingId === asset.id}
                            aria-label={`${playingId === asset.id ? "Pause" : "Play"} ${asset.filename}`}
                            onClick={() => togglePlay(asset)}
                          >
                            {playingId === asset.id ? "Pause" : "Play"}
                          </button>
                        )}
                        <button
                          type="button"
                          className="o-btn o-btn-small"
                          aria-label={`Download ${asset.filename}`}
                          onClick={() => void download(asset)}
                        >
                          Download
                        </button>
                        <button
                          type="button"
                          id={`orin-lib-del-${asset.id}`}
                          className="o-btn o-btn-small"
                          aria-label={`Delete ${asset.filename}`}
                          aria-expanded={confirmId === asset.id}
                          onClick={() => setConfirmId(confirmId === asset.id ? null : asset.id)}
                        >
                          Delete
                        </button>
                        {confirmId === asset.id ? (
                          <>
                            <button type="button" className="o-btn o-btn-small" onClick={() => void remove(asset)}>
                              Yes, delete {asset.filename}
                            </button>
                            <button
                              type="button"
                              className="o-btn o-btn-small"
                              onClick={() => {
                                focusAfter(`orin-lib-del-${asset.id}`);
                                setConfirmId(null);
                              }}
                            >
                              Keep
                            </button>
                          </>
                        ) : null}
                      </div>
                      {asset.kind !== "image" && (
                        <div className="o-row" style={{ gap: 6 }}>
                          <span className="o-muted" id={`orin-send-${asset.id}`}>
                            Send to
                          </span>
                          <Link
                            className={css.sendLink}
                            href={handoff.studioWithAsset(asset.id)}
                            onClick={onClose}
                            aria-describedby={`orin-send-${asset.id}`}
                          >
                            Notation Studio
                          </Link>
                          {isSound(asset) && (
                            <>
                              <Link
                                className={css.sendLink}
                                href={handoff.workspaceWithAsset(asset.id)}
                                onClick={onClose}
                                aria-describedby={`orin-send-${asset.id}`}
                              >
                                Workspace
                              </Link>
                              <Link
                                className={css.sendLink}
                                href={handoff.chatWithAsset(asset.id)}
                                onClick={onClose}
                                aria-describedby={`orin-send-${asset.id}`}
                              >
                                Chat
                              </Link>
                            </>
                          )}
                        </div>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </section>
          );
        })}
        <SavedProjects onClose={onClose} />
      </div>
    </Dialog>
  );
}

/** A labelled select of Library files plus a button: "pick from the Library" on any screen. */
export function LibraryPicker({
  label,
  buttonLabel = "Use this file",
  kinds = ["audio", "stem"],
  onPick,
  disabled = false,
}: {
  label: string;
  buttonLabel?: string;
  kinds?: Asset["kind"][];
  onPick: (asset: Asset) => void;
  disabled?: boolean;
}) {
  const { assets, loading, error } = useLibrary();
  const [id, setId] = useState("");
  const options = assets.filter((a) => kinds.includes(a.kind));
  const placeholder = options.length ? "Choose a file..." : loading ? "Loading..." : "Your Library has no matching files";
  return (
    <div className="o-stack" style={{ gap: 6 }}>
      <div className="o-row" style={{ alignItems: "flex-end" }}>
        <label className="o-label" style={{ flex: 1, minWidth: 160 }}>
          {label}
          <select className="o-select" value={id} onChange={(e) => setId(e.target.value)} disabled={disabled}>
            <option value="">{placeholder}</option>
            {options.map((a) => (
              <option key={a.id} value={a.id}>
                {a.filename}
                {a.duration_s != null ? ` (${clock(a.duration_s)})` : ""}
                {a.standin ? " - stand-in" : ""}
              </option>
            ))}
          </select>
        </label>
        <button
          type="button"
          className="o-btn"
          disabled={disabled || !id}
          onClick={() => {
            const asset = options.find((a) => a.id === id);
            if (!asset) return;
            onPick(asset);
            setId("");
          }}
        >
          {buttonLabel}
        </button>
      </div>
      {error ? <p className="o-muted">The Library could not be loaded. {error}</p> : null}
    </div>
  );
}
