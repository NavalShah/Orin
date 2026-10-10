# The Intermediate Representation (IR)

The IR is the hub every conversion passes through. It is defined in `orin/ir/models.py` (pydantic) and serialises to JSON. Spec section 6.

## What it does not contain

- **No note names.** Pitch is cents relative to the tonic.
- **No bar lines.** Time is seconds, plus a position in pulses when a pulse was found.
- **No tradition's vocabulary.** Ornaments have neutral names and measured parameters.

A renderer may add names, bars or a tala later. The IR never does.

## Units

| Unit | Meaning |
|---|---|
| cents | `1200 * log2(f / tonic_hz)`, unfolded, so it can be negative or above 1200 |
| pulse | the smallest grid unit. One beat = `cycle.subdivision` pulses; one cycle = `cycle.length_pulses` pulses; `bpm = 60 / (pulse_s * subdivision)` |

## Top level

| Field | Contents |
|---|---|
| `source` | audio or notes, file name, the format the user chose (`format_hint`) or that was detected, duration, sample rate, loudness, content hash |
| `tonic` | Hz, method (`drone`, `histogram`, `key_profile`, `declared`, `user`, `assumed`), confidence, the top-3 candidates, the offset from A = 440 Hz |
| `mode` | closest matches with scores and lookalikes. Never a single certain answer |
| `tempo` | bpm or null, confidence, `free_time` |
| `cycle` | length in pulses, subdivision, template id, confidence, phase, swing ratio |
| `events[]` | the notes |
| `contour[]` | `{t_s, cents or null, confidence}` every 10 ms; null means unvoiced |
| `meta` | warnings, the processing log, and how the analysis was run |

## A note event

```json
{
  "id": "e7",
  "onset_s": 3.21, "offset_s": 4.02,
  "onset_pulse": 12.84, "duration_pulses": 3.24,
  "pitch_cents": 386.0,
  "pitch_cents_median": 384.5, "pitch_cents_min": 301.0, "pitch_cents_max": 468.0,
  "loudness_db": -18.2,
  "voiced_fraction": 0.97,
  "ornament": {"kind": "oscillation", "amplitude_cents": 82.0, "rate_hz": 5.1,
               "shape": "sine", "position": "whole", "coverage": 0.9,
               "contour_ref": [321, 402], "confidence": 0.8},
  "confidence": 0.86
}
```

- `pitch_cents` is the anchor: the duration-weighted **median** of the note's plateau, not the mean.
- An ornament is described by what was measured. `amplitude_cents` is half the peak-to-peak width for `oscillation` and `vibrato`, and the total extent for `glide` and `grace`.
- `contour_ref` points into `contour[]`, so a renderer can look at the real curve.

## Confidence

Every stage writes its own confidence. A note's `confidence` is the smaller of the mean pitch-tracker confidence over the note and the stability of its plateau. Notes read from typed input have confidence 1.0, with `guessed: true` where the parser had to guess. Tonic, mode, tempo and cycle each carry their own number. The UI flags notes under 0.5.

## Invariants

Checked by `orin.ir.validate.validate_ir`, which returns plain sentences:

- events are in time order and do not overlap (one melody line)
- pitch values are finite (NaN and infinity are rejected when the model is built)
- contour timestamps increase
- a JSON round trip is lossless
- free-time music carries no bpm

## Immutability

IR models are frozen and use tuples. An adapter receives an IR and returns a new score; it cannot change what it was given. To change an IR, build a copy with `model_copy(update=...)`.

## The processing log

`meta.processing_log` lists each stage with its parameters, duration and whether it came from cache. The UI shows it under "How was this made?".
