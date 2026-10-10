# Practitioner review checklist

Generated from the adapter files in `orin/formats/` by `python -m orin review`. Do not edit by hand.

Every table below is a draft written from references by people who are not practitioners of
these traditions. Each format ships marked **unreviewed** until someone who is checks it.
Tick a row when it is right; correct the adapter file when it is not, and say who reviewed it in
`reviewer_note`. Only then change `review_status`.

Cents are nominal: one reference value per note, with a tolerance. They are used to decide which
written note a performed pitch belongs to. They are never shown to users as measured tuning.

## Western (staff notation) (`western_classical`)

Status: **unreviewed**. Standard theory tables; not yet checked by a second person.

What Orin tells users about this format:
- One melody line only: chords and harmony are not transcribed.
- Staff notation has twelve pitches per octave: anything in between is rounded and reported.
- A key signature is chosen to keep accidentals few. It is a spelling aid, not a claim about the mode.

### Pitch positions

| Name | Cents above the tonic | Tolerance | Other readings | OK? |
|---|---|---|---|---|
| 1 | 0 | 0 |  | [ ] |
| b2 | 100 | 0 |  | [ ] |
| 2 | 200 | 0 |  | [ ] |
| b3 | 300 | 0 |  | [ ] |
| 3 | 400 | 0 |  | [ ] |
| 4 | 500 | 0 |  | [ ] |
| #4 | 600 | 0 | b5 | [ ] |
| 5 | 700 | 0 |  | [ ] |
| b6 | 800 | 0 |  | [ ] |
| 6 | 900 | 0 |  | [ ] |
| b7 | 1000 | 0 |  | [ ] |
| 7 | 1100 | 0 |  | [ ] |

### Modes

| Mode | Kind | Notes (nominal cents) | Written on | Source | Confidence | OK? |
|---|---|---|---|---|---|---|
| Major | scale | 1 (0) 2 (200) 3 (400) 4 (500) 5 (700) 6 (900) 7 (1100) |  | Standard Western music theory (12-tone equal temperament) | high | [ ] |
| Natural minor | scale | 1 (0) 2 (200) b3 (300) 4 (500) 5 (700) b6 (800) b7 (1000) |  | Standard Western music theory (12-tone equal temperament) | high | [ ] |
| Harmonic minor | scale | 1 (0) 2 (200) b3 (300) 4 (500) 5 (700) b6 (800) 7 (1100) |  | Standard Western music theory (12-tone equal temperament) | high | [ ] |
| Melodic minor | scale | 1 (0) 2 (200) b3 (300) 4 (500) 5 (700) 6 (900) 7 (1100); descending: 1 (0) 2 (200) b3 (300) 4 (500) 5 (700) b6 (800) b7 (1000) |  | Standard Western music theory (12-tone equal temperament) | high | [ ] |
| Dorian | mode | 1 (0) 2 (200) b3 (300) 4 (500) 5 (700) 6 (900) b7 (1000) |  | Standard Western music theory (12-tone equal temperament) | high | [ ] |
| Phrygian | mode | 1 (0) b2 (100) b3 (300) 4 (500) 5 (700) b6 (800) b7 (1000) |  | Standard Western music theory (12-tone equal temperament) | high | [ ] |
| Lydian | mode | 1 (0) 2 (200) 3 (400) #4 (600) 5 (700) 6 (900) 7 (1100) |  | Standard Western music theory (12-tone equal temperament) | high | [ ] |
| Mixolydian | mode | 1 (0) 2 (200) 3 (400) 4 (500) 5 (700) 6 (900) b7 (1000) |  | Standard Western music theory (12-tone equal temperament) | high | [ ] |
| Locrian | mode | 1 (0) b2 (100) b3 (300) 4 (500) b5 (600) b6 (800) b7 (1000) |  | Standard Western music theory (12-tone equal temperament) | high | [ ] |
| Major pentatonic | scale | 1 (0) 2 (200) 3 (400) 5 (700) 6 (900) |  | Standard Western music theory (12-tone equal temperament) | high | [ ] |
| Minor pentatonic | scale | 1 (0) b3 (300) 4 (500) 5 (700) b7 (1000) |  | Standard Western music theory (12-tone equal temperament) | high | [ ] |

