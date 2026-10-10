"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { FormatScore } from "@/lib/api";
import { scheduleNotes, scoreToSynthNotes } from "@/lib/audio/synth";
import { audioContext } from "./engine";

/**
 * Plays what the notation says (not what was performed) through the shared synth, and
 * reports which note is sounding so the score can highlight it (spec 9.2, 11.2).
 */
export function useScorePlayer(score: FormatScore | null) {
  const [playing, setPlaying] = useState(false);
  const [currentId, setCurrentId] = useState<string | null>(null);
  const [unavailable, setUnavailable] = useState(false);
  const handle = useRef<{ stop(): void } | null>(null);
  const frame = useRef(0);

  const stop = useCallback(() => {
    handle.current?.stop();
    handle.current = null;
    if (frame.current) cancelAnimationFrame(frame.current);
    frame.current = 0;
    setPlaying(false);
    setCurrentId(null);
  }, []);

  const play = useCallback(() => {
    stop();
    if (!score || score.notes.length === 0) return;
    const ctx = audioContext();
    if (!ctx) {
      setUnavailable(true);
      return;
    }
    // Start at the first note rather than playing any silence before it.
    const lead = Math.min(...score.notes.map((n) => n.onset_s));
    const notes = scoreToSynthNotes(score).map((n) => ({ ...n, start: n.start - lead }));
    const startAt = ctx.currentTime + 0.08;
    const scheduled = scheduleNotes(ctx, ctx.destination, notes, startAt);
    handle.current = scheduled;
    setPlaying(true);

    const tick = () => {
      const t = ctx.currentTime - startAt + lead;
      if (ctx.currentTime > scheduled.endTime + 0.15) {
        stop();
        return;
      }
      let id: string | null = null;
      for (const n of score.notes) {
        if (n.onset_s <= t && t < n.onset_s + n.duration_s) id = n.id;
      }
      setCurrentId(id);
      frame.current = requestAnimationFrame(tick);
    };
    frame.current = requestAnimationFrame(tick);
  }, [score, stop]);

  // Stop when the score changes or the screen goes away.
  useEffect(() => stop, [score, stop]);

  return { playing, currentId, play, stop, unavailable };
}
