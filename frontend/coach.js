/* Coach.io — wires the Stitch static frontend to the real backend API.
 * Included on every page; it detects the page and hooks it up. */
(function () {
  "use strict";
  // API base: when served on :3000 (serve), talk to the backend on :8000; if the
  // site is ever served same-origin, use relative URLs.
  var API = location.port === "3000" ? "http://localhost:8000" : "";
  var GAME = { game_id: "ea-fc", edition: "26" };

  // ---- API helpers ---------------------------------------------------------
  function j(url, opts) {
    return fetch(API + url, opts).then(function (r) {
      if (!r.ok) throw new Error(url + " -> " + r.status);
      return r.status === 204 ? null : r.json();
    });
  }
  function createMatch(side, extra) {
    var capture = { player_side: side, source: "web" };
    for (var k in (extra || {})) if (extra[k]) capture[k] = extra[k];
    return j("/api/matches", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        game_id: GAME.game_id, edition: GAME.edition, source_type: "video_native",
        capture: capture,
      }),
    });
  }
  function uploadSource(id, file) {
    var fd = new FormData();
    fd.append("file", file, file.name);
    return fetch(API + "/api/matches/" + id + "/source", { method: "POST", body: fd })
      .then(function (r) { if (!r.ok) throw new Error("upload " + r.status); });
  }
  function completeMatch(id) {
    return j("/api/matches/" + id + "/complete", { method: "POST" });
  }
  var getMatch = function (id) { return j("/api/matches/" + id); };
  var listMatches = function () {
    return j("/api/matches?game_id=" + GAME.game_id + "&edition=" + GAME.edition + "&limit=8")
      .catch(function () { return []; });
  };

  // ---- utilities -----------------------------------------------------------
  function qs(k) { return new URLSearchParams(location.search).get(k); }
  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
    return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); }
  function times(text) {  // pull "(12:30, 34:10)" chips out of a point string
    var m = String(text).match(/\((\d{1,3}:\d{2}(?:\s*,\s*\d{1,3}:\d{2})*)\)\s*$/);
    if (!m) return { text: text, chips: [] };
    return { text: String(text).slice(0, m.index).trim(), chips: m[1].split(/\s*,\s*/) };
  }
  function resultOf(o) { return (o && o.result) || ""; }
  function badge(res) {
    res = (res || "").toLowerCase();
    if (res === "win") return ["WIN", "bg-primary-container text-on-primary-container", "text-primary"];
    if (res === "loss") return ["LOSS", "bg-error-container text-on-error-container", "text-error"];
    return ["DRAW", "bg-surface-variant text-on-surface-variant", "text-on-surface"];
  }

  // ---- UPLOAD page ---------------------------------------------------------
  function initUpload() {
    var side = "home";
    // Perspective toggle — match on the explicit data-side marker (robust to the
    // icon text inside each button, which broke a textContent match).
    var pBtns = [].slice.call(document.querySelectorAll("[data-side]"));
    function setSide(s) {
      side = s;
      pBtns.forEach(function (b) {
        var on = b.getAttribute("data-side") === s;
        b.className = on
          ? "px-4 py-1.5 rounded-sm bg-primary/20 text-primary font-label-sm text-label-sm flex items-center gap-2 transition-colors shadow-[inset_0_0_8px_rgba(63,229,108,0.2)]"
          : "px-4 py-1.5 rounded-sm text-on-surface-variant hover:text-on-surface font-label-sm text-label-sm flex items-center gap-2 transition-colors";
      });
    }
    pBtns.forEach(function (b) {
      b.addEventListener("click", function () { setSide(b.getAttribute("data-side")); });
    });
    setSide("home");  // establish the default selected state on load

    // Controls scheme + skill level selectors (fed to the coach for calibration).
    var ctrlRow = document.querySelector(".flex.flex-wrap.items-center.justify-between");
    if (ctrlRow && !document.getElementById("cx-controls")) {
      var selWrap = document.createElement("div");
      selWrap.className = "flex items-center gap-3";
      selWrap.innerHTML =
        '<label class="font-label-sm text-label-sm text-on-surface-variant uppercase flex items-center gap-2">Controls'
        + '<select id="cx-controls" class="bg-surface-dim border border-outline-variant/50 rounded px-2 py-1 text-on-surface text-label-sm">'
        + '<option>Classic</option><option>Alternate</option></select></label>'
        + '<label class="font-label-sm text-label-sm text-on-surface-variant uppercase flex items-center gap-2">Level'
        + '<select id="cx-skill" class="bg-surface-dim border border-outline-variant/50 rounded px-2 py-1 text-on-surface text-label-sm">'
        + '<option>Casual</option><option>Competitive</option><option>Pro</option></select></label>';
      ctrlRow.appendChild(selWrap);
    }

    var input = document.createElement("input");
    input.type = "file"; input.accept = "video/*"; input.style.display = "none";
    document.body.appendChild(input);

    function go(file) {
      if (!file) return;
      var box = document.querySelector(".border-dashed");
      if (box) box.querySelector("h2").textContent = "Uploading " + (file.size / 1e6).toFixed(0) + " MB…";
      var extra = {
        control_scheme: (document.getElementById("cx-controls") || {}).value || "",
        skill_level: (document.getElementById("cx-skill") || {}).value || "",
      };
      createMatch(side, extra)
        .then(function (m) { return uploadSource(m.match_id, file).then(function () { return m; }); })
        .then(function (m) { return completeMatch(m.match_id).then(function () { return m; }); })
        .then(function (m) { location.href = "/analyzing/?id=" + m.match_id; })
        .catch(function (e) { if (box) box.querySelector("h2").textContent = "Error: " + e.message; });
    }
    input.addEventListener("change", function () { go(input.files[0]); });
    // "Browse Files" button + the whole dropzone
    var browse = [].slice.call(document.querySelectorAll("button")).find(function (b) {
      return /browse files/i.test(b.textContent); });
    if (browse) { browse.onclick = function (e) { e.preventDefault(); input.click(); }; }
    var dz = document.querySelector(".border-dashed");
    if (dz) {
      dz.addEventListener("click", function (e) { if (e.target === browse) return; input.click(); });
      dz.addEventListener("dragover", function (e) { e.preventDefault(); });
      dz.addEventListener("drop", function (e) { e.preventDefault(); go(e.dataTransfer.files[0]); });
    }
    loadRecent();
  }

  function loadRecent() {
    var col = document.querySelector(".lg\\:col-span-4");
    if (!col) return;
    listMatches().then(function (ms) {
      var cards = (ms || []).filter(function (m) { return m.status === "complete"; }).slice(0, 6);
      if (!cards.length) return;
      var html = col.querySelector(".flex.items-center.justify-between").outerHTML;  // keep header
      cards.forEach(function (m) {
        var b = badge(resultOf(m.outcome)), letter = b[0][0];
        var opp = (m.outcome && m.outcome.score) || "";
        html += '<div class="group relative flex items-center gap-4 p-4 rounded-lg bg-surface-container border border-outline-variant/30 hover:bg-surface-container-high transition-colors cursor-pointer overflow-hidden" onclick="location.href=\'/report/?id=' + m.id + '\'">'
          + '<div class="absolute left-0 top-0 bottom-0 w-[4px] ' + (letter === "W" ? "bg-primary" : letter === "L" ? "bg-error" : "bg-tertiary") + '"></div>'
          + '<div class="w-12 h-12 rounded bg-surface-variant text-on-surface flex items-center justify-center font-display-lg text-headline-lg-mobile shrink-0">' + letter + '</div>'
          + '<div class="flex-grow min-w-0"><div class="flex justify-between items-baseline mb-1"><h4 class="font-body-md text-on-surface font-semibold truncate">' + esc(opp || "Match") + '</h4></div>'
          + '<div class="text-on-surface-variant font-label-sm text-label-sm"><span class="text-primary group-hover:underline">View Analysis</span></div></div></div>';
      });
      col.innerHTML = html;
    });
  }

  // ---- ANALYZING page ------------------------------------------------------
  function stepPct(stage, status, detail) {
    var t = ((status || "") + " " + (detail || "") + " " + (stage || "")).toLowerCase();
    if (/done|complete/.test(t)) return 100;
    if (/learn/.test(t)) return 92;
    if (/synthes/.test(t)) return 80;
    if (/roster/.test(t)) return 66;
    if (/consensus/.test(t)) return 60;
    if (/generat/.test(t)) return 55;
    if (/watch|running/.test(t)) return 42;
    if (/google is processing|processing/.test(t)) return 26;
    if (/upload/.test(t)) return 16;
    if (/start|loading|queued/.test(t)) return 8;
    return 0;
  }
  function labelFor(pct) {
    if (pct >= 100) return "Done";
    if (pct >= 92) return "Learning";
    if (pct >= 80) return "Writing your coaching report";
    if (pct >= 60) return "Cross-checking (2 viewings) + reading your squad";
    if (pct >= 42) return "Watching your match";
    if (pct >= 16) return "Uploading to the AI";
    return "Starting up";
  }
  function initAnalyzing() {
    var id = qs("id");
    if (!id) { location.href = "/upload/"; return; }
    var main = document.querySelector("main") || document.body;
    // Progress UI injected at the top (design-consistent).
    var box = document.createElement("div");
    box.className = "max-w-xl mx-auto mt-8 mb-10 text-center";
    box.innerHTML =
      '<div class="font-display-lg text-display-lg text-primary" id="cx-pct">0%</div>'
      + '<div class="text-on-surface-variant mb-4" id="cx-step">Starting up</div>'
      + '<div class="w-full h-3 rounded-full bg-surface-variant overflow-hidden"><div id="cx-bar" class="h-full bg-primary transition-all duration-500" style="width:0%"></div></div>'
      + '<div class="font-label-sm text-label-sm text-on-surface-variant mt-3">A full-match read takes ~2-3 min. You can leave this page — the report is saved.</div>';
    main.insertBefore(box, main.firstChild);

    var pct = 0, terminal = { complete: 1, failed: 1, over_budget: 1 }, finished = false;
    function set(p, lbl) {
      p = Math.max(pct, Math.min(100, p)); pct = p;
      document.getElementById("cx-pct").textContent = Math.round(p) + "%";
      document.getElementById("cx-bar").style.width = p + "%";
      document.getElementById("cx-step").textContent = lbl || labelFor(p);
    }
    function done(status) {
      if (finished) return; finished = true;
      if (status === "complete") { set(100, "Done"); setTimeout(function () { location.href = "/report/?id=" + id; }, 400); }
      else { document.getElementById("cx-step").textContent = "Analysis " + status + " — please try again"; }
    }
    // Live step stream.
    try {
      var es = new EventSource(API + "/api/matches/" + id + "/progress");
      es.addEventListener("progress", function (e) {
        var d = JSON.parse(e.data); var p = stepPct(d.stage, d.status, d.detail);
        if (p) set(p, d.detail || labelFor(p));
      });
      es.addEventListener("done", function () { es.close(); getMatch(id).then(function (m) { done(m.status); }); });
      es.addEventListener("error", function () { es.close(); });
    } catch (_) {}
    // Polling safety net (also drives a gentle creep so the bar always moves).
    var tries = 0;
    (function poll() {
      getMatch(id).then(function (m) {
        if (terminal[m.status]) return done(m.status);
        if (pct < 40) set(pct + 2);  // gentle creep until real steps arrive
      }).catch(function () {});
      if (!finished && ++tries < 200) setTimeout(poll, 3000);
    })();
  }

  // ---- REPORT page ---------------------------------------------------------
  function section(title, items, color, icon) {
    if (!items || !items.length) return "";
    var lis = items.map(function (it) {
      var t = times(it);
      var chips = t.chips.map(function (c) {
        return '<button class="time-chip self-start px-2 py-1 rounded-full font-label-sm text-label-sm text-secondary-fixed-dim cursor-pointer hover:text-white flex items-center gap-1" data-t="' + c + '" data-c="' + esc(t.text) + '"><span class="material-symbols-outlined text-[14px]">play_circle</span> ' + esc(c) + "</button>";
      }).join("");
      return '<li class="flex flex-col gap-2"><span>' + esc(t.text) + "</span>" + (chips ? '<div class="flex flex-wrap gap-2">' + chips + "</div>" : "") + "</li>";
    }).join("");
    return '<div class="bg-surface-container-low p-6 rounded-lg border-l-4 ' + color + ' flex flex-col"><div class="flex items-center gap-3 mb-4"><div class="p-2 rounded bg-white/5"><span class="material-symbols-outlined ' + color.replace("border-", "text-") + '">' + icon + '</span></div><h3 class="font-headline-lg-mobile text-headline-lg-mobile">' + title + '</h3></div><ul class="space-y-4 font-body-md text-on-surface flex-grow">' + lis + "</ul></div>";
  }

  function renderReport(m) {
    var main = document.querySelector("main");
    var rep = (m.insights || []).filter(function (i) { return i.kind === "coaching_report"; })[0];
    if (!rep) { main.innerHTML = '<p class="text-on-surface-variant py-20 text-center">No coaching report for this match yet.</p>'; return; }
    var p = rep.payload || {}, o = m.outcome || {};
    var b = badge(o.result), score = o.score || "—";
    var goals = (p.goals || []).map(function (g) {
      var scored = String(g.type || "").toLowerCase().indexOf("scor") === 0;
      var deep = g.deep && g.deep.root_cause ? g.deep : null;
      var deepHtml = deep ? '<div class="mt-2 p-3 rounded bg-surface-container-high border border-outline-variant"><p class="font-label-sm text-label-sm text-error mb-1 flex items-center gap-1"><span class="material-symbols-outlined text-[14px]">zoom_in</span> DEEP READ' + (deep.defender ? " · " + esc(deep.defender) : "") + '</p>' + (deep.what_happened ? '<p class="text-on-surface text-sm">' + esc(deep.what_happened) + "</p>" : "") + (deep.root_cause ? '<p class="text-on-surface-variant text-sm mt-1"><b>Root cause:</b> ' + esc(deep.root_cause) + "</p>" : "") + (deep.fix ? '<p class="text-on-surface-variant text-sm mt-1"><b>Fix:</b> ' + esc(deep.fix) + "</p>" : "") + "</div>" : "";
      var note = g.summary + (g.fix ? "  ·  Fix: " + g.fix : "");
      var timeChip = '<button class="time-chip flex items-center gap-1 px-2 py-1 rounded-full font-label-sm text-label-sm cursor-pointer ' + (scored ? "text-primary hover:bg-primary/10" : "text-error hover:bg-error/10") + '" data-t="' + esc(g.time) + '" data-c="' + esc(note) + '"><span class="material-symbols-outlined text-[16px]">play_circle</span> ' + esc(g.time) + "</button>";
      return '<div class="flex items-start gap-4 p-3 rounded bg-surface-container border-l-2 ' + (scored ? "border-primary" : "border-error") + '"><div class="flex flex-col items-start gap-1 min-w-[92px]"><span class="font-label-sm text-label-sm ' + (scored ? "text-primary" : "text-error") + '">' + (scored ? "GOAL" : "CONCEDED") + "</span>" + timeChip + '</div><div class="w-full"><p class="text-on-surface">' + esc(g.summary) + "</p>" + (!scored && g.fix && !deep ? '<p class="text-on-surface-variant text-sm mt-1">Fix: ' + esc(g.fix) + "</p>" : "") + deepHtml + "</div></div>";
    }).join("");
    var st = p.stats || {};
    var statList = [["goals_for", "GF"], ["goals_against", "GA"], ["shots", "Shots"], ["big_chances", "Big chances"], ["goals_conceded_from_crosses", "Conceded (crosses)"], ["defensive_errors", "Def. errors"]]
      .filter(function (s) { return typeof st[s[0]] === "number"; })
      .map(function (s) { return '<div class="bg-surface-container rounded p-3 text-center"><div class="font-display-lg text-headline-lg-mobile text-primary">' + st[s[0]] + '</div><div class="font-label-sm text-label-sm text-on-surface-variant uppercase">' + s[1] + "</div></div>"; }).join("");
    var ev = (p.evidence_log || []).map(function (e) {
      var t = times(e); var tm = (String(e).match(/\[([^\]]+)\]/) || [])[1] || "";
      return '<div class="flex items-start gap-4"><div class="min-w-[60px] pt-1"><span class="font-label-sm text-label-sm text-secondary-fixed-dim">' + esc(tm ? "[" + tm + "]" : "") + '</span></div><div class="w-full"><div class="bg-surface-container p-3 rounded border-l-2 border-outline-variant"><p class="font-body-md text-on-surface-variant">' + esc(String(e).replace(/^\s*\[[^\]]*\]\s*/, "")) + "</p></div></div></div>";
    }).join("");

    main.innerHTML =
      '<section class="mb-8"><div class="flex flex-col md:flex-row justify-between items-start md:items-end border-b border-surface-variant pb-6 mb-6"><div>'
      + '<p class="font-label-sm text-label-sm text-on-surface-variant mb-2 uppercase tracking-widest">Match Report' + (p.your_team && p.your_team.abbrev ? " • You: " + esc(p.your_team.abbrev) : "") + '</p>'
      + '<h1 class="font-display-lg text-headline-lg-mobile md:text-display-lg flex items-center gap-4">' + esc(o.score ? "Score " + score : "Match") + '<span class="' + b[1] + ' font-label-sm text-label-sm px-3 py-1 rounded tracking-wider font-bold">' + b[0] + "</span></h1></div>"
      + '<div class="mt-4 md:mt-0 font-display-lg text-display-lg ' + b[2] + ' tracking-tighter">' + esc(score) + "</div></div></section>"
      + '<section class="mb-10"><div class="bg-surface-container p-6 md:p-8 rounded-lg border-l-4 border-primary relative overflow-hidden"><h2 class="font-headline-lg text-headline-lg-mobile md:text-headline-lg mb-4 flex items-center gap-2"><span class="material-symbols-outlined text-primary">smart_toy</span> Coach\'s Summary</h2><p class="font-body-lg text-body-lg text-on-surface-variant max-w-4xl">' + esc(rep.summary) + "</p></div></section>"
      + '<section class="mb-12"><div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">'
      + section("What you did well", p.strengths, "border-primary", "check_circle")
      + section("Recurring mistakes", p.recurring_mistakes, "border-error", "warning")
      + section("Positioning", p.positioning_issues, "border-tertiary", "map")
      + section("Decision-making", p.decision_patterns, "border-secondary-fixed-dim", "lightbulb")
      + section("Practice focus", p.practice_drills, "border-outline", "my_location")
      + "</div></section>"
      + (goals ? '<section class="mb-12"><h2 class="font-headline-lg text-headline-lg-mobile md:text-headline-lg mb-4 flex items-center gap-2"><span class="material-symbols-outlined text-primary">scoreboard</span> Goal by goal</h2><div class="grid gap-3">' + goals + "</div></section>" : "")
      + (statList ? '<section class="mb-12"><h2 class="font-headline-lg text-headline-lg-mobile mb-4">Match stats</h2><div class="grid grid-cols-3 md:grid-cols-6 gap-3">' + statList + "</div></section>" : "")
      + (ev ? '<section class="mt-8"><details class="group bg-surface-container-low rounded-lg border border-surface-variant overflow-hidden"><summary class="flex justify-between items-center font-headline-lg-mobile p-6 cursor-pointer list-none"><div class="flex items-center gap-2"><span class="material-symbols-outlined">receipt_long</span> Evidence Log</div><span class="material-symbols-outlined transition-transform group-open:-rotate-180">expand_more</span></summary><div class="p-6 pt-0 border-t border-surface-variant"><div class="space-y-4 mt-4">' + ev + "</div></div></details></section>" : "");

    document.querySelectorAll(".time-chip").forEach(function (c) {
      c.addEventListener("click", function () {
        location.href = "/moment/?id=" + m.id
          + "&t=" + encodeURIComponent(c.getAttribute("data-t") || "")
          + "&c=" + encodeURIComponent(c.getAttribute("data-c") || "");
      });
    });
  }

  function initReport() {
    var id = qs("id");
    var main = document.querySelector("main");
    if (main) main.innerHTML = '<p class="text-on-surface-variant py-20 text-center">Loading report…</p>';
    var load = id ? getMatch(id) : listMatches().then(function (ms) {
      var done = (ms || []).filter(function (m) { return m.status === "complete"; })[0];
      return done ? getMatch(done.id) : null;
    });
    load.then(function (m) { if (m) renderReport(m); else if (main) main.innerHTML = '<p class="text-on-surface-variant py-20 text-center">No matches yet — upload one.</p>'; })
      .catch(function (e) { if (main) main.innerHTML = '<p class="text-error py-20 text-center">' + esc(e.message) + "</p>"; });
  }

  // ---- MOMENT viewer -------------------------------------------------------
  function toSeconds(t) {
    if (!t) return 0;
    var p = String(t).split(":").map(Number);
    if (p.length === 2) return p[0] * 60 + p[1];
    if (p.length === 3) return p[0] * 3600 + p[1] * 60 + p[2];
    return Number(t) || 0;
  }
  function buildMoments(m) {
    // Collect every timestamped item in the report into one sorted playlist.
    var rep = (m.insights || []).filter(function (i) { return i.kind === "coaching_report"; })[0];
    if (!rep) return [];
    var p = rep.payload || {}, out = [];
    (p.goals || []).forEach(function (g) {
      var scored = String(g.type || "").toLowerCase().indexOf("scor") === 0;
      var note = String(g.summary || "") + (g.fix ? "  ·  Fix: " + g.fix : "");
      if (g.time) out.push({ t: g.time, secs: toSeconds(g.time), kind: scored ? "GOAL" : "CONCEDED",
        color: scored ? "primary" : "error", note: note });
    });
    var sections = [["strengths", "STRENGTH", "primary"], ["recurring_mistakes", "MISTAKE", "error"],
      ["positioning_issues", "POSITIONING", "tertiary"], ["decision_patterns", "DECISION", "secondary-container"]];
    sections.forEach(function (s) {
      (p[s[0]] || []).forEach(function (item) {
        var parsed = times(item);
        parsed.chips.forEach(function (c) {
          out.push({ t: c, secs: toSeconds(c), kind: s[1], color: s[2], note: parsed.text });
        });
      });
    });
    out.sort(function (a, b) { return a.secs - b.secs; });
    return out;
  }

  function initMoment() {
    var id = qs("id"), t = qs("t"), c = qs("c");
    var main = document.querySelector("main") || document.body;
    var load = id ? getMatch(id) : listMatches().then(function (ms) {
      var done = (ms || []).filter(function (m) { return m.status === "complete"; })[0];
      return done ? getMatch(done.id) : null;
    });
    load.then(function (m) {
      if (!m) { main.innerHTML = '<p class="text-on-surface-variant py-20 text-center">No analysed matches yet — <a class="text-primary underline" href="/upload/">upload one</a>.</p>'; return; }
      renderMoments(m, t, c);
    }).catch(function (e) {
      main.innerHTML = '<p class="text-error py-20 text-center">' + esc(e.message) + "</p>";
    });
  }

  function renderMoments(m, startT, startC) {
    var main = document.querySelector("main") || document.body;
    var o = m.outcome || {}, b = badge(resultOf(o));
    var moments = buildMoments(m);
    var cur = -1;
    // If a ?t was passed, start on the nearest moment; else the first.
    if (startT) {
      var target = toSeconds(startT), best = -1, bestD = 1e9;
      moments.forEach(function (mo, i) { var d = Math.abs(mo.secs - target); if (d < bestD) { bestD = d; best = i; } });
      cur = best;
    } else if (moments.length) cur = 0;

    var listHtml = moments.map(function (mo, i) {
      return '<button data-i="' + i + '" class="cx-mom w-full text-left flex items-start gap-3 p-3 rounded-lg border border-white/5 bg-surface-container hover:bg-surface-container-high transition-colors">'
        + '<span class="font-label-sm text-label-sm text-' + mo.color + ' whitespace-nowrap pt-0.5">' + esc(mo.t) + "</span>"
        + '<span class="min-w-0"><span class="font-label-sm text-label-sm text-' + mo.color + ' block mb-1">' + esc(mo.kind) + "</span>"
        + '<span class="text-on-surface-variant text-sm block truncate">' + esc(mo.note) + "</span></span></button>";
    }).join("");

    main.innerHTML =
      '<section class="mb-6 flex flex-col md:flex-row md:items-end justify-between gap-3">'
      + '<div><p class="font-label-sm text-label-sm text-on-surface-variant uppercase tracking-widest mb-2">Moments</p>'
      + '<h1 class="font-display-lg text-headline-lg-mobile md:text-display-lg flex items-center gap-4">' + esc(o.score ? "Score " + o.score : "Match")
      + '<span class="' + b[1] + ' font-label-sm text-label-sm px-3 py-1 rounded tracking-wider font-bold">' + b[0] + "</span></h1></div>"
      + '<a href="/report/?id=' + m.id + '" class="text-on-surface-variant hover:text-primary flex items-center gap-1"><span class="material-symbols-outlined">receipt_long</span> Full report</a>'
      + "</section>"
      + '<div class="grid grid-cols-1 lg:grid-cols-12 gap-6">'
      + '<div class="lg:col-span-8">'
      + '<div class="rounded-lg overflow-hidden bg-black border border-surface-variant glow-primary">'
      + '<video id="cx-video" controls playsinline preload="metadata" class="w-full max-h-[62vh] bg-black"></video></div>'
      + '<div id="cx-note" class="mt-4"></div>'
      + '<div class="mt-4 flex items-center justify-between gap-4">'
      + '<button id="cx-prev" class="flex-1 flex items-center justify-center gap-2 font-label-sm text-label-sm text-on-surface-variant bg-surface-container hover:bg-surface-variant hover:text-on-surface py-3 px-4 rounded border border-white/5 transition-all active:scale-95"><span class="material-symbols-outlined">arrow_back</span> Prev moment</button>'
      + '<div id="cx-count" class="font-label-sm text-label-sm text-on-surface-variant whitespace-nowrap"></div>'
      + '<button id="cx-next" class="flex-1 flex items-center justify-center gap-2 font-label-sm text-label-sm text-surface-dim bg-primary hover:bg-primary-fixed py-3 px-4 rounded transition-all active:scale-95 font-bold">Next moment <span class="material-symbols-outlined">arrow_forward</span></button>'
      + "</div></div>"
      + '<div class="lg:col-span-4"><p class="font-label-sm text-label-sm text-on-surface-variant uppercase tracking-widest mb-3">Timeline (' + moments.length + ")</p>"
      + '<div id="cx-list" class="flex flex-col gap-2 max-h-[70vh] overflow-y-auto custom-scrollbar pr-1">'
      + (listHtml || '<p class="text-on-surface-variant text-sm">No timestamped moments in this report.</p>') + "</div></div>"
      + "</div>";

    var v = document.getElementById("cx-video");
    v.src = API + "/api/matches/" + m.id + "/video";
    var pending = startT ? toSeconds(startT) : (cur >= 0 ? moments[cur].secs : 0);
    var ready = false;
    function seekTo(secs) {
      pending = secs;
      if (!ready) return;
      try { v.currentTime = Math.max(0, secs - 2); } catch (e) {}
      v.play().catch(function () {});
    }
    function armed() { if (ready) return; ready = true; if (pending) seekTo(pending); }
    v.addEventListener("loadedmetadata", armed);
    v.addEventListener("canplay", armed);

    function note(html) { document.getElementById("cx-note").innerHTML = html; }
    function noteCard(label, color, text) {
      return '<div class="p-5 rounded-lg bg-surface-container border-l-4 border-' + color + '">'
        + '<div class="flex items-center gap-2 mb-2"><span class="material-symbols-outlined text-' + color + '">smart_toy</span>'
        + '<span class="font-label-sm text-label-sm text-on-surface-variant uppercase tracking-wider">Coach\'s note · ' + esc(label) + "</span></div>"
        + '<p class="font-body-lg text-body-lg text-on-surface">' + esc(text) + "</p></div>";
    }
    var listBtns = [].slice.call(document.querySelectorAll(".cx-mom"));
    function select(i, seek) {
      if (i < 0 || i >= moments.length) return;
      cur = i;
      var mo = moments[i];
      listBtns.forEach(function (btn, k) {
        btn.className = btn.className.replace(/ (ring-1 ring-primary bg-surface-container-high)/g, "");
        if (k === i) btn.className += " ring-1 ring-primary bg-surface-container-high";
      });
      var el = listBtns[i]; if (el && el.scrollIntoView) el.scrollIntoView({ block: "nearest" });
      note(noteCard(mo.kind + " · " + mo.t, mo.color, mo.note));
      document.getElementById("cx-count").textContent = (i + 1) + " / " + moments.length;
      if (seek !== false) seekTo(mo.secs);
    }
    listBtns.forEach(function (btn) {
      btn.addEventListener("click", function () { select(parseInt(btn.getAttribute("data-i"), 10)); });
    });
    document.getElementById("cx-prev").addEventListener("click", function () { select(cur - 1); });
    document.getElementById("cx-next").addEventListener("click", function () { select(cur + 1); });

    if (cur >= 0) select(cur, false);           // highlight + note without forcing autoplay
    else if (startC) note(noteCard("Moment", "primary", startC));
    document.getElementById("cx-count").textContent = moments.length ? ((cur + 1) + " / " + moments.length) : "0 / 0";
  }

  // ---- nav: ensure a "Moments" link exists on every page --------------------
  function injectMomentsNav() {
    if (document.querySelector('nav a[href="/moment/"]')) return;
    // Clone the styling of each nav's Trends link so it matches that page's nav.
    [].slice.call(document.querySelectorAll('nav a[href="/trends/"]')).forEach(function (tr) {
      var a = tr.cloneNode(false);
      a.href = "/moment/";
      a.textContent = "Moments";
      tr.parentNode.insertBefore(a, tr);
    });
  }

  // ---- router --------------------------------------------------------------
  document.addEventListener("DOMContentLoaded", function () {
    injectMomentsNav();
    var path = location.pathname.replace(/index\.html$/, "");
    if (/\/upload\/?$/.test(path)) initUpload();
    else if (/\/analyzing\/?$/.test(path)) initAnalyzing();
    else if (/\/report\/?$/.test(path)) initReport();
    else if (/\/moment\/?$/.test(path)) initMoment();
  });
})();
