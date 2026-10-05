const { useState, useEffect, useRef, useCallback } = React;

const API = ""; // same origin

async function api(path, opts) {
  const res = await fetch(API + path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch {}
    throw new Error(detail);
  }
  return res.json();
}

// Live score preview using the same formula the server uses
// (base - tokens*weight - attempts*penalty, floored). Weights match defaults.
function previewScore(base, tokens, attempts) {
  const raw = base - tokens * 0.5 - attempts * 10;
  return Math.max(100, Math.round(raw));
}

function useToast() {
  const [toast, setToast] = useState(null);
  const show = useCallback((msg, kind) => {
    setToast({ msg, kind });
    setTimeout(() => setToast(null), 3200);
  }, []);
  const node = toast ? <div className={"toast " + (toast.kind || "")}>{toast.msg}</div> : null;
  return [node, show];
}

function Gate({ onEnter }) {
  const [name, setName] = useState("");
  return (
    <div className="gate">
      <h2>Access the grid</h2>
      <p>VOLT is a prompt-injection range modelled on an electricity control
         system. Ten substations, ten ways in. Pick a callsign for the
         leaderboard.</p>
      <input
        autoFocus placeholder="callsign (e.g. sparky_07)"
        value={name} onChange={(e) => setName(e.target.value)}
        onKeyDown={(e) => e.key === "Enter" && name.trim() && onEnter(name.trim())}
      />
      <button className="btn primary" style={{ width: "100%" }}
        disabled={!name.trim()} onClick={() => onEnter(name.trim())}>
        Connect
      </button>
    </div>
  );
}

function LevelRail({ levels, activeId, onPick }) {
  return (
    <div className="rail">
      <div className="hdr">SUBSTATIONS</div>
      {levels.map((l) => (
        <button key={l.id}
          className={"lvl" + (l.id === activeId ? " active" : "") + (l.cleared ? " cleared" : "")}
          onClick={() => onPick(l.id)}>
          <span className="n">{l.cleared ? "✓" : l.number}</span>
          <span>
            <span className="ttl">{l.title}</span>
            <span className="tech">{l.technique}</span>
          </span>
          {l.best > 0 && <span className="best">{l.best}</span>}
        </button>
      ))}
    </div>
  );
}

function Meters({ level, tokens, attempts, solved }) {
  const est = solved ? null : previewScore(level.base_points, tokens, attempts);
  return (
    <div className="meters">
      <div className="m">tokens spent<b>{tokens}</b></div>
      <div className="m">failed attempts<b>{attempts}</b></div>
      <div className="m est">{solved ? "locked score" : "score if solved now"}
        <b>{solved ? "—" : est}</b></div>
    </div>
  );
}

function Console({ level, session, onScored, onToast, onRefreshLevels }) {
  const [log, setLog] = useState([]);
  const [text, setText] = useState("");
  const [tokens, setTokens] = useState(0);
  const [attempts, setAttempts] = useState(0);
  const [solved, setSolved] = useState(level.cleared);
  const [frags, setFrags] = useState([]);
  const [busy, setBusy] = useState(false);
  const logRef = useRef(null);

  // reset view state whenever we switch levels
  useEffect(() => {
    setLog([]); setTokens(0); setAttempts(0); setSolved(level.cleared); setFrags([]); setText("");
  }, [level.id]);

  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [log]);

  async function send() {
    const t = text.trim();
    if (!t || busy) return;
    setBusy(true);
    setLog((l) => [...l, { who: "user", text: t }]);
    setText("");
    try {
      const r = await api(`/api/levels/${level.id}/message`, {
        method: "POST",
        body: JSON.stringify({ sid: session.sid, text: t }),
      });
      setTokens(r.tokens_spent);
      setAttempts(r.attempts);
      if (r.meta && r.meta.fragments) setFrags(r.meta.fragments);
      const kind = r.blocked ? "block" : r.leaked ? "breach" : "bot";
      setLog((l) => [...l, { who: kind, text: r.reply }]);
      if (r.leaked && !solved) {
        setSolved(true);
        onToast(`Breach confirmed — ${level.title} cleared. Score ${r.score} (best ${r.best}).`, "good");
        onScored();
        onRefreshLevels();
      } else if (r.leaked) {
        onToast(`Re-solved. Score ${r.score} (best ${r.best}).`, "good");
        onRefreshLevels();
      }
    } catch (e) {
      onToast("Transmission failed: " + e.message, "bad");
      setLog((l) => l.slice(0, -1));
      setText(t);
    } finally {
      setBusy(false);
    }
  }

  async function control(kind) {
    try {
      await api(`/api/levels/${level.id}/${kind}`, {
        method: "POST",
        body: JSON.stringify({ sid: session.sid }),
      });
      setLog([]); setTokens(0); setAttempts(0); setFrags([]);
      if (kind === "reset") {
        setSolved(false);
        onToast("Level reset — conversation, meters and stored state wiped.", "");
      } else {
        onToast("New chat — conversation cleared, persistent memory kept.", "");
      }
    } catch (e) {
      onToast("Control failed: " + e.message, "bad");
    }
  }

  const statusClass = solved ? "breach" : "live";
  const statusText = solved ? "● BREACHED" : "● LIVE / SECURED";

  return (
    <div className="console">
      <div className="head">
        <div className="meta">
          <div className="eyebrow">LEVEL {level.number} · {level.codename}</div>
          <h2>{level.title}</h2>
          <p>{level.briefing}</p>
        </div>
        <div className={"status-lamp " + statusClass}>{statusText}</div>
      </div>

      <Meters level={level} tokens={tokens} attempts={attempts} solved={solved} />

      <div className="log" ref={logRef}>
        {log.length === 0 && (
          <div className="empty">
            — channel open to {level.codename} —<br />
            send a message to begin probing.<br />
            a correct extraction trips the breach lamp automatically.
          </div>
        )}
        {log.map((m, i) => (
          <div key={i} className={"msg " + (m.who === "user" ? "user" : m.who)}>
            <div className="who">
              {m.who === "user" ? session.player
                : m.who === "block" ? "INPUT FILTER"
                : m.who === "breach" ? "⚡ BREACH" : level.codename}
            </div>
            {m.text}
          </div>
        ))}
      </div>

      {level.id === "l3" && frags.length > 0 && (
        <div className="tray">
          <span style={{ color: "var(--steel-dim)" }}>stored fragments:</span>
          {frags.map((f, i) => (
            <span key={i} className="frag"><span className="k">{f.name}</span> = {f.value}</span>
          ))}
        </div>
      )}

      <div className="composer">
        <textarea
          value={text} placeholder={`message ${level.codename}…  (Enter to send, Shift+Enter for newline)`}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }}
        />
        <div className="controls">
          <button className="btn primary" disabled={busy || !text.trim()} onClick={send}>
            {busy ? "transmitting…" : "Send"}
          </button>
          {level.starter && (
            <button className="btn ghost" title="Drop a large sample payload into the composer"
              onClick={() => setText(level.starter)}>Insert sample payload</button>
          )}
          <span className="spacer"></span>
          <button className="btn ghost" onClick={() => control("new-chat")}>New chat</button>
          <button className="btn ghost danger" onClick={() => control("reset")}>Reset level</button>
        </div>
      </div>

      <details className="drawer">
        <summary>▸ Briefing &amp; hint</summary>
        <div className="body">
          <div><b style={{ color: "var(--steel)" }}>Technique:</b> {level.technique}</div>
          <div style={{ marginTop: 8 }}>{level.hint}</div>
          <div className="lesson"><b>Lesson:</b> {level.lesson}</div>
        </div>
      </details>
    </div>
  );
}

