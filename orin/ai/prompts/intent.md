<!-- version: 1 -->
Task: classify the user's latest message into exactly one intent.

- "chat": a question, an explanation, theory, small talk. Reply only.
- "generate": the user wants new music made, or wants a change to music made earlier in this thread ("make it slower", "same but with piano").
- "transcribe": the user wants notation (staff, sargam, tab, note names) for an attached track.
- "convert": the user wants an attached track re-made in another style ("make this Carnatic piece in rock style", "as a pop arrangement"). Needs an attachment.
- "clarify": the request is too vague to act on ("make something", an empty or one-word message), or it needs a track and none is attached.

Guidance
- A question about how to do something is "chat", even if it mentions making music.
- "Convert this to staff notation" is "transcribe": the result is notation, not new audio.
- A selected genre chip plus a short description is "generate".
- When unsure between "chat" and "clarify", prefer "chat".

Return JSON with:
- "intent": one of the five strings above
- "reason": one short sentence

Examples
- "What is a gamaka?" -> chat
- "Make a short Carnatic alapana that turns into jazz piano" -> generate
- "make it slower" (after a generated track) -> generate
- "Can I get the notation for this?" (attachment: yes) -> transcribe
- "Turn this into a rock arrangement" (attachment: yes) -> convert
- "Convert this to rock" (attachment: no) -> clarify
- "music" -> clarify
