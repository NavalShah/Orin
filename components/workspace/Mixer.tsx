"use client";

import { useEffect, useRef } from "react";
import type { Transport } from "@/lib/audio/engine";
import type { Track } from "@/lib/workspace/project";
import type { Action } from "@/lib/workspace/reducer";
import type { DispatchOptions } from "@/lib/workspace/store";
import { panText, percent, Slider } from "./fields";
import s from "./workspace.module.css";

type Props = {
  tracks: Track[];
  masterGain: number;
  transport: Transport;
  playing: boolean;
  dispatch: (action: Action, opts?: DispatchOptions) => void;
};

const KIND_LABEL: Record<Track["kind"], string> = { audio: "Audio", midi: "MIDI", drums: "Drums" };

/** Volume, pan, mute, solo and a level meter per track, and the master (spec 12.2). */
export function Mixer({ tracks, masterGain, transport, playing, dispatch }: Props) {
  const fills = useRef(new Map<string, HTMLElement>());
  const register = (id: string) => (el: HTMLElement | null) => {
    if (el) fills.current.set(id, el);
    else fills.current.delete(id);
  };

  // Meters read the analysers every frame and write straight to the DOM: no re-render.
  useEffect(() => {
    const bars = fills.current;
    if (!playing) {
      bars.forEach((el) => (el.style.clipPath = "inset(0 100% 0 0)"));
      return;
    }
    const shown = new Map<string, number>();
    let raf = 0;
    const draw = () => {
      for (const [id, el] of bars) {
        const level = transport.level(id);
        const previous = shown.get(id) ?? 0;
        const next = level > previous ? level : previous * 0.92; // rise at once, fall slowly
        shown.set(id, next);
        // Square root: quiet signals still move the meter visibly.
        const hidden = 100 - Math.min(1, Math.sqrt(next)) * 100;
        el.style.clipPath = `inset(0 ${hidden.toFixed(1)}% 0 0)`;
      }
      raf = requestAnimationFrame(draw);
    };
    raf = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(raf);
  }, [playing, transport]);

  const anySolo = tracks.some((t) => t.solo);

  return (
    <div className={s.mixer}>
      {tracks.length === 0 ? <p className={s.hint}>Add a track to see its channel here.</p> : null}
      {tracks.map((track) => {
        const silent = track.mute || (anySolo && !track.solo);
        return (
          <section key={track.id} className={s.strip} aria-label={`${track.name} channel`}>
            <h3 className={s.stripName} title={track.name}>
              {track.name}
            </h3>
            <p className={s.stripKind}>
              {KIND_LABEL[track.kind]}
              {silent ? " · silent" : ""}
            </p>
            <div className={s.meter} role="img" aria-label={`${track.name} level meter`}>
              <div className={s.meterFill} ref={register(track.id)} />
            </div>
            <Slider
              label="Volume"
              min={0}
              max={1.5}
              step={0.01}
              value={track.gain}
              valueText={percent(track.gain)}
              showValue
              onChange={(v) => dispatch({ type: "setTrackGain", trackId: track.id, gain: v }, { coalesce: `gain:${track.id}` })}
            />
            <Slider
              label="Pan"
              min={-1}
              max={1}
              step={0.05}
              value={track.pan}
              valueText={panText(track.pan)}
              showValue
              onChange={(v) => dispatch({ type: "setTrackPan", trackId: track.id, pan: v }, { coalesce: `pan:${track.id}` })}
            />
            <div className={s.stripButtons}>
              <button
                type="button"
                className="o-btn o-btn-small"
                aria-pressed={track.mute}
                aria-label={`Mute ${track.name}`}
                onClick={() => dispatch({ type: "setTrackMute", trackId: track.id, mute: !track.mute })}
              >
                Mute
              </button>
              <button
                type="button"
                className="o-btn o-btn-small"
                aria-pressed={track.solo}
                aria-label={`Solo ${track.name}`}
                onClick={() => dispatch({ type: "setTrackSolo", trackId: track.id, solo: !track.solo })}
              >
                Solo
              </button>
            </div>
          </section>
        );
      })}
      <section className={`${s.strip} ${s.masterStrip}`} aria-label="Master channel">
        <h3 className={s.stripName}>Master</h3>
        <p className={s.stripKind}>Output</p>
        <div className={s.meter} role="img" aria-label="Master level meter">
          <div className={s.meterFill} ref={register("master")} />
        </div>
        <Slider
          label="Volume"
          min={0}
          max={1.5}
          step={0.01}
          value={masterGain}
          valueText={percent(masterGain)}
          showValue
          onChange={(v) => dispatch({ type: "setMasterGain", gain: v }, { coalesce: "master" })}
        />
      </section>
    </div>
  );
}
