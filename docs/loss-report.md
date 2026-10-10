# The loss report

Whenever music from one tradition is written in another, some of it cannot be carried over. Orin lists exactly what, note by note. The gap is the teaching material, not a bug. Types are in `orin/loss/`; the report is produced by the conversion engine in `orin/formats/engine.py`. Spec section 9.3.

## Two channels

| Channel | Holds | Costs fidelity? |
|---|---|---|
| `items` | what the target notation could not carry | by its `penalty`. Pitch, ornament and timing items cost something; tuning-system, mode, cycle and dynamics items are listed but cost nothing |
| `notices` | assumptions and analysis doubt | no |

They are kept apart so the report never blames the target tradition for Orin's own uncertainty. "This note is a quarter tone, and a staff cannot write it" is loss. "Orin was not sure where this wavering note sits" is a notice.

## Loss items

| Code | Meaning | Typical severity |
|---|---|---|
| `MICROTONE_ROUNDED` | a pitch between the target's pitches was moved to the nearest one | major at 40 cents or more, else minor |
| `TEMPERAMENT_RETUNED` | the tuning system changed (for example just intonation to equal temperament). One entry for the whole piece | info |
| `ORNAMENT_DROPPED` | pitch movement with no symbol in the target | by width: info, minor, major |
| `ORNAMENT_APPROXIMATED` | pitch movement reduced to a mark that says it happens but not how | minor |
| `ORNAMENT_NOT_SUPPLIED` | the target idiom expects ornaments the source does not have; Orin does not invent them | info |
| `TIMING_SHIFT` | a note start was moved to fit the target grid | info or minor |
| `FREE_TIME_MEASURED` | unmetered music was forced into bars | major |
| `CYCLE_REINTERPRETED` | tala, iqa' or meter matched by length only, or assumed | minor |
| `MODE_APPROXIMATED` | a target mode has the same pitches but is not the same thing | minor |
| `MODE_UNREPRESENTABLE` | no target mode has these pitches | minor or major |
| `OCTAVE_FOLDED` | a note was moved an octave to fit | minor |
| `DYNAMICS_DROPPED` | loudness is not written in this output | info |

## Notices

`OUT_OF_MODE_NOTE`, `PITCH_ANCHOR_UNCERTAIN`, `TRANSPOSED_FOR_NOTATION`, `DRONE_NOT_NOTATED`, `TONIC_ASSUMED`, `TUNING_OFFSET`.

## Rules the engine follows

- **Nothing is rounded silently.** Every pitch change above 5 cents is logged (the threshold is configurable).
- **A change of tuning system is not a wrong note.** Small changes are grouped into one `TEMPERAMENT_RETUNED` entry listing the notes. `MICROTONE_ROUNDED` is raised per note only when the performed pitch is outside the written note's tolerance and more than 25 cents from it.
- **Severity follows how far between two writable pitches the note was.** A note at least 80% of the way to the midpoint between its two neighbours is major (40 of 50 cents on a piano; a neutral second in Carnatic notation, which sits between R1 and R2), otherwise minor. `ambiguous` in the item's detail means it was almost exactly halfway.
- **An iqa' is never named from its length.** Maqsum, Baladi and Wahda are all four beats and differ in drum strokes, so a melody converted to Arabic notation gets a plain "4-beat cycle" unless you choose the iqa'.
- **A mode is a soft constraint.** A note inside a mode degree's tolerance is written as that degree. A note outside it is written as played, on the nearest pitch the notation has, with an `OUT_OF_MODE_NOTE` notice. It is never pulled into the mode. If the mode guess is wrong, the notes are still right.
- **No mode is applied across traditions unless you ask.** Modes of the target with the same pitches are offered as suggestions.
- **Free time stays free.** Every format can be written without bar lines, so unmetered music loses nothing in timing unless you ask for bars.
- **Tradition names are hedged.** An oscillation is "kampita-like" only in Carnatic output, and only when Carnatic was chosen, not guessed.

## The fidelity score

One number from 0 to 1, built so every item shows what it cost.

```
err_i        = for a note written as a mode degree: cents outside that degree's tolerance (0 inside it)
               for a note rounded to a plain grid pitch: 50 * distance / (half the gap between
               the two writable pitches around it), so the exact midpoint always counts as 50
pitch_loss_i = 0.5 if octave-folded else clip((err_i - 25) / 25, 0, 1)
orn_loss_i   = clip(half_extent_i / 100, 0.1, 1) * coverage_i * k_i     (k: 1 dropped, 0.5 approximated, 0 kept)
time_loss_i  = clip(|onset_shift_i| / (0.5 * grid_s), 0, 1)
S_x          = 1 - sum(d_i * loss_x_i) / sum(d_i)                       (d_i = note duration)
F            = 0.45 * S_pitch + 0.35 * S_ornament + 0.20 * S_timing
```

- Forcing free-time music into bars caps `S_timing` at 0.5.
- Each item's `penalty` is its share of the score, so `F = 1 - sum(penalties)`.
- The weights are a design choice, not a measurement. The same explanation is shown in the UI under "How is this scored?" (`orin.loss.HOW_SCORED`).
- Analysis confidence is a separate number. A low-confidence transcription can have perfect fidelity; the score only measures what the conversion lost.

## Explanations

Each item carries an `explain_key`: its code in lower case plus an optional qualifier, such as `ornament_dropped.oscillation` or `microtone_rounded.12tet`. Gemini explains the selected items from the report's own numbers and is told not to invent names or values. With no key, template text for the same key is used. Explanations are cached by key, learner level and language.

## Export

`LossReport.model_dump_json()` for JSON and `LossReport.to_text()` for a readable version.
