# Agent Guide

Guidance for any AI agent working in this repo. Keep changes small and match existing style.

## What's here

| Path | What it is |
|---|---|
| `app/`, `app.js`, `index.html`, `styles.css` | Next.js piano-sketch web app (see [README.md](README.md)) |
| `pipeline/` | Python audio analysis: tempo, notes, tonic |
| `tests/` | pytest suite and sample audio/output for the pipeline |
| `docs/` | Human-readable design and pipeline docs |

The app and the pipeline are independent. The app does not call the pipeline yet.

## Commands

```bash
# Web app
npm install
npm run dev        # dev server
npm run build      # production build

# Pipeline (Python 3, from the repo root)
pip install -r pipeline/requirements.txt
python -m pipeline path/to/audio.wav --backend cqt --out result.json
python -m pipeline path/to/audio.wav --backend pyin   # monophonic melody

# Tests
pytest tests/
```

## Rules of thumb

- **Don't commit or push** unless asked.
- **Don't commit secrets.** `.env` is untracked. Never print or copy its values.
- **Don't add dependencies** without saying why. Pipeline deps go in `pipeline/requirements.txt`.
- **Keep the pipeline pure.** Stages take arrays or paths and return plain dicts/lists. No printing inside the library; printing belongs to `__main__.py`.
- **Add a test** for any pipeline behavior change. Use synthetic signals (see `tests/test_pipeline.py`); don't add large audio files.
- **Don't overclaim musical results.** The key estimate is a Western baseline. Carnatic tonic (Sa) and raga analysis are out of scope until designed with a musician (see the README).

## Where to look first

- Pipeline stages and parameters: [docs/pipeline.md](docs/pipeline.md)
- Module entry points: `pipeline/analyze.py` (`analyze()`), `pipeline/__main__.py` (CLI)
- Web app logic: `app.js`, `app/page.jsx`
