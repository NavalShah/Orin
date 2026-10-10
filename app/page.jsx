"use client";

import { useEffect, useRef, useState } from "react";

const startingNotes = [
  { s: "Sa", n: 60, y: 56, d: .42 }, { s: "Ri", n: 62, y: 46, d: .42 },
  { s: "Ga", n: 64, y: 35, d: .42 }, { s: "Ma", n: 65, y: 25, d: .42 },
  { s: "Pa", n: 67, y: 15, d: .42 }, { s: "Ma", n: 65, y: 25, d: .42 },
  { s: "Ga", n: 64, y: 35, d: .42 }, { s: "Ri", n: 62, y: 46, d: .68 },
];
const pitchNames = ["C", "C♯", "D", "D♯", "E", "F", "F♯", "G", "G♯", "A", "A♯", "B"];
const swaras = ["Sa", "Ri", "Ga", "Ma", "Pa", "Dha", "Ni", "Sa"];
const whiteNotes = [60, 62, 64, 65, 67, 69, 71, 72, 74, 76, 77, 79, 81, 83];
const keyOffsets = { C: 0, D: 2, G: 7, Am: -3 };
const pitchName = (m) => `${pitchNames[((m % 12) + 12) % 12]}${Math.floor(m / 12) - 1}`;

function Icon({ children, className = "" }) { return <span aria-hidden="true" className={`icon ${className}`}>{children}</span>; }

