"use client";

import { useEffect, useRef, useState } from "react";
import { api, errorText, type Asset, type Peaks } from "@/lib/api";
import { clock } from "@/lib/format";
import css from "./audio.module.css";

function draw(canvas: HTMLCanvasElement | null, peaks: number[], progress: number) {
  if (!canvas) return;
  let ctx: CanvasRenderingContext2D | null = null;
  try {
    ctx = canvas.getContext("2d");
  } catch {
    ctx = null;
  }
  if (!ctx) return;
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth || 600;
  const h = canvas.clientHeight || 72;
  if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
  }
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  const style = getComputedStyle(canvas);
  const rest = style.getPropertyValue("--o-ink-2").trim() || "#4a4d46";
  const played = style.getPropertyValue("--o-accent").trim() || "#a8522b";
  const mid = h / 2;
  ctx.fillStyle = rest;
  ctx.fillRect(0, mid, w, 1);
  const n = peaks.length;
  if (n === 0) return;
  for (let x = 0; x < w; x += 2) {
    const p = Math.min(1, Math.max(0, peaks[Math.min(n - 1, Math.floor((x / w) * n))] ?? 0));
    const bar = Math.max(1, p * (h - 6));
    ctx.fillStyle = x / w < progress ? played : rest;
    ctx.fillRect(x, mid - bar / 2, 1.5, bar);
  }
  // The play position is a line as well as a colour change.
  if (progress > 0 && progress < 1) {
    ctx.fillStyle = played;
    ctx.fillRect(Math.round(progress * w), 0, 2, h);
  }
}

/** The waveform of a Library asset with a play control and a seek slider. */
export function Waveform({
  asset,
  onTime,
}: {
  asset: Pick<Asset, "id" | "url" | "filename" | "duration_s">;
  /** Called with the play position in seconds while playing, and null when playback stops. */
  onTime?: (t: number | null) => void;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const audioRef = useRef<HTMLAudioElement>(null);
  const [peaks, setPeaks] = useState<Peaks | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [playing, setPlaying] = useState(false);
  const [time, setTime] = useState(0);
  const [duration, setDuration] = useState(asset.duration_s ?? 0);
  const [width, setWidth] = useState(0);

  useEffect(() => {
    let live = true;
    setPeaks(null);
    setError(null);
    setTime(0);
    setPlaying(false);
    setDuration(asset.duration_s ?? 0);
    api
      .peaks(asset.id, 600)
      .then((p) => {
        if (!live) return;
        setPeaks(p);
        if (p.duration_s > 0) setDuration(p.duration_s);
      })
      .catch((err) => {
        if (live) setError(`The waveform could not be drawn. ${errorText(err)}`);
      });
    return () => {
      live = false;
    };
  }, [asset.id, asset.duration_s]);

  useEffect(() => {
    const onResize = () => setWidth(window.innerWidth);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  useEffect(() => {
    draw(canvasRef.current, peaks?.peaks ?? [], duration > 0 ? time / duration : 0);
  }, [peaks, time, duration, width]);

  function toggle() {
    const audio = audioRef.current;
    if (!audio) return;
    if (audio.paused) {
      setError(null);
      audio.play().catch(() => setError("This file could not be played in the browser."));
    } else {
      audio.pause();
    }
  }

  function seek(value: number) {
    const audio = audioRef.current;
    if (audio) audio.currentTime = value;
    setTime(value);
  }

  return (
    <div className={css.wave}>
      <canvas
        ref={canvasRef}
        className={css.canvas}
        role="img"
        aria-label={`Waveform of ${asset.filename}${duration ? `, ${clock(duration)} long` : ""}`}
      />
      <audio
        ref={audioRef}
        src={api.assetUrl(asset)}
        preload="metadata"
        onLoadedMetadata={(e) => {
          const d = e.currentTarget.duration;
          if (Number.isFinite(d) && d > 0) setDuration(d);
        }}
        onTimeUpdate={(e) => {
          setTime(e.currentTarget.currentTime);
          onTime?.(e.currentTarget.currentTime);
        }}
        onPlay={() => setPlaying(true)}
        onPause={() => {
          setPlaying(false);
          onTime?.(null);
        }}
        onEnded={() => {
          setPlaying(false);
          onTime?.(null);
        }}
        onError={() => setError("This file could not be loaded for playback.")}
      />
      <div className={css.controls}>
        <button type="button" className="o-btn o-btn-small" onClick={toggle} aria-label={`${playing ? "Pause" : "Play"} ${asset.filename}`}>
          <span aria-hidden="true">{playing ? "❚❚" : "▶"}</span> {playing ? "Pause" : "Play"}
        </button>
        <input
          className={css.seek}
          type="range"
          min={0}
          max={duration > 0 ? duration : 1}
          step={0.05}
          value={Math.min(time, duration > 0 ? duration : 1)}
          onChange={(e) => seek(Number(e.target.value))}
          aria-label={`Position in ${asset.filename}`}
          aria-valuetext={`${clock(time)} of ${clock(duration)}`}
          disabled={!(duration > 0)}
        />
        <span className="o-mono o-muted">
          {clock(time)} / {clock(duration)}
        </span>
      </div>
      {error && (
        <p className="o-muted" role="status">
          {error}
        </p>
      )}
    </div>
  );
}
