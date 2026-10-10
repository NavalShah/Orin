"""Small IR builders for tests and the eval runner. Pure: no audio, no files.

``make_ir`` builds an IR from a list of cents. One ready-made phrase per format follows;
``phrase_for(format_id)`` returns the one for a format. Depends only on ``orin.ir`` and
``orin.formats``.

The phrases are test material, not repertoire. Their pitches are the nominal values of
the adapter tables, which are unreviewed drafts.
"""
from __future__ import annotations

import math
from typing import Callable, Mapping, Optional, Sequence, Union

from ..formats import registry
from ..ir.models import IR, Cycle, Mode, NoteEvent, Ornament, Source, Tempo, Tonic

D3_HZ = 146.83
C4_HZ = 261.63
_NAMES = ("C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B")

OrnamentSpec = Union[Ornament, Sequence[Ornament]]


def oscillation(amplitude_cents: float, rate_hz: float = 5.0, coverage: float = 0.8) -> Ornament:
    """``amplitude_cents`` is half the peak-to-peak width."""
    return Ornament(kind="oscillation", amplitude_cents=amplitude_cents, rate_hz=rate_hz, shape="sine",
                    position="whole", cycles=rate_hz * 0.4, coverage=coverage, confidence=0.9)


def vibrato(amplitude_cents: float = 25.0, rate_hz: float = 6.0, coverage: float = 0.7) -> Ornament:
    return Ornament(kind="vibrato", amplitude_cents=amplitude_cents, rate_hz=rate_hz, shape="sine",
                    position="whole", cycles=rate_hz * 0.4, coverage=coverage, confidence=0.9)


def glide(extent_cents: float, shape: str = "up", position: str = "start", coverage: float = 0.4) -> Ornament:
    """``extent_cents`` is the whole distance travelled."""
    return Ornament(kind="glide", amplitude_cents=extent_cents, shape=shape, position=position,  # type: ignore[arg-type]
                    coverage=coverage, confidence=0.9)


def grace(extent_cents: float = 100.0, shape: str = "touch_above", coverage: float = 0.15) -> Ornament:
    return Ornament(kind="grace", amplitude_cents=extent_cents, shape=shape, position="start",
                    coverage=coverage, confidence=0.9)


def _nearest_note(hz: float) -> tuple[str, float]:
    """(name like "D3", offset in cents from that 12-TET pitch at A4 = 440)."""
    cents = 1200.0 * math.log2(hz / 440.0) + 900.0  # above C4
    index = int(math.floor(cents / 100.0 + 0.5))
    return f"{_NAMES[index % 12]}{4 + index // 12}", cents - 100.0 * index


def _mode(mode_id: Optional[str], source_format: Optional[str]) -> Mode:
    if not mode_id:
        return Mode()
    if source_format and registry.has(source_format):
        entry = registry.get(source_format).META.mode(mode_id)
        if entry is not None:
            return Mode(label=entry.label, label_system=registry.get(source_format).META.id, mode_id=entry.id,
                        allowed_pitches_cents=entry.pitch_set(), confidence=1.0, user_selected=True)
    return Mode(mode_id=mode_id, label=mode_id, label_system=source_format, confidence=1.0, user_selected=True)


def make_ir(
    cents: Sequence[float],
    *,
    durations: Union[None, float, Sequence[float]] = None,
    tonic_hz: float = D3_HZ,
    source_format: Optional[str] = None,
    mode_id: Optional[str] = None,
    ornaments: Optional[Mapping[int, OrnamentSpec]] = None,
    free_time: bool = False,
    pulse_s: float = 0.25,
    subdivision: int = 2,
    cycle_pulses: Optional[int] = None,
    template_id: Optional[str] = None,
    onsets_s: Optional[Sequence[float]] = None,
    velocities: Optional[Sequence[Optional[int]]] = None,
    loudness_db: Optional[Sequence[Optional[float]]] = None,
    tonic_method: str = "declared",
    tonic_confidence: float = 1.0,
    kind: str = "notes",
) -> IR:
    """Build an IR from pitches in cents above the tonic.

    durations     note lengths in pulses: one number for all, or one per note. Default: one beat.
    ornaments     {note index: Ornament or [dominant, extra, ...]}
    free_time     no tempo and no pulses, as for an alapana or a taqsim
    pulse_s       seconds per pulse; ``subdivision`` pulses make one beat
    cycle_pulses  cycle length in pulses (None = unknown)
    onsets_s      explicit note starts in seconds. The notes then carry no pulse numbers,
                  like notes found in audio, so the engine has to snap them.
    """
    n = len(cents)
    if durations is None:
        lengths = [float(subdivision)] * n
    elif isinstance(durations, (int, float)):
        lengths = [float(durations)] * n
    else:
        lengths = [float(d) for d in durations]
    typed = not free_time and onsets_s is None

    starts_pulse: list[float] = []
    total = 0.0
    for length in lengths:
        starts_pulse.append(total)
        total += length
    starts_s = [float(t) for t in onsets_s] if onsets_s is not None else [p * pulse_s for p in starts_pulse]

    events = []
    for i in range(n):
        end = starts_s[i] + lengths[i] * pulse_s
        if i + 1 < n:
            end = min(end, starts_s[i + 1])
        spec = (ornaments or {}).get(i)
        orns = [spec] if isinstance(spec, Ornament) else list(spec or [])
        dominant = orns[0] if orns else Ornament()
        swing = dominant.amplitude_cents if dominant.kind in ("oscillation", "vibrato") else 0.0
        events.append(NoteEvent(
            id=f"e{i + 1}", onset_s=starts_s[i], offset_s=end,
            onset_pulse=starts_pulse[i] if typed else None,
            duration_pulses=lengths[i] if typed else None,
            pitch_cents=float(cents[i]), pitch_cents_median=float(cents[i]),
            pitch_cents_min=float(cents[i]) - swing, pitch_cents_max=float(cents[i]) + swing,
            velocity=velocities[i] if velocities else None,
            loudness_db=loudness_db[i] if loudness_db else None,
            ornament=dominant, extra_ornaments=tuple(orns[1:]),
            confidence=1.0 if kind == "notes" else 0.9))

    nearest, offset = _nearest_note(tonic_hz)
    return IR(
        source=Source(kind=kind, format_hint=source_format,  # type: ignore[arg-type]
                      duration_s=events[-1].offset_s if events else 0.0,
                      notes_syntax="note_names" if kind == "notes" else None),
        tonic=Tonic(hz=tonic_hz, cents_ref_hz=tonic_hz, confidence=tonic_confidence, method=tonic_method,
                    tuning_offset_cents=round(offset, 2), nearest_note=nearest),
        mode=_mode(mode_id, source_format),
        tempo=Tempo(bpm=None if free_time else 60.0 / (pulse_s * subdivision),
                    confidence=0.0 if free_time else 1.0, free_time=free_time),
        cycle=Cycle() if free_time else Cycle(
            length_pulses=cycle_pulses, subdivision=subdivision, template_id=template_id,
            pulse_s=pulse_s, confidence=1.0 if cycle_pulses else 0.0),
        events=tuple(events),
    )


