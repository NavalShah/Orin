// Undo and redo over whole-project snapshots (spec 12.4). Snapshots share structure, so
// a step costs little. A drag is one step: ``preview`` while the pointer moves, ``commit``
// on pointer-up. Anything new clears redo.

export const HISTORY_LIMIT = 100;
/** Repeated changes to the same control inside this window count as one step. */
export const COALESCE_MS = 800;

export type History<T> = {
  past: T[];
  present: T;
  future: T[];
  /** The state when the current gesture began; null between gestures. */
  base: T | null;
  lastKey: string | null;
  lastAt: number;
};

export function createHistory<T>(present: T): History<T> {
  return { past: [], present, future: [], base: null, lastKey: null, lastAt: 0 };
}

export const canUndo = <T>(h: History<T>) => h.past.length > 0 || (h.base !== null && h.base !== h.present);
export const canRedo = <T>(h: History<T>) => h.future.length > 0;

const cap = <T>(past: T[], limit: number) => (past.length > limit ? past.slice(past.length - limit) : past);

/** Record a finished change as one undo step. */
export function push<T>(
  h: History<T>,
  next: T,
  opts: { coalesce?: string; now?: number; limit?: number } = {},
): History<T> {
  if (h.base !== null) h = commit(h, opts.limit);
  if (next === h.present) return h;
  const now = opts.now ?? Date.now();
  const merge =
    opts.coalesce !== undefined && opts.coalesce === h.lastKey && now - h.lastAt < COALESCE_MS && h.past.length > 0;
  if (merge) return { ...h, present: next, future: [], lastAt: now };
  return {
    past: cap([...h.past, h.present], opts.limit ?? HISTORY_LIMIT),
    present: next,
    future: [],
    base: null,
    lastKey: opts.coalesce ?? null,
    lastAt: now,
  };
}

/** Start a gesture. Previews are computed from this state and nothing is recorded until commit. */
export function begin<T>(h: History<T>): History<T> {
  return h.base !== null ? h : { ...h, base: h.present };
}

/** Show an in-progress change without recording it. */
export function preview<T>(h: History<T>, next: T): History<T> {
  if (next === h.present && h.base !== null) return h;
  return { ...h, present: next, base: h.base ?? h.present };
}

/** End the gesture: everything since ``begin`` becomes one undo step. */
export function commit<T>(h: History<T>, limit = HISTORY_LIMIT): History<T> {
  if (h.base === null) return h;
  if (h.base === h.present) return { ...h, base: null };
  return { past: cap([...h.past, h.base], limit), present: h.present, future: [], base: null, lastKey: null, lastAt: 0 };
}

/** Abandon the gesture and go back to where it began. */
export function cancel<T>(h: History<T>): History<T> {
  return h.base === null ? h : { ...h, present: h.base, base: null };
}

export function undo<T>(h: History<T>): History<T> {
  if (h.base !== null) h = commit(h);
  if (h.past.length === 0) return h;
  const previous = h.past[h.past.length - 1];
  return {
    past: h.past.slice(0, -1),
    present: previous,
    future: [h.present, ...h.future],
    base: null,
    lastKey: null,
    lastAt: 0,
  };
}

export function redo<T>(h: History<T>): History<T> {
  if (h.base !== null) h = commit(h);
  if (h.future.length === 0) return h;
  const [next, ...rest] = h.future;
  return { past: [...h.past, h.present], present: next, future: rest, base: null, lastKey: null, lastAt: 0 };
}
