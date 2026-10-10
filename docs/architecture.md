# Architecture

Orin converts music between traditions through a neutral intermediate representation (IR) and reports what could not be carried over. Three layers: a core library, a thin API, and a web app with three screens.

```mermaid
flowchart LR
    subgraph Browser [Web app: Next.js, app/(orin)/]
        C[Chat]
        N[Notation Studio]
        W[Workspace]
    end

    subgraph API [server/: FastAPI, /api/v1]
        R[Routes + auth]
        J[Job queue]
        ST[(SQLite + files)]
    end

    subgraph Core [orin/: core library]
        direction TB
        IN[ingest: audio or notes] --> P[pitch: torchcrepe / pYIN]
        P --> T[tonic]
        T --> SG[segment + ornaments]
        SG --> RH[rhythm]
        RH --> M[mode: closest matches]
        M --> IR[[IR]]
        IR --> AD[format adapter: quantize, snap, drop]
        AD --> RN[renderers: staff, sargam, names, ABC, tab, MIDI]
        AD --> LR[[Loss report]]
    end

    subgraph AI [orin/ai/]
        G[Gemini: chat, spec, explanations]
        E[ElevenLabs: music, narration]
        O[Offline stand-ins]
    end

    C & N & W <--> R
    R --> J --> Core
    J --> AI
    R <--> ST
    G -.no key.-> O
    E -.no key or plan.-> O
```

## The three layers

| Layer | Path | Rule |
|---|---|---|
| Core | `orin/` | Pure library. No web imports, no printing. Stages take arrays or models and return models. |
| API | `server/` | Thin. Long work (transcription, separation, generation) runs as a background job with streamed progress. Third-party keys never leave it. |
| App | `app/(orin)/`, `components/`, `lib/` | Talks only to the Orin API through `lib/api.ts`. The Workspace's audio engine runs entirely in the browser. |

## Why an IR and not staff notation as the hub

Staff notation has twelve pitches per octave and bar lines. Using it as the hub would discard microtones, ornaments and free rhythm before any target format saw them. The IR keeps pitch as cents from the tonic, time as seconds, and the full pitch contour, so each format decides for itself what it can write and reports the rest. See [ir.md](ir.md) and [loss-report.md](loss-report.md).

## One engine, many formats

A format (tradition) is one adapter file: a pitch grid, modes, rhythm cycles, an ornament table and a list of renderers. One generic engine does every conversion, so six formats give 36 input-output pairs with no pair-specific code. See [adding-a-format.md](adding-a-format.md).

## Handoffs between the screens

Handoffs are plain URLs (`lib/handoff.ts`), so they survive a reload.

| From | To | What moves |
|---|---|---|
| Chat | Workspace | a generated track (`/workspace?import_asset=`) |
| Chat | Notation Studio | a generated track to transcribe (`/studio?asset=`) |
| Notation Studio | Workspace | the notation as a piano-roll clip, microtones kept as detune (`/workspace?import_conversion=`) |
| Workspace | Notation Studio | a bounced selection (`/studio?asset=`) |
| Notation Studio | Chat | a loss report to ask about (`/chat?conversion=`) |

## Offline mode

With no API keys, or with `ORIN_OFFLINE=1`, Gemini and ElevenLabs are replaced by built-in stand-ins: keyword intent, a template music spec, template explanations keyed by loss code, a synthesised placeholder clip, and the browser's own speech synthesis. Stand-in output is always labelled as such.

## Data

SQLite at `ORIN_DATA_DIR/orin.db`: users, sessions, threads, messages, assets, conversions, projects, jobs, explanation cache. Files are stored by content hash under `ORIN_DATA_DIR/assets`, behind an interface that could be swapped for object storage. Every query is scoped to the signed-in user.