export default function Home() {
  const [notes, setNotes] = useState(startingNotes);
  const [selected, setSelected] = useState(-1);
  const [editing, setEditing] = useState(false);
  const [key, setKey] = useState("C");
  const [toast, setToast] = useState("");
  const [modal, setModal] = useState(false);
  const audioRef = useRef(null);
  const timers = useRef([]);
  const toastTimer = useRef(null);

  useEffect(() => () => { timers.current.forEach(clearTimeout); clearTimeout(toastTimer.current); }, []);
  function notify(message) {
    setToast(message); clearTimeout(toastTimer.current);
    toastTimer.current = setTimeout(() => setToast(""), 2200);
  }
  function tone(midi, start = 0, duration = .4, velocity = .13) {
    const Audio = window.AudioContext || window.webkitAudioContext;
    if (!Audio) { notify("Audio is not supported in this browser"); return; }
    if (!audioRef.current) audioRef.current = new Audio();
    const ctx = audioRef.current;
    if (ctx.state === "suspended") ctx.resume();
    const osc = ctx.createOscillator(), gain = ctx.createGain(), at = ctx.currentTime + start;
    osc.type = "triangle"; osc.frequency.value = 440 * 2 ** ((midi - 69) / 12);
    gain.gain.setValueAtTime(.0001, at); gain.gain.exponentialRampToValueAtTime(velocity, at + .018);
    gain.gain.exponentialRampToValueAtTime(.0001, at + duration);
    osc.connect(gain); gain.connect(ctx.destination); osc.start(at); osc.stop(at + duration + .03);
  }
  function playPhrase() {
    timers.current.forEach(clearTimeout); timers.current = [];
    let cursor = 0;
    notes.forEach((note, i) => {
      tone(note.n, cursor, note.d); const delay = cursor * 1000;
      timers.current.push(setTimeout(() => setSelected(i), delay)); cursor += note.d + .045;
    });
    timers.current.push(setTimeout(() => setSelected(-1), cursor * 1000));
  }
  function changeKey(value) {
    const offset = keyOffsets[value] ?? 0;
    setKey(value); setNotes((current) => current.map((note, i) => ({ ...note, n: startingNotes[i % startingNotes.length].n + offset })));
    notify(`Piano sketch moved to ${value === "Am" ? "A minor" : `${value} major`}`);
  }
  function selectNote(i) {
    setSelected(i);
    if (editing) {
      setNotes((current) => current.map((note, index) => index === i ? { ...note, n: note.n + 1, s: swaras[(i + 1) % swaras.length], y: Math.max(4, note.y - 7) } : note));
      notify("Pitch updated");
    } else tone(notes[i].n);
  }
  function addNote() {
    if (notes.length >= 12) return notify("This sketch supports up to 12 notes");
    const prev = notes.at(-1); setNotes((current) => [...current, { s: "Sa", n: prev.n + 2, y: prev.y, d: .42 }]);
    notify("Added a note to your melody");
  }
  function download() {
    const data = { title: "Carnatic melody — piano sketch", source: "Carnatic-inspired sargam phrase", target: "Piano", key, notes: notes.map((n) => ({ sargam: n.s, pitch: pitchName(n.n), midi: n.n, durationSeconds: n.d })) };
    const link = document.createElement("a"); link.href = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }));
    link.download = "raga-to-keys-sketch.json"; link.click(); URL.revokeObjectURL(link.href); notify("Sketch saved as a JSON file");
  }

  return <main className="app-shell">
    <aside className="sidebar">
      <a className="brand" href="#top"><span className="brand-mark">r<span>↗</span></span><span>raga<span className="brand-light">to</span>keys</span></a>
      <div className="side-label">YOUR WORKSPACE</div>
      <button className="side-link active"><Icon>◉</Icon> Studio</button>
      <button className="side-link" onClick={() => document.querySelector("#learn-card")?.scrollIntoView({ behavior: "smooth" })}><Icon>◈</Icon> Learn the sounds <span className="new-tag">NEW</span></button>
      <div className="side-label recent-label">RECENT SKETCHES <button onClick={() => notify("Start a new sketch by editing this phrase")} aria-label="Add sketch">＋</button></div>
      <button className="recent-item"><i className="recent-dot" />Morning phrase<small>2 min ago</small></button>
      <button className="recent-item"><i className="recent-dot dot-lilac" />Untitled sketch<small>Yesterday</small></button>
      <div className="sidebar-bottom"><span className="help-icon">✳</span><div><strong>A space to explore</strong><p>Every tradition has something to teach us.</p></div><span className="bottom-arrow">↗</span></div>
    </aside>
    <div className="main-area" id="top">
      <header className="topbar"><div className="breadcrumbs">Studio <span>/</span> <strong>New sketch</strong></div><div className="top-actions"><span className="save-state"><i /> All changes saved</span><button className="avatar" aria-label="Profile">A</button></div></header>
      <div className="content">
        <section className="intro"><div className="intro-copy-wrap"><div className="eyebrow"><i /> YOUR MUSICAL BRIDGE</div><h1>One melody.<br /><em>Two ways to hear it.</em></h1><p className="intro-copy">Explore a Carnatic phrase, shape it into a piano sketch, and learn what makes each tradition distinct.</p></div><div className="intro-art" aria-hidden="true"><div className="art-ring ring-one"/><div className="art-ring ring-two"/><span className="art-note note-a">♪</span><span className="art-note note-b">♫</span><div className="art-center">ஸா<span>SA</span></div><div className="art-caption">LISTEN · EXPLORE · PLAY</div></div></section>

        <section className="workspace-card">
          <div className="card-heading"><div><div className="section-kicker">01 <span>·</span> START WITH A PHRASE</div><h2>Your source melody</h2></div><button className="text-button" onClick={() => { setNotes(startingNotes); setSelected(-1); setKey("C"); notify("Example phrase restored"); }}><Icon>↺</Icon> Reset example</button></div>
          <div className="source-row"><div className="source-badge"><span className="source-symbol">ச</span><span><strong>Carnatic</strong><small>South Indian classical</small></span></div><div className="source-description"><span className="music-chip">Raga-inspired phrase</span><span className="music-chip">{notes.length} notes</span><span className="music-chip">Adi tala feel</span><button className="info-button" title="This is a learning sketch, not a full transcription" onClick={() => setModal(true)}>i</button></div><button className="listen-button" onClick={playPhrase}><Icon>▶</Icon> Listen</button></div>
          <div className="phrase-label"><span>MELODY SKETCH</span><span className="edit-hint">Click a note to hear it · add a note with +</span></div>
          <div className="notation-wrap"><div className="staff"><div className="staff-lines">{[0,1,2,3,4].map((x) => <i key={x}/>)}</div><div className="treble">𝄞</div><div className="notes">{notes.map((note, i) => <button key={i} className={`note ${selected === i ? "selected" : ""}`} style={{ "--note-y": `${note.y}px` }} aria-label={`${note.s}, ${pitchName(note.n)}. Click to change pitch.`} onClick={() => selectNote(i)}><span className="swara">{note.s}</span><span className="western">{pitchName(note.n)}</span></button>)}</div><button className="add-note" title="Add a note" onClick={addNote}>+</button><div className="beat-markers"><span>1</span><span>2</span><span>3</span><span>4</span></div></div></div>
          <div className="notation-foot"><span><i className="legend-dot"/> Sargam syllables <b>·</b> approximate pitch mapping</span><button className={`subtle-button ${editing ? "editing-on" : ""}`} onClick={() => { setEditing(!editing); notify(editing ? "Melody edit complete" : "Select a note to step its pitch upward"); }}><Icon>✎</Icon> {editing ? "Done editing" : "Edit melody"}</button></div>
          <div className="conversion-divider"><span/><div className="convert-icon">↓</div><span/></div>
          <div className="card-heading target-heading"><div><div className="section-kicker">02 <span>·</span> FIND YOUR WAY INTO IT</div><h2>Make it playable on piano</h2></div><label className="key-select"><span>KEY</span><select value={key} onChange={(e) => changeKey(e.target.value)} aria-label="Choose piano key"><option value="C">C major</option><option value="D">D major</option><option value="G">G major</option><option value="Am">A minor</option></select></label></div>
          <div className="piano-panel"><div className="piano-topline"><div><span className="piano-label">PIANO SKETCH</span><span className="piano-subtitle">A simple starting point for your instrument</span></div><div className="play-controls"><button className="play-button" onClick={playPhrase}><Icon>▶</Icon> Play sketch</button><button className="icon-button" title="Stop playback" onClick={() => { timers.current.forEach(clearTimeout); timers.current = []; setSelected(-1); }}>■</button></div></div><div className="piano-roll">{notes.map((note, i) => <button key={i} className={`roll-note ${selected === i ? "roll-active" : ""}`} onClick={() => { setSelected(i); tone(note.n); }} title={`${note.s} — play note`}>{pitchName(note.n)}</button>)}</div><div className="keyboard" aria-label="Playable piano keyboard">{whiteNotes.map((m, i) => <span className="white-slot" key={m}><button className={`key ${selected >= 0 && notes[selected]?.n === m ? "key-active" : ""}`} aria-label={`Play ${pitchName(m)}`} onClick={() => { tone(m, 0, .5, .15); setSelected(-1); }}>{pitchName(m)}</button>{i < whiteNotes.length - 1 && whiteNotes[i + 1] - m === 2 && <button className="black-key" style={{ left: "100%" }} aria-label={`Play ${pitchName(m + 1)}`} onClick={() => tone(m + 1, 0, .5, .15)}/>}</span>)}</div><div className="piano-foot"><span><i className="legend-dot piano-dot"/> Select a note above to hear it on piano</span><button className="subtle-button" onClick={download}><Icon>↓</Icon> Save sketch</button></div></div>
        </section>

        <section className="learn-card" id="learn-card"><div className="learn-icon">✳</div><div className="learn-copy"><div className="section-kicker">A LITTLE CONTEXT</div><h3>What travels with the melody?</h3><p>We keep the shape and movement of the phrase as we map it to piano. The original ornaments and expressive pitch bends are part of the Carnatic performance; this first sketch simplifies them so you can find the notes on a keyboard.</p><button className="learn-link" onClick={() => setModal(true)}>Explore the musical ideas <span>↗</span></button></div><div className="learn-side"><div className="learn-stat"><span>SOURCE</span><strong>Voice / instrument</strong></div><div className="learn-stat"><span>ARRANGED FOR</span><strong>Western piano</strong></div><div className="learn-stat"><span>WHAT CHANGES</span><strong>Ornaments simplified</strong></div></div></section>
        <footer>Made for curious ears <span>·</span> Music is a conversation across traditions.</footer>
      </div>
    </div>
    {toast && <div className="toast" role="status">{toast}</div>}
    {modal && <div className="modal-backdrop" role="presentation" onMouseDown={(e) => { if (e.target === e.currentTarget) setModal(false); }}><section className="modal" role="dialog" aria-modal="true" aria-labelledby="modal-title"><button className="modal-close" onClick={() => setModal(false)} aria-label="Close">×</button><div className="section-kicker">MUSICAL IDEAS</div><h2 id="modal-title">Hear the shape, then the details.</h2><p>This sketch maps a short sargam-style phrase onto nearby piano notes. It keeps the up-and-down contour so a pianist can explore the melody, while simplifying features that need more nuanced notation.</p><div className="idea-grid"><div><span>01</span><strong>Pitch movement</strong><p>Follow the phrase as it rises and settles.</p></div><div><span>02</span><strong>Rhythmic feel</strong><p>Notice the repeating pulse underneath the melody.</p></div><div><span>03</span><strong>Ornamentation</strong><p>Small slides and turns give a performance its voice.</p></div></div><p className="modal-note">This is an educational sketch, not a definitive transcription. A musician’s interpretation brings details that a piano roll cannot capture.</p></section></div>}
  </main>;
}
