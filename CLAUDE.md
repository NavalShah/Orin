# CLAUDE.md

Read [AGENTS.md](AGENTS.md) first. It has the layout, commands, and rules for this repo. This file adds notes for Claude Code.

## Context

- Orin converts music between traditions through a neutral intermediate representation (IR) and reports what was lost. The spec is [requirements.txt](requirements.txt); its section numbers are used throughout the code.
- Three layers: `orin/` (core library), `server/` (FastAPI), and the Next.js app under `app/(orin)/`.
- `pipeline/` is the original prototype analysis. Keep it working and don't restructure it unless asked. The home page (`app/page.tsx`) keeps the original sketch's look.
- Tradition data (Carnatic, Arabic, Persian) is unreviewed. Flag anything musically uncertain instead of guessing, and put the doubt in the data (`caveats`, `confidence`) so the UI shows it.

## Working style

- Keep answers short. Show code only where it changes.
- Run the tests for what you changed before reporting done, and report the actual result: `pytest tests/` for Python, `npm run test` for the app.
- The Python venv is outside the repo. If you need one, create it in the session scratchpad, not in the repo.
- Don't create files in `tests/` beyond tests and small fixtures. Signal generators live in `orin/eval/synth.py`.
- Don't open the browser or start the dev server unless asked.
- Never read or print `.env`.

## Git

- Don't commit, push, or open PRs unless asked.
- If asked to commit, use the existing commit style (short lowercase messages like `ui`, `node`).
