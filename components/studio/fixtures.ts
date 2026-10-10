// Contract-shaped sample data for component tests. The ids are made up on purpose:
// nothing in the UI may depend on a particular format id (spec 4.5).
import type {
  ConversionOut, FormatInfo, FormatsResponse, FormatScore, IRSummary, LossItem, LossReport, Notice, ScoreNote,
} from "@/lib/api";

export function makeFormat(over: Partial<FormatInfo> & Pick<FormatInfo, "id" | "display_name" | "family" | "family_label">): FormatInfo {
  return {
    description: "",
    aliases: [],
    pitch_system: "12tet",
    tonic_model: "movable",
    rhythm_model: "meter",
    tonic_word: "tonic",
    review_status: "unreviewed",
    reviewer_note: "",
    caveats: [],
    renderers: ["svg", "names", "describe"],
    primary_renderer: "svg",
    input_parsers: ["note_names"],
    monophonic_only: true,
    modes: [],
    rhythm_templates: [],
    default_template_id: null,
    ...over,
  };
}

export const FIXTURE_FORMATS: FormatInfo[] = [
  makeFormat({
    id: "fmt_staff",
    display_name: "Staff Notation",
    family: "fam_west",
    family_label: "Western",
    caveats: ["Pitches between the keys are rounded."],
    reviewer_note: "Checked against two textbooks only.",
    review_status: "self_checked",
  }),
  makeFormat({ id: "fmt_pop", display_name: "Pop Lead Sheet", family: "fam_west", family_label: "Western" }),
  makeFormat({
    id: "fmt_swara",
    display_name: "Swara Notation",
    family: "fam_south",
    family_label: "South Asian",
    tonic_word: "Sa",
    primary_renderer: "sargam",
    renderers: ["sargam", "describe"],
    caveats: ["The raga table is a draft.", "Gamaka marks are approximate."],
    modes: [
      { id: "m1", label: "Mode One", kind: "raga", pitches_cents: [0, 200, 400, 700, 900], degree_names: ["S", "R", "G", "P", "D"], source: "draft", confidence: "low", caveats: [], lookalikes: [] },
    ],
    rhythm_templates: [
      { id: "t8", label: "Eight-beat cycle", cycle_beats: 8, subdivision: 1, structure: ["4", "2", "2"], accent_pattern: [1, 0, 0, 0, 0.5, 0, 0.5, 0], source: "draft", confidence: "medium", caveats: [] },
    ],
    default_template_id: "t8",
  }),
];

function families(formats: FormatInfo[]): FormatsResponse["families"] {
  const out: FormatsResponse["families"] = [];
  for (const f of formats) {
    const fam = out.find((x) => x.id === f.family);
    if (fam) fam.format_ids.push(f.id);
    else out.push({ id: f.family, label: f.family_label, format_ids: [f.id] });
  }
  return out;
}

export function makeRegistryFixture(extra: FormatInfo[] = []): FormatsResponse {
  const formats = [...FIXTURE_FORMATS, ...extra];
  return {
    formats,
    families: families(formats),
    renderers: [
      { kind: "svg", label: "Staff", media_type: "image/svg+xml", ext: "svg" },
      { kind: "sargam", label: "Sargam text", media_type: "text/plain", ext: "txt" },
      { kind: "names", label: "Note names", media_type: "text/plain", ext: "txt" },
      { kind: "describe", label: "Plain-language description", media_type: "text/plain", ext: "txt" },
    ],
    limits: { max_upload_mb: 25, max_audio_seconds: 300, max_generate_seconds: 120, daily_generations: 10 },
    offline: { llm: true, music: true, tts: true, reason: "No keys set" },
    sargam_help: "S R G M P D N, lower case for the lower variety, ' for the upper octave.",
    detect_limits: ["Two neighbouring styles can look the same in a short clip."],
    monophonic_note: "Orin writes one melody line at a time.",
  };
}