Caveats shown with these modes:
- Rises with a raised 6th and 7th and usually falls as natural minor.

### Rhythm cycles

| Cycle | Beats x subdivision | Structure | Accents | Grouping | Source | Confidence | OK? |
|---|---|---|---|---|---|---|---|
| 4/4 | 4 x 4 | strong weak medium weak | 1 0.3 0.6 0.3 |  | Standard Western music theory (12-tone equal temperament) | high | [ ] |
| 3/4 | 3 x 4 | strong weak weak | 1 0.3 0.3 |  | Standard Western music theory (12-tone equal temperament) | high | [ ] |
| 2/4 | 2 x 4 | strong weak | 1 0.4 |  | Standard Western music theory (12-tone equal temperament) | high | [ ] |
| 6/8 | 2 x 3 | strong medium | 1 0.6 |  | Standard Western music theory (12-tone equal temperament) | high | [ ] |

### Ornaments

| Measured movement | Called | Written as | OK? |
|---|---|---|---|
| vibrato | vibrato | not written | [ ] |
| oscillation | pitch oscillation | not written | [ ] |
| glide | glissando | a glissando line | [ ] |
| grace | grace note | a grace note | [ ] |

## Carnatic (Sangeetham) (`carnatic`)

Status: **unreviewed**. Draft tables written from standard references. Not reviewed by a Carnatic musician.

What Orin tells users about this format:
- Draft, not reviewed by a Carnatic musician.
- Ragas are matched on pitches only and shown as closest matches. Gamakas, which are part of a raga's identity, are not modelled.
- Swara positions are nominal: twelve positions, not the 22 shrutis, and not measured tuning.
- A tala cannot be proven from the melody alone. Orin suggests one; choose it yourself if you know it.
- One melody line only.

### Pitch positions

| Name | Cents above the Sa | Tolerance | Other readings | OK? |
|---|---|---|---|---|
| S | 0 | 15 |  | [ ] |
| R1 | 112 | 30 |  | [ ] |
| R2 | 204 | 30 | G1 | [ ] |
| G2 | 316 | 30 | R3 | [ ] |
| G3 | 386 | 30 |  | [ ] |
| M1 | 498 | 20 |  | [ ] |
| M2 | 590 | 30 |  | [ ] |
| P | 702 | 15 |  | [ ] |
| D1 | 814 | 30 |  | [ ] |
| D2 | 884 | 30 | N1 | [ ] |
| N2 | 1018 | 30 | D3 | [ ] |
| N3 | 1088 | 30 |  | [ ] |

### Modes

| Mode | Kind | Notes (nominal cents) | Written on | Source | Confidence | OK? |
|---|---|---|---|---|---|---|
| Mayamalavagowla (mela 15) | melakarta | S (0) R1 (112) G3 (386) M1 (498) P (702) D1 (814) N3 (1088) |  | Standard 72-melakarta chart; to be cross-checked against Sambamoorthy, South Indian Music. | high | [ ] |
| Shankarabharanam (mela 29) | melakarta | S (0) R2 (204) G3 (386) M1 (498) P (702) D2 (884) N3 (1088) |  | Standard 72-melakarta chart; to be cross-checked against Sambamoorthy, South Indian Music. | high | [ ] |
| Kalyani (mela 65) | melakarta | S (0) R2 (204) G3 (386) M2 (590) P (702) D2 (884) N3 (1088) |  | Standard 72-melakarta chart; to be cross-checked against Sambamoorthy, South Indian Music. | high | [ ] |
| Kharaharapriya (mela 22) | melakarta | S (0) R2 (204) G2 (316) M1 (498) P (702) D2 (884) N2 (1018) |  | Standard 72-melakarta chart; to be cross-checked against Sambamoorthy, South Indian Music. | high | [ ] |
| Harikambhoji (mela 28) | melakarta | S (0) R2 (204) G3 (386) M1 (498) P (702) D2 (884) N2 (1018) |  | Standard 72-melakarta chart; to be cross-checked against Sambamoorthy, South Indian Music. | high | [ ] |
| Mohanam | janya | S (0) R2 (204) G3 (386) P (702) D2 (884) |  | Common reference listings of the raga's arohana and avarohana; to be checked by a practitioner. | medium | [ ] |
| Hamsadhwani | janya | S (0) R2 (204) G3 (386) P (702) N3 (1088) |  | Common reference listings of the raga's arohana and avarohana; to be checked by a practitioner. | medium | [ ] |
| Hindolam | janya | S (0) G2 (316) M1 (498) D1 (814) N2 (1018) |  | Common reference listings of the raga's arohana and avarohana; to be checked by a practitioner. | medium | [ ] |
| Shuddha Saveri | janya | S (0) R2 (204) M1 (498) P (702) D2 (884) |  | Common reference listings of the raga's arohana and avarohana; to be checked by a practitioner. | medium | [ ] |
| Madhyamavati | janya | S (0) R2 (204) M1 (498) P (702) N2 (1018) |  | Common reference listings of the raga's arohana and avarohana; to be checked by a practitioner. | medium | [ ] |

