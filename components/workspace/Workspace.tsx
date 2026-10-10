"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { api, errorText, type Asset } from "@/lib/api";
import { renderOffline, Transport } from "@/lib/audio/engine";
import { encodeMidi } from "@/lib/audio/smf";
import { encodeWav } from "@/lib/audio/wav";
import { handoff, readHandoff } from "@/lib/handoff";
import { BufferCache } from "@/lib/workspace/buffers";
import { conversionToClip } from "@/lib/workspace/conversion";
import { downloadBlob, safeFilename } from "@/lib/workspace/download";
import { canRedo, canUndo } from "@/lib/workspace/history";
import { importAudioFile, stripExtension } from "@/lib/workspace/importAudio";
import {
  clipEnd, findClip, projectEndSec, uid, type AudioClip, type DrumVoice, type MidiClip, type Project, type TrackKind,
} from "@/lib/workspace/project";
import { soloClipProject, type Action } from "@/lib/workspace/reducer";
import { ProjectStore, type DispatchOptions } from "@/lib/workspace/store";
import { gridStepSec, secPerBar } from "@/lib/workspace/time";
import { BrowserPanel } from "./BrowserPanel";
import { Inspector } from "./Inspector";
import { Mixer } from "./Mixer";
import { PianoRoll } from "./PianoRoll";
import { GRID_CHOICES, Playlist } from "./Playlist";
import { StepSequencer } from "./StepSequencer";
import { TransportBar } from "./TransportBar";
import { useDerivedPosition } from "./usePosition";
import { usePersistence } from "./usePersistence";
import s from "./workspace.module.css";

type Notice = { kind: "error" | "info"; text: string };
type Tab = "mixer" | "piano" | "steps";
const TABS: Array<{ id: Tab; label: string }> = [
  { id: "mixer", label: "Mixer" },
  { id: "piano", label: "Piano roll" },
  { id: "steps", label: "Step sequencer" },
];

/** The drum step being played. A leaf, so the rest of the workspace doesn't re-render per frame. */
function LiveSteps({ transport, project, store, onAddDrums }: { transport: Transport; project: Project; store: ProjectStore; onAddDrums: () => void }) {
  const stepSec = secPerBar(project.tempo, project.timeSignature) / (project.drums?.steps ?? 16);
  const steps = project.drums?.steps ?? 16;
  const derive = useCallback(
    (sec: number) => (transport.playing ? Math.floor(sec / stepSec + 1e-6) % steps : -1),
    [transport, stepSec, steps],
  );
  const current = useDerivedPosition(transport, derive);
  return (
    <StepSequencer
      drums={project.drums}
      currentStep={current}
      onToggle={(voice: DrumVoice, step: number) => {
        const wasOn = project.drums?.rows.find((r) => r.voice === voice)?.steps[step];
        store.dispatch({ type: "toggleStep", voice, step });
        if (!wasOn && !transport.playing) transport.auditionDrum(voice);
      }}
      onGain={(voice, gain) => store.dispatch({ type: "setDrumRowGain", voice, gain }, { coalesce: `drumgain:${voice}` })}
      onPan={(voice, pan) => store.dispatch({ type: "setDrumRowPan", voice, pan }, { coalesce: `drumpan:${voice}` })}
      onAddDrums={onAddDrums}
    />
  );
}