export function makeNote(over: Partial<ScoreNote> & Pick<ScoreNote, "id">): ScoreNote {
  return {
    source_event_id: null,
    cents: 0,
    source_cents: 0,
    cents_delta: 0,
    name: "S",
    degree: 1,
    octave: 0,
    in_mode: true,
    start_pulse: 0,
    duration_pulses: 1,
    onset_s: 0,
    duration_s: 0.5,
    source_onset_s: 0,
    velocity: 80,
    ornaments: [],
    confidence: 1,
    ...over,
  };
}

export function makeLossItem(over: Partial<LossItem> & Pick<LossItem, "code" | "human_text">): LossItem {
  return {
    severity: "minor",
    event_id: null,
    event_ids: [],
    time_s: null,
    pulse: null,
    cycle_index: null,
    detail: {},
    explain_key: over.code.toLowerCase(),
    penalty: 0,
    ...over,
  };
}

export function makeNotice(over: Partial<Notice> & Pick<Notice, "code" | "human_text">): Notice {
  return { event_id: null, time_s: null, detail: {}, explain_key: over.code.toLowerCase(), ...over };
}

export const FIXTURE_NOTES: ScoreNote[] = [
  makeNote({ id: "n1", source_event_id: "e1", name: "S", cents: 0, onset_s: 0, source_onset_s: 0 }),
  makeNote({ id: "n2", source_event_id: "e2", name: "R", cents: 200, source_cents: 231, cents_delta: -31, onset_s: 0.5, source_onset_s: 0.52, confidence: 0.3 }),
  makeNote({ id: "n3", source_event_id: "e3", name: "G", cents: 400, onset_s: 1, source_onset_s: 1.01 }),
];

export const FIXTURE_LOSS: LossReport = {
  items: [
    makeLossItem({ code: "MICROTONE_ROUNDED", severity: "major", event_id: "e2", time_s: 0.52, human_text: "This note was rounded by 31 cents.", penalty: 0.04 }),
    makeLossItem({ code: "ORNAMENT_DROPPED", severity: "minor", event_id: "e3", time_s: 1.01, human_text: "The pitch movement on this note is shown as a plain note.", penalty: 0.02 }),
    makeLossItem({ code: "MICROTONE_ROUNDED", severity: "minor", event_id: "e3", time_s: 1.01, human_text: "This note was rounded by 12 cents.", penalty: 0 }),
  ],
  notices: [makeNotice({ code: "TONIC_ASSUMED", human_text: "No tonic could be found with confidence, so D was assumed." })],
  summary: {
    counts_by_code: { MICROTONE_ROUNDED: 2, ORNAMENT_DROPPED: 1 },
    max_cents_delta: 31,
    fidelity_score: 0.82,
    pitch_score: 0.9,
    ornament_score: 0.7,
    timing_score: 0.95,
  },
  source_format: "fmt_swara",
  target_format: "fmt_staff",
};

export const FIXTURE_SCORE: FormatScore = {
  format_id: "fmt_staff",
  source_format: "fmt_swara",
  options: {
    tonic_hz: 146.83,
    a4_hz: 440,
    mode_id: null,
    mode_label: null,
    template_id: null,
    tempo_bpm: 80,
    pulse_s: 0.5,
    subdivision: 1,
    free_time: false,
    loss_threshold_cents: 5,
    written_tonic: { step: "D", alter_cents: 0 },
    written_tonic_octave: 4,
    transposition_cents: 0,
    explicit_style: true,
  },
  notes: FIXTURE_NOTES,
  cycle: null,
  phase_pulses: 0,
  warnings: [],
};

