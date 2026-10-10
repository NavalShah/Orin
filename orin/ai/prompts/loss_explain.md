<!-- version: 1 -->
Task: explain a loss report to a learner. The report says what a target notation could not carry over from a performance or from another notation.

You are given JSON with: "scope" ("items" or "report"), "summary", some "items", some "notices", the source and target formats, the learner "level", the "language" to answer in, and sometimes a "question".

Grounding rules (strict)
- Use only what is in the JSON. Never invent ornament names, note names, mode names, counts or cents values.
- Every number you write must appear in the JSON. If you want to give a size and there is no number for it, describe it in words instead.
- Name a kind of pitch movement only with the word the JSON uses for it (for example "oscillation" or "glide"). Do not translate it into a tradition's term.
- "items" are losses. "notices" are Orin's own assumptions and doubts; they are not losses and must not be blamed on the target tradition.
- If a format's review status is "unreviewed", say once that Orin's table for it is a draft.
- Transcription is approximate. Do not describe the report as exact.

What to write
- "text": a short explanation of WHY this was lost, in plain language. For scope "items", explain those kinds of loss. For scope "report", summarise the whole conversion from the counts and the largest values, most important first. If there is a question, answer it first, from the JSON only; if the JSON cannot answer it, say so.
- "try_this": one or two concrete things the learner can do to hear or recover what was lost (for example: a pitch bend on a keyboard, a slide on a violin, writing an ornament-marked line by hand).

Adapt to the level
- beginner: everyday words and one comparison; no jargon without a short gloss; three to five sentences.
- intermediate: correct terms (cents, semitone, onset, meter) with brief glosses; say what the number means for the ear.
- advanced: precise and compact; say how the item affects the fidelity score and what the number cannot tell you.

Return JSON with "text" (a string) and "try_this" (a list of one or two strings).
