"use client";

import {
  createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type FormEvent, type ReactNode,
} from "react";
import { api, errorText, getToken, onAuthChange, type User, type UserPrefs } from "@/lib/api";
import css from "./Shell.module.css";

export type SessionValue = {
  user: User;
  prefs: UserPrefs;
  /** Saves on the server; the change shows at once and is rolled back if the save fails. */
  updatePrefs: (prefs: Partial<UserPrefs>) => Promise<void>;
  signOut: () => Promise<void>;
  prefsError: string | null;
};

const SessionContext = createContext<SessionValue | null>(null);

export function useSession(): SessionValue {
  const value = useContext(SessionContext);
  if (!value) throw new Error("useSession must be used inside the Orin shell, after sign-in.");
  return value;
}

/** For tests and stories: provide a session without the gate. */
export function SessionProvider({ value, children }: { value: SessionValue; children: ReactNode }) {
  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

type Phase = "checking" | "anonymous" | "ready" | "unreachable";

/** Shows the profile picker until there is a session, then renders the app (spec 10.2). */
export function SessionGate({ children }: { children: ReactNode }) {
  const [phase, setPhase] = useState<Phase>("checking");
  const [user, setUser] = useState<User | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [prefsError, setPrefsError] = useState<string | null>(null);
  const userRef = useRef<User | null>(null);
  userRef.current = user;

  const check = useCallback(() => {
    if (!getToken()) {
      setUser(null);
      setPhase("anonymous");
      return;
    }
    setPhase("checking");
    api
      .me()
      .then((me) => {
        setUser(me);
        setPhase("ready");
      })
      .catch((err) => {
        if (!getToken()) {
          // The server rejected the token; api.ts already cleared it.
          setUser(null);
          setPhase("anonymous");
          return;
        }
        setError(errorText(err));
        setPhase("unreachable");
      });
  }, []);

  useEffect(() => {
    check();
    // Sign-out, or a rejected token, from anywhere in the app.
    return onAuthChange(() => {
      if (!getToken()) {
        setUser(null);
        setPhase("anonymous");
      }
    });
  }, [check]);

  const updatePrefs = useCallback(async (prefs: Partial<UserPrefs>) => {
    const before = userRef.current;
    if (!before) return;
    setPrefsError(null);
    setUser({ ...before, prefs: { ...before.prefs, ...prefs } });
    try {
      setUser(await api.updatePrefs(prefs));
    } catch (err) {
      setUser(before);
      setPrefsError(`Your preference was not saved. ${errorText(err)}`);
    }
  }, []);

  const signOut = useCallback(async () => {
    try {
      await api.signOut();
    } catch {
      // api.signOut clears the token even when the request fails.
    }
  }, []);

  const value = useMemo<SessionValue | null>(
    () => (user ? { user, prefs: user.prefs, updatePrefs, signOut, prefsError } : null),
    [user, updatePrefs, signOut, prefsError],
  );

  if (phase === "ready" && value) return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;

  if (phase === "unreachable") {
    return (
      <div className={css.gate}>
        <h1>Orin</h1>
        <div className="o-alert" role="alert">
          <p>{error}</p>
          <p style={{ marginTop: 8 }}>
            <button type="button" className="o-btn" onClick={check}>
              Try again
            </button>
          </p>
        </div>
      </div>
    );
  }

  if (phase === "checking") {
    return (
      <div className={css.gate}>
        <h1>Orin</h1>
        <p role="status">Opening your profile...</p>
      </div>
    );
  }

  return (
    <ProfilePicker
      onSignedIn={(u) => {
        setUser(u);
        setPhase("ready");
      }}
    />
  );
}

function ProfilePicker({ onSignedIn }: { onSignedIn: (user: User) => void }) {
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    const trimmed = name.trim();
    if (!trimmed) {
      setError("Type a name to continue.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const session = await api.signIn(trimmed);
      onSignedIn(session.user);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <main id="orin-main" className={css.gate}>
      <h1>Orin</h1>
      <p className="o-muted">Hear music, see it written in different traditions, and see what each notation leaves out.</p>
      <form className="o-panel o-stack" onSubmit={submit} aria-labelledby="orin-profile-title">
        <h2 id="orin-profile-title">Choose a profile</h2>
        <p id="orin-profile-note">
          This is a demo profile with no password. Anyone who types the same name on this server opens the same profile,
          so do not keep anything private here.
        </p>
        <label className="o-label">
          Profile name
          <input
            className="o-input"
            value={name}
            onChange={(e) => setName(e.target.value)}
            maxLength={40}
            autoComplete="nickname"
            aria-describedby="orin-profile-note"
            required
          />
        </label>
        {error && (
          <p className="o-alert" role="alert">
            {error}
          </p>
        )}
        <div>
          <button type="submit" className="o-btn o-btn-primary" disabled={busy}>
            {busy ? "Opening..." : "Continue"}
          </button>
        </div>
      </form>
    </main>
  );
}
