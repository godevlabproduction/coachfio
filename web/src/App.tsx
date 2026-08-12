// Phase 0 UI: deliberately ugly. One page: drop a clip -> frames extracted in
// the browser -> uploaded -> pipeline runs -> raw JSON back. Plus a bare trends
// list. No styling effort beyond "readable".

import { useEffect, useRef, useState } from "react";
import {
  clipUrl,
  completeMatch,
  createMatch,
  frameUrl,
  getMatch,
  getTrends,
  getUsage,
  isFrameKey,
  listGames,
  runPool,
  uploadFrame,
  uploadSource,
  type GameInfo,
} from "./api";
import { extractFrames, type ExtractedFrame } from "./frameExtractor";

type Phase = "idle" | "extracting" | "uploading" | "processing" | "done" | "error";

export function App() {
  const [games, setGames] = useState<GameInfo[]>([]);
  const [game, setGame] = useState("ea-fc@26");
  const [platform, setPlatform] = useState("ps5");
  const [resolution, setResolution] = useState("1920x1080");
  const [fps, setFps] = useState(1);
  const [maxWidth, setMaxWidth] = useState(1600);
  const [playerSide, setPlayerSide] = useState("home");
  const [mode, setMode] = useState<"frames" | "video_native">("frames");

  const [phase, setPhase] = useState<Phase>("idle");
  const [progress, setProgress] = useState<{ done: number; total: number }>({ done: 0, total: 0 });
  const [preview, setPreview] = useState<string | undefined>();
  const [log, setLog] = useState<string[]>([]);
  const [result, setResult] = useState<any>(null);
  const [trends, setTrends] = useState<any[]>([]);
  const [usage, setUsage] = useState<any>(null);
  const esRef = useRef<EventSource | null>(null);

  useEffect(() => {
    listGames().then(setGames).catch(() => {});
    getUsage().then(setUsage).catch(() => {});
  }, []);

  const [gid, edition] = game.split("@");

  function addLog(line: string) {
    setLog((l) => [...l, line]);
  }

  async function refreshTrends() {
    setTrends(await getTrends(gid, edition));
  }
  useEffect(() => {
    refreshTrends();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [game, phase === "done"]);

  async function handleFile(file: File) {
    setResult(null);
    setLog([]);
    setProgress({ done: 0, total: 0 });

    // Native whole-video path: upload the raw file, let Gemini watch it. No
    // in-browser frame extraction.
    if (mode === "video_native") {
      try {
        setPhase("uploading");
        const match = await createMatch({
          game_id: gid, edition, source_type: "video_native",
          capture: { player_side: playerSide, source_file: file.name },
        });
        addLog(`match ${match.match_id} created (native video)`);
        addLog(`uploading ${(file.size / 1e6).toFixed(1)} MB video…`);
        await uploadSource(match.match_id, file);
        addLog("video uploaded; sending to Gemini");
        setPhase("processing");
        await completeMatch(match.match_id);
        subscribe(match.match_id);
      } catch (e: any) {
        addLog(`ERROR: ${e.message ?? e}`);
        setPhase("error");
      }
      return;
    }

    setPhase("extracting");
    try {
      // 1. Extract frames in the browser.
      const frames: ExtractedFrame[] = [];
      for await (const f of extractFrames(file, {
        fps,
        maxWidth,
        quality: 0.72,
        onProgress: (done, total, prev) => {
          setProgress({ done, total });
          if (prev) setPreview(prev);
        },
      })) {
        frames.push(f);
      }
      addLog(`extracted ${frames.length} frames in-browser (${(frames.reduce((a, f) => a + f.blob.size, 0) / 1e6).toFixed(1)} MB total)`);

      // 2. Create match.
      const match = await createMatch({
        game_id: gid,
        edition,
        source_type: "video",
        capture: { platform, resolution, fps, player_side: playerSide, source_file: file.name },
      });
      addLog(`match ${match.match_id} created`);

      // 3. Upload frames (concurrency-limited).
      setPhase("uploading");
      setProgress({ done: 0, total: frames.length });
      let uploaded = 0;
      await runPool(frames, 4, async (fr) => {
        await uploadFrame(match.match_id, fr.index, fr.timestampMs, fr.blob);
        uploaded++;
        setProgress({ done: uploaded, total: frames.length });
      });
      addLog(`uploaded ${uploaded} frames`);

      // 4. Kick off the pipeline + subscribe to progress.
      setPhase("processing");
      await completeMatch(match.match_id);
      subscribe(match.match_id);
    } catch (e: any) {
      addLog(`ERROR: ${e.message ?? e}`);
      setPhase("error");
    }
  }

  function subscribe(matchId: string) {
    esRef.current?.close();
    const terminal = ["complete", "failed", "over_budget"];
    let finished = false;

    const finish = (m: any) => {
      if (finished) return;
      finished = true;
      esRef.current?.close();
      setResult(m);
      setPhase("done");
      refreshTrends();
      getUsage().then(setUsage).catch(() => {});
    };

    // SAFETY NET: poll the match to completion independently of the live stream.
    // The SSE connection can drop on a long run (idle/proxy timeout); without this
    // the finished report would never appear even though the backend saved it.
    const startedAt = Date.now();
    const poll = async () => {
      if (finished) return;
      try {
        const m = await getMatch(matchId);
        if (terminal.includes(m.status)) return finish(m);
      } catch {
        /* transient network — keep polling */
      }
      if (Date.now() - startedAt < 8 * 60 * 1000) setTimeout(poll, 3000);
      else {
        addLog("timed out waiting for the report (still processing on the server)");
        setPhase("error");
      }
    };
    setTimeout(poll, 3000);

    // Live progress log (nice-to-have; not the source of truth for completion).
    const es = new EventSource(`/api/matches/${matchId}/progress`);
    esRef.current = es;
    es.addEventListener("progress", (e: MessageEvent) => {
      const d = JSON.parse(e.data);
      addLog(`[${d.stage}] ${d.status}${d.detail ? " — " + d.detail : ""}`);
    });
    es.addEventListener("done", async () => {
      es.close();
      let m = await getMatch(matchId);
      for (let i = 0; i < 10 && !terminal.includes(m.status); i++) {
        await new Promise((r) => setTimeout(r, 500));
        m = await getMatch(matchId);
      }
      finish(m);
    });
    es.addEventListener("error", () => {
      addLog("progress stream closed (still checking for your report…)");
      es.close();  // polling above will still deliver the finished report
    });
  }

  const busy = phase === "extracting" || phase === "uploading" || phase === "processing";

  return (
    <div style={{ fontFamily: "monospace", maxWidth: 900, margin: "20px auto", padding: 12 }}>
      <h2>Coach.io — Phase 0 (OCR reliability test)</h2>
      <p style={{ color: "#666" }}>
        Drop a match clip. Frames are extracted <b>in your browser</b> and only the frames upload.
        The backend reads the HUD (OCR) and runs event/insight analysis (local or cloud).
        {usage && (
          <span> · matches used: <b>{usage.matches_analyzed}/{usage.limit}</b></span>
        )}
      </p>

      <fieldset disabled={busy} style={{ marginBottom: 12 }}>
        <legend>capture</legend>
        <label>
          game{" "}
          <select value={game} onChange={(e) => setGame(e.target.value)}>
            {games.map((g) => (
              <option key={`${g.game_id}@${g.edition}`} value={`${g.game_id}@${g.edition}`}>
                {g.display_name}
              </option>
            ))}
            {games.length === 0 && <option value="ea-fc@26">EA Sports FC 26</option>}
          </select>
        </label>{" "}
        <label>
          platform{" "}
          <select value={platform} onChange={(e) => setPlatform(e.target.value)}>
            <option>ps5</option>
            <option>xbox</option>
            <option>pc</option>
          </select>
        </label>{" "}
        <label>
          resolution{" "}
          <input value={resolution} onChange={(e) => setResolution(e.target.value)} size={9} />
        </label>{" "}
        <label>
          fps{" "}
          <input type="number" value={fps} min={0.5} step={0.5} onChange={(e) => setFps(Number(e.target.value))} style={{ width: 50 }} />
        </label>{" "}
        <label>
          maxWidth{" "}
          <input type="number" value={maxWidth} step={80} onChange={(e) => setMaxWidth(Number(e.target.value))} style={{ width: 70 }} />
        </label>{" "}
        <label>
          you are{" "}
          <select value={playerSide} onChange={(e) => setPlayerSide(e.target.value)}>
            <option value="home">home (left / top of scoreboard)</option>
            <option value="away">away (right / bottom of scoreboard)</option>
          </select>
        </label>{" "}
        <label>
          mode{" "}
          <select value={mode} onChange={(e) => setMode(e.target.value as any)}>
            <option value="frames">frames (OCR + vision, per-segment)</option>
            <option value="video_native">full video → Gemini (one call)</option>
          </select>
        </label>
        {mode === "video_native" && (
          <div style={{ color: "#0a6", fontSize: 12, marginTop: 6 }}>
            Uploads the whole video to Gemini (no frame extraction). Keep clips reasonably
            short; large files upload slowly.
          </div>
        )}
      </fieldset>

      <div
        onDragOver={(e) => e.preventDefault()}
        onDrop={(e) => {
          e.preventDefault();
          const f = e.dataTransfer.files?.[0];
          if (f && !busy) handleFile(f);
        }}
        style={{ border: "2px dashed #999", padding: 30, textAlign: "center", background: busy ? "#f6f6f6" : "#fff" }}
      >
        {busy ? (
          <div>
            <div>
              <b>{phase}</b> {progress.done}/{progress.total}
            </div>
            {preview && <img src={preview} alt="frame" style={{ maxWidth: 320, marginTop: 8, border: "1px solid #ccc" }} />}
          </div>
        ) : (
          <div>
            drop a video here, or{" "}
            <input
              type="file"
              accept="video/*"
              onChange={(e) => e.target.files?.[0] && handleFile(e.target.files[0])}
            />
          </div>
        )}
      </div>

      {log.length > 0 && (
        <pre style={{ background: "#111", color: "#0f0", padding: 10, marginTop: 12, maxHeight: 220, overflow: "auto" }}>
          {log.join("\n")}
        </pre>
      )}

      {result && (
        <>
          {result.events?.length > 0 && (
            <>
              <h3>events</h3>
              <table border={1} cellPadding={4} style={{ borderCollapse: "collapse", width: "100%" }}>
                <thead><tr><th>time</th><th>category</th><th>type</th><th>moment</th></tr></thead>
                <tbody>
                  {result.events.map((e: any) => (
                    <tr key={e.id}>
                      <td>{Math.floor(e.timestamp_ms / 1000)}s</td>
                      <td>{e.category}</td>
                      <td>{e.game_event_type}</td>
                      <td>
                        {e.payload?.clip ? (
                          <video src={clipUrl(result.id, e.payload.clip)} controls width={200} />
                        ) : (e.frame_refs || []).filter(isFrameKey).slice(0, 1).map((k: string) => (
                          <img key={k} src={frameUrl(result.id, k)} width={160} style={{ border: "1px solid #ccc" }} />
                        ))}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}

          {(result.insights || [])
            .filter((i: any) => i.kind === "coaching_report")
            .map((rep: any) => (
              <div key={rep.id} style={{ border: "2px solid #0a6", borderRadius: 6, padding: 14, marginTop: 12 }}>
                <h3 style={{ marginTop: 0 }}>
                  🎯 Coaching report{rep.payload?.player_side ? ` — you = ${rep.payload.player_side}` : ""}
                </h3>
                <p style={{ fontSize: 15 }}>{rep.summary}</p>
                {[
                  ["What you did well", "strengths"],
                  ["Recurring mistakes", "recurring_mistakes"],
                  ["Positioning issues", "positioning_issues"],
                  ["Decision-making patterns", "decision_patterns"],
                  ["What to practice", "practice_drills"],
                ].map(([title, key]) => {
                  const items: string[] = rep.payload?.[key] || [];
                  return items.length ? (
                    <div key={key} style={{ marginTop: 10 }}>
                      <div style={{ fontWeight: "bold" }}>{title}</div>
                      <ul style={{ margin: "4px 0" }}>
                        {items.map((t, i) => <li key={i}>{t}</li>)}
                      </ul>
                    </div>
                  ) : null;
                })}

                {/* NEW: match stats strip */}
                {rep.payload?.stats && Object.keys(rep.payload.stats).length > 0 && (
                  <div style={{ marginTop: 12 }}>
                    <div style={{ fontWeight: "bold" }}>Match stats</div>
                    <div style={{ color: "#444", fontSize: 13 }}>
                      {([
                        ["goals_for", "GF"], ["goals_against", "GA"], ["shots", "Shots"],
                        ["big_chances", "Big chances"], ["goals_conceded_from_crosses", "Conceded (crosses)"],
                        ["defensive_errors", "Def. errors"],
                      ] as [string, string][])
                        .filter(([k]) => typeof rep.payload.stats[k] === "number")
                        .map(([k, lbl]) => `${lbl}: ${rep.payload.stats[k]}`)
                        .join("   |   ")}
                    </div>
                  </div>
                )}

                {/* NEW: goal-by-goal breakdown */}
                {Array.isArray(rep.payload?.goals) && rep.payload.goals.length > 0 && (
                  <div style={{ marginTop: 12 }}>
                    <div style={{ fontWeight: "bold" }}>Goal by goal</div>
                    <ul style={{ margin: "4px 0" }}>
                      {rep.payload.goals.map((g: any, i: number) => {
                        const scored = String(g.type || "").toLowerCase().startsWith("scor");
                        return (
                          <li key={i} style={{ marginBottom: 4 }}>
                            <b style={{ color: scored ? "#0a6" : "#b00" }}>
                              {g.time} {scored ? "GOAL" : "CONCEDED"}
                            </b>{" "}
                            {g.summary}
                            {!scored && g.fix && (
                              <div style={{ color: "#555", fontStyle: "italic", fontSize: 13 }}>Fix: {g.fix}</div>
                            )}
                          </li>
                        );
                      })}
                    </ul>
                  </div>
                )}
              </div>
            ))}

          <h3>extracted match (raw JSON)</h3>
          <p style={{ color: "#666" }}>
            status <b>{result.status}</b> · parse confidence <b>{result.parse_confidence ?? "n/a"}</b> · cost ${result.cost_usd?.toFixed?.(4) ?? "0.0000"}
            {result.warnings?.length > 0 && (
              <span style={{ color: "#b00" }}> · {result.warnings.length} warning(s)</span>
            )}
          </p>
          <pre style={{ background: "#f4f4f4", padding: 10, overflow: "auto", maxHeight: 400 }}>
            {JSON.stringify(result, null, 2)}
          </pre>
        </>
      )}

      <h3>trends ({game})</h3>
      {trends.length === 0 ? (
        <p style={{ color: "#666" }}>no matches analysed yet</p>
      ) : (
        <table border={1} cellPadding={4} style={{ borderCollapse: "collapse" }}>
          <thead>
            <tr>
              <th>metric</th>
              <th>latest</th>
              <th>prev</th>
              <th>Δ</th>
              <th>avg</th>
              <th>n</th>
              <th>improving?</th>
            </tr>
          </thead>
          <tbody>
            {trends.map((t) => (
              <tr key={t.key}>
                <td>{t.label}</td>
                <td>{t.latest ?? "—"}{t.unit ?? ""}</td>
                <td>{t.previous ?? "—"}</td>
                <td>{t.delta ?? "—"}</td>
                <td>{t.average ?? "—"}</td>
                <td>{t.points?.length ?? 0}</td>
                <td>{t.improving === null ? "—" : t.improving ? "↑ yes" : "↓ no"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
