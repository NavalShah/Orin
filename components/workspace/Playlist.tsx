"use client";

import {
  useEffect, useRef, useState, type DragEvent, type KeyboardEvent, type PointerEvent as ReactPointerEvent, type RefObject,
} from "react";
import type { Transport } from "@/lib/audio/engine";
import { drawPeaks, type WavePeaks } from "@/lib/audio/peaks";
import type { BufferCache } from "@/lib/workspace/buffers";
import { projectEndSec, type Clip, type Drums, type Project, type Track, type TrackKind } from "@/lib/workspace/project";
import type { ProjectStore } from "@/lib/workspace/store";
import { clipLabel, formatBarsBeats, gridStepSec, secPerBar, secPerBeat, snapSec } from "@/lib/workspace/time";
import { TextField } from "./fields";
import { usePosition } from "./usePosition";
import s from "./workspace.module.css";

const HEADER_W = 184;
const LANE_H = 80;
const CLIP_TITLE_H = 20;
export const ASSET_MIME = "application/x-orin-asset";

export const GRID_CHOICES: Array<{ label: string; division: number | "bar" }> = [
  { label: "Bar", division: "bar" },
  { label: "Beat", division: 1 },
  { label: "1/2 beat", division: 2 },
  { label: "1/4 beat", division: 4 },
  { label: "1/8 beat", division: 8 },
];

type Props = {
  project: Project;
  store: ProjectStore;
  transport: Transport;
  cache: BufferCache;
  /** Changes when audio finishes loading, so waveforms redraw. */
  cacheVersion: number;
  selectedClipId: string | null;
  onSelectClip: (clipId: string | null) => void;
  /** Double-click or Enter on a clip. */
  onOpenClip: (clipId: string) => void;
  pxPerSec: number;
  onZoom: (pxPerSec: number) => void;
  snap: boolean;
  onSnap: (snap: boolean) => void;
  /** Grid steps per beat. */
  division: number;
  gridChoice: string;
  onGridChoice: (label: string) => void;
  onSeek: (sec: number) => void;
  onAddTrack: (kind: TrackKind) => void;
  onAddMidiClip: (trackId: string) => void;
  onEditSteps: () => void;
  onDropAsset: (assetId: string, trackId: string | null, sec: number) => void;
  onDropFiles: (files: File[], trackId: string | null, sec: number) => void;
  onSplit: () => void;
  onDuplicate: () => void;
  onDelete: () => void;
};

// --- clip drawing -------------------------------------------------------------

function ClipCanvas({ clip, peaks, width, height }: { clip: Clip; peaks: WavePeaks | undefined; width: number; height: number }) {
  const ref = useRef<HTMLCanvasElement>(null);
  const pixels = Math.max(1, Math.min(4096, Math.round(width)));
  const notes = clip.kind === "midi" ? clip.notes : null;
  const offsetSec = clip.kind === "audio" ? clip.offsetSec : 0;
  const fadeIn = clip.kind === "audio" ? clip.fadeInSec : 0;
  const fadeOut = clip.kind === "audio" ? clip.fadeOutSec : 0;
  const lengthSec = clip.lengthSec;

  useEffect(() => {
    const canvas = ref.current;
    const g = canvas?.getContext("2d");
    if (!canvas || !g) return;
    g.clearRect(0, 0, canvas.width, canvas.height);
    g.fillStyle = getComputedStyle(canvas).color || "#20231f";
    g.strokeStyle = g.fillStyle;
    if (notes) {
      const shown = notes.filter((n) => n.startSec < lengthSec);
      if (shown.length === 0) return;
      const lo = Math.min(...shown.map((n) => n.midi)) - 1;
      const hi = Math.max(...shown.map((n) => n.midi)) + 1;
      const rowH = canvas.height / (hi - lo + 1);
      const scale = canvas.width / lengthSec;
      for (const n of shown) {
        const w = Math.max(2, Math.min(n.durSec, lengthSec - n.startSec) * scale - 1);
        g.fillRect(n.startSec * scale, (hi - n.midi) * rowH, w, Math.max(2, Math.min(6, rowH - 1)));
      }
      return;
    }
    if (peaks) drawPeaks(g, peaks, { x: 0, y: 0, width: canvas.width, height: canvas.height }, offsetSec, offsetSec + lengthSec);
    // Fades: a line from silence up to full level, and back down.
    const scale = canvas.width / lengthSec;
    g.lineWidth = 1.5;
    g.beginPath();
    if (fadeIn > 0) {
      g.moveTo(0, canvas.height);
      g.lineTo(fadeIn * scale, 1);
    }
    if (fadeOut > 0) {
      g.moveTo((lengthSec - fadeOut) * scale, 1);
      g.lineTo(canvas.width, canvas.height);
    }
    g.stroke();
  }, [notes, peaks, offsetSec, lengthSec, fadeIn, fadeOut, pixels, height]);

  return <canvas ref={ref} className={s.clipCanvas} width={pixels} height={height} aria-hidden="true" />;
}