Caveats shown with these modes:
- Matched on pitches only. A raga is also its phrases and gamakas, which Orin does not model.
- Mohanam, Hindolam, Shuddha Saveri and Madhyamavati use the same five-note shape starting from different notes, so a wrong Sa turns one into another.
- Has no Pa; a drone may be tuned Sa-Ma instead of Sa-Pa.

### Rhythm cycles

| Cycle | Beats x subdivision | Structure | Accents | Grouping | Source | Confidence | OK? |
|---|---|---|---|---|---|---|---|
| Adi tala (8) | 8 x 4 | clap finger finger finger clap wave clap wave | 1 0.3 0.3 0.3 0.7 0.2 0.7 0.2 | 4+2+2 | Common teaching descriptions of the tala's angas and counts; to be checked by a practitioner. | high | [ ] |
| Rupakam (3-count, as commonly performed) | 3 x 4 | clap clap wave | 1 0.6 0.3 | 3 | Common teaching descriptions of the tala's angas and counts; to be checked by a practitioner. | medium | [ ] |
| Misra Chapu (7) | 7 x 2 | clap - - clap - clap - | 1 0.2 0.2 0.8 0.2 0.8 0.2 | 3+2+2 | Common teaching descriptions of the tala's angas and counts; to be checked by a practitioner. | medium | [ ] |
| Khanda Chapu (5) | 5 x 2 | clap - clap clap - | 1 0.2 0.7 0.7 0.2 | 2+3 | Common teaching descriptions of the tala's angas and counts; to be checked by a practitioner. | medium | [ ] |

Caveats shown with these cycles:
- Accent weights are Orin's modelling choice.
- Hand gestures are not confirmed; only the 2+3 grouping is.
- Hand gestures are not confirmed; only the 3+2+2 grouping is.
- The formal Rupaka tala is a drutam plus a laghu (6 counts); this is the 3-count form heard in performance.

### Ornaments

| Measured movement | Called | Written as | OK? |
|---|---|---|---|
| oscillation | kampita-like oscillation | a wavy mark (~) | [ ] |
| vibrato | light shake | not written | [ ] |
| glide | jaru-like slide | a slide mark (/ or \) | [ ] |
| grace | quick touch of a neighbouring note | not written | [ ] |

## Western Pop (`western_pop`)

Status: **unreviewed**. Standard theory tables; not yet checked by a second person.

What Orin tells users about this format:
- One melody line only: chords and harmony are not transcribed.
- Only the lead line is written. Chords, verse and chorus structure are not detected.
- From a short single line, pop and rock usually cannot be told apart.

### Pitch positions

| Name | Cents above the key note | Tolerance | Other readings | OK? |
|---|---|---|---|---|
| 1 | 0 | 0 |  | [ ] |
| b2 | 100 | 0 |  | [ ] |
| 2 | 200 | 0 |  | [ ] |
| b3 | 300 | 0 |  | [ ] |
| 3 | 400 | 0 |  | [ ] |
| 4 | 500 | 0 |  | [ ] |
| #4 | 600 | 0 | b5 | [ ] |
| 5 | 700 | 0 |  | [ ] |
| b6 | 800 | 0 |  | [ ] |
| 6 | 900 | 0 |  | [ ] |
| b7 | 1000 | 0 |  | [ ] |
| 7 | 1100 | 0 |  | [ ] |

### Modes

