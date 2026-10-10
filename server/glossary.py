"""Glossary for the UI tooltips (spec 11.4, 13.5).

Plain, short and hedged on purpose. These are a cautious textbook's first sentence, not
a definition a practitioner would sign: traditions differ between regions, schools and
teachers. Like the format tables, this text is UNREVIEWED until a musician checks it.
"""
from __future__ import annotations

# (term, tradition or None, definition)
ENTRIES: tuple[tuple[str, str | None, str], ...] = (
    ("Sa", "Indian classical",
     "The home note in Indian classical music, short for Shadja. It is not a fixed pitch: each singer or "
     "player chooses where Sa sits, and every other note is heard in relation to it."),
    ("swara", "Indian classical",
     "A note of the scale in Indian classical music. The seven names are Sa, Ri (or Re), Ga, Ma, Pa, "
     "Da (or Dha) and Ni. Several of them come in more than one position, lower or higher."),
    ("raga", "Indian classical",
     "A melodic framework in Indian classical music. A raga is more than a set of notes: it usually also "
     "implies which notes to dwell on, typical phrases, and how notes are ornamented. Two ragas can share "
     "the same notes and still be different."),
    ("tala", "Indian classical",
     "A repeating rhythmic cycle in Indian classical music, with a set number of beats, often kept with "
     "claps, finger counts and waves of the hand. Adi tala, for example, is commonly counted as 8 beats."),
    ("gamaka", "Indian classical",
     "A general word for the ways a note is shaken, slid into or touched from a neighbouring note, "
     "especially in Carnatic music. In many ragas this movement is part of the note itself, not "
     "decoration added on top."),
    ("kampita", "Carnatic",
     "One kind of gamaka: an oscillation, where the pitch swings back and forth around a note or between "
     "notes. How wide and how fast depends on the raga and the performer. Orin only measures the movement "
     "and calls it 'kampita-like'; naming it for certain is a musician's job."),
    ("sargam", "Indian classical",
     "Singing or writing a melody with the swara syllables (Sa Ri Ga Ma Pa Da Ni) instead of staff "
     "notation. The marks used for octaves and note lengths differ between schools and books."),
    ("tonic", None,
     "The home note of a piece: the pitch the melody keeps returning to and tends to rest on. Orin "
     "measures every other pitch as a distance from it."),
    ("cents", None,
     "A small unit for the distance between two pitches. The step between neighbouring piano keys is "
     "100 cents and an octave is 1200. Many listeners do not notice a difference of a few cents in a melody."),
    ("maqam", "Arabic",
     "A melodic mode in Arabic music; related systems exist in Turkish and other traditions. A maqam is "
     "usually described as small note groups (ajnas) joined together, with a typical path through them, "
     "and it often uses notes that fall between the piano keys."),
    ("jins", "Arabic",
     "A small group of neighbouring notes, usually three to five, that works as a building block of a "
     "maqam. The plural is ajnas."),
    ("iqa'", "Arabic",
     "A rhythmic pattern or cycle in Arabic music, usually taught as a sequence of low 'dum' and high "
     "'tak' drum strokes. The plural is iqa'at."),
    ("taqsim", "Arabic",
     "An improvised solo that explores a maqam, usually without a steady beat. Because it is free in "
     "time, any bar lines Orin writes over one are Orin's addition, not the player's."),
    ("dastgah", "Persian",
     "One of the main modal systems of Persian classical music. Each dastgah gathers a set of traditional "
     "melodic models (gusheh-ha) with their own resting notes and character. It is commonly taught as "
     "seven dastgah-ha plus five related avaz, though sources group them differently."),
    ("gusheh", "Persian",
     "A short traditional melodic model inside a Persian dastgah. Performers learn many of them, "
     "collected in a repertoire called the radif, and improvise from them."),
    ("koron", "Persian",
     "A sign in Persian notation that lowers a note by less than a semitone, very roughly a quarter tone. "
     "The exact amount is not fixed: it varies with the dastgah and the performer."),
    ("sori", "Persian",
     "A sign in Persian notation that raises a note by less than a semitone, very roughly a quarter tone. "
     "Like the koron, its exact size varies in practice."),
    ("finalis", None,
     "The note a melody in a given mode typically comes to rest on at the end; the word comes from the "
     "study of chant modes. Orin uses it where 'tonic' would claim too much about how the music is organised."),
    ("quarter tone", None,
     "An interval of about half a semitone, around 50 cents. Notes described as quarter tones in Arabic "
     "or Persian music are often not exactly halfway between piano keys: their tuning varies by region, "
     "mode and player."),
    ("swing", "Jazz and popular",
     "A rhythmic feel in which pairs of short notes are played long-short instead of evenly. How uneven "
     "depends on the style and the tempo, and it is usually felt rather than written out exactly."),
    ("equal temperament", "Western",
     "The tuning of modern pianos and most Western instruments: the octave is divided into twelve equal "
     "steps of 100 cents. It lets music move between keys freely, and in exchange most intervals differ "
     "slightly from their pure, beatless versions."),
    ("pitch bend", None,
     "Sliding a note's pitch smoothly up or down, as when a guitarist pushes a string sideways. In MIDI "
     "it is a message that shifts a note away from its piano-key pitch; Orin uses it so in-between notes "
     "play back closer to the original, though some synthesisers ignore it."),
    ("drone", None,
     "A note, or a pair of notes, held or repeated through a piece as a reference, such as the tanpura in "
     "Indian classical music. It usually sounds the home note, often together with the fifth above it."),
    ("octave", None,
     "The distance from a note to the next one that sounds like the same note, only higher: its frequency "
     "is double. One octave is 1200 cents."),
)


def entries() -> list[dict[str, str | None]]:
    return [{"term": term, "tradition": tradition, "definition": text} for term, tradition, text in ENTRIES]
