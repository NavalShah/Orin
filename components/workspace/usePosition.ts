"use client";

import { useEffect, useState } from "react";
import type { Transport } from "@/lib/audio/engine";

/** The playhead in seconds. Updates every frame while playing, and on seek, pause and stop. */
export function usePosition(transport: Transport): number {
  return useDerivedPosition(transport, (sec) => sec);
}

/**
 * Something computed from the playhead (the current drum step, say). The component only
 * re-renders when the computed value changes, not on every frame.
 */
export function useDerivedPosition<T>(transport: Transport, derive: (sec: number) => T): T {
  const [value, setValue] = useState(() => derive(transport.position));
  useEffect(() => {
    let raf = 0;
    const loop = () => {
      setValue(derive(transport.position));
      if (transport.playing) raf = requestAnimationFrame(loop);
    };
    const restart = () => {
      cancelAnimationFrame(raf);
      loop();
    };
    const off = transport.subscribe(restart);
    restart();
    return () => {
      off();
      cancelAnimationFrame(raf);
    };
  }, [transport, derive]);
  return value;
}