| Mode | Kind | Notes (nominal cents) | Written on | Source | Confidence | OK? |
|---|---|---|---|---|---|---|
| Major | scale | 1 (0) 2 (200) 3 (400) 4 (500) 5 (700) 6 (900) 7 (1100) |  | Standard Western music theory (12-tone equal temperament) | high | [ ] |
| Natural minor | scale | 1 (0) 2 (200) b3 (300) 4 (500) 5 (700) b6 (800) b7 (1000) |  | Standard Western music theory (12-tone equal temperament) | high | [ ] |
| Mixolydian | mode | 1 (0) 2 (200) 3 (400) 4 (500) 5 (700) 6 (900) b7 (1000) |  | Standard Western music theory (12-tone equal temperament) | high | [ ] |
| Dorian | mode | 1 (0) 2 (200) b3 (300) 4 (500) 5 (700) 6 (900) b7 (1000) |  | Standard Western music theory (12-tone equal temperament) | high | [ ] |
| Major pentatonic | scale | 1 (0) 2 (200) 3 (400) 5 (700) 6 (900) |  | Standard Western music theory (12-tone equal temperament) | high | [ ] |
| Minor pentatonic | scale | 1 (0) b3 (300) 4 (500) 5 (700) b7 (1000) |  | Standard Western music theory (12-tone equal temperament) | high | [ ] |

### Rhythm cycles

| Cycle | Beats x subdivision | Structure | Accents | Grouping | Source | Confidence | OK? |
|---|---|---|---|---|---|---|---|
| 4/4 | 4 x 4 | strong weak medium weak | 1 0.3 0.6 0.3 |  | Standard Western music theory (12-tone equal temperament) | high | [ ] |
| 6/8 | 2 x 3 | strong medium | 1 0.6 |  | Standard Western music theory (12-tone equal temperament) | high | [ ] |
| 3/4 | 3 x 4 | strong weak weak | 1 0.3 0.3 |  | Standard Western music theory (12-tone equal temperament) | high | [ ] |
| 12/8 | 4 x 3 | strong weak medium weak | 1 0.3 0.6 0.3 |  | Standard Western music theory (12-tone equal temperament) | high | [ ] |

Caveats shown with these cycles:
- Often the written form of a swung 4/4.

### Ornaments

| Measured movement | Called | Written as | OK? |
|---|---|---|---|
| vibrato | vibrato | not written | [ ] |
| oscillation | pitch oscillation | not written | [ ] |
| glide | slide | a slide line | [ ] |
| grace | grace note | a grace note | [ ] |

## Rock (`rock`)

Status: **unreviewed**. Standard theory tables; the tab fingering is a simple lowest-position choice.

What Orin tells users about this format:
- One melody line only: chords and harmony are not transcribed.
- Power chords and riffs with more than one note at a time are reduced to their top line.
- Tab is in standard tuning (E A D G B E). Fingering is one playable option, not the player's choice.
- From a short single line, pop and rock usually cannot be told apart.

### Pitch positions

| Name | Cents above the key note | Tolerance | Other readings | OK? |
|---|---|---|---|---|
| 1 | 0 | 0 |  | [ ] |
| b2 | 100 | 0 |  | [ ] |
| 2 | 200 | 0 |  | [ ] |
| b3 | 300 | 0 |  | [ ] |
| 3 | 400 | 0 |  | [ ] |
| 4 | 500 | 0 |  | [ ] |
| #4 | 600 | 0 | b5 | [ ] |
| 5 | 700 | 0 |  | [ ] |
| b6 | 800 | 0 |  | [ ] |
| 6 | 900 | 0 |  | [ ] |
| b7 | 1000 | 0 |  | [ ] |
| 7 | 1100 | 0 |  | [ ] |

### Modes

