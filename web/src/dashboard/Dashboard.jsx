import { useEffect, useState } from "react";

const MAX_TIMELINE = 30;
const MAX_LOG = 100;

/** Live /ws/events subscription with auto-reconnect. */
function useEvents(onEvent) {
  const [connected, setConnected] = useState(false);
  useEffect(() => {
    let ws;
    let timer;
    let closed = false;
    const open = () => {
      const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
      ws = new WebSocket(`${proto}//${window.location.host}/ws/events`);
      ws.onopen = () => setConnected(true);
      ws.onmessage = (m) => onEvent(JSON.parse(m.data));
      ws.onclose = () => {
        setConnected(false);
        if (!closed) timer = setTimeout(open, 1500);
      };
    };
    open();
    return () => { closed = true; clearTimeout(timer); ws?.close(); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  return connected;
}

function timeAgo(iso, now) {
  const s = Math.max(0, Math.round((now - new Date(iso).getTime()) / 1000));
  if (s < 5) return "just now";
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  return `${Math.round(s / 3600)} h ago`;
}

/** Frame with box overlays and, in guidance, an arrow from bottom-center to the target. */
function FrameView({ view }) {
  if (!view.frame) return <div className="frame empty">Waiting for the phone…</div>;
  const pct = (v) => `${v * 100}%`;
  return (
    <div className="frame">
      <img src={view.frame} alt="Latest camera frame" />
      {view.boxes.map((b, i) => (
        <div key={i} className="box" style={{
          left: pct(b.box[0]), top: pct(b.box[1]),
          width: pct(b.box[2] - b.box[0]), height: pct(b.box[3] - b.box[1]),
        }}>
          <span>{b.label}</span>
        </div>
      ))}
      {view.arrow && (
        <svg className="arrow" viewBox="0 0 1 1" preserveAspectRatio="none" aria-hidden="true">
          <defs>
            <marker id="head" markerWidth="6" markerHeight="6" refX="3" refY="3" orient="auto">
              <path d="M0,0 L6,3 L0,6 z" fill="#ffe14d" />
            </marker>
          </defs>
          <line x1="0.5" y1="0.98" x2={view.arrow.point[0]} y2={view.arrow.point[1]}
            stroke="#ffe14d" strokeWidth="0.012" markerEnd="url(#head)" vectorEffect="non-scaling-stroke" />
        </svg>
      )}
      {view.arrow && (
        <div className="readout">
          {view.arrow.angle}° · {view.arrow.clock} o'clock · {view.arrow.distance}
        </div>
      )}
    </div>
  );
}

export default function Dashboard() {
  const [view, setView] = useState({ frame: null, boxes: [], arrow: null });
  const [timeline, setTimeline] = useState([]);
  const [log, setLog] = useState([]);
  const [mock, setMock] = useState(null);
  const [vision, setVision] = useState(null); // live Perceptron latency from /api/health
  const [now, setNow] = useState(Date.now());

  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 5000);
    return () => clearInterval(t);
  }, []);

  const connected = useEvents((ev) => {
    setMock(ev.mock);
    if (ev.type === "sighting") {
      setView((v) => ({
        frame: ev.frame ?? v.frame,
        boxes: ev.sightings.map((s) => ({ box: s.box, label: s.item })),
        arrow: null,
      }));
      // TODO(C): collapse repeats (same item/spot) and backfill the timeline on load.
      setTimeline((t) => [...ev.sightings.map((s) => ({ ...s, key: Math.random() })), ...t].slice(0, MAX_TIMELINE));
    } else if (ev.type === "guide") {
      setView((v) => ({
        frame: ev.frame ?? v.frame,
        boxes: ev.found ? [{ box: ev.box, label: ev.item }] : [],
        arrow: ev.found ? { point: ev.point, angle: ev.angle, clock: ev.clock, distance: ev.distance } : null,
      }));
    } else if (ev.type === "chat") {
      setLog((l) => [{ text: ev.say, source: "chat", user: ev.user_text, provider: ev.provider, at: Date.now() }, ...l].slice(0, MAX_LOG));
    } else if (ev.type === "say") {
      setLog((l) => [{ text: ev.text, source: ev.source, at: Date.now() }, ...l].slice(0, MAX_LOG));
    }
  });

  // Mode badge and live Perceptron latency (p50/p95), refreshed every few seconds.
  useEffect(() => {
    const load = () => fetch("/api/health").then((r) => r.json())
      .then((h) => { setMock(h.mock); setVision(h.vision ?? null); }).catch(() => {});
    load();
    const t = setInterval(load, 3000);
    return () => clearInterval(t);
  }, []);

  return (
    <main className="dash">
      <header>
        <h1>Sightline</h1>
        {mock && <span className="mock-badge">MOCK MODE: fake detections</span>}
        {vision && (
          <span className="latency" aria-label="Perceptron latency">
            Perceptron p50 {vision.p50_ms ?? "–"} ms · p95 {vision.p95_ms ?? "–"} ms ·
            {" "}{vision.calls} ok · {vision.errors} errors · {vision.dropped} dropped
          </span>
        )}
        <span className={`conn ${connected ? "ok" : "bad"}`}>{connected ? "Live" : "Reconnecting…"}</span>
      </header>
      <section className="col-main" aria-label="Live view">
        <FrameView view={view} />
      </section>
      <section className="col-side" aria-label="Memory timeline">
        <h2>Memory</h2>
        <ul className="timeline">
          {timeline.length === 0 && <li className="muted">No sightings yet.</li>}
          {timeline.map((s) => (
            <li key={s.key}>
              {s.thumb ? <img src={s.thumb} alt="" /> : <div className="nothumb" />}
              <div>
                <strong>{s.item}</strong>
                <div>{s.room} · {s.spot}</div>
                <div className="muted">{timeAgo(s.ts, now)}</div>
              </div>
            </li>
          ))}
        </ul>
      </section>
      <section className="col-log" aria-label="What the audio is saying">
        <h2>What the audio is saying</h2>
        <ul className="log" aria-live="polite">
          {log.length === 0 && <li className="muted">Nothing spoken yet.</li>}
          {log.map((l, i) => (
            <li key={i}>
              {l.user && <div className="muted">You: “{l.user}”</div>}
              <span className="src">{l.source}</span> “{l.text}”
              {l.provider && <span className="prov">{l.provider}</span>}
            </li>
          ))}
        </ul>
      </section>
    </main>
  );
}
