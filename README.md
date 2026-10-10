# Orin

Orin converts and arranges music between traditions, so someone who knows one system can engage with another. A person who knows Carnatic music and a person who plays piano cannot easily share material: Carnatic melody is hard to write on a staff, and a piano cannot play its ornaments. Orin does the conversion and then tells you exactly what was lost on the way.

That list, the **loss report**, is the point. "This gamaka has no symbol on a staff." "This note was 50 cents between two piano keys." The gap is where the learning is.

## What it does

| Part | What you do |
|---|---|
| **Chat** | Talk to Orin (Gemini). Ask for music and it refines your request into a spec, then generates it (ElevenLabs). |
| **Notation Studio** | Give it audio or typed notes, pick how to read it and how to write it, and get notation plus the loss report. |
| **Workspace** | A small FL Studio-style editor in the browser: load audio, arrange clips, edit a piano roll, add drums, export WAV. |

Anything made in one part can be sent to the others.

Supported formats: Western staff, Western pop, rock (staff and guitar tab), Carnatic (sargam), Arabic maqam (staff with half-flats) and Persian dastgah (staff with koron and sori). Any of them converts to any other through a neutral intermediate representation. The architecture is extensible; the list is not "everything".

## Status and honest limits

Built for a hackathon. As of 2026-10-10:

**What has been checked**

- 1,244 offline Python tests pass (`pytest tests/`), including all 36 format-to-format conversions.
- 200 frontend unit and component tests pass, the production build succeeds, and 5 browser smoke tests cover the three screens, the downloads and the handoffs between screens.
- The full flow runs with no API keys: chat request, spec card, generated stand-in clip, transcription, notation, loss report, explanation, Workspace, WAV export.
- A 60-second synthesised clip transcribes in about 8 seconds on a laptop CPU, without stem separation.

**What has not**

- **No real recording has been tested.** Every audio test uses synthesised signals. Expect lower accuracy on real singing and playing. The harness for a real-recording test set exists (`orin/eval/`), but its manifest is empty.
- **No live Gemini or ElevenLabs call has been made.** Those paths are tested against fakes only. Run `pytest -m live` once keys are in place, and check the model names in `.env.example` against the providers' current lists.
- **The tradition tables are unreviewed drafts**, the Persian ones most of all. See "Musical honesty" below.
- **Accessibility** was built in (keyboard operation, labels, live regions, text alternatives for every score) and exercised through role-based tests, but it has not been audited or tried with a screen reader.
- **The CI workflow** has never run; it starts working when the branch is pushed.

**Known limits**

- One melody line only. Chords and harmony are not transcribed.
- Style auto-detect often answers "Not sure - please choose", especially on clips under 10 seconds or music that mixes styles. It cannot separate Arabic from Persian, or classical from pop from rock, on a short single line.
- A tala, iqa' or meter suggested from a melody alone is a guess. Choose it yourself when you know it.
- Sign-in is a profile picker with no password. Anyone who types a name opens that profile. Do not put this on a public server as it is.
- Music generation needs a paid ElevenLabs plan. Without one, generated tracks are synthesised stand-ins and are labelled as such.
- Not built: the optional Basic Pitch cross-check, chord labels, chat memory beyond the current thread, and Workspace effects, time-stretch and pitch-shift.

## Setup

You need Python 3.11, Node 22.12 or newer, and ffmpeg.

```bash
# Python: keep the environment outside the repo
python3.11 -m venv ~/.venvs/orin
source ~/.venvs/orin/bin/activate
pip install -r requirements.txt

# Web app
npm install

# Configuration
cp .env.example .env      # then fill in the keys you have
```

`requirements.txt` is also the project spec: the comment lines are the specification and section 15 at the bottom is the pinned package list. Use Python 3.11; on 3.12 the optional Basic Pitch package cannot be installed on macOS.

The heavy packages (torch, torchcrepe, demucs) are optional. Without them Orin falls back to pYIN pitch tracking and skips stem separation, and says so in the result.

## Run

```bash
# API (terminal 1)
uvicorn server.main:app --port 8000

# Web app (terminal 2)
npm run dev
```

Open http://localhost:3000/chat, /studio or /workspace. Pick any profile name: there are no passwords, and this is not real authentication.

With no API keys, or with `ORIN_OFFLINE=1`, everything still runs on built-in stand-ins, and the UI says which parts are stand-ins.

From the command line:

```bash
python -m orin formats
python -m orin convert examples/mohanam.sargam --from carnatic --to western_classical --out out/
python -m orin transcribe recording.wav --style carnatic --to western_classical --out out/
```

## Environment variables

| Variable | Purpose |
|---|---|
| `GEMINI_API_KEY`, `GEMINI_MODEL` | Chat, spec refinement, explanations. The model id is never hardcoded; set it here |
| `ELEVENLABS_API_KEY` | Music generation (needs a paid plan) and narration |
| `ELEVENLABS_VOICE_ID` | Optional narration voice |
| `ELEVENLABS_MUSIC_MODEL`, `ELEVENLABS_TTS_MODEL` | Optional; empty means the provider default |
| `ORIN_OFFLINE` | `1` forces the stand-ins |
| `ORIN_MAX_UPLOAD_MB`, `ORIN_MAX_AUDIO_SECONDS` | Upload limits (10 MB, 300 s) |
| `ORIN_DATA_DIR` | Where uploads, cache and the SQLite file live (`./data`) |
| `ORIN_SECRET_KEY` | Signs asset links |
| `ORIN_APP_ORIGIN` | The only origin CORS allows (`http://localhost:3000`) |
| `NEXT_PUBLIC_ORIN_API` | Where the browser finds the API (`http://127.0.0.1:8000`) |

Keys stay on the server. `.env` is untracked; never commit it.

## Test

```bash
pytest tests/              # offline tests; live API tests are skipped
pytest tests/ -m live      # real Gemini and ElevenLabs calls; needs keys
npm run test               # component and unit tests
npm run build
npx playwright test        # browser smoke tests; set ORIN_PYTHON to your environment's python
```

## Musical honesty

- Every tradition table (ragas, talas, maqamat, iqa'at, dastgah-ha) is a draft written from references and marked **unreviewed**. [docs/review-checklist.md](docs/review-checklist.md) lists every value with its source for a practitioner to check.
- Orin never names one raga, maqam or dastgah as certain. It shows closest matches and the other modes that share those pitches.
- Transcription is approximate by design and handles one melody line. Confidence is shown, and the tonic can always be set by hand.
- Tuning tables are nominal reference values, not measurements.

## Layout

| Path | What it is |
|---|---|
| `requirements.txt` | The spec and the pinned Python packages |
| `orin/` | Core library: audio or notes -> IR -> notation and loss report |
| `server/` | FastAPI backend |
| `app/(orin)/`, `components/`, `lib/` | The three screens |
| `pipeline/`, `app/page.jsx` | The original prototype: a Western-baseline analysis and a piano-sketch page |
| `tests/`, `e2e/` | pytest and Playwright |
| `docs/` | [Architecture](docs/architecture.md), [the IR](docs/ir.md), [the loss report](docs/loss-report.md), [adding a format](docs/adding-a-format.md), [review checklist](docs/review-checklist.md) |

## The original sketch

The page at `/` is the first prototype, "Raga to Keys": a hard-coded Carnatic-inspired phrase you can play and nudge on a piano. It is a learning sketch, not a transcription, and it does not use the Orin pipeline.