| Mode | Kind | Notes (nominal cents) | Written on | Source | Confidence | OK? |
|---|---|---|---|---|---|---|
| Minor pentatonic | scale | 1 (0) b3 (300) 4 (500) 5 (700) b7 (1000) |  | Standard Western music theory (12-tone equal temperament) | high | [ ] |
| Blues scale | scale | 1 (0) b3 (300) 4 (500) b5 (600) 5 (700) b7 (1000) |  | Standard Western music theory (12-tone equal temperament) | high | [ ] |
| Natural minor | scale | 1 (0) 2 (200) b3 (300) 4 (500) 5 (700) b6 (800) b7 (1000) |  | Standard Western music theory (12-tone equal temperament) | high | [ ] |
| Mixolydian | mode | 1 (0) 2 (200) 3 (400) 4 (500) 5 (700) 6 (900) b7 (1000) |  | Standard Western music theory (12-tone equal temperament) | high | [ ] |
| Dorian | mode | 1 (0) 2 (200) b3 (300) 4 (500) 5 (700) 6 (900) b7 (1000) |  | Standard Western music theory (12-tone equal temperament) | high | [ ] |
| Major | scale | 1 (0) 2 (200) 3 (400) 4 (500) 5 (700) 6 (900) 7 (1100) |  | Standard Western music theory (12-tone equal temperament) | high | [ ] |
| Major pentatonic | scale | 1 (0) 2 (200) 3 (400) 5 (700) 6 (900) |  | Standard Western music theory (12-tone equal temperament) | high | [ ] |

Caveats shown with these modes:
- Blue notes are bent between these pitches in practice, by anything from a quarter tone to a semitone.

### Rhythm cycles

| Cycle | Beats x subdivision | Structure | Accents | Grouping | Source | Confidence | OK? |
|---|---|---|---|---|---|---|---|
| 4/4 | 4 x 4 | strong weak medium weak | 1 0.3 0.6 0.3 |  | Standard Western music theory (12-tone equal temperament) | high | [ ] |
| 6/8 | 2 x 3 | strong medium | 1 0.6 |  | Standard Western music theory (12-tone equal temperament) | high | [ ] |
| 12/8 | 4 x 3 | strong weak medium weak | 1 0.3 0.6 0.3 |  | Standard Western music theory (12-tone equal temperament) | high | [ ] |

Caveats shown with these cycles:
- Often the written form of a swung 4/4.

### Ornaments

| Measured movement | Called | Written as | OK? |
|---|---|---|---|
| vibrato | vibrato | a vibrato mark (~) in the tab | [ ] |
| oscillation (0-120 cents) | wide vibrato | a vibrato mark (~) in the tab | [ ] |
| oscillation (120-any cents) | pitch oscillation | not written | [ ] |
| glide (0-250 cents) | bend | a string bend (b) or release (r) in the tab | [ ] |
| glide (250-any cents) | slide | a slide (/ or \) in the tab | [ ] |
| grace | grace note | a hammer-on (h) or pull-off (p) in the tab | [ ] |

## Arabic Maqam (`arabic_maqam`)

Status: **unreviewed**. Draft tables derived from MaqamWorld's ajnas. Not reviewed by a practitioner.

