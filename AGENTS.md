# Agent Guide

Guidance for any AI agent working in this repo. Match existing style.

## What's here

| Path | What it is |
|---|---|
| `requirements.txt` | The Orin spec (comment lines) plus the pinned Python deps (section 15). Read the section you are implementing |
| `orin/` | Core library: audio or notes -> IR -> per-tradition notation and a loss report |
| `server/` | FastAPI backend, a thin layer over `orin/` |
| `app/(orin)/`, `components/`, `lib/` | Next.js app: Chat, Notation Studio, Workspace (TypeScript) |
| `app/page.tsx`, `components/home/` | The public home page, in the look of the original Raga to Keys sketch |
| `app.js`, `index.html`, `styles.css` | The first, pre-Next prototype of that sketch. Unused; leave it alone unless asked |
| `pipeline/` | The original Western-baseline analysis (tempo, notes, key). `orin/` imports from it |
| `tests/`, `e2e/` | pytest suite; Playwright smoke tests |
| `docs/` | Architecture, IR, loss report, adding a format, practitioner review checklist |

## Commands

```bash
# Python 3.11, venv outside the repo
pip install -r requirements.txt
pytest tests/                    # offline tests; live API tests are skipped
pytest tests/ -m live            # real Gemini / ElevenLabs calls, needs keys
python -m orin convert examples/mohanam.sargam --from carnatic --to western_classical --out out/
ORIN_OFFLINE=1 uvicorn server.main:app --port 8000

# Web app
npm install
npm run dev                      # http://localhost:3000, Orin screens at /chat /studio /workspace
npm run test                     # vitest
npm run build
npx playwright test

# Original pipeline CLI
python -m pipeline path/to/audio.wav --backend pyin
```

## Rules of thumb

- **Don't commit or push** unless asked.
- **Don't commit secrets.** `.env` is untracked. Never read, print or copy its values. Keys are used server-side only and never go in a `NEXT_PUBLIC_*` variable.
- **Don't add dependencies** without saying why. Python deps go in `requirements.txt` section 15, pinned. `pipeline/requirements.txt` covers only the original pipeline.
- **Keep `orin/` pure.** No printing and no FastAPI imports. Stages take arrays or models and return models. Printing belongs to `orin/__main__.py`.
- **Add a test** for any behaviour change. Use synthesised signals (`orin/eval/synth.py`); don't add audio files. Real recordings go through the eval manifest (`orin/eval/`).
- **Never hardcode model ids.** Read them from env.
- **Offline first.** Every Gemini and ElevenLabs path must work with no keys, through the stand-ins in `orin/ai/offline.py`.
- **Adapters never mutate the IR and never raise on odd music.** They degrade and record a loss item or a notice.

## Musical honesty

- Tradition tables (ragas, talas, maqamat, iqa'at, dastgah-ha) are drafts. Every row carries a `source` and a `confidence`, and every format ships `review_status: "unreviewed"`. Don't add a value without a source. Don't mark anything reviewed; only a practitioner can.
- Never present one raga, maqam or dastgah as certain. Show closest matches and their lookalikes.
- The analyser emits neutral ornament names (oscillation, glide, grace, vibrato). Only an adapter applies a tradition's name, and only when that style was chosen explicitly.
- Tuning tables are nominal. Don't display them as measured tuning.
- Transcription is approximate by design. Show confidence; don't overclaim.

## Where to look first

- Spec: `requirements.txt` (section numbers are cited in code and docs)
- IR: `orin/ir/models.py`, `docs/ir.md`
- Formats and the conversion engine: `orin/formats/base.py`, `docs/adding-a-format.md`
- Audio pipeline: `orin/pipeline.py`, per-style numbers in `orin/config/styles.toml`
- API contract: `server/schemas.py`
- App shell and API client: `app/(orin)/layout.tsx`, `lib/api.ts`

<!-- BEGIN:nextjs-agent-rules -->

## This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` (resolved from this file's directory; in monorepos the `next` package may not be visible from the repo root) before writing any code. Heed deprecation notices.

This block is written and re-added by `next dev` — verify at `node_modules/next/dist/server/lib/generate-agent-files.js`. Removing it from a diff only re-creates the uncommitted change; committing it with your work keeps the tree clean.

<!-- END:nextjs-agent-rules -->
