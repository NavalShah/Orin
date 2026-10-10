"""CLI: python -m pipeline input.wav [--backend cqt|pyin] [--out result.json]"""
import argparse
import json

from .analyze import analyze


def main() -> None:
    parser = argparse.ArgumentParser(description='Analyze tempo, notes, and tonic from audio.')
    parser.add_argument('audio', help='path to an audio file')
    parser.add_argument('--backend', choices=['cqt', 'pyin'], default='cqt',
                        help='cqt = polyphonic (default); pyin = monophonic melody')
    parser.add_argument('--out', help='write full JSON result to this path')
    args = parser.parse_args()

    result = analyze(args.audio, backend=args.backend)

    if args.out:
        with open(args.out, 'w') as f:
            json.dump(result, f, indent=2)
        print(f'wrote {args.out}')

    key = result['key']
    key_str = f"{key['tonic']} {key['mode']} (r={key['correlation']})" if key else 'n/a'
    print(f"duration: {result['duration_s']} s  tuning: {result['tuning_semitones']:+} st")
    print(f"tempo: {result['tempo']['bpm']} BPM (confidence {result['tempo']['confidence']})")
    print(f"key: {key_str}")
    print(f"notes: {len(result['notes'])}")
    for n in result['notes'][:20]:
        print(f"  {n['onset']:7.3f}-{n['offset']:7.3f}  {n['pitch_class']}{n['octave']}  vel {n['velocity']}")
    if len(result['notes']) > 20:
        print(f"  ... {len(result['notes']) - 20} more (use --out for full JSON)")


if __name__ == '__main__':
    main()
