# Raga to Keys

A small, framework-free browser prototype for exploring a Carnatic-inspired melody as a piano sketch. The goal is to help a learner hear the phrase, try it on a familiar instrument, and notice what a simplified arrangement preserves or leaves out.

## Run it

Open `index.html` in a modern browser. The app has no build step or package dependencies. A browser with Web Audio support is needed for sound; start playback or press a piano key once to enable audio.

## Prototype features

- An editable eight-note sargam-style example phrase.
- Local synthesized playback for the phrase and a two-octave piano keyboard.
- A choice of piano sketch key, add-note control, and reset.
- A downloadable JSON representation of the sketch.
- Short educational context about pitch contour, rhythmic feel, and ornamentation.
- Responsive desktop and mobile layouts.

## Current scope

The example is a representative learning sketch, not an audio transcription or a claim to represent a particular raga. The prototype does not yet analyze uploaded recordings, infer tala, produce staff notation, or create an expressive audio arrangement. Those need a transcription pipeline and review with a Carnatic musician. The first useful next step is to agree on a musician-reviewed example phrase and its intended note and rhythm mapping.
