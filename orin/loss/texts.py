"""One plain sentence per loss item or notice, and the keys used to explain them.

The sentences only restate numbers that are in the item's ``detail``. Gemini (or the
offline stand-in) explains *why*, looked up by ``explain_key``.
"""
from __future__ import annotations

from typing import Any

TEMPLATES: dict[str, str] = {
    "MICROTONE_ROUNDED": "This note was performed about {cents_delta_abs:.0f} cents {direction} the nearest pitch {target} can write, so it is shown as {written}; the in-between pitch is lost.",
    "TEMPERAMENT_RETUNED": "{target} uses a different tuning reference, so {count} notes shift by up to {max_cents:.0f} cents; the notes are the same, the tuning system is not.",
    "ORNAMENT_DROPPED": "The pitch movement on this note (about {amplitude_cents:.0f} cents wide{rate_text}) has no symbol in {target} and is shown as a plain note.",
    "ORNAMENT_APPROXIMATED": "The pitch movement on this note is marked as {approximation}, which says it happens but not how wide, fast or shaped it is.",
    "ORNAMENT_NOT_SUPPLIED": "The source has plain notes; a {target} performer would add ornaments that depend on the mode, and Orin does not invent them.",
    "TIMING_SHIFT": "This note's start was moved by {shift_ms:.0f} ms to fit the {grid} grid.",
    "FREE_TIME_MEASURED": "The source has no steady pulse; the bar lines and note lengths here were added by Orin.",
    "CYCLE_REINTERPRETED": "The cycle is shown as {target_cycle}; its length was matched, but the original grouping and accents are not shown.",
    "MODE_APPROXIMATED": "{target_mode} uses the same pitches but is not the same thing; its phrases, emphasis and ornaments are not implied.",
    "MODE_UNREPRESENTABLE": "No {target} mode has these pitches, so the notes are written without a mode name.",
    "OCTAVE_FOLDED": "This note is outside the range {target} can show and was moved an octave.",
    "DYNAMICS_DROPPED": "Loudness changes are not written in this output.",
    # notices
    "OUT_OF_MODE_NOTE": "This note is outside {mode}, so it is written as played instead of being moved into the mode.",
    "PITCH_ANCHOR_UNCERTAIN": "This note's pitch moves too much to pin down; it is written as {written}, the nearest note of {mode}.",
    "TRANSPOSED_FOR_NOTATION": "Written at the conventional pitch for {mode} ({written_tonic}); it sounds {interval_cents:+.0f} cents from there.",
    "DRONE_NOT_NOTATED": "A drone was heard and used to find the tonic; it is not written in the notation.",
    "TONIC_ASSUMED": "No tonic could be found with confidence, so {tonic} was assumed. Set it by hand if this is wrong.",
    "TUNING_OFFSET": "The recording sits {offset_cents:+.0f} cents from standard pitch (A = 440 Hz); this is reported, not corrected.",
}


def human_text(code: str, **detail: Any) -> str:
    """Fill the template for ``code``. Missing fields are left visible, never invented."""
    template = TEMPLATES.get(code)
    if template is None:
        return code.replace("_", " ").capitalize() + "."
    d = dict(detail)
    if "cents_delta" in d:
        d.setdefault("cents_delta_abs", abs(d["cents_delta"]))
        # cents_delta = written - source: a positive delta means the performance was below the written pitch
        d.setdefault("direction", "below" if d["cents_delta"] > 0 else "above")
    rate = d.get("rate_hz")
    d.setdefault("rate_text", f", {rate:.0f} times a second" if rate else "")

    class _Keep(dict):
        def __missing__(self, key: str) -> str:
            return "{" + key + "}"

    try:
        return template.format_map(_Keep(d))
    except (ValueError, TypeError):
        return template


def explain_key(code: str, qualifier: str | None = None) -> str:
    """Stable key for explanations: the code in lower case, plus an optional qualifier.

    Qualifiers in use: the ornament kind for ornament codes (``ornament_dropped.oscillation``)
    and the target pitch system for pitch codes (``microtone_rounded.12tet``). Explanation
    lookups fall back from the full key to the bare code.
    """
    key = code.lower()
    return f"{key}.{qualifier.lower()}" if qualifier else key


def base_key(key: str) -> str:
    return key.split(".", 1)[0]
