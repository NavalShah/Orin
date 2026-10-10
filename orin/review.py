"""Builds the practitioner review checklist from the adapter tables (spec D9).

Every tradition table in Orin is an unreviewed draft. This lists each value with its
source, so a musician can check them one by one before anyone else sees them:

    python -m orin review > docs/review-checklist.md
"""
from __future__ import annotations

from .formats import registry
from .formats.base import FormatMeta, ModeEntry


def _degrees(mode: ModeEntry) -> str:
    def row(ds) -> str:
        return " ".join(f"{d.name} ({d.cents:g})" for d in ds)

    parts = [row(mode.ascending)]
    if mode.descending:
        parts.append("descending: " + row(mode.descending))
    if mode.variable:
        parts.append("also: " + row(mode.variable))
    return "; ".join(parts)


def _format_section(meta: FormatMeta) -> list[str]:
    out = [
        f"## {meta.display_name} (`{meta.id}`)",
        "",
        f"Status: **{meta.review_status}**. {meta.reviewer_note}",
        "",
        "What Orin tells users about this format:",
        *[f"- {c}" for c in meta.caveats],
        "",
        "### Pitch positions",
        "",
        "| Name | Cents above the " + meta.tonic_word + " | Tolerance | Other readings | OK? |",
        "|---|---|---|---|---|",
    ]
    for g in meta.grid:
        alt = ", ".join(name for name, _ in g.alt) or ""
        out.append(f"| {g.name} | {g.cents:g} | {g.tol:g} | {alt} | [ ] |")

    out += ["", "### Modes", "", "| Mode | Kind | Notes (nominal cents) | Written on | Source | Confidence | OK? |", "|---|---|---|---|---|---|---|"]
    for m in meta.mode_table:
        tonic = ""
        if m.conventional_tonic:
            alter = m.conventional_tonic.alter_cents
            tonic = m.conventional_tonic.step + (f" {alter:+g} cents" if alter else "")
        out.append(f"| {m.label} | {m.kind} | {_degrees(m)} | {tonic} | {m.source} | {m.confidence} | [ ] |")
    notes = [(m.label, c) for m in meta.mode_table for c in m.caveats]
    if notes:
        out += ["", "Caveats shown with these modes:"]
        seen: set[str] = set()
        for label, caveat in notes:
            if caveat not in seen:
                seen.add(caveat)
                out.append(f"- {caveat}")

    out += ["", "### Rhythm cycles", "", "| Cycle | Beats x subdivision | Structure | Accents | Grouping | Source | Confidence | OK? |", "|---|---|---|---|---|---|---|---|"]
    for t in meta.rhythm_templates:
        out.append(
            f"| {t.label} | {t.cycle_beats} x {t.subdivision} | {' '.join(t.structure)} | "
            f"{' '.join(f'{a:g}' for a in t.accent_pattern)} | {'+'.join(map(str, t.groups))} | {t.source} | {t.confidence} | [ ] |"
        )
    cycle_caveats = {c for t in meta.rhythm_templates for c in t.caveats}
    if cycle_caveats:
        out += ["", "Caveats shown with these cycles:", *[f"- {c}" for c in sorted(cycle_caveats)]]

    out += ["", "### Ornaments", "", "| Measured movement | Called | Written as | OK? |", "|---|---|---|---|"]
    for r in meta.ornament_table:
        width = ""
        if r.min_amplitude_cents > 0 or r.max_amplitude_cents < 1e8:
            lo = f"{r.min_amplitude_cents:.0f}" if r.min_amplitude_cents > 0 else "0"
            hi = f"{r.max_amplitude_cents:.0f}" if r.max_amplitude_cents < 1e8 else "any"
            width = f" ({lo}-{hi} cents)"
        if r.action == "drop":
            shown = "not written"
        else:
            shown = r.approximation or r.symbol or r.action
        out.append(f"| {r.neutral_kind}{width} | {r.local_name} | {shown} | [ ] |")
    out.append("")
    return out


def build() -> str:
    lines = [
        "# Practitioner review checklist",
        "",
        "Generated from the adapter files in `orin/formats/` by `python -m orin review`. Do not edit by hand.",
        "",
        "Every table below is a draft written from references by people who are not practitioners of",
        "these traditions. Each format ships marked **unreviewed** until someone who is checks it.",
        "Tick a row when it is right; correct the adapter file when it is not, and say who reviewed it in",
        "`reviewer_note`. Only then change `review_status`.",
        "",
        "Cents are nominal: one reference value per note, with a tolerance. They are used to decide which",
        "written note a performed pitch belongs to. They are never shown to users as measured tuning.",
        "",
    ]
    for meta in registry.list_meta():
        lines += _format_section(meta)
    return "\n".join(lines).rstrip() + "\n"