function App() {
  const [session, setSession] = useState(() => {
    const raw = localStorage.getItem("volt_session");
    return raw ? JSON.parse(raw) : null;
  });
  const [levels, setLevels] = useState([]);
  const [activeId, setActiveId] = useState(null);
  const [total, setTotal] = useState(0);
  const [board, setBoard] = useState(null);
  const [toastNode, showToast] = useToast();

  const refreshLevels = useCallback(async () => {
    if (!session) return;
    try {
      const d = await api(`/api/levels?sid=${session.sid}`);
      setLevels(d.levels);
      setTotal(d.total);
      setActiveId((a) => a || (d.levels[0] && d.levels[0].id));
    } catch (e) { showToast("Could not load levels: " + e.message, "bad"); }
  }, [session, showToast]);

  useEffect(() => { refreshLevels(); }, [refreshLevels]);

  async function enter(player) {
    try {
      const existing = session && session.player === player ? session.sid : null;
      const s = await api("/api/session", {
        method: "POST", body: JSON.stringify({ player, sid: existing }),
      });
      setSession(s);
      localStorage.setItem("volt_session", JSON.stringify(s));
    } catch (e) { showToast("Connect failed: " + e.message, "bad"); }
  }

  async function openBoard() {
    try { setBoard((await api("/api/leaderboard")).entries); }
    catch (e) { showToast("Leaderboard unavailable: " + e.message, "bad"); }
  }

  function logout() {
    localStorage.removeItem("volt_session");
    setSession(null); setLevels([]); setActiveId(null);
  }

  if (!session) {
    return (<div className="shell"><Header /><Gate onEnter={enter} /></div>);
  }

  const active = levels.find((l) => l.id === activeId);
  const cleared = levels.filter((l) => l.cleared).length;

  return (
    <div className="shell">
      <Header>
        <div className="hud">
          <span className="chip">OP <b>{session.player}</b></span>
          <span className="chip">CLEARED <b>{cleared}/{levels.length}</b></span>
          <span className="chip score">SCORE <b>{total}</b></span>
          <button className="btn ghost" onClick={openBoard}>Leaderboard</button>
          <button className="btn ghost" onClick={logout}>Exit</button>
        </div>
      </Header>

      <div className="grid">
        <LevelRail levels={levels} activeId={activeId} onPick={setActiveId} />
        {active && (
          <Console
            key={active.id}
            level={active}
            session={session}
            onScored={() => {}}
            onToast={showToast}
            onRefreshLevels={refreshLevels}
          />
        )}
      </div>

      {board && (
        <div className="overlay" onClick={() => setBoard(null)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <div className="mh"><h3>Grid leaderboard</h3></div>
            <table>
              <thead><tr><th>#</th><th>operator</th><th>cleared</th><th className="num">score</th></tr></thead>
              <tbody>
                {board.length === 0 && <tr><td colSpan="4" style={{color:"var(--steel-dim)"}}>no scores yet — be the first.</td></tr>}
                {board.map((e, i) => (
                  <tr key={i}><td>{i + 1}</td><td>{e.player}</td><td>{e.cleared}</td><td className="num">{e.total}</td></tr>
                ))}
              </tbody>
            </table>
            <div style={{ padding: "12px 18px" }}>
              <button className="btn ghost" onClick={() => setBoard(null)}>Close</button>
            </div>
          </div>
        </div>
      )}
      {toastNode}
    </div>
  );
}

function Header({ children }) {
  return (
    <div className="topbar">
      <div className="brand">
        <span className="bolt">⚡</span>
        <span>
          <h1>VOLT</h1>
          <div className="sub">prompt-injection grid // substation range</div>
        </span>
      </div>
      {children}
    </div>
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(<App />);