type ClipDrag = { mode: "move" | "trimStart" | "trimEnd"; startX: number; startY: number; moved: boolean; clip: Clip };

type ClipViewProps = {
  clip: Clip;
  row: number;
  trackName: string;
  project: Project;
  store: ProjectStore;
  layer: RefObject<HTMLDivElement | null>;
  pxPerSec: number;
  quantise: (sec: number) => number;
  selected: boolean;
  peaks: WavePeaks | undefined;
  status: string | null;
  onSelect: (clipId: string) => void;
  onOpen: (clipId: string) => void;
};

function ClipView({ clip, row, trackName, project, store, layer, pxPerSec, quantise, selected, peaks, status, onSelect, onOpen }: ClipViewProps) {
  const drag = useRef<ClipDrag | null>(null);
  const width = Math.max(6, clip.lengthSec * pxPerSec);

  const down = (e: ReactPointerEvent<HTMLElement>, mode: ClipDrag["mode"]) => {
    if (e.button !== 0) return;
    e.stopPropagation();
    e.currentTarget.setPointerCapture(e.pointerId);
    drag.current = { mode, startX: e.clientX, startY: e.clientY, moved: false, clip };
    onSelect(clip.id);
  };

  const move = (e: ReactPointerEvent<HTMLElement>) => {
    const d = drag.current;
    if (!d) return;
    const dx = e.clientX - d.startX;
    if (!d.moved) {
      if (Math.abs(dx) < 3 && Math.abs(e.clientY - d.startY) < 8) return;
      d.moved = true;
      store.begin();
    }
    const dt = dx / pxPerSec;
    if (d.mode === "move") {
      // The lane under the pointer; the reducer refuses a track of another kind.
      const top = layer.current?.getBoundingClientRect().top ?? 0;
      const target = project.tracks[Math.floor((e.clientY - top) / LANE_H)];
      store.dispatch(
        { type: "moveClip", clipId: clip.id, startSec: quantise(d.clip.startSec + dt), trackId: target?.id },
        { transient: true },
      );
    } else if (d.mode === "trimStart") {
      store.dispatch({ type: "trimClipStart", clipId: clip.id, startSec: quantise(d.clip.startSec + dt) }, { transient: true });
    } else {
      store.dispatch(
        { type: "trimClipEnd", clipId: clip.id, endSec: quantise(d.clip.startSec + d.clip.lengthSec + dt) },
        { transient: true },
      );
    }
  };

  const up = () => {
    const d = drag.current;
    drag.current = null;
    if (d?.moved) store.commit({ type: "moveClip", clipId: clip.id, startSec: clip.startSec });
  };

  const cancel = () => {
    if (drag.current?.moved) store.cancel();
    drag.current = null;
  };

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key === "Enter") {
      e.preventDefault();
      onOpen(clip.id);
    }
  };

  return (
    <div
      role="button"
      tabIndex={0}
      className={s.clip}
      data-kind={clip.kind}
      data-selected={selected ? "true" : undefined}
      aria-pressed={selected}
      aria-label={`${clipLabel(clip, project.tempo, project.timeSignature)}, on track ${trackName}`}
      style={{ left: clip.startSec * pxPerSec, top: row * LANE_H + 4, width, height: LANE_H - 8 }}
      onPointerDown={(e) => down(e, "move")}
      onPointerMove={move}
      onPointerUp={up}
      onPointerCancel={cancel}
      onDoubleClick={() => onOpen(clip.id)}
      onFocus={() => onSelect(clip.id)}
      onKeyDown={onKeyDown}
    >
      <span className={s.clipTitle}>
        <span className={s.clipKind}>{clip.kind === "audio" ? "AUDIO" : "MIDI"}</span>
        {clip.name}
      </span>
      <ClipCanvas clip={clip} peaks={peaks} width={width} height={LANE_H - 8 - CLIP_TITLE_H} />
      {status ? <span className={s.clipStatus}>{status}</span> : null}
      <span className={s.trimStart} onPointerDown={(e) => down(e, "trimStart")} title="Drag to trim the start" aria-hidden="true" />
      <span className={s.trimEnd} onPointerDown={(e) => down(e, "trimEnd")} title="Drag to trim the end" aria-hidden="true" />
    </div>
  );
}