# ---------------------------------------------------------------------------
# One phrase per format
# ---------------------------------------------------------------------------

def western_classical_phrase() -> IR:
    """A typed C major scale in 4/4, quarter notes at 120 bpm."""
    return make_ir([0, 200, 400, 500, 700, 900, 1100, 1200], durations=4, tonic_hz=C4_HZ,
                   source_format="western_classical", mode_id="major", pulse_s=0.125, subdivision=4,
                   cycle_pulses=16, template_id="4_4")


def western_pop_phrase() -> IR:
    """A syncopated hook on a sixteenth-note grid: notes start off the beat."""
    return make_ir([0, 400, 700, 900, 700, 400, 200, 0], durations=[3, 3, 2, 3, 3, 2, 6, 10], tonic_hz=C4_HZ,
                   source_format="western_pop", mode_id="major", pulse_s=0.125, subdivision=4,
                   cycle_pulses=16, template_id="4_4")


def rock_phrase() -> IR:
    """A minor-pentatonic riff with a whole-tone bend up into the fifth and a shake on the last note."""
    return make_ir([0, 300, 500, 700, 1000, 700, 500, 300, 0], durations=[2, 2, 2, 4, 2, 2, 2, 4, 12],
                   tonic_hz=164.81, source_format="rock", mode_id="minor_pentatonic", pulse_s=0.125,
                   subdivision=4, cycle_pulses=16, template_id="4_4",
                   ornaments={3: glide(200.0, "up"), 8: vibrato(30.0)})


def carnatic_phrase() -> IR:
    """Mohanam on nominal just-intonation swaras in Adi tala, with an oscillation on Ga and a slide down to Pa."""
    return make_ir([0, 204, 386, 702, 884, 1200, 884, 702, 386, 204, 0],
                   durations=[4, 4, 8, 4, 4, 8, 4, 8, 8, 4, 8], tonic_hz=D3_HZ,
                   source_format="carnatic", mode_id="mohanam", pulse_s=0.125, subdivision=4,
                   cycle_pulses=32, template_id="adi",
                   ornaments={2: oscillation(80.0, rate_hz=5.5), 7: glide(182.0, "down"),
                              8: [oscillation(60.0, rate_hz=5.0), glide(120.0, "up")]})


def arabic_phrase() -> IR:
    """Maqam Rast, up and back, with its two half-flat degrees (350 and 1050 cents)."""
    return make_ir([0, 200, 350, 500, 700, 900, 1050, 1200, 1050, 900, 700, 500, 350, 200, 0],
                   durations=[2, 2, 2, 2, 2, 2, 2, 4, 2, 2, 2, 2, 2, 2, 4], tonic_hz=C4_HZ,
                   source_format="arabic_maqam", mode_id="rast", pulse_s=0.25, subdivision=2,
                   cycle_pulses=8, template_id="maqsum", ornaments={7: oscillation(40.0, rate_hz=6.0)})


def persian_phrase() -> IR:
    """The opening area of Shur in free time, with its koron second (135 cents)."""
    return make_ir([0, 135, 295, 499, 295, 135, 0, 135, 0], durations=[3, 2, 2, 5, 2, 3, 4, 2, 7],
                   tonic_hz=D3_HZ, source_format="persian_dastgah", mode_id="shur", free_time=True,
                   pulse_s=0.2, ornaments={3: oscillation(35.0, rate_hz=6.5), 5: grace(140.0, "touch_above")})


PHRASES: dict[str, Callable[[], IR]] = {
    "western_classical": western_classical_phrase,
    "carnatic": carnatic_phrase,
    "western_pop": western_pop_phrase,
    "rock": rock_phrase,
    "arabic_maqam": arabic_phrase,
    "persian_dastgah": persian_phrase,
}


def phrase_for(format_id: str) -> IR:
    """The ready-made phrase for a format id."""
    return PHRASES[format_id]()
