"use client";

import { useCallback, useEffect, useRef } from "react";

/**
 * Keeps keyboard focus when the focused control is about to disappear (a confirm button,
 * an inline form, a deleted row): name the element that should take focus, by id, and it
 * is focused after the next render.
 */
export function useFocusAfter(): (id: string) => void {
  const pending = useRef<string | null>(null);
  useEffect(() => {
    const id = pending.current;
    if (!id) return;
    const el = document.getElementById(id);
    if (el) {
      pending.current = null;
      el.focus();
    }
  });
  return useCallback((id: string) => {
    pending.current = id;
  }, []);
}
