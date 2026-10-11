import Link from "next/link";
import { HeroBridge } from "./HeroBridge";
import { Reveal } from "./Reveal";
import { Traditions } from "./Traditions";
import css from "./home.module.css";

const PARTS = [
  {
    href: "/chat",
    kicker: "01",
    verb: "ASK",
    title: "Chat",
    line: "Ask about a tradition, or describe a piece of music and have it generated for you.",
    cta: "Open the Chat",
  },
  {
    href: "/studio",
    kicker: "02",
    verb: "WRITE",
    title: "Notation Studio",
    line: "Give it a recording or typed notes. Choose how to read it and how to write it, and see what the new notation could not carry.",
    cta: "Open the Notation Studio",
  },
  {
    href: "/workspace",
    kicker: "03",
    verb: "MAKE",
    title: "Workspace",
    line: "Arrange clips, edit notes in a piano roll, add drums and export. Anything from the other two can be sent here.",
    cta: "Open the Workspace",
  },
];

// Taken from a real Orin run on a synthesised phrase (Carnatic read, Western staff written).
const SAMPLE_LOSS = [
  { level: "Major loss", mark: "▲", text: "The pitch movement on this note, about 142 cents wide and 5 times a second, has no symbol on a staff and is shown as a plain note." },
  { level: "Minor loss", mark: "△", text: "The slide into this note is marked as a line, which says it happens but not how wide, fast or shaped it is." },
  { level: "For information", mark: "ⓘ", text: "The staff uses a different tuning reference, so 5 notes shift by up to 12 cents. The notes are the same; the tuning system is not." },
];

/** The public front door: what Orin is, the idea in one small interaction, and the three ways in. */
export function Home() {
  return (
    <div className={css.page}>
      <a className={css.skip} href="#orin-home-main">
        Skip to content
      </a>
      <header className={css.top}>
        <Link href="/" className={css.brand} aria-label="Orin, home">
          <span className={css.brandMark} aria-hidden="true">
            o<span>↗</span>
          </span>
          <span>orin</span>
        </Link>
        <nav aria-label="Orin" className={css.topNav}>
          <Link href="/chat">Chat</Link>
          <Link href="/studio">Notation Studio</Link>
          <Link href="/workspace">Workspace</Link>
          <Link href="/studio" className={css.open}>
            Open Orin
          </Link>
        </nav>
      </header>

      <main id="orin-home-main" className={css.main}>
        <section className={css.hero} aria-labelledby="orin-home-title">
          <div className={css.heroCopy}>
            <div className={css.eyebrow}>
              <i aria-hidden="true" /> YOUR MUSICAL BRIDGE
            </div>
            <h1 id="orin-home-title">
              One melody.
              <br />
              <em>Two ways to hear it.</em>
            </h1>
            <p className={css.lede}>
              Orin converts, transcribes, and generates music from one music tradition to another. We empower cross-cultural collaboration, professional music production, and music appreciation.
            </p>
            <div className={css.heroActions}>
              <Link href="/studio" className={css.primary}>
                Open the Notation Studio
              </Link>
              <a href="#orin-home-parts" className={css.secondary}>
                See the three parts
              </a>
            </div>
          </div>
          <div className={css.heroArt} aria-hidden="true">
            <div className={`${css.ring} ${css.ringOne}`} />
            <div className={`${css.ring} ${css.ringTwo}`} />
            <span className={`${css.artNote} ${css.noteA}`}>♪</span>
            <span className={`${css.artNote} ${css.noteB}`}>♫</span>
            <div className={css.artCenter}>
              ஸா<span>SA</span>
            </div>
            <div className={css.artCaption}>LISTEN · COMPARE · PLAY</div>
          </div>
        </section>

        <section className={css.card} aria-labelledby="orin-home-try">
          <div className={css.kicker}>
            TRY IT <span>·</span> NO SIGN-IN NEEDED
          </div>
          <h2 id="orin-home-try">Converting Microtones to Western Staff Notation</h2>
          <HeroBridge />
        </section>

        <Reveal>
          <section id="orin-home-parts" aria-labelledby="orin-home-parts-title" className={css.section}>
            <div className={css.kicker}>
              THREE WAYS IN <span>·</span> EACH HANDS ITS WORK TO THE NEXT
            </div>
            <h2 id="orin-home-parts-title">Ask, write, make</h2>
            <ol className={css.parts}>
              {PARTS.map((part) => (
                <li key={part.href}>
                  <Link href={part.href} className={css.part}>
                    <span className={css.partKicker}>
                      {part.kicker} <span>·</span> {part.verb}
                    </span>
                    <strong>{part.title}</strong>
                    <span className={css.partLine}>{part.line}</span>
                    <span className={css.partCta}>
                      {part.cta} <span aria-hidden="true">↗</span>
                    </span>
                  </Link>
                </li>
              ))}
            </ol>
          </section>
        </Reveal>

        <Reveal>
          <section className={css.split} aria-labelledby="orin-home-loss">
            <div>
              <div className={css.kicker}>THE LOSS REPORT</div>
              <h2 id="orin-home-loss">Every conversion says what it left out</h2>
              <p className={css.body}>
                A staff has twelve pitches in an octave and no sign for a slow, wide oscillation. Other styles of music use different notation. When
                Orin transcribes a melody, it makes sure to preserve the original intent of the melody, creating arrangements that facilitate multi-cultural collaboration.
              </p>
              <p className={css.body}>
                Transcription is approximate by design and follows one melody line. Orin shows how sure it is, and you can
                always set the tonic, key, and tempo yourself.
              </p>
            </div>
            <div className={css.sample} role="group" aria-label="Example loss report">
              <p className={css.sampleHead}>
                <span>Example</span> Carnatic, written as Western staff
              </p>
              <p className={css.fidelity}>
                Fidelity <strong>0.87</strong> <span>out of 1</span>
              </p>
              <ul>
                {SAMPLE_LOSS.map((item) => (
                  <li key={item.level}>
                    <span className={css.level}>
                      <span aria-hidden="true">{item.mark}</span> {item.level}
                    </span>
                    {item.text}
                  </li>
                ))}
              </ul>
            </div>
          </section>
        </Reveal>

        <Reveal>
          <section className={css.section} aria-labelledby="orin-home-traditions">
            <div className={css.kicker}>
              SIX TRADITIONS <span>·</span> ANY ONE TO ANY OTHER
            </div>
            <h2 id="orin-home-traditions">What Orin can read and write</h2>
            <Traditions />
            <p className={css.honest}>
              The tables behind these (ragas, talas, maqamat, dastgah-ha) are drafts written from references. They are marked
              unreviewed until a practitioner of each tradition has checked them.
            </p>
          </section>
        </Reveal>
      </main>

      <footer className={css.footer}>
        Made for curious ears <span>·</span> Music is a conversation across traditions.
      </footer>
    </div>
  );
}