// --- ruler, playhead, lanes -----------------------------------------------------

function PlayheadLine({ transport, pxPerSec, scroller }: { transport: Transport; pxPerSec: number; scroller: RefObject<HTMLDivElement | null> }) {
  const x = HEADER_W + usePosition(transport) * pxPerSec;
  // Keep the playhead in view while playing.
  useEffect(() => {
    const el = scroller.current;
    if (!el || !transport.playing) return;
    if (x > el.scrollLeft + el.clientWidth - 24 || x < el.scrollLeft + HEADER_W) {
      el.scrollLeft = Math.max(0, x - HEADER_W - 60);
    }
  }, [x, scroller, transport]);
  return <div className={s.playhead} style={{ transform: `translateX(${x}px)` }} aria-hidden="true" />;
}

type RulerProps = {
  project: Project;
  store: ProjectStore;
  transport: Transport;
  pxPerSec: number;
  width: number;
  contentSec: number;
  quantise: (sec: number) => number;
  step: number;
  onSeek: (sec: number) => void;
};

function Ruler({ project, store, transport, pxPerSec, width, contentSec, quantise, step, onSeek }: RulerProps) {
  const drag = useRef<{ startX: number; startSec: number; moved: boolean } | null>(null);
  // The position for assistive tech: refreshed on play, pause, seek and stop, not every frame.
  const [rest, setRest] = useState(0);
  useEffect(() => {
    const read = () => setRest(transport.position);
    read();
    return transport.subscribe(read);
  }, [transport]);

  const bar = secPerBar(project.tempo, project.timeSignature);
  const barPx = bar * pxPerSec;
  const every = barPx >= 44 ? 1 : barPx >= 22 ? 2 : barPx >= 11 ? 4 : 8;
  const labels = [];
  for (let i = 0; i * bar < contentSec; i += every) labels.push(i);

  const secAt = (e: ReactPointerEvent<HTMLDivElement>) =>
    Math.max(0, (e.clientX - e.currentTarget.getBoundingClientRect().left) / pxPerSec);

  const onDown = (e: ReactPointerEvent<HTMLDivElement>) => {
    if (e.button !== 0) return;
    e.currentTarget.setPointerCapture(e.pointerId);
    drag.current = { startX: e.clientX, startSec: secAt(e), moved: false };
  };
  const onMove = (e: ReactPointerEvent<HTMLDivElement>) => {
    const d = drag.current;
    if (!d) return;
    if (!d.moved) {
      if (Math.abs(e.clientX - d.startX) < 5) return;
      d.moved = true;
      store.begin();
    }
    const now = secAt(e);
    const startSec = quantise(Math.min(d.startSec, now));
    const endSec = quantise(Math.max(d.startSec, now));
    store.dispatch({ type: "setLoop", loop: { enabled: endSec > startSec, startSec, endSec } }, { transient: true });
  };
  const onUp = (e: ReactPointerEvent<HTMLDivElement>) => {
    const d = drag.current;
    drag.current = null;
    if (!d) return;
    if (d.moved) store.commit({ type: "setLoop", loop: {} });
    else onSeek(quantise(secAt(e)));
  };
  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const jump = e.shiftKey ? bar : step;
    let to: number | null = null;
    if (e.key === "ArrowRight") to = transport.position + jump;
    else if (e.key === "ArrowLeft") to = transport.position - jump;
    else if (e.key === "Home") to = 0;
    else if (e.key === "End") to = projectEndSec(project);
    if (to === null) return;
    e.preventDefault();
    onSeek(Math.max(0, to));
  };

  return (
    <div
      className={s.ruler}
      style={{ width }}
      role="slider"
      tabIndex={0}
      aria-label="Playhead. Arrow keys move it; drag across the ruler to set the loop region"
      aria-valuemin={0}
      aria-valuemax={Math.round(contentSec)}
      aria-valuenow={Math.round(rest * 10) / 10}
      aria-valuetext={`bar ${formatBarsBeats(rest, project.tempo, project.timeSignature)}`}
      onPointerDown={onDown}
      onPointerMove={onMove}
      onPointerUp={onUp}
      onPointerCancel={() => {
        if (drag.current?.moved) store.cancel();
        drag.current = null;
      }}
      onKeyDown={onKeyDown}
    >
      {labels.map((i) => (
        <span key={i} className={s.rulerBar} style={{ left: i * barPx }}>
          {i + 1}
        </span>
      ))}
      <div
        className={s.loopBand}
        data-on={project.loop.enabled ? "true" : undefined}
        style={{ left: project.loop.startSec * pxPerSec, width: Math.max(2, (project.loop.endSec - project.loop.startSec) * pxPerSec) }}
      >
        <span>{project.loop.enabled ? "Loop" : "Loop off"}</span>
      </div>
    </div>
  );
}