What Orin tells users about this format:
- Draft, not reviewed by a practitioner.
- Maqam tuning varies by region and performer. The 24-step grid here is for naming notes, not for tuning them.
- A maqam is more than a scale: its ajnas, its modulations and its typical paths are not modelled.
- Each maqam is written at its conventional pitch (Rast on C, Bayati on D); the sounding pitch is stated beside it.
- The rhythmic cycle (iqa') cannot be identified from a melody. Choose it yourself if you know it.
- One melody line only.

### Pitch positions

| Name | Cents above the tonic | Tolerance | Other readings | OK? |
|---|---|---|---|---|
| 1 | 0 | 25 |  | [ ] |
| 1+ | 50 | 25 |  | [ ] |
| b2 | 100 | 25 |  | [ ] |
| 2- | 150 | 25 |  | [ ] |
| 2 | 200 | 25 |  | [ ] |
| 2+ | 250 | 25 |  | [ ] |
| b3 | 300 | 25 |  | [ ] |
| 3- | 350 | 25 |  | [ ] |
| 3 | 400 | 25 |  | [ ] |
| 4- | 450 | 25 |  | [ ] |
| 4 | 500 | 25 |  | [ ] |
| 4+ | 550 | 25 |  | [ ] |
| #4 | 600 | 25 |  | [ ] |
| 5- | 650 | 25 |  | [ ] |
| 5 | 700 | 25 |  | [ ] |
| 5+ | 750 | 25 |  | [ ] |
| b6 | 800 | 25 |  | [ ] |
| 6- | 850 | 25 |  | [ ] |
| 6 | 900 | 25 |  | [ ] |
| 6+ | 950 | 25 |  | [ ] |
| b7 | 1000 | 25 |  | [ ] |
| 7- | 1050 | 25 |  | [ ] |
| 7 | 1100 | 25 |  | [ ] |
| 7+ | 1150 | 25 |  | [ ] |

### Modes

| Mode | Kind | Notes (nominal cents) | Written on | Source | Confidence | OK? |
|---|---|---|---|---|---|---|
| Maqam Rast | maqam | 1 (0) 2 (200) 3- (350) 4 (500) 5 (700) 6 (900) 7- (1050); also: b7 (1000) | C | Ajnas from MaqamWorld; cents derived from the ajnas on a nominal 24-step grid at the conventional tonic. To be copied from MaqamWorld or Inside Arabic Music by a practitioner. | medium | [ ] |
| Maqam Bayati | maqam | 1 (0) 2- (150) b3 (300) 4 (500) 5 (700) b6 (800) b7 (1000); also: 6- (850) | D | Ajnas from MaqamWorld; cents derived from the ajnas on a nominal 24-step grid at the conventional tonic. To be copied from MaqamWorld or Inside Arabic Music by a practitioner. | medium | [ ] |
| Maqam Hijaz | maqam | 1 (0) b2 (100) 3 (400) 4 (500) 5 (700) b6 (800) b7 (1000); also: 6- (850) | D | Ajnas from MaqamWorld; cents derived from the ajnas on a nominal 24-step grid at the conventional tonic. To be copied from MaqamWorld or Inside Arabic Music by a practitioner. | medium | [ ] |
| Maqam Nahawand | maqam | 1 (0) 2 (200) b3 (300) 4 (500) 5 (700) b6 (800) 7 (1100); also: b7 (1000) | C | Ajnas from MaqamWorld; cents derived from the ajnas on a nominal 24-step grid at the conventional tonic. To be copied from MaqamWorld or Inside Arabic Music by a practitioner. | medium | [ ] |
| Maqam Kurd | maqam | 1 (0) b2 (100) b3 (300) 4 (500) 5 (700) b6 (800) b7 (1000) | D | Ajnas from MaqamWorld; cents derived from the ajnas on a nominal 24-step grid at the conventional tonic. To be copied from MaqamWorld or Inside Arabic Music by a practitioner. | medium | [ ] |
| Maqam Ajam | maqam | 1 (0) 2 (200) 3 (400) 4 (500) 5 (700) 6 (900) 7 (1100); also: b7 (1000) | B -100 cents | Ajnas from MaqamWorld; cents derived from the ajnas on a nominal 24-step grid at the conventional tonic. To be copied from MaqamWorld or Inside Arabic Music by a practitioner. | medium | [ ] |
| Maqam Saba | maqam | 1 (0) 2- (150) b3 (300) b4 (400) 5 (700) b6 (800) b7 (1000) | D | Ajnas from MaqamWorld; cents derived from the ajnas on a nominal 24-step grid at the conventional tonic. To be copied from MaqamWorld or Inside Arabic Music by a practitioner. | medium | [ ] |
| Maqam Huzam | maqam | 1 (0) 2- (150) 3- (350) 4- (450) 5+ (750) 6- (850) 7- (1050) | E -50 cents | Ajnas from MaqamWorld; cents derived from the ajnas on a nominal 24-step grid at the conventional tonic. To be copied from MaqamWorld or Inside Arabic Music by a practitioner. | medium | [ ] |

Caveats shown with these modes:
- Half-flat degrees are nominal: their real tuning depends on the maqam, the region and the performer.
- The upper part often uses a lowered 7th, especially descending; this is a tendency, not a rule.
- Its 2nd is reported slightly lower than the half-flat of Rast.
- The wide step between the 2nd and 3rd is usually played narrower than written.
- Also taught on C.
- Saba does not repeat at the octave: the notes above are not copies of the lower ones. Only the lower octave is modelled.
- Its tonic is itself a half-flat note. Huzam is the common member of the Sikah family; Sikah itself is rarer and not included.

### Rhythm cycles

| Cycle | Beats x subdivision | Structure | Accents | Grouping | Source | Confidence | OK? |
|---|---|---|---|---|---|---|---|
| Maqsum (4/4) | 4 x 2 | D T - T D - T - | 1 0.6 0 0.6 1 0 0.6 0 |  | Stroke patterns cross-checked between two rhythm libraries; MaqamWorld is the authority to verify against. | high | [ ] |
| Baladi (4/4) | 4 x 2 | D D - T D - T - | 1 1 0 0.6 1 0 0.6 0 |  | Stroke patterns cross-checked between two rhythm libraries; MaqamWorld is the authority to verify against. | high | [ ] |
| Wahda (4/4) | 4 x 2 | D - - T - - T - | 1 0 0 0.6 0 0 0.6 0 |  | Stroke patterns cross-checked between two rhythm libraries; MaqamWorld is the authority to verify against. | medium | [ ] |
| Malfuf (2/4) | 2 x 4 | D - - T - - T - | 1 0 0 0.6 0 0 0.6 0 |  | Stroke patterns cross-checked between two rhythm libraries; MaqamWorld is the authority to verify against. | high | [ ] |
| Sama'i Thaqil (10/8) | 10 x 1 | D - - T - D D T - - | 1 0 0 0.6 0 1 1 0.6 0 0 |  | Stroke patterns cross-checked between two rhythm libraries; MaqamWorld is the authority to verify against. | medium | [ ] |

Caveats shown with these cycles:
- D = dum, T = tak, - = rest. Accent weights are Orin's modelling choice.
- Naming varies by region.

### Ornaments

| Measured movement | Called | Written as | OK? |
|---|---|---|---|
| oscillation | shake | a wavy line | [ ] |
| vibrato | vibrato | not written | [ ] |
| glide | slide | a slide line | [ ] |
| grace | grace note | a grace note | [ ] |

## Persian (Dastgah) (`persian_dastgah`)

Status: **unreviewed**. Draft computed from secondary summaries of Farhat. The least verified table in Orin.

What Orin tells users about this format:
- Draft, not reviewed by a practitioner, and the least verified table in Orin.
- A dastgah is a family of melodic models (gusheh-ha), not a scale. Only the opening area is modelled, so a change of gusheh will look like notes outside the mode.
- Koron and sori are not exact quarter tones, and their size varies.
- The reference note is called the finalis. Each mode is written at a conventional pitch; the sounding pitch is stated beside it.
- Persian and Arabic music usually cannot be told apart from a short clip.
- One melody line only.

### Pitch positions

| Name | Cents above the finalis | Tolerance | Other readings | OK? |
|---|---|---|---|---|
| 1 | 0 | 25 |  | [ ] |
| 1+ | 50 | 25 |  | [ ] |
| b2 | 100 | 25 |  | [ ] |
| 2- | 150 | 25 |  | [ ] |
| 2 | 200 | 25 |  | [ ] |
| 2+ | 250 | 25 |  | [ ] |
| b3 | 300 | 25 |  | [ ] |
| 3- | 350 | 25 |  | [ ] |
| 3 | 400 | 25 |  | [ ] |
| 4- | 450 | 25 |  | [ ] |
| 4 | 500 | 25 |  | [ ] |
| 4+ | 550 | 25 |  | [ ] |
| #4 | 600 | 25 |  | [ ] |
| 5- | 650 | 25 |  | [ ] |
| 5 | 700 | 25 |  | [ ] |
| 5+ | 750 | 25 |  | [ ] |
| b6 | 800 | 25 |  | [ ] |
| 6- | 850 | 25 |  | [ ] |
| 6 | 900 | 25 |  | [ ] |
| 6+ | 950 | 25 |  | [ ] |
| b7 | 1000 | 25 |  | [ ] |
| 7- | 1050 | 25 |  | [ ] |
| 7 | 1100 | 25 |  | [ ] |
| 7+ | 1150 | 25 |  | [ ] |

### Modes

| Mode | Kind | Notes (nominal cents) | Written on | Source | Confidence | OK? |
|---|---|---|---|---|---|---|
| Dastgah-e Shur | dastgah | 1 (0) 2 koron (135) b3 (295) 4 (499) 5 (703) b6 (793) b7 (997); also: 5 koron (634) | D | Interval classes after Hormoz Farhat (semitone ~90, small neutral second ~135, large neutral second ~160, whole tone ~204, plus tone ~270 cents) as given in secondary summaries; scale rows read from an unattributed reference table. To be checked against Farhat (1990). | low | [ ] |
| Dastgah-e Mahur | dastgah | 1 (0) 2 (204) 3 (408) 4 (498) 5 (702) 6 (906) 7 (1110) | C | Interval classes after Hormoz Farhat (semitone ~90, small neutral second ~135, large neutral second ~160, whole tone ~204, plus tone ~270 cents) as given in secondary summaries; scale rows read from an unattributed reference table. To be checked against Farhat (1990). | medium | [ ] |
| Dastgah-e Homayun | dastgah | 1 (0) 2 koron (135) 3 (405) 4 (498) 5 (702) b6 (792) b7 (996) | G | Interval classes after Hormoz Farhat (semitone ~90, small neutral second ~135, large neutral second ~160, whole tone ~204, plus tone ~270 cents) as given in secondary summaries; scale rows read from an unattributed reference table. To be checked against Farhat (1990). | low | [ ] |
| Avaz-e Bayat-e Esfahan | avaz | 1 (0) 2 (204) b3 (294) 4 (498) 5 (702) 6 koron (837) 7 (1107) | G | Interval classes after Hormoz Farhat (semitone ~90, small neutral second ~135, large neutral second ~160, whole tone ~204, plus tone ~270 cents) as given in secondary summaries; scale rows read from an unattributed reference table. To be checked against Farhat (1990). | low | [ ] |
| Dastgah-e Chahargah | dastgah | 1 (0) 2 koron (135) 3 (405) 4 (498) 5 (702) 6 koron (837) 7 (1107) | C | Interval classes after Hormoz Farhat (semitone ~90, small neutral second ~135, large neutral second ~160, whole tone ~204, plus tone ~270 cents) as given in secondary summaries; scale rows read from an unattributed reference table. To be checked against Farhat (1990). | low | [ ] |
| Dastgah-e Segah | dastgah | 1 (0) 2- (160) 3- (364) 4 (499) 5 koron (659) 6- (863) 7- (1067); also: b7 (998) | E -60 cents | Interval classes after Hormoz Farhat (semitone ~90, small neutral second ~135, large neutral second ~160, whole tone ~204, plus tone ~270 cents) as given in secondary summaries; scale rows read from an unattributed reference table. To be checked against Farhat (1990). | low | [ ] |
| Dastgah-e Nava | dastgah | 1 (0) 2 (204) b3 (294) 4 (498) 5 (702) 6 koron (837) b7 (997) | G | Interval classes after Hormoz Farhat (semitone ~90, small neutral second ~135, large neutral second ~160, whole tone ~204, plus tone ~270 cents) as given in secondary summaries; scale rows read from an unattributed reference table. To be checked against Farhat (1990). | low | [ ] |

Caveats shown with these modes:
- Neutral degrees here are flexible in performance; the cents are nominal.
- The 5th is usually lowered by a microtone in descent; that is part of Shur, not a wrong note.
- Shares its pitches with the Western major scale; it is not the same thing.
- The wide step between the 2nd and 3rd is unstable in size.
- Classed as an avaz of Homayun in the 7 + 5 scheme; some theorists treat it as independent. Its relation to Homayun as written here is Orin's derivation.
- Both wide steps are unstable in size.
- The finalis is itself a koron note. Its lowered 5th separates it from the Arabic Sikah family; the two should not be equated.

### Rhythm cycles

| Cycle | Beats x subdivision | Structure | Accents | Grouping | Source | Confidence | OK? |
|---|---|---|---|---|---|---|---|
| 6/8 | 2 x 3 | strong medium | 1 0.6 |  | Plain meters, not named cycles. Reng is commonly in 6/8; other attributions are unverified. | medium | [ ] |
| 2/4 | 2 x 4 | strong weak | 1 0.4 |  | Plain meters, not named cycles. Reng is commonly in 6/8; other attributions are unverified. | low | [ ] |
| 4/4 | 4 x 4 | strong weak medium weak | 1 0.3 0.6 0.3 |  | Plain meters, not named cycles. Reng is commonly in 6/8; other attributions are unverified. | low | [ ] |

### Ornaments

| Measured movement | Called | Written as | OK? |
|---|---|---|---|
| oscillation | shake | a wavy line | [ ] |
| vibrato | vibrato | not written | [ ] |
| glide | slide | a slide line | [ ] |
| grace | grace note | a grace note | [ ] |
