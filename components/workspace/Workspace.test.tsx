import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// The Workspace against a fake server and a fake audio stack: enough to check that the
// screens are wired to the project model, persistence and the handoff links.

const nav = vi.hoisted(() => ({ search: "", push: vi.fn() }));
vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(nav.search),
  useRouter: () => ({ push: nav.push, replace: vi.fn() }),
}));

const server = vi.hoisted(() => {
  const state = { projects: new Map<string, { id: string; name: string; project: Record<string, unknown>; version: number }>(), next: 1 };
  const out = (p: { id: string; name: string; project: Record<string, unknown>; version: number }) => ({
    ...p, created_at: "2026-10-10T10:00:00Z", updated_at: "2026-10-10T10:00:00Z",
  });
  return { state, out };
});

vi.mock("../../lib/api", () => {
  class ApiError extends Error {}
  const api = {
    listAssets: vi.fn(async () => [
      { id: "asset1", kind: "audio", source: "generated", filename: "alapana.mp3", media_type: "audio/mpeg", size_bytes: 10, sha256: "x", duration_s: 3, meta: {}, created_at: "", url: "/a/1", standin: false },
    ]),
    getAsset: vi.fn(async (id: string) => ({ id, kind: "audio", source: "generated", filename: "alapana.mp3", media_type: "audio/mpeg", size_bytes: 10, sha256: "x", duration_s: 3, meta: {}, created_at: "", url: "/a/1", standin: false })),
    assetBytes: vi.fn(async () => new ArrayBuffer(8)),
    uploadAsset: vi.fn(async () => ({ id: "bounce1", kind: "audio", source: "bounce", filename: "b.wav", url: "/a/2" })),
    projects: vi.fn(async () =>
      [...server.state.projects.values()].map((p) => ({ id: p.id, name: p.name, version: 1, updated_at: "2026-10-10T10:00:00Z", track_count: 1, clip_count: 0, duration_s: 0, tempo: 120 })),
    ),
    createProject: vi.fn(async (name: string, project: Record<string, unknown>, version = 1) => {
      const id = `p${server.state.next++}`;
      const saved = { id, name, project: JSON.parse(JSON.stringify(project)), version };
      server.state.projects.set(id, saved);
      return server.out(saved);
    }),
    saveProject: vi.fn(async (id: string, name: string, project: Record<string, unknown>, version = 1) => {
      const saved = { id, name, project: JSON.parse(JSON.stringify(project)), version };
      server.state.projects.set(id, saved);
      return server.out(saved);
    }),
    getProject: vi.fn(async (id: string) => {
      const p = server.state.projects.get(id);
      if (!p) throw new ApiError("No such project.");
      return server.out(p);
    }),
    duplicateProject: vi.fn(),
    conversion: vi.fn(async (id: string) => ({
      id,
      output_format: "western_classical",
      source_asset: { filename: "alapana.mp3" },
      score: {
        options: { tonic_hz: 146.83, tempo_bpm: 90 },
        notes: [
          { cents: 0, onset_s: 0, duration_s: 0.5, velocity: 80 },
          { cents: 350, onset_s: 0.5, duration_s: 0.5, velocity: 80 },
        ],
      },
    })),
  };
  return {
    api,
    ApiError,
    errorText: (err: unknown) => (err instanceof Error ? err.message : "Something went wrong."),
  };
});

import { api } from "../../lib/api";
import { fakeBuffer, FakeContext } from "../../lib/workspace/testing/fakeAudioContext";
import Workspace from "./Workspace";

let contexts: FakeContext[] = [];

beforeEach(() => {
  nav.search = "";
  nav.push.mockClear();
  server.state.projects.clear();
  server.state.next = 1;
  contexts = [];
  vi.mocked(api.createProject).mockClear();
  vi.mocked(api.saveProject).mockClear();
  vi.mocked(api.uploadAsset).mockClear();

  class LiveContext extends FakeContext {
    constructor() {
      super();
      this.state = "running";
      contexts.push(this);
    }
  }
  class OfflineContext extends FakeContext {
    constructor(_channels: number, length: number, sampleRate: number) {
      super(sampleRate, length);
      contexts.push(this);
    }
    decodeAudioData() {
      return Promise.resolve(fakeBuffer(3, 48000));
    }
  }
  vi.stubGlobal("AudioContext", LiveContext);
  vi.stubGlobal("OfflineAudioContext", OfflineContext);
  HTMLCanvasElement.prototype.getContext = (() => null) as never;
  HTMLElement.prototype.setPointerCapture = () => undefined;
  window.history.replaceState(null, "", "/workspace");
});