/** The drum pattern as a one-bar picture, repeated along the lane. */
function drumPattern(drums: Drums): string {
  const cells: string[] = [];
  drums.rows.forEach((row, r) =>
    row.steps.forEach((on, i) => {
      if (on) cells.push(`<rect x="${i + 0.12}" y="${r + 0.2}" width="0.76" height="0.6" fill="#f1efe9"/>`);
    }),
  );
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${drums.steps} ${drums.rows.length}" preserveAspectRatio="none">${cells.join("")}</svg>`;
  return `url("data:image/svg+xml,${encodeURIComponent(svg)}")`;
}

const KIND_LABEL: Record<TrackKind, string> = { audio: "Audio", midi: "MIDI", drums: "Drums" };

function TrackHeader({ track, store, onAddMidiClip, onEditSteps }: { track: Track; store: ProjectStore; onAddMidiClip: (id: string) => void; onEditSteps: () => void }) {
  return (
    <div className={s.trackHeader} style={{ width: HEADER_W, height: LANE_H }} role="group" aria-label={`Track ${track.name}`}>
      <div className={s.trackHeaderTop}>
        <span className={s.trackKind}>{KIND_LABEL[track.kind]}</span>
        <TextField
          label={`Name of track ${track.name}`}
          hideLabel
          maxLength={60}
          value={track.name}
          onCommit={(name) => store.dispatch({ type: "renameTrack", trackId: track.id, name })}
        />
      </div>
      <div className={s.trackHeaderButtons}>
        <button
          type="button"
          className={s.miniButton}
          aria-pressed={track.mute}
          aria-label={`Mute ${track.name}`}
          title="Mute"
          onClick={() => store.dispatch({ type: "setTrackMute", trackId: track.id, mute: !track.mute })}
        >
          M
        </button>
        <button
          type="button"
          className={s.miniButton}
          aria-pressed={track.solo}
          aria-label={`Solo ${track.name}`}
          title="Solo"
          onClick={() => store.dispatch({ type: "setTrackSolo", trackId: track.id, solo: !track.solo })}
        >
          S
        </button>
        {track.kind === "midi" ? (
          <button type="button" className={s.miniButton} aria-label={`New clip on ${track.name}`} onClick={() => onAddMidiClip(track.id)}>
            + Clip
          </button>
        ) : null}
        {track.kind === "drums" ? (
          <button type="button" className={s.miniButton} aria-label="Edit drum steps" onClick={onEditSteps}>
            Steps
          </button>
        ) : null}
        <button
          type="button"
          className={s.miniButton}
          aria-label={`Remove track ${track.name}`}
          title="Remove track"
          onClick={() => store.dispatch({ type: "removeTrack", trackId: track.id })}
        >
          Remove
        </button>
      </div>
    </div>
  );
}

// --- the playlist ---------------------------------------------------------------