export const FIXTURE_IR: IRSummary = {
  source: {
    kind: "audio", filename: "clip.wav", format_hint: null, detected_format: "fmt_swara", detected_format_confidence: 0.62,
    detected_candidates: [], duration_s: 1.5, sample_rate: 44100, channels: 1, loudness_dbfs: -18, sha256: null, notes_syntax: null,
  },
  tonic: {
    hz: 146.83, cents_ref_hz: 146.83, confidence: 0.55, method: "histogram", tuning_offset_cents: 0, nearest_note: "D3",
    candidates: [
      { hz: 146.83, confidence: 0.55, method: "histogram" },
      { hz: 220, confidence: 0.3, method: "histogram" },
      { hz: 196, confidence: 0.15, method: "histogram" },
    ],
  },
  mode: {
    label: "Mode One", label_system: "raga", mode_id: "m1", allowed_pitches_cents: [0, 200, 400, 700, 900], confidence: 0.5, user_selected: false,
    candidates: [
      { id: "m1", label: "Mode One", label_system: "raga", score: 0.5, lookalikes: ["fmt_staff:major_pentatonic", "fmt_swara:m2~", "fmt_swara:m3~"] },
      { id: "m4", label: "Mode Four", label_system: "raga", score: 0.3, lookalikes: [] },
    ],
  },
  tempo: { bpm: 80, confidence: 0.7, free_time: false },
  cycle: { length_pulses: 8, subdivision: 1, label: "Eight-beat cycle", confidence: 0.4, template_id: "t8", pulse_s: 0.75, phase_pulses: 0, swing_ratio: null },
  analysis: { style: "fmt_swara", profile_id: "default", sensitivity: 1, trackers: ["pyin"], user_overrides: {} },
  note_count: 3,
  duration_s: 1.5,
  events: [
    { id: "e1", onset_s: 0, offset_s: 0.5, pitch_cents: 0, ornament_kind: null, ornament_amplitude_cents: 0, confidence: 0.9 },
    { id: "e2", onset_s: 0.52, offset_s: 1, pitch_cents: 231, ornament_kind: "oscillation", ornament_amplitude_cents: 60, confidence: 0.3 },
    { id: "e3", onset_s: 1.01, offset_s: 1.5, pitch_cents: 400, ornament_kind: null, ornament_amplitude_cents: 0, confidence: 0.8 },
  ],
  contour: { t_s: [0, 0.25, 0.5, 0.75, 1, 1.25], cents: [0, 5, null, 231, 400, 398] },
  warnings: [{ code: "SHORT_CLIP", message: "The clip is short, so the mode match is weak.", stage: "mode" }],
  low_confidence_event_ids: ["e2"],
  processing_log: [
    { stage: "pitch", version: "1", params: {}, duration_ms: 420, cache_hit: false, note: "pyin" },
    { stage: "tonic", version: "1", params: {}, duration_ms: 12, cache_hit: true, note: null },
  ],
};

export function makeConversion(over: Partial<ConversionOut> = {}): ConversionOut {
  return {
    id: "conv1",
    status: "done",
    created_at: "2026-10-10T10:00:00Z",
    input_format: "auto",
    source_format: "fmt_swara",
    detected: [
      { format_id: "fmt_swara", score: 0.62 },
      { format_id: "fmt_staff", score: 0.27 },
      { format_id: "fmt_pop", score: 0.11 },
    ],
    needs_style_choice: false,
    detect_message: null,
    output_format: "fmt_staff",
    same_format: false,
    source_asset: null,
    notes_text: null,
    ir: FIXTURE_IR,
    score: FIXTURE_SCORE,
    loss: FIXTURE_LOSS,
    outputs: [
      { kind: "names", label: "Note names", media_type: "text/plain", ext: "txt", text: "D E F#", tokens: [{ note_id: "n1", start: 0, end: 1 }, { note_id: "n2", start: 2, end: 3 }, { note_id: "n3", start: 4, end: 6 }], pages: [], note_ids: [], asset_id: null, url: null, warnings: [] },
      { kind: "describe", label: "Plain-language description", media_type: "text/plain", ext: "txt", text: "Three notes rising from D.", tokens: [], pages: [], note_ids: [], asset_id: null, url: null, warnings: [] },
    ],
    missing: [],
    suggestions: [],
    how_scored: "Fidelity runs from 0 to 1 and is built from three parts.",
    params: {},
    parse_problems: [],
    error: null,
    ...over,
  };
}
