# CLAUDE.md

Read [AGENTS.md](AGENTS.md) first. It has the layout, commands, and rules for this repo. This file adds notes for Claude Code.

## Context

- The repo has two parts: a Next.js sketch app and a Python audio pipeline (`pipeline/`).
- The app is a learning sketch. The README is explicit that it does not analyze recordings. Don't wire the pipeline into the app unless asked.
- Musical scope: the pipeline is a Western-profile baseline. Carnatic work (Sa, raga, gamakas) needs design with a musician, so flag it rather than guessing.

## Working style

- Keep answers short. Show code only where it changes.
- For pipeline changes, run `pytest tests/` before reporting done. Report the actual result.
- The Python venv is outside the repo. If you need one, create it in the session scratchpad, not in the repo.
- Don't create files in `tests/` beyond tests and small fixtures.
- Don't open the browser or start the dev server unless asked.

## Git

- Don't commit, push, or open PRs unless asked.
- If asked to commit, use the existing commit style (short lowercase messages like `ui`, `node`).
