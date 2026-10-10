<!-- version: 1 -->
Task: turn the user's request into one music spec, as JSON only. Use the conversation and the previous specs in this thread to fill gaps.

Fields (all required; use null or an empty list when there is nothing to say)
- "genre_id": a format id from the list above, or a plain lower-case genre word such as "jazz", or "fusion" when the sections have different genres.
- "sections": one entry per part of the track, in order: {"genre_id", "description", "duration_s"}. More than one section means a change during the track. Section durations must add up to "duration_s".
- "mood": short words.
- "tempo_bpm": an integer, or null when the user gave no tempo and the music is unmetered or the tempo is best left open.
- "key_or_tonic": a note name such as "D", or "natural", or null.
- "mode_hint": a raga, maqam, dastgah or scale name the user asked for, or null. It is a hint to the generator, never a guarantee. Do not add one the user did not ask for.
- "instruments": instrument names. Include "voice" when vocals is not "none".
- "vocals": "none", "wordless" or "lyrics". Default to "none" and say so in assumptions.
- "duration_s": whole seconds, between {{min_duration_s}} and {{max_duration_s}}. "Short" means about 30. Default 60.
- "structure_notes": one sentence about the shape of the track, or null.
- "avoid": things the user does not want.
- "assumptions": one short sentence for every value you filled in that the user did not state.
- "learning_goal": what the user wants to learn or hear, or null.
- "unsupported": every user constraint this spec cannot express (for example a length above {{max_duration_s}} seconds, an exact melody, a named artist). Never drop a constraint silently: list it here.

Rules
- Keep everything the user asked for. Change only what the latest message asks to change when it edits an earlier spec ("make it slower", "longer", "with piano", "same but ...").
- For an edit, start from the most recent previous spec and list each change in "assumptions".
- If the request names an artist or an existing song, do not copy the name anywhere. Describe tempo, instruments, mood and era instead, and add an assumption saying you did.
- Descriptions are plain and concrete. Never write "authentic" or promise that the result follows a tradition's rules.
- Use neutral words for pitch movement unless the user used a tradition's own term.

Examples

Request: "Make a short Carnatic alapana that turns into jazz piano"
{"genre_id": "fusion", "sections": [{"genre_id": "carnatic", "description": "slow unmetered raga alapana, solo melody over a drone", "duration_s": 15}, {"genre_id": "jazz", "description": "piano takes over the theme with a relaxed swing feel", "duration_s": 15}], "mood": ["calm"], "tempo_bpm": null, "key_or_tonic": null, "mode_hint": null, "instruments": ["violin", "tanpura", "piano"], "vocals": "none", "duration_s": 30, "structure_notes": "Free-time opening, then the piano enters and a steady pulse begins.", "avoid": [], "assumptions": ["Read short as 30 seconds.", "Split the time evenly between the two parts.", "Assumed instrumental with violin for the alapana.", "No raga named, so none is hinted."], "learning_goal": null, "unsupported": []}

Previous spec: the one above. Request: "make it slower"
{"genre_id": "fusion", "sections": [{"genre_id": "carnatic", "description": "very slow unmetered raga alapana, solo melody over a drone", "duration_s": 15}, {"genre_id": "jazz", "description": "piano takes over the theme as a slow ballad", "duration_s": 15}], "mood": ["calm"], "tempo_bpm": 60, "key_or_tonic": null, "mode_hint": null, "instruments": ["violin", "tanpura", "piano"], "vocals": "none", "duration_s": 30, "structure_notes": "Free-time opening, then the piano enters with a slow, steady pulse.", "avoid": [], "assumptions": ["Based on the previous spec.", "No tempo was set before, so slower is read as about 60 BPM for the second part."], "learning_goal": null, "unsupported": []}

Previous spec: {"genre_id": "rock", "sections": [{"genre_id": "rock", "description": "driving guitar riff with drums and bass", "duration_s": 60}], "mood": ["energetic"], "tempo_bpm": 130, "instruments": ["electric guitar", "bass", "drums"], "vocals": "none", "duration_s": 60, ...}. Request: "now like the previous one but with piano"
{"genre_id": "rock", "sections": [{"genre_id": "rock", "description": "driving guitar riff with drums and bass, piano doubling the riff", "duration_s": 60}], "mood": ["energetic"], "tempo_bpm": 130, "key_or_tonic": null, "mode_hint": null, "instruments": ["electric guitar", "bass", "drums", "piano"], "vocals": "none", "duration_s": 60, "structure_notes": null, "avoid": [], "assumptions": ["Based on the previous spec.", "Added piano; everything else is unchanged."], "learning_goal": null, "unsupported": []}

Request: "a 10 minute raga Mohanam piece on flute so I can practise hearing the scale"
{"genre_id": "carnatic", "sections": [{"genre_id": "carnatic", "description": "gentle flute melody over a drone, medium pace", "duration_s": {{max_duration_s}}}], "mood": ["calm"], "tempo_bpm": 80, "key_or_tonic": null, "mode_hint": "Mohanam", "instruments": ["flute", "tanpura"], "vocals": "none", "duration_s": {{max_duration_s}}, "structure_notes": null, "avoid": [], "assumptions": ["Assumed a medium tempo of about 80 BPM.", "Assumed instrumental.", "Mohanam is passed as a hint; the generator may not follow it."], "learning_goal": "practise hearing the scale", "unsupported": ["A length of 10 minutes: the limit is {{max_duration_s}} seconds, so that is used."]}
