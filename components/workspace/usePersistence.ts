"use client";

import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import { api, errorText, type ProjectOut } from "@/lib/api";
import { migrate, newProject, PROJECT_VERSION, type Project } from "@/lib/workspace/project";
import type { ActionType } from "@/lib/workspace/reducer";
import type { ProjectStore } from "@/lib/workspace/store";
import type { SaveState } from "./TransportBar";

const AUTOSAVE_MS = 10_000;
const KEY_ACTION_DELAY_MS = 1200;
/** Edits that change the shape of the project are saved soon after; the rest wait for autosave. */
const KEY_ACTIONS = new Set<ActionType>([
  "batch", "addTrack", "removeTrack", "addClip", "deleteClip", "splitClip", "duplicateClip", "setName",
]);

function setUrl(projectId: string | null) {
  if (typeof window === "undefined") return;
  const query = projectId ? `?project=${encodeURIComponent(projectId)}` : "";
  window.history.replaceState(null, "", `${window.location.pathname}${query}`);
}

/**
 * Project persistence (spec 12.7): the first change creates the project on the server,
 * then it is saved every ten seconds while there are unsaved changes and shortly after
 * key actions. The project id lives in the URL, so a reload reopens it.
 */
export function usePersistence(store: ProjectStore, onError: (text: string) => void) {
  const history = useSyncExternalStore(store.subscribe, store.getSnapshot, store.getSnapshot);
  const [projectId, setProjectId] = useState<string | null>(null);
  const [saveState, setSaveState] = useState<SaveState>("saved");
  const idRef = useRef<string | null>(null);
  const savedRef = useRef<Project>(store.project);
  const chain = useRef<Promise<unknown>>(Promise.resolve());
  const failed = useRef(false);
  const opening = useRef<string | null>(null);
  const onErrorRef = useRef(onError);
  onErrorRef.current = onError;

  const adopt = useCallback((id: string | null) => {
    idRef.current = id;
    setProjectId(id);
    setUrl(id);
  }, []);

  /** Save now if there is anything to save. Saves run one after another. Resolves false on failure. */
  const saveNow = useCallback((): Promise<boolean> => {
    const run = async (): Promise<boolean> => {
      const snapshot = store.project;
      if (idRef.current && snapshot === savedRef.current) return true;
      setSaveState("saving");
      try {
        const body = { ...snapshot, updatedAt: new Date().toISOString() };
        if (idRef.current) await api.saveProject(idRef.current, snapshot.name, body, PROJECT_VERSION);
        else adopt((await api.createProject(snapshot.name, body, PROJECT_VERSION)).id);
        savedRef.current = snapshot;
        failed.current = false;
        setSaveState(store.project === snapshot ? "saved" : "dirty");
        return true;
      } catch (err) {
        failed.current = true;
        setSaveState("error");
        onErrorRef.current(`The project could not be saved. ${errorText(err)}`);
        return false;
      }
    };
    const next = chain.current.then(run, run);
    chain.current = next.catch(() => undefined);
    return next;
  }, [store, adopt]);

  // Track unsaved changes; the first change creates the project.
  useEffect(() => {
    if (history.present === savedRef.current) {
      setSaveState((s) => (s === "saving" ? s : "saved"));
      return;
    }
    setSaveState((s) => (s === "saving" ? s : "dirty"));
    if (!idRef.current && history.base === null && !failed.current) void saveNow();
  }, [history, saveNow]);

  // Autosave, save soon after key actions, and save on the way out.
  useEffect(() => {
    const dirty = () => store.project !== savedRef.current && !store.gesture;
    const interval = setInterval(() => {
      if (dirty()) void saveNow();
    }, AUTOSAVE_MS);
    let soon: ReturnType<typeof setTimeout> | undefined;
    const off = store.onAction((action) => {
      if (!KEY_ACTIONS.has(action.type)) return;
      clearTimeout(soon);
      soon = setTimeout(() => void saveNow(), KEY_ACTION_DELAY_MS);
    });
    return () => {
      clearInterval(interval);
      clearTimeout(soon);
      off();
      if (idRef.current && dirty()) void saveNow();
    };
  }, [store, saveNow]);

  const load = useCallback(
    (out: ProjectOut) => {
      store.load({ ...migrate(out.project), name: out.name });
      savedRef.current = store.project;
      failed.current = false;
      adopt(out.id);
      setSaveState("saved");
    },
    [store, adopt],
  );

  /** Open a saved project. Unsaved changes to the current one are saved first. */
  const open = useCallback(
    async (id: string): Promise<boolean> => {
      if (opening.current === id || idRef.current === id) return true;
      opening.current = id;
      try {
        if (idRef.current && store.project !== savedRef.current) await saveNow();
        load(await api.getProject(id));
        return true;
      } catch (err) {
        onErrorRef.current(`That project could not be opened. ${errorText(err)}`);
        return false;
      } finally {
        opening.current = null;
      }
    },
    [store, saveNow, load],
  );

  const startNew = useCallback(async () => {
    if (idRef.current && store.project !== savedRef.current) await saveNow();
    store.load(newProject());
    savedRef.current = store.project;
    failed.current = false;
    adopt(null);
    setSaveState("saved");
  }, [store, saveNow, adopt]);

  /** Save the current state as a new project under a new name, and carry on in the copy. */
  const saveAs = useCallback(
    async (name: string): Promise<boolean> => {
      const previous = idRef.current;
      await chain.current;
      store.dispatch({ type: "setName", name });
      idRef.current = null;
      const ok = await saveNow();
      if (!ok) idRef.current = previous;
      return ok;
    },
    [store, saveNow],
  );

  /** Make a copy on the server and keep working in the original. Returns the copy. */
  const duplicate = useCallback(async (): Promise<ProjectOut | null> => {
    if (!(await saveNow()) || !idRef.current) return null;
    try {
      return await api.duplicateProject(idRef.current);
    } catch (err) {
      onErrorRef.current(`The project could not be duplicated. ${errorText(err)}`);
      return null;
    }
  }, [saveNow]);

  const currentId = useCallback(() => idRef.current, []);

  return { projectId, saveState, saveNow, open, startNew, saveAs, duplicate, currentId };
}
