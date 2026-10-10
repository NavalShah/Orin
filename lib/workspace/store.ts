// The one place the current project lives. Plain TypeScript so the audio engine, the
// autosave loop and React (through useSyncExternalStore) all read the same state.
import * as H from "./history";
import { newProject, type Project } from "./project";
import { reduce, type Action } from "./reducer";

export type DispatchOptions = {
  /** Part of a drag: show it, but record nothing until ``commit``. Computed from the state at ``begin``. */
  transient?: boolean;
  /** Changes with the same key in quick succession (a slider being dragged) make one undo step. */
  coalesce?: string;
};

export class ProjectStore {
  private history: H.History<Project>;
  private listeners = new Set<() => void>();
  private actionListeners = new Set<(action: Action) => void>();

  constructor(project: Project = newProject()) {
    this.history = H.createHistory(project);
  }

  subscribe = (fn: () => void) => {
    this.listeners.add(fn);
    return () => {
      this.listeners.delete(fn);
    };
  };
  getSnapshot = () => this.history;

  /** Called after every recorded (not transient) action, with the action. */
  onAction(fn: (action: Action) => void) {
    this.actionListeners.add(fn);
    return () => {
      this.actionListeners.delete(fn);
    };
  }

  get project(): Project {
    return this.history.present;
  }
  /** True while a drag is in progress. */
  get gesture(): boolean {
    return this.history.base !== null;
  }

  private set(next: H.History<Project>) {
    if (next === this.history) return;
    this.history = next;
    this.listeners.forEach((fn) => fn());
  }

  dispatch(action: Action, opts: DispatchOptions = {}) {
    if (opts.transient) {
      this.set(H.preview(this.history, reduce(this.history.base ?? this.history.present, action)));
      return;
    }
    const before = this.history;
    this.set(H.push(before, reduce(before.present, action), { coalesce: opts.coalesce }));
    if (this.history !== before) this.actionListeners.forEach((fn) => fn(action));
  }

  begin() {
    this.set(H.begin(this.history));
  }
  /** End a drag. ``action`` names what the drag did, for listeners (autosave). */
  commit(action?: Action) {
    const before = this.history;
    this.set(H.commit(before));
    if (action && this.history.present !== before.base) this.actionListeners.forEach((fn) => fn(action));
  }
  cancel() {
    this.set(H.cancel(this.history));
  }
  undo() {
    this.set(H.undo(this.history));
  }
  redo() {
    this.set(H.redo(this.history));
  }

  /** Replace the project (open, new). History starts again. */
  load(project: Project) {
    this.set(H.createHistory(project));
  }
}