afterEach(() => {
  vi.unstubAllGlobals();
});

const tab = (name: string) => screen.getByRole("tab", { name });

describe("Workspace", () => {
  it("renders the transport, browser, playlist, inspector and lower panel with labelled controls", async () => {
    render(<Workspace />);
    for (const name of ["Play", "Pause", "Stop", "Undo", "Redo", "Save", "Save as", "Duplicate project", "New project", "Export WAV", "Split at playhead", "Duplicate clip", "Delete clip"]) {
      expect(screen.getByRole("button", { name })).toBeInTheDocument();
    }
    expect(screen.getByRole("button", { name: "Loop off" })).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByRole("button", { name: "Metronome off" })).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByRole("textbox", { name: "BPM" })).toHaveValue("120");
    expect(screen.getByRole("slider", { name: "Master volume" })).toBeInTheDocument();
    expect(screen.getByText("Saved")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Library" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Previous sessions" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "From my computer" })).toBeInTheDocument();
    expect(await screen.findByText("alapana.mp3")).toBeInTheDocument();
    expect(tab("Mixer")).toHaveAttribute("aria-selected", "true");
  });

  it("creates the project on the first change, then shows it as saved with its id in the URL", async () => {
    const user = userEvent.setup();
    render(<Workspace />);
    await user.click(screen.getByRole("button", { name: "+ Audio track" }));
    await waitFor(() => expect(api.createProject).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.getByText("Saved")).toBeInTheDocument());
    expect(window.location.search).toBe("?project=p1");
    const stored = server.state.projects.get("p1")!.project as { version: number; tracks: Array<{ kind: string }> };
    expect(stored.version).toBe(1);
    expect(stored.tracks.map((t) => t.kind)).toEqual(["audio"]);
    // The mixer has a strip for the track and one for the master.
    expect(screen.getByRole("region", { name: "Audio 1 channel" })).toBeInTheDocument();
    // Mute is on the track header and on the mixer strip.
    expect(screen.getAllByRole("button", { name: "Mute Audio 1", pressed: false })).toHaveLength(2);
  });

  it("undo and redo work from the keyboard", async () => {
    const user = userEvent.setup();
    render(<Workspace />);
    await user.click(screen.getByRole("button", { name: "+ Audio track" }));
    expect(screen.getByRole("group", { name: "Track Audio 1" })).toBeInTheDocument();
    await user.keyboard("{Control>}z{/Control}");
    expect(screen.queryByRole("group", { name: "Track Audio 1" })).not.toBeInTheDocument();
    await user.keyboard("{Control>}{Shift>}z{/Shift}{/Control}");
    expect(screen.getByRole("group", { name: "Track Audio 1" })).toBeInTheDocument();
  });

  it("adds a MIDI track with an empty clip and opens the piano roll; a drum track opens the step sequencer", async () => {
    const user = userEvent.setup();
    render(<Workspace />);
    await user.click(screen.getByRole("button", { name: "+ MIDI track" }));
    expect(tab("Piano roll")).toHaveAttribute("aria-selected", "true");
    const clip = screen.getByRole("button", { name: /^MIDI clip Pattern, starts at bar 1, 8\.0 seconds, on track MIDI 1$/ });
    expect(clip).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("group", { name: "Notes of Pattern" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "+ Drum track" }));
    expect(tab("Step sequencer")).toHaveAttribute("aria-selected", "true");
    await user.click(screen.getByRole("button", { name: "Kick step 1" }));
    expect(screen.getByRole("button", { name: "Kick step 1" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "+ Drum track" })).toBeDisabled();
  });

  it("adds a Library asset to the timeline, then moves, splits and deletes the clip", async () => {
    const user = userEvent.setup();
    render(<Workspace />);
    await user.click(await screen.findByRole("button", { name: "Add alapana.mp3 to the timeline" }));
    const clip = await screen.findByRole("button", { name: /^Audio clip alapana, starts at bar 1, 3\.0 seconds/ });
    expect(clip).toHaveAttribute("aria-pressed", "true");

    // Inspector shows the clip; arrow keys nudge it by one grid step (1/4 beat at 120 BPM = 0.125 s).
    expect(screen.getByRole("textbox", { name: "Start (seconds)" })).toHaveValue("0");
    clip.focus();
    await user.keyboard("{ArrowRight}{ArrowRight}");
    expect(screen.getByRole("textbox", { name: "Start (seconds)" })).toHaveValue("0.25");

    // Drag by 120 px at 60 px per second: two seconds later, snapped to the grid.
    fireEvent.pointerDown(clip, { button: 0, pointerId: 1, clientX: 100, clientY: 10 });
    fireEvent.pointerMove(clip, { pointerId: 1, clientX: 160, clientY: 10 });
    fireEvent.pointerMove(clip, { pointerId: 1, clientX: 220, clientY: 10 });
    fireEvent.pointerUp(clip, { pointerId: 1, clientX: 220, clientY: 10 });
    expect(screen.getByRole("textbox", { name: "Start (seconds)" })).toHaveValue("2.25");
    // The whole drag is one undo step.
    await user.keyboard("{Control>}z{/Control}");
    expect(screen.getByRole("textbox", { name: "Start (seconds)" })).toHaveValue("0.25");
    await user.keyboard("{Control>}z{/Control}");
    expect(screen.getByRole("textbox", { name: "Start (seconds)" })).toHaveValue("0");

    // Set the length from the inspector, then split at the playhead with S.
    const ruler = screen.getByRole("slider", { name: /^Playhead/ });
    ruler.focus();
    await user.keyboard("{Shift>}{ArrowRight}{/Shift}"); // one bar = 2 s
    clip.focus();
    await user.keyboard("s");
    const parts = screen.getAllByRole("button", { name: /^Audio clip alapana/ });
    expect(parts).toHaveLength(2);
    expect(parts[0]).toHaveAccessibleName(/starts at bar 1, 2\.0 seconds/);
    expect(parts[1]).toHaveAccessibleName(/starts at bar 2, 1\.0 seconds/);

    parts[1].focus();
    await user.keyboard("{Delete}");
    expect(screen.getAllByRole("button", { name: /^Audio clip alapana/ })).toHaveLength(1);
  });

  it("plays on Space: the audio context is created then, and the clip is scheduled", async () => {
    const user = userEvent.setup();
    render(<Workspace />);
    await user.click(await screen.findByRole("button", { name: "Add alapana.mp3 to the timeline" }));
    const clip = await screen.findByRole("button", { name: /^Audio clip alapana/ });
    const live = () => contexts.filter((c) => c.state === "running");
    expect(live()).toHaveLength(0);
    clip.focus();
    await user.keyboard(" ");
    expect(live()).toHaveLength(1);
    expect(screen.getByRole("button", { name: "Play" })).toHaveAttribute("aria-pressed", "true");
    const sources = live()[0].sources.filter((s) => s.starts.length > 0);
    expect(sources).toHaveLength(1);
    expect(sources[0].starts[0]).toMatchObject({ offset: 0, duration: 3 });
    await user.keyboard(" ");
    expect(screen.getByRole("button", { name: "Play" })).toHaveAttribute("aria-pressed", "false");
    expect(sources[0].stops).toHaveLength(1);
  });

  it("imports a conversion from the handoff link as a MIDI clip that keeps its microtones", async () => {
    nav.search = "import_conversion=conv9";
    render(<Workspace />);
    const clip = await screen.findByRole("button", { name: /^MIDI clip alapana \(western_classical\), starts at bar 1, 1\.0 seconds, on track Notation$/ });
    expect(clip).toBeInTheDocument();
    expect(api.conversion).toHaveBeenCalledWith("conv9");
    expect(tab("Piano roll")).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("textbox", { name: "BPM" })).toHaveValue("90");
    // 350 cents above D3: F3 plus about 50 cents, with a visible marker.
    const detuned = screen.getByRole("button", { name: /^Note F3 \+50 cents, starts at 0\.50 seconds/ });
    expect(within(detuned).getByText(/\+50c/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^Note D3, starts at 0\.00 seconds/ })).toBeInTheDocument();
    expect(screen.getByText(/keep their detune/)).toBeInTheDocument();

    // Selecting the note shows pitch and detune in the inspector; detune is editable.
    const user = userEvent.setup();
    await user.click(detuned);
    const detune = screen.getByRole("textbox", { name: "Detune (cents)" });
    expect(detune).toHaveValue("50");
    expect(screen.getByRole("textbox", { name: "Pitch (MIDI number)" })).toHaveValue("53");
    await user.clear(detune);
    await user.type(detune, "-20{Enter}");
    expect(screen.getByRole("button", { name: /^Note F3 -20 cents/ })).toBeInTheDocument();
  });

  it("draws a note in the piano roll with the pointer and removes it with Delete", async () => {
    const user = userEvent.setup();
    render(<Workspace />);
    await user.click(screen.getByRole("button", { name: "+ MIDI track" }));
    const grid = screen.getByRole("group", { name: "Notes of Pattern" });
    // 140 px per second; rows are 16 px from MIDI 96 down. y = 40 is row 2: A#6 (94).
    fireEvent.pointerDown(grid, { button: 0, pointerId: 1, clientX: 70, clientY: 40 });
    fireEvent.pointerMove(grid, { pointerId: 1, clientX: 140, clientY: 40 });
    fireEvent.pointerUp(grid, { pointerId: 1, clientX: 140, clientY: 40 });
    const note = screen.getByRole("button", { name: "Note A#6, starts at 0.50 seconds, 0.50 seconds long" });
    expect(note).toHaveAttribute("aria-pressed", "true");
    note.focus();
    await user.keyboard("{ArrowDown}{ArrowRight}");
    expect(screen.getByRole("button", { name: "Note A6, starts at 0.63 seconds, 0.50 seconds long" })).toBeInTheDocument();
    await user.keyboard("{Delete}");
    expect(screen.queryByRole("button", { name: /^Note / })).not.toBeInTheDocument();
    // The clip itself is still there.
    expect(screen.getByRole("button", { name: /^MIDI clip Pattern/ })).toBeInTheDocument();
  });

  it("opens a saved project from the URL and restores it", async () => {
    server.state.projects.set("p7", {
      id: "p7",
      name: "Raga sketch",
      version: 1,
      project: {
        version: 1, name: "Raga sketch", tempo: 84, timeSignature: [3, 4],
        tracks: [{ id: "t1", name: "Tanpura", kind: "audio", gain: 0.5, pan: 0, mute: true, solo: false }],
        clips: [{ id: "c1", trackId: "t1", kind: "audio", assetId: "asset1", name: "Drone", startSec: 0, offsetSec: 0, lengthSec: 3, gain: 1, fadeInSec: 0, fadeOutSec: 0 }],
      },
    });
    nav.search = "project=p7";
    render(<Workspace />);
    expect(await screen.findByRole("button", { name: /^Audio clip Drone, starts at bar 1, 3\.0 seconds, on track Tanpura$/ })).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "BPM" })).toHaveValue("84");
    expect(screen.getByRole("textbox", { name: "Project name" })).toHaveValue("Raga sketch");
    expect(screen.getAllByRole("button", { name: "Mute Tanpura", pressed: true })).toHaveLength(2);
    expect(screen.getByText("Saved")).toBeInTheDocument();
    expect(api.createProject).not.toHaveBeenCalled();
    // The current session is marked in the list.
    expect(await screen.findByText("Open now")).toBeInTheDocument();
  });

  it("bounces the selected clip, uploads it and hands off to the Notation Studio", async () => {
    const user = userEvent.setup();
    render(<Workspace />);
    await user.click(await screen.findByRole("button", { name: "Add alapana.mp3 to the timeline" }));
    await screen.findByRole("button", { name: /^Audio clip alapana/ });
    await user.click(screen.getByRole("button", { name: "Transcribe selection" }));
    await waitFor(() => expect(nav.push).toHaveBeenCalledWith("/studio?asset=bounce1"));
    const [blob, opts] = vi.mocked(api.uploadAsset).mock.calls[0];
    expect(blob.type).toBe("audio/wav");
    expect(blob.size).toBe(44 + 3 * 22050 * 4); // 3 s of 16-bit stereo at the smaller rate used for analysis
    expect(opts).toMatchObject({ source: "bounce", filename: "alapana bounce.wav" });
    // The render used the same scheduling as playback: one source for the clip.
    const offline = contexts.find((c) => c.rendered)!;
    expect(offline.sources.filter((s) => s.starts.length > 0)).toHaveLength(1);
  });

  it("refuses a file that is not audio with a clear message", async () => {
    render(<Workspace />);
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    const file = new File(["hello"], "notes.pdf", { type: "application/pdf" });
    await act(async () => {
      fireEvent.change(input, { target: { files: [file] } });
    });
    expect(await screen.findByText(/\.pdf files are not supported\. Use wav, mp3, m4a, flac or ogg\./)).toBeInTheDocument();
  });
});