/** The FL Studio-inspired workspace (spec 12): transport, browser, playlist, mixer, piano roll, step sequencer. */
export default function Workspace() {
  const params = useSearchParams();
  const router = useRouter();

  const [store] = useState(() => new ProjectStore());
  const [cache] = useState(() => new BufferCache());
  const [transport] = useState(() => new Transport({ getProject: () => store.project, getBuffer: cache.get }));

  const history = useSyncExternalStore(store.subscribe, store.getSnapshot, store.getSnapshot);
  const project = history.present;
  const cacheVersion = useSyncExternalStore(cache.subscribe, cache.getVersion, cache.getVersion);
  const playing = useSyncExternalStore(transport.subscribe, () => transport.playing, () => false);

  const [selection, setSelection] = useState<{ clipId: string | null; noteId: string | null }>({ clipId: null, noteId: null });
  const [pianoClipId, setPianoClipId] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("mixer");
  const [pxPerSec, setPxPerSec] = useState(60);
  const [snap, setSnap] = useState(true);
  const [gridChoice, setGridChoice] = useState("1/4 beat");
  const [notice, setNotice] = useState<Notice | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);

  const showError = useCallback((text: string) => setNotice({ kind: "error", text }), []);
  const persistence = usePersistence(store, showError);
  const { saveState, saveNow } = persistence;

  const grid = GRID_CHOICES.find((g) => g.label === gridChoice) ?? GRID_CHOICES[3];
  const beatsInBar = (project.timeSignature[0] * 4) / project.timeSignature[1];
  const division = grid.division === "bar" ? 1 / beatsInBar : grid.division;

  const dispatch = useCallback((action: Action, opts?: DispatchOptions) => store.dispatch(action, opts), [store]);

  const selectedClip = findClip(project, selection.clipId);
  const pianoClip = useMemo(() => {
    const chosen = findClip(project, pianoClipId);
    return chosen?.kind === "midi" ? chosen : undefined;
  }, [project, pianoClipId]);
  const selectedNote =
    selectedClip?.kind === "midi" && selection.noteId ? selectedClip.notes.find((n) => n.id === selection.noteId) : undefined;

  const selectClip = useCallback(
    (clipId: string | null) => {
      setSelection((prev) => (prev.clipId === clipId && prev.noteId === null ? prev : { clipId, noteId: null }));
      if (clipId && findClip(store.project, clipId)?.kind === "midi") setPianoClipId(clipId);
    },
    [store],
  );
  const openClip = useCallback(
    (clipId: string) => {
      selectClip(clipId);
      if (findClip(store.project, clipId)?.kind === "midi") setTab("piano");
    },
    [selectClip, store],
  );
  const selectNote = useCallback(
    (noteId: string | null) => setSelection((prev) => ({ clipId: pianoClipId ?? prev.clipId, noteId })),
    [pianoClipId],
  );

  // --- engine --------------------------------------------------------------

  // Mixer changes apply at once; clip changes reschedule, after the drag has ended.
  useEffect(() => {
    transport.sync(history.base !== null);
  }, [transport, project, history.base]);

  // Audio that finishes loading during playback joins in.
  useEffect(() => {
    transport.refresh();
  }, [transport, cacheVersion]);

  useEffect(() => () => transport.dispose(), [transport]);

  // Fetch and decode the audio behind every clip.
  useEffect(() => {
    for (const clip of project.clips) {
      if (clip.kind !== "audio") continue;
      const id = clip.assetId;
      if (cache.has(id) || cache.isLoading(id) || cache.errorOf(id)) continue;
      cache.load(id, async () => api.assetBytes(await api.getAsset(id)), clip.name).catch(() => undefined);
    }
  }, [project.clips, cache]);

  const play = useCallback(() => {
    try {
      transport.play();
    } catch {
      showError("This browser could not start audio playback.");
    }
  }, [transport, showError]);
  const toggle = useCallback(() => (transport.playing ? transport.pause() : play()), [transport, play]);
  const seek = useCallback((sec: number) => transport.seek(sec), [transport]);

  // --- editing -------------------------------------------------------------

  const addMidiClip = useCallback(
    (trackId: string | null) => {
      const p = store.project;
      const bar = secPerBar(p.tempo, p.timeSignature);
      const id = trackId ?? p.tracks.find((t) => t.kind === "midi")?.id ?? uid("trk");
      const clip: MidiClip = {
        id: uid("clip"),
        trackId: id,
        kind: "midi",
        name: "Pattern",
        startSec: Math.floor(transport.position / bar + 1e-6) * bar,
        lengthSec: 4 * bar,
        tonicHz: p.tonicHz,
        notes: [],
      };
      const actions: Action[] = [{ type: "addClip", clip }];
      if (!p.tracks.some((t) => t.id === id)) actions.unshift({ type: "addTrack", kind: "midi", id });
      store.dispatch({ type: "batch", actions });
      setSelection({ clipId: clip.id, noteId: null });
      setPianoClipId(clip.id);
      setTab("piano");
    },
    [store, transport],
  );

  const addTrack = useCallback(
    (kind: TrackKind) => {
      if (kind === "midi") {
        // A new MIDI track comes with an empty clip, ready for the piano roll.
        addMidiClip(uid("trk"));
        return;
      }
      store.dispatch({ type: "addTrack", kind });
      if (kind === "drums") setTab("steps");
    },
    [store, addMidiClip],
  );

  const addAssetClip = useCallback(
    async (asset: Asset, where: { trackId?: string | null; startSec?: number } = {}) => {
      setBusy(`Loading ${asset.filename}`);
      try {
        const buffer = await cache.load(asset.id, () => api.assetBytes(asset), asset.filename);
        const trackId = where.trackId ?? uid("trk");
        const clip: AudioClip = {
          id: uid("clip"),
          trackId,
          kind: "audio",
          assetId: asset.id,
          name: stripExtension(asset.filename),
          startSec: where.startSec ?? transport.position,
          offsetSec: 0,
          lengthSec: buffer.duration,
          gain: 1,
          fadeInSec: 0,
          fadeOutSec: 0,
          assetDurationSec: buffer.duration,
        };
        const actions: Action[] = [{ type: "addClip", clip }];
        if (!where.trackId) actions.unshift({ type: "addTrack", kind: "audio", id: trackId });
        store.dispatch({ type: "batch", actions });
        setSelection({ clipId: clip.id, noteId: null });
      } catch (err) {
        showError(`${asset.filename} could not be added. ${errorText(err)}`);
      } finally {
        setBusy(null);
      }
    },
    [cache, store, transport, showError],
  );

  const dropAsset = useCallback(
    async (assetId: string, trackId: string | null, sec: number) => {
      try {
        await addAssetClip(await api.getAsset(assetId), { trackId, startSec: sec });
      } catch (err) {
        showError(errorText(err));
      }
    },
    [addAssetClip, showError],
  );

  const dropFiles = useCallback(
    async (files: File[], trackId: string | null, sec: number) => {
      let at = sec;
      for (const file of files) {
        setBusy(`Bringing in ${file.name}`);
        try {
          const { asset, buffer } = await importAudioFile(file, cache);
          await addAssetClip(asset, { trackId, startSec: at });
          at += buffer.duration;
          setRefreshKey((n) => n + 1);
        } catch (err) {
          showError(err instanceof Error ? err.message : "That file could not be imported.");
        } finally {
          setBusy(null);
        }
      }
    },
    [cache, addAssetClip, showError],
  );

  const splitAtPlayhead = useCallback(() => {
    const at = transport.position;
    const p = store.project;
    const targets = selection.clipId
      ? p.clips.filter((c) => c.id === selection.clipId)
      : p.clips.filter((c) => c.startSec < at && clipEnd(c) > at);
    const actions: Action[] = targets
      .filter((c) => c.startSec < at && clipEnd(c) > at)
      .map((c) => ({ type: "splitClip", clipId: c.id, atSec: at }));
    if (actions.length === 0) {
      setNotice({ kind: "info", text: "Move the playhead inside a clip to split it there." });
      return;
    }
    store.dispatch({ type: "batch", actions });
  }, [store, transport, selection.clipId]);

  const duplicateSelected = useCallback(() => {
    if (!selection.clipId) return;
    const newId = uid("clip");
    store.dispatch({ type: "duplicateClip", clipId: selection.clipId, newId });
    setSelection({ clipId: newId, noteId: null });
  }, [store, selection.clipId]);

  const deleteSelected = useCallback(() => {
    if (selection.clipId && selection.noteId) {
      store.dispatch({ type: "deleteNote", clipId: selection.clipId, noteId: selection.noteId });
      setSelection({ clipId: selection.clipId, noteId: null });
    } else if (selection.clipId) {
      store.dispatch({ type: "deleteClip", clipId: selection.clipId });
      setSelection({ clipId: null, noteId: null });
    }
  }, [store, selection]);

  /** Arrow keys: move the selected note or clip by one grid step; up and down change a note's pitch. */
  const nudge = useCallback(
    (dx: number, dy: number): boolean => {
      const p = store.project;
      const clip = findClip(p, selection.clipId);
      if (!clip) return false;
      const step = gridStepSec(p.tempo, division);
      if (clip.kind === "midi" && selection.noteId) {
        const note = clip.notes.find((n) => n.id === selection.noteId);
        if (!note) return false;
        store.dispatch(
          { type: "moveNote", clipId: clip.id, noteId: note.id, startSec: Math.max(0, note.startSec + dx * step), midi: note.midi + dy },
          { coalesce: `nudge:${note.id}` },
        );
        return true;
      }
      if (dx === 0) return false;
      store.dispatch(
        { type: "moveClip", clipId: clip.id, startSec: Math.max(0, clip.startSec + dx * step) },
        { coalesce: `nudge:${clip.id}` },
      );
      return true;
    },
    [store, selection, division],
  );

  // --- export and handoffs -------------------------------------------------

  /** Make sure every audio clip in a project has its audio decoded before rendering. */
  const ensureAudio = useCallback(
    async (p: Project) => {
      for (const clip of p.clips) {
        if (clip.kind !== "audio" || cache.has(clip.assetId)) continue;
        await cache.load(clip.assetId, async () => api.assetBytes(await api.getAsset(clip.assetId)), clip.name);
      }
    },
    [cache],
  );

  const exportWav = useCallback(async () => {
    const p = store.project;
    const end = projectEndSec(p);
    if (end <= 0) {
      setNotice({ kind: "info", text: "There is nothing to export yet. Add a clip or a drum pattern first." });
      return;
    }
    setBusy("Rendering the mixdown");
    try {
      await ensureAudio(p);
      const rendered = await renderOffline(p, cache.all(), { startSec: 0, endSec: end, sampleRate: 44100 });
      downloadBlob(encodeWav(rendered), `${safeFilename(p.name, "mixdown")}.wav`);
      setNotice({ kind: "info", text: `Exported ${safeFilename(p.name, "mixdown")}.wav (${end.toFixed(1)} seconds).` });
    } catch (err) {
      showError(`The mixdown could not be rendered. ${errorText(err)}`);
    } finally {
      setBusy(null);
    }
  }, [store, cache, ensureAudio, showError]);

  const exportMidi = useCallback(() => {
    const p = store.project;
    const clip = findClip(p, selection.clipId);
    if (!clip || clip.kind !== "midi") return;
    const notes = clip.notes
      .filter((n) => n.startSec < clip.lengthSec)
      .map((n) => ({ ...n, durSec: Math.min(n.durSec, clip.lengthSec - n.startSec) }));
    const blob = new Blob([encodeMidi(notes, p.tempo)], { type: "audio/midi" });
    downloadBlob(blob, `${safeFilename(clip.name, "clip")}.mid`);
    const microtonal = notes.filter((n) => Math.abs(n.detuneCents) >= 0.05).length;
    setNotice({
      kind: "info",
      text: `Exported ${notes.length} notes as ${safeFilename(clip.name, "clip")}.mid.${microtonal ? ` ${microtonal} microtonal notes are written as pitch bends (range 2 semitones).` : ""}`,
    });
  }, [store, selection.clipId]);

  /** Workspace -> Notation (spec 3.5): bounce the selected clip, or the loop region, and transcribe it. */
  const transcribe = useCallback(async () => {
    const p = store.project;
    const clip = findClip(p, selection.clipId);
    const source = clip ? soloClipProject(p, clip.id) : p.loop.enabled ? { ...p, metronome: false } : null;
    if (!source) {
      setNotice({ kind: "info", text: "Select a clip, or switch the loop on, to choose what to transcribe." });
      return;
    }
    const startSec = clip ? clip.startSec : p.loop.startSec;
    const endSec = clip ? clipEnd(clip) : p.loop.endSec;
    const name = clip ? clip.name : `${p.name} loop`;
    setBusy("Bouncing the selection");
    try {
      await ensureAudio(source);
      // Half the usual rate: this bounce is only analysed (pitch tracking runs at 16 kHz), and a
      // 44.1 kHz stereo WAV passes the server's 10 MB upload limit after about a minute.
      const rendered = await renderOffline(source, cache.all(), { startSec, endSec, sampleRate: 22050 });
      setBusy("Sending the bounce to the Library");
      const asset = await api.uploadAsset(encodeWav(rendered), { source: "bounce", filename: `${safeFilename(name, "selection")} bounce.wav` });
      await saveNow();
      router.push(handoff.studioWithAsset(asset.id));
    } catch (err) {
      showError(`The selection could not be sent to the Notation Studio. ${errorText(err)}`);
    } finally {
      setBusy(null);
    }
  }, [store, selection.clipId, cache, ensureAudio, saveNow, router, showError]);

  /** Notation -> piano roll (spec 3.5, 12.5): the score becomes a MIDI clip, microtones kept as detune. */
  const importConversion = useCallback(
    async (conversionId: string) => {
      setBusy("Bringing in the notation");
      try {
        const conversion = await api.conversion(conversionId);
        const trackId = uid("trk");
        const clip = conversionToClip(conversion, { trackId, startSec: transport.position });
        if (!clip) {
          setNotice({ kind: "error", text: "That conversion has no notes to bring in yet. Finish it in the Notation Studio first." });
          return;
        }
        const p = store.project;
        const actions: Action[] = [
          { type: "addTrack", kind: "midi", id: trackId, name: "Notation" },
          { type: "addClip", clip },
        ];
        const tempo = conversion.score?.options.tempo_bpm;
        if (p.clips.length === 0 && tempo) actions.unshift({ type: "setTempo", tempo });
        if (p.tonicHz === null && clip.tonicHz) actions.push({ type: "setTonic", tonicHz: clip.tonicHz });
        store.dispatch({ type: "batch", actions });
        setSelection({ clipId: clip.id, noteId: null });
        setPianoClipId(clip.id);
        setTab("piano");
        const microtonal = clip.notes.filter((n) => Math.abs(n.detuneCents) >= 5).length;
        setNotice({
          kind: "info",
          text: `Brought in ${clip.notes.length} notes from the Notation Studio as a MIDI clip.${microtonal ? ` ${microtonal} of them sit between piano keys and keep their detune.` : ""} This is what the notation says, not the original performance.`,
        });
      } catch (err) {
        showError(`The notation could not be brought in. ${errorText(err)}`);
      } finally {
        setBusy(null);
      }
    },
    [store, transport, showError],
  );

  // Open ?project=, then apply ?import_conversion= and ?import_asset= once each.
  const projectParam = params.get("project");
  const { importConversion: conversionParam, importAsset: assetParam } = readHandoff(params);
  // Each link is acted on once, and one after another: an import must not race the
  // project it is being added to.
  const handled = useRef(new Set<string>());
  const linkQueue = useRef<Promise<void>>(Promise.resolve());
  const { open: openProject, currentId } = persistence;
  useEffect(() => {
    const once = (key: string) => {
      if (handled.current.has(key)) return false;
      handled.current.add(key);
      return true;
    };
    linkQueue.current = linkQueue.current.then(async () => {
      if (projectParam && once(`p:${projectParam}`) && projectParam !== currentId()) {
        if (await openProject(projectParam)) setSelection({ clipId: null, noteId: null });
      }
      if (conversionParam && once(`c:${conversionParam}`)) await importConversion(conversionParam);
      if (assetParam && once(`a:${assetParam}`)) {
        try {
          await addAssetClip(await api.getAsset(assetParam));
        } catch (err) {
          showError(`That track could not be opened in the Workspace. ${errorText(err)}`);
        }
      }
    });
  }, [projectParam, conversionParam, assetParam, openProject, currentId, importConversion, addAssetClip, showError]);

  // The sessions list changes whenever a save lands.
  const firstSaveState = useRef(true);
  useEffect(() => {
    if (firstSaveState.current) {
      firstSaveState.current = false;
      return;
    }
    if (saveState === "saved") setRefreshKey((n) => n + 1);
  }, [saveState, persistence.projectId]);

  // --- project menu --------------------------------------------------------

  const resetView = useCallback(() => {
    transport.stop();
    setSelection({ clipId: null, noteId: null });
    setPianoClipId(null);
  }, [transport]);

  const onOpenProject = useCallback(
    async (id: string) => {
      setBusy("Opening the project");
      resetView();
      await openProject(id);
      setBusy(null);
    },
    [openProject, resetView],
  );

  const onNew = useCallback(async () => {
    resetView();
    await persistence.startNew();
    setNotice(null);
  }, [persistence, resetView]);

  const onSaveAs = useCallback(async () => {
    const name = window.prompt("Name for the new project", `${store.project.name} copy`)?.trim();
    if (!name) return;
    if (await persistence.saveAs(name)) setNotice({ kind: "info", text: `Saved as "${name}". You are now working in the new project.` });
  }, [persistence, store]);

  const onDuplicate = useCallback(async () => {
    const copy = await persistence.duplicate();
    if (copy) {
      setRefreshKey((n) => n + 1);
      setNotice({ kind: "info", text: `Duplicated as "${copy.name}". Find it under Previous sessions.` });
    }
  }, [persistence]);

  // A file dropped beside a drop zone must not make the browser leave the page.
  useEffect(() => {
    const guard = (e: DragEvent) => {
      if (e.dataTransfer?.types.includes("Files")) e.preventDefault();
    };
    window.addEventListener("dragover", guard);
    window.addEventListener("drop", guard);
    return () => {
      window.removeEventListener("dragover", guard);
      window.removeEventListener("drop", guard);
    };
  }, []);

  // --- keyboard (spec 13.5) --------------------------------------------------

  const keys = useRef({ toggle, deleteSelected, splitAtPlayhead, nudge, saveNow, selection });
  keys.current = { toggle, deleteSelected, splitAtPlayhead, nudge, saveNow, selection };
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.defaultPrevented) return;
      const k = keys.current;
      const target = e.target instanceof HTMLElement ? e.target : null;
      const tag = target?.tagName ?? "";
      const typing = tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || !!target?.isContentEditable;
      const textual = typing && !(target instanceof HTMLInputElement && target.type === "range");
      const mod = e.metaKey || e.ctrlKey;
      const key = e.key.toLowerCase();

      if (mod && key === "z") {
        if (textual) return; // let the text field undo its own typing
        e.preventDefault();
        if (e.shiftKey) store.redo();
        else store.undo();
        return;
      }
      if (mod && key === "y" && !textual) {
        e.preventDefault();
        store.redo();
        return;
      }
      if (mod && key === "s") {
        e.preventDefault();
        void k.saveNow();
        return;
      }
      if (mod || e.altKey || typing) return;

      if (e.key === " ") {
        // On a real button or link, Space presses it.
        if (tag === "BUTTON" || tag === "A" || tag === "SUMMARY") return;
        e.preventDefault();
        k.toggle();
      } else if (e.key === "Delete" || e.key === "Backspace") {
        if (!k.selection.clipId) return;
        e.preventDefault();
        k.deleteSelected();
      } else if (key === "s") {
        e.preventDefault();
        k.splitAtPlayhead();
      } else if (e.key === "ArrowLeft" || e.key === "ArrowRight" || e.key === "ArrowUp" || e.key === "ArrowDown") {
        const dx = e.key === "ArrowLeft" ? -1 : e.key === "ArrowRight" ? 1 : 0;
        const dy = e.key === "ArrowUp" ? 1 : e.key === "ArrowDown" ? -1 : 0;
        if (k.nudge(dx, dy)) e.preventDefault();
      } else if (e.key === "Escape") {
        setSelection((prev) => (prev.noteId ? { clipId: prev.clipId, noteId: null } : { clipId: null, noteId: null }));
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [store]);

  return (
    <div className={s.workspace}>
      <h1 className="o-sr-only">Workspace</h1>
      <TransportBar
        project={project}
        transport={transport}
        playing={playing}
        dispatch={dispatch}
        onPlay={play}
        onPause={() => transport.pause()}
        onStop={() => transport.stop()}
        canUndo={canUndo(history)}
        canRedo={canRedo(history)}
        onUndo={() => store.undo()}
        onRedo={() => store.redo()}
        saveState={saveState}
        stored={persistence.projectId !== null}
        onSave={() => void saveNow()}
        onSaveAs={() => void onSaveAs()}
        onDuplicate={() => void onDuplicate()}
        onNew={() => void onNew()}
        onExportWav={() => void exportWav()}
        busy={busy !== null}
      />

      <div className={s.notices} aria-live="polite">
        {busy ? (
          <p className={s.busy} role="status">
            {busy}...
          </p>
        ) : null}
        {notice ? (
          <div className={notice.kind === "error" ? "o-alert" : "o-note"} role={notice.kind === "error" ? "alert" : "status"}>
            <span>{notice.text}</span>
            <button type="button" className="o-btn o-btn-small o-btn-quiet" onClick={() => setNotice(null)}>
              Dismiss
            </button>
          </div>
        ) : null}
      </div>

      <div className={s.middle}>
        <BrowserPanel
          cache={cache}
          currentProjectId={persistence.projectId}
          refreshKey={refreshKey}
          onAddAsset={(asset) => void addAssetClip(asset)}
          onOpenProject={(id) => void onOpenProject(id)}
          busy={busy !== null}
        />
        <Playlist
          project={project}
          store={store}
          transport={transport}
          cache={cache}
          cacheVersion={cacheVersion}
          selectedClipId={selection.clipId}
          onSelectClip={selectClip}
          onOpenClip={openClip}
          pxPerSec={pxPerSec}
          onZoom={setPxPerSec}
          snap={snap}
          onSnap={setSnap}
          division={division}
          gridChoice={grid.label}
          onGridChoice={setGridChoice}
          onSeek={seek}
          onAddTrack={addTrack}
          onAddMidiClip={addMidiClip}
          onEditSteps={() => setTab("steps")}
          onDropAsset={(id, trackId, sec) => void dropAsset(id, trackId, sec)}
          onDropFiles={(files, trackId, sec) => void dropFiles(files, trackId, sec)}
          onSplit={splitAtPlayhead}
          onDuplicate={duplicateSelected}
          onDelete={deleteSelected}
        />
        <Inspector
          key={selectedClip?.id ?? "none"}
          project={project}
          clip={selectedClip}
          note={selectedNote}
          dispatch={dispatch}
          onTranscribe={() => void transcribe()}
          onExportMidi={exportMidi}
          onOpenPianoRoll={() => selectedClip && openClip(selectedClip.id)}
          busy={busy !== null}
        />
      </div>

      <section className={s.bottom} aria-label="Mixer, piano roll and step sequencer">
        <div
          className={s.tabs}
          role="tablist"
          aria-label="Lower panel"
          onKeyDown={(e) => {
            // Left and right arrows move between tabs, as tabs do everywhere.
            if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
            if (!(e.target instanceof HTMLElement) || e.target.getAttribute("role") !== "tab") return;
            e.preventDefault();
            const index = TABS.findIndex((t) => t.id === tab);
            const next = TABS[(index + (e.key === "ArrowRight" ? 1 : TABS.length - 1)) % TABS.length];
            setTab(next.id);
            document.getElementById(`ws-tab-${next.id}`)?.focus();
          }}
        >
          {TABS.map((t) => (
            <button
              key={t.id}
              type="button"
              role="tab"
              id={`ws-tab-${t.id}`}
              aria-selected={tab === t.id}
              aria-controls="ws-tabpanel"
              tabIndex={tab === t.id ? 0 : -1}
              className={s.tab}
              onClick={() => setTab(t.id)}
            >
              {t.label}
            </button>
          ))}
          <span className={s.shortcuts}>
            Space: play or pause {"·"} S: split {"·"} Delete: remove {"·"} Arrows: nudge {"·"} Ctrl/Cmd+Z: undo
          </span>
        </div>
        <div className={s.tabPanel} role="tabpanel" id="ws-tabpanel" aria-labelledby={`ws-tab-${tab}`}>
          {tab === "mixer" ? (
            <Mixer tracks={project.tracks} masterGain={project.masterGain} transport={transport} playing={playing} dispatch={dispatch} />
          ) : tab === "piano" ? (
            <PianoRoll
              clip={pianoClip}
              store={store}
              transport={transport}
              tempo={project.tempo}
              timeSignature={project.timeSignature}
              snap={snap}
              division={division}
              selectedNoteId={selection.clipId === pianoClip?.id ? selection.noteId : null}
              onSelectNote={selectNote}
              onNewClip={() => addMidiClip(null)}
            />
          ) : (
            <LiveSteps transport={transport} project={project} store={store} onAddDrums={() => addTrack("drums")} />
          )}
        </div>
      </section>
    </div>
  );
}
