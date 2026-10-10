"""Command line: python -m orin <command>

    convert     notes file (sargam, note names, ABC, MusicXML, MIDI) or a saved IR -> notation + loss report
    transcribe  audio file -> IR (and optionally straight on to notation)
    formats     list the registered formats
    review      print the practitioner review checklist

All printing in the package happens here.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional


def _write_outputs(conversion, out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for kind, r in conversion.renders.items():
        if r.pages:
            for i, page in enumerate(r.pages, start=1):
                path = out_dir / (f"notation.{r.ext}" if len(r.pages) == 1 else f"notation-{i}.{r.ext}")
                path.write_text(page, encoding="utf-8")
                written.append(path)
        elif r.data is not None:
            path = out_dir / f"notation.{r.ext}"
            path.write_bytes(r.data)
            written.append(path)
        elif r.text is not None:
            # Several text kinds share the .txt extension, so the kind goes in the name.
            path = out_dir / f"{kind}.{r.ext}"
            path.write_text(r.text, encoding="utf-8")
            written.append(path)
    (out_dir / "loss.json").write_text(conversion.loss.model_dump_json(indent=2), encoding="utf-8")
    (out_dir / "loss.txt").write_text(conversion.loss.to_text(), encoding="utf-8")
    written += [out_dir / "loss.json", out_dir / "loss.txt"]
    return written


def _print_conversion(conversion, written: list[Path]) -> None:
    from .formats import registry

    s = conversion.loss.summary
    target = registry.get(conversion.target_format).META
    print(f"{conversion.source_format or 'unknown'} -> {conversion.target_format}"
          + ("  (clean-up: same format in and out)" if conversion.same_format else ""))
    print(f"notes: {len(conversion.score.notes)}   fidelity: {s.fidelity_score:.2f} "
          f"(pitch {s.pitch_score:.2f}, ornament {s.ornament_score:.2f}, timing {s.timing_score:.2f})")
    if target.review_status != "practitioner_reviewed":
        print(f"note: the {target.display_name} tables are {target.review_status.replace('_', ' ')}.")
    # A staff cannot be printed in a terminal, so show the nearest text notation instead.
    for kind in (target.primary_renderer, "sargam", "names", "abc"):
        shown = conversion.renders.get(kind)
        if shown is not None and shown.text and kind not in ("svg", "musicxml"):
            print()
            print(shown.text.rstrip())
            break
    print()
    print(conversion.loss.to_text().rstrip())
    for m in conversion.missing:
        print(f"missing output '{m.kind}': {m.message}" + (f" Closest alternative: {m.alternative}." if m.alternative else ""))
    if conversion.suggestions:
        names = ", ".join(c.label for c in conversion.suggestions)
        print(f"modes in {target.display_name} with the same pitches (offered, not applied): {names}")
    if written:
        print()
        print("wrote: " + ", ".join(str(p) for p in written))


def _convert_options(args):
    from .formats.base import ConvertOptions

    return ConvertOptions(mode_id=args.target_mode, template_id=args.target_cycle,
                          force_measured=args.force_measured)


def cmd_convert(args) -> int:
    from .convert import convert
    from .ir.validate import load_ir
    from .pipeline import notes_to_ir

    path = Path(args.input)
    data = path.read_bytes()
    if path.suffix == ".json":
        ir = load_ir(data)
    else:
        ir = notes_to_ir(data, format_id=args.source, syntax=args.syntax, filename=path.name)
    conversion = convert(ir, args.to, _convert_options(args))
    written = _write_outputs(conversion, Path(args.out)) if args.out else []
    _print_conversion(conversion, written)
    return 0


def cmd_transcribe(args) -> int:
    from .ir.validate import dump_ir
    from .options import AnalysisOptions
    from .pipeline import audio_to_ir

    def progress(stage: str, fraction: float, message: str) -> None:
        if args.verbose:
            print(f"  [{fraction:4.0%}] {stage}: {message}", file=sys.stderr)

    options = AnalysisOptions(style=args.style, tonic_hz=args.tonic, mode_id=args.mode,
                              sensitivity=args.sensitivity, voice=args.voice, separate=args.separate)
    ir = audio_to_ir(args.input, options, progress=progress)
    t = ir.tonic
    print(f"duration: {ir.source.duration_s:.2f} s   notes: {len(ir.events)}")
    print(f"tonic: {t.hz:.2f} Hz ({t.nearest_note or '?'}, {t.tuning_offset_cents:+.0f} cents)  "
          f"method {t.method}, confidence {t.confidence:.2f}")
    if ir.source.detected_candidates:
        ranked = ", ".join(f"{c.format_id} ({c.score:.0%})" for c in ir.source.detected_candidates)
        print(f"style: {ranked}")
    if ir.mode.candidates:
        ranked = ", ".join(f"{c.label} ({c.score:.2f})" for c in ir.mode.candidates)
        print(f"closest modes: {ranked}")
    if ir.tempo.free_time:
        print("rhythm: free time (no steady pulse found)")
    elif ir.tempo.bpm:
        print(f"rhythm: {ir.tempo.bpm:.0f} BPM, cycle {ir.cycle.label or ir.cycle.length_pulses or 'unknown'} "
              f"(confidence {ir.cycle.confidence:.2f})")
    for e in ir.events[:20]:
        orn = f"  {e.ornament.kind} {e.ornament.amplitude_cents:.0f}c" if e.ornament.kind else ""
        print(f"  {e.onset_s:7.3f}-{e.offset_s:7.3f}  {e.pitch_cents:8.1f} cents  conf {e.confidence:.2f}{orn}")
    if len(ir.events) > 20:
        print(f"  ... {len(ir.events) - 20} more")
    for w in ir.meta.warnings:
        print(f"warning: {w.message}")

    out = Path(args.out) if args.out else None
    written: list[Path] = []
    if out:
        out.mkdir(parents=True, exist_ok=True)
        (out / "ir.json").write_text(dump_ir(ir, indent=2), encoding="utf-8")
        written.append(out / "ir.json")
    if args.to:
        from .convert import convert

        conversion = convert(ir, args.to, _convert_options(args))
        if out:
            written += _write_outputs(conversion, out)
        print()
        _print_conversion(conversion, written)
    elif written:
        print("wrote: " + ", ".join(str(p) for p in written))
    return 0


def cmd_formats(args) -> int:
    from .formats import registry

    for family, label, ids in registry.families():
        print(label)
        for fid in ids:
            m = registry.get(fid).META
            print(f"  {m.id:18s} {m.display_name}  [{m.review_status.replace('_', ' ')}]")
            print(f"  {'':18s} modes: {len(m.mode_table)}, cycles: {len(m.rhythm_templates)}, "
                  f"outputs: {', '.join(m.renderers)}")
    return 0


def cmd_review(args) -> int:
    from .review import build

    sys.stdout.write(build())
    return 0


def _add_target_options(p: argparse.ArgumentParser) -> None:
    p.add_argument("--target-mode", help="apply this mode of the target format (never applied unless asked)")
    p.add_argument("--target-cycle", help="rhythm template id of the target format")
    p.add_argument("--force-measured", action="store_true", help="write free-time music in bars (logged as loss)")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m orin", description="Convert music between traditions and report what was lost.")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("convert", help="notes file or saved IR -> notation and loss report")
    p.add_argument("input", help="notes file (.sargam, .notes, .abc, .musicxml, .mid) or an IR .json")
    p.add_argument("--from", dest="source", help="format the notes are written in, e.g. carnatic")
    p.add_argument("--to", required=True, help="format to write, e.g. western_classical")
    p.add_argument("--syntax", help="sargam | note_names | abc | musicxml | midi (default: detect)")
    p.add_argument("--out", help="directory for the output files")
    _add_target_options(p)
    p.set_defaults(func=cmd_convert)

    p = sub.add_parser("transcribe", help="audio file -> IR, and optionally on to notation")
    p.add_argument("input", help="audio file (wav, mp3, m4a, flac, ogg)")
    p.add_argument("--style", default="auto", help="format to read the audio as, or 'auto'")
    p.add_argument("--to", help="also convert to this format")
    p.add_argument("--tonic", type=float, help="tonic in Hz, if you know it")
    p.add_argument("--mode", help="mode id, if you know it")
    p.add_argument("--sensitivity", type=float, default=1.0)
    p.add_argument("--voice", default="auto", choices=["auto", "male", "female", "instrument"])
    p.add_argument("--separate", default="auto", choices=["auto", "on", "off"])
    p.add_argument("--out", help="directory for ir.json and any notation files")
    p.add_argument("-v", "--verbose", action="store_true", help="show stage progress")
    _add_target_options(p)
    p.set_defaults(func=cmd_transcribe)

    sub.add_parser("formats", help="list the registered formats").set_defaults(func=cmd_formats)
    sub.add_parser("review", help="print the practitioner review checklist").set_defaults(func=cmd_review)

    args = parser.parse_args(argv)
    from .errors import NotesParseError, OrinError

    try:
        return args.func(args)
    except NotesParseError as exc:
        for problem in exc.problems:
            print(f"{args.input}:{problem.line}:{problem.column}: {problem.severity}: {problem.message} [{problem.code}]",
                  file=sys.stderr)
        return 2
    except OrinError as exc:
        print(f"error: {exc.message}" + (f" {exc.hint}" if exc.hint else ""), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