/** The arrangement: a ruler in bars, one lane per track, clips you can drag, trim and split (spec 12.2). */
export function Playlist(props: Props) {
  const { project, store, transport, cache, pxPerSec, snap, division, selectedClipId } = props;
  const scroller = useRef<HTMLDivElement>(null);
  const layer = useRef<HTMLDivElement>(null);
  const [dropping, setDropping] = useState(false);

  const bar = secPerBar(project.tempo, project.timeSignature);
  const beat = secPerBeat(project.tempo);
  const step = gridStepSec(project.tempo, division);
  const contentSec = Math.max(projectEndSec(project) + 8 * bar, project.loop.endSec + 2 * bar, 24 * bar);
  const width = contentSec * pxPerSec;
  const barPx = bar * pxPerSec;
  const beatPx = beat * pxPerSec;
  const quantise = (sec: number) => (snap ? snapSec(sec, project.tempo, division) : Math.max(0, sec));

  // Bar lines are brighter than beat lines; beat lines go when they would crowd.
  const gridImages = [`linear-gradient(to right, rgba(241, 239, 233, 0.28) 1px, transparent 1px)`];
  const gridSizes = [`${barPx}px 100%`];
  if (beatPx >= 10) {
    gridImages.push(`linear-gradient(to right, var(--o-daw-grid) 1px, transparent 1px)`);
    gridSizes.push(`${beatPx}px 100%`);
  }

  const rowOf = new Map(project.tracks.map((t, i) => [t.id, i]));
  const hasSelection = !!selectedClipId && project.clips.some((c) => c.id === selectedClipId);

  // Where a drop landed: a time, and the audio track under it if there is one.
  const dropTarget = (e: DragEvent) => {
    const rect = layer.current?.getBoundingClientRect();
    const sec = rect ? quantise((e.clientX - rect.left) / pxPerSec) : 0;
    const track = rect ? project.tracks[Math.floor((e.clientY - rect.top) / LANE_H)] : undefined;
    return { sec, trackId: track?.kind === "audio" ? track.id : null };
  };
  const accepts = (e: DragEvent) => e.dataTransfer.types.includes(ASSET_MIME) || e.dataTransfer.types.includes("Files");
  const onDragOver = (e: DragEvent) => {
    if (!accepts(e)) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "copy";
    setDropping(true);
  };
  const onDrop = (e: DragEvent) => {
    if (!accepts(e)) return;
    e.preventDefault();
    setDropping(false);
    const { sec, trackId } = dropTarget(e);
    const assetId = e.dataTransfer.getData(ASSET_MIME);
    if (assetId) props.onDropAsset(assetId, trackId, sec);
    else if (e.dataTransfer.files.length) props.onDropFiles(Array.from(e.dataTransfer.files), trackId, sec);
  };

  return (
    <section className={s.playlist} aria-label="Playlist">
      <div className={s.panelTools}>
        <div className={s.group} role="group" aria-label="Add a track">
          <button type="button" className="o-btn o-btn-small" onClick={() => props.onAddTrack("audio")}>
            + Audio track
          </button>
          <button type="button" className="o-btn o-btn-small" onClick={() => props.onAddTrack("midi")}>
            + MIDI track
          </button>
          <button
            type="button"
            className="o-btn o-btn-small"
            onClick={() => props.onAddTrack("drums")}
            disabled={project.tracks.some((t) => t.kind === "drums")}
          >
            + Drum track
          </button>
        </div>
        <div className={s.group} role="group" aria-label="Selected clip">
          <button type="button" className="o-btn o-btn-small" onClick={props.onSplit} title="S">
            Split at playhead
          </button>
          <button type="button" className="o-btn o-btn-small" onClick={props.onDuplicate} disabled={!hasSelection} aria-label="Duplicate clip">
            Duplicate
          </button>
          <button type="button" className="o-btn o-btn-small" onClick={props.onDelete} disabled={!hasSelection} aria-label="Delete clip" title="Delete key">
            Delete
          </button>
        </div>
        <span className={s.spacer} />
        <div className={s.group} role="group" aria-label="Grid and zoom">
          <button type="button" className="o-btn o-btn-small" aria-pressed={snap} onClick={() => props.onSnap(!snap)}>
            Snap {snap ? "on" : "off"}
          </button>
          <label className={s.inlineField}>
            <span>Grid</span>
            <select className="o-select" value={props.gridChoice} onChange={(e) => props.onGridChoice(e.target.value)}>
              {GRID_CHOICES.map((g) => (
                <option key={g.label} value={g.label}>
                  {g.label}
                </option>
              ))}
            </select>
          </label>
          <button type="button" className="o-btn o-btn-small" aria-label="Zoom out timeline" onClick={() => props.onZoom(Math.max(8, pxPerSec / 1.4))}>
            Zoom out
          </button>
          <button type="button" className="o-btn o-btn-small" aria-label="Zoom in timeline" onClick={() => props.onZoom(Math.min(600, pxPerSec * 1.4))}>
            Zoom in
          </button>
        </div>
      </div>

      <div
        className={s.timeline}
        ref={scroller}
        data-dropping={dropping ? "true" : undefined}
        onDragOver={onDragOver}
        onDragLeave={() => setDropping(false)}
        onDrop={onDrop}
      >
        <div className={s.timelineInner} style={{ width: HEADER_W + width }}>
          <div className={s.rulerRow}>
            <div className={s.corner} style={{ width: HEADER_W }}>
              Tracks
            </div>
            <Ruler
              project={project}
              store={store}
              transport={transport}
              pxPerSec={pxPerSec}
              width={width}
              contentSec={contentSec}
              quantise={quantise}
              step={step}
              onSeek={props.onSeek}
            />
          </div>

          <div className={s.lanes} style={{ height: Math.max(1, project.tracks.length) * LANE_H }}>
            {project.tracks.map((track) => (
              <div key={track.id} className={s.trackRow} style={{ height: LANE_H }}>
                <TrackHeader track={track} store={store} onAddMidiClip={props.onAddMidiClip} onEditSteps={props.onEditSteps} />
                <div
                  className={s.lane}
                  data-kind={track.kind}
                  style={{
                    width,
                    backgroundImage:
                      track.kind === "drums" && project.drums ? [drumPattern(project.drums), ...gridImages].join(", ") : gridImages.join(", "),
                    backgroundSize: track.kind === "drums" && project.drums ? [`${barPx}px 100%`, ...gridSizes].join(", ") : gridSizes.join(", "),
                  }}
                  onPointerDown={() => props.onSelectClip(null)}
                >
                  {track.kind === "drums" ? <span className={s.laneNote}>Drum pattern, repeats every bar</span> : null}
                </div>
              </div>
            ))}
            {project.tracks.length === 0 ? (
              <p className={s.emptyTimeline} style={{ left: HEADER_W + 16 }}>
                No tracks yet. Add a track above, drop an audio file here, or add something from the browser on the left.
              </p>
            ) : null}

            <div className={s.clipLayer} ref={layer} style={{ left: HEADER_W, width }}>
              {project.loop.enabled ? (
                <div
                  className={s.loopShade}
                  style={{ left: project.loop.startSec * pxPerSec, width: (project.loop.endSec - project.loop.startSec) * pxPerSec }}
                  aria-hidden="true"
                />
              ) : null}
              {project.clips.map((clip) => {
                const row = rowOf.get(clip.trackId);
                if (row === undefined) return null;
                const status =
                  clip.kind !== "audio" || cache.has(clip.assetId)
                    ? null
                    : cache.errorOf(clip.assetId)
                      ? "Audio could not be loaded"
                      : "Loading audio...";
                return (
                  <ClipView
                    key={clip.id}
                    clip={clip}
                    row={row}
                    trackName={project.tracks[row].name}
                    project={project}
                    store={store}
                    layer={layer}
                    pxPerSec={pxPerSec}
                    quantise={quantise}
                    selected={clip.id === selectedClipId}
                    peaks={clip.kind === "audio" ? cache.peaksOf(clip.assetId) : undefined}
                    status={status}
                    onSelect={props.onSelectClip}
                    onOpen={props.onOpenClip}
                  />
                );
              })}
            </div>
          </div>
          <PlayheadLine transport={transport} pxPerSec={pxPerSec} scroller={scroller} />
        </div>
      </div>
    </section>
  );
}
