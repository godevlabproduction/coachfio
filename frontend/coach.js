/* Coachfio - wires the static frontend to the backend API.
 * Loaded on every page; it detects the page and hooks it up.
 *
 * Markup contract: this file finds elements through `data-cx-*` attributes and
 * a few stable ids/classes (`[data-side]`, `.time-chip`, `#cx-controls`,
 * `#cx-skill`). It deliberately does NOT match on styling classes - the old
 * version keyed off `.lg\:col-span-4` and a button whose text read "Browse
 * files", which broke on any restyle. Keep the data attributes when editing the
 * HTML and the pages stay decoupled from the CSS.
 */
(function () {
  "use strict";

  // API base: when served on :3000 (npm run dev) talk to the backend on :8000;
  // served same-origin by the API itself, relative URLs are correct.
  var API = location.port === "3000" ? "http://localhost:8000" : "";
  // Which game this hostname serves. One subdomain per game (fifa.coachfio.com),
  // one frontend for all of them, so this is asked for at boot rather than baked
  // in. The literal below is only the fallback for when /api/site cannot be
  // reached; the server is the authority, so a new game never needs a frontend
  // edit. `SITE` carries the display name and the list of sibling sites.
  var GAME = { game_id: "ea-fc", edition: "26" };
  var SITE = { game: null, sites: [] };

  // ---- identity ------------------------------------------------------------
  // The signed-in account. The backend reads it from the X-User-Id header at the
  // `current_user` auth seam; a hosted provider replaces this whole block with a
  // real token and nothing else in the app has to change.
  var ID_KEY = "coachio.user";
  function identity() {
    try { return localStorage.getItem(ID_KEY) || ""; } catch (_) { return ""; }
  }
  function setIdentity(v) {
    try { localStorage.setItem(ID_KEY, v); } catch (_) {}
  }
  function clearIdentity() {
    try { localStorage.removeItem(ID_KEY); } catch (_) {}
  }
  function withAuth(headers) {
    var h = headers || {};
    var id = identity();
    if (id) h["X-User-Id"] = id;
    return h;
  }
  // For <video> and EventSource, which cannot set request headers.
  function authQuery(sep) {
    var id = identity();
    return id ? (sep || "?") + "u=" + encodeURIComponent(id) : "";
  }

  // ---- API helpers ---------------------------------------------------------
  function j(url, opts) {
    opts = opts || {};
    opts.headers = withAuth(opts.headers);
    return fetch(API + url, opts).then(function (r) {
      if (!r.ok) {
        // Surface the API's own message ("that email already exists…") rather
        // than a status code the reader can do nothing with.
        return r.json().catch(function () { return {}; }).then(function (body) {
          var err = new Error(body.detail || (r.status === 402
            ? "You've hit the free match limit."
            : "Request failed (" + r.status + ")"));
          err.status = r.status;  // callers branch on 404 vs transient failure
          throw err;
        });
      }
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
  // XHR rather than fetch: it reports upload progress, and a match video is big
  // enough that a silent multi-minute POST feels broken.
  function uploadSource(id, file, onProgress) {
    return new Promise(function (resolve, reject) {
      var fd = new FormData();
      fd.append("file", file, file.name);
      var xhr = new XMLHttpRequest();
      xhr.open("POST", API + "/api/matches/" + id + "/source");
      if (identity()) xhr.setRequestHeader("X-User-Id", identity());
      xhr.upload.onprogress = function (e) {
        if (e.lengthComputable && onProgress) onProgress(e.loaded / e.total);
      };
      xhr.onload = function () {
        if (xhr.status >= 200 && xhr.status < 300) resolve();
        else if (xhr.status === 402) reject(new Error("You've hit the free match limit."));
        else reject(new Error("Upload failed (" + xhr.status + ")"));
      };
      xhr.onerror = function () { reject(new Error("Upload failed - is the server running?")); };
      xhr.send(fd);
    });
  }
  function completeMatch(id) { return j("/api/matches/" + id + "/complete", { method: "POST" }); }
  var getMatch = function (id) { return j("/api/matches/" + id); };
  var listMatches = function () {
    return j("/api/matches?game_id=" + GAME.game_id + "&edition=" + GAME.edition)
      .catch(function () { return []; });
  };
  var getTrends = function (last) {
    return j("/api/matches/trends/" + GAME.game_id + "/" + GAME.edition
             + (last ? "?last=" + last : ""))
      .catch(function () { return []; });
  };
  var getPatterns = function (last) {
    return j("/api/matches/patterns/" + GAME.game_id + "/" + GAME.edition
             + "?last=" + (last || 50))
      .catch(function () { return { matches: 0, issues: [] }; });
  };

  // ---- utilities -----------------------------------------------------------
  function qs(k) { return new URLSearchParams(location.search).get(k); }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }
  function $(sel, root) { return (root || document).querySelector(sel); }
  function $$(sel, root) { return [].slice.call((root || document).querySelectorAll(sel)); }

  // Pull "(12:30, 34:10)" chips off the end of a coaching point.
  //
  // The trailing `[.!?]?` matters: the model writes the sentence's full stop
  // AFTER the bracket ("...far corner (01:55, 02:18)."), and requiring `)` to be
  // the very last character meant an entire report rendered with no clickable
  // timestamps at all. Also tolerates a trailing bare "." left behind once the
  // bracket is stripped.
  function times(text) {
    // Report points come back as {point, evidence_ids} on the two-pass path and as
    // bare strings on the single-call path. Stringifying an object here rendered a
    // literal "[object Object]" in place of every coaching point.
    if (text && typeof text === "object" && !Array.isArray(text)) text = text.point;
    var s = String(text == null ? "" : text);
    var m = s.match(/\((\d{1,3}:\d{2}(?:\s*,\s*\d{1,3}:\d{2})*)\)\s*[.!?;,]?\s*$/);
    if (!m) return { text: s, chips: [] };
    return {
      text: s.slice(0, m.index).replace(/[\s.,;]+$/, "").trim(),
      chips: m[1].split(/\s*,\s*/),
    };
  }
  function resultOf(o) { return (o && o.result) || ""; }
  // -> [label, badge modifier, result modifier, text colour class]
  function badge(res) {
    res = (res || "").toLowerCase();
    if (res === "win") return ["Win", "badge--win", "result--win", "k-good"];
    if (res === "loss") return ["Loss", "badge--loss", "result--loss", "k-bad"];
    return ["Draw", "badge--draw", "result--draw", ""];
  }
  // Page header for report/moments: caption, score, result badge, one action.
  // Plain type on the page ground - no panel, no accent bar.
  function scoreHeader(o, eyebrow, actionsHtml) {
    var b = badge(resultOf(o));
    return '<div class="page-head"><div>'
      + '<p class="eyebrow">' + eyebrow + "</p>"
      + '<div class="scoreline"><span class="scoreline__value">' + esc(o.score || "-") + "</span>"
      + '<span class="badge ' + b[1] + '">' + b[0] + "</span></div></div>"
      + (actionsHtml || "") + "</div>";
  }
  function fmtDate(iso) {
    if (!iso) return "";
    var d = new Date(iso);
    if (isNaN(d)) return "";
    return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  }
  function icon(name, cls) {
    return '<span class="material-symbols-outlined' + (cls ? " " + cls : "") + '">' + name + "</span>";
  }
  // ---- confirm dialog -------------------------------------------------------
  // Replaces window.confirm, which renders in the OS chrome: white, system font,
  // and it says "localhost:8000 says" above your copy. Returns a Promise so call
  // sites read the same as the native one they replaced.
  function confirmDialog(opts) {
    return new Promise(function (resolve) {
      var prev = document.activeElement;
      var host = document.createElement("div");
      host.className = "modal";
      host.innerHTML =
        '<div class="modal__backdrop" data-cx-cancel></div>'
        + '<div class="modal__box" role="alertdialog" aria-modal="true"'
        + ' aria-labelledby="cx-dlg-t" aria-describedby="cx-dlg-d">'
        + '<div class="modal__head">'
        + '<span class="tile ' + (opts.danger ? "tile--danger" : "tile--accent") + '">'
        + icon(opts.icon || (opts.danger ? "warning" : "help")) + "</span>"
        + '<h2 class="modal__title" id="cx-dlg-t">' + esc(opts.title) + "</h2></div>"
        + '<p class="modal__text" id="cx-dlg-d">' + esc(opts.text || "") + "</p>"
        + '<div class="modal__actions">'
        + '<button type="button" class="btn btn--ghost" data-cx-cancel>'
        + esc(opts.cancelLabel || "Cancel") + "</button>"
        + '<button type="button" class="btn ' + (opts.danger ? "btn--danger" : "btn--primary")
        + '" data-cx-ok>' + esc(opts.confirmLabel || "Confirm") + "</button>"
        + "</div></div>";
      document.body.appendChild(host);
      document.body.classList.add("is-modal");

      function close(result) {
        document.removeEventListener("keydown", onKey);
        document.body.classList.remove("is-modal");
        host.remove();
        // Send focus back where it came from, or the page loses its place.
        if (prev && prev.focus) prev.focus();
        resolve(result);
      }
      function onKey(e) {
        if (e.key === "Escape") close(false);
        // Keep Tab inside the dialog while it is open.
        if (e.key === "Tab") {
          var f = $$("button", host);
          var i = f.indexOf(document.activeElement);
          if (i === -1) { f[0].focus(); e.preventDefault(); return; }
          var next = e.shiftKey ? i - 1 : i + 1;
          if (next < 0 || next >= f.length) { f[e.shiftKey ? f.length - 1 : 0].focus(); e.preventDefault(); }
        }
      }
      $$("[data-cx-cancel]", host).forEach(function (b) {
        b.addEventListener("click", function () { close(false); });
      });
      $("[data-cx-ok]", host).addEventListener("click", function () { close(true); });
      document.addEventListener("keydown", onKey);
      $("[data-cx-ok]", host).focus();
    });
  }

  function emptyState(iconName, title, text, actionHtml) {
    return '<div class="empty"><div class="empty__icon">' + icon(iconName) + "</div>"
      + '<p class="empty__title">' + esc(title) + "</p>"
      + '<p class="empty__text">' + esc(text) + "</p>"
      + (actionHtml ? '<div class="empty__actions">' + actionHtml + "</div>" : "")
      + "</div>";
  }
  function errorState(msg) {
    return '<div class="alert alert--danger">' + icon("error")
      + '<div class="alert__body"><p class="alert__title">Something went wrong</p>'
      + '<p class="muted t-sm">' + esc(msg) + "</p></div></div>";
  }
  function showError(msg) {
    var box = $("[data-cx-error]");
    if (!box) return;
    box.innerHTML = errorState(msg);
    box.hidden = false;
  }

  // ---- shared: recent match list ------------------------------------------
  function matchRow(m) {
    var b = badge(resultOf(m.outcome));
    var score = (m.outcome && m.outcome.score) || "-";
    // The W/L/D marker already carries the result; no second colour cue needed.
    return '<a class="match-row" href="/report/?id=' + esc(m.id) + '">'
      + '<span class="result ' + b[2] + '">' + b[0][0] + "</span>"
      + '<span class="match-row__main">'
      + '<span class="match-row__title mono">' + esc(score) + "</span>"
      + '<span class="match-row__meta">' + esc(b[0])
      + (m.created_at ? " · " + esc(fmtDate(m.created_at)) : "") + "</span></span>"
      + icon("chevron_right", "faint") + "</a>";
  }
  function loadRecent(quiet) {
    var host = $("[data-cx-recent]");
    if (!host) return;
    if (!identity()) {
      host.innerHTML = signInPrompt("Your matches live in your account",
        "Create an account to upload a match and keep your reports and progress.", quiet);
      return;
    }
    listMatches().then(function (ms) {
      var done = (ms || []).filter(function (m) { return m.status === "complete"; });
      done.sort(function (a, b) { return (b.created_at || "").localeCompare(a.created_at || ""); });
      done = done.slice(0, 6);
      if (!done.length) {
        host.innerHTML = emptyState("video_library", "No matches yet",
          "Upload your first match and the coach will break it down for you.",
          '<a class="btn btn--primary btn--sm" href="/upload/">Upload a match</a>');
        return;
      }
      host.innerHTML = '<div class="stack-s reveal-list">' + done.map(matchRow).join("") + "</div>";
    });
  }

  // ---- UPLOAD page ---------------------------------------------------------
  function initUpload() {
    var side = "home";
    var sideBtns = $$("[data-side]");
    function setSide(s) {
      side = s;
      sideBtns.forEach(function (b) {
        b.setAttribute("aria-pressed", String(b.getAttribute("data-side") === s));
      });
    }
    sideBtns.forEach(function (b) {
      b.addEventListener("click", function () { setSide(b.getAttribute("data-side")); });
    });
    setSide("home");

    var guest = !identity();
    if (guest) {
      // Guests can see the whole flow; only the analysis itself needs an account,
      // because the result has to belong to someone.
      var head = $(".page-head");
      if (head) {
        var banner = document.createElement("div");
        banner.className = "alert alert--info";
        banner.style.marginBottom = "24px";
        banner.innerHTML = icon("info")
          + '<div class="alert__body"><p class="alert__title">Create an account to analyse a match</p>'
          + '<p class="muted t-sm">Your report, video and progress are saved to your account. '
          + "It takes a few seconds and you pick your coaching level as you go.</p>"
          + '<div class="row" style="margin-top:12px">'
          + '<a class="btn btn--primary btn--sm" href="/signin/?signup=1">Create account</a>'
          + '<a class="btn btn--ghost btn--sm" href="/signin/">Sign in</a></div></div>';
        head.parentNode.insertBefore(banner, head.nextSibling);
      }
    } else {
      // Pre-fill from the account so an upload inherits the player's coaching level
      // without them re-picking it every time. Still overridable for this match.
      // Side is deliberately NOT pre-filled from the profile - it changes every
      // match, so it always starts at Home and you pick it here.
      getAccount().then(function (d) {
        var p = d.profile || {};
        var ctrl = $("#cx-controls"), skill = $("#cx-skill");
        if (ctrl && p.control_scheme) ctrl.value = p.control_scheme;
        if (skill && p.skill_level) skill.value = p.skill_level;
        // Coach accounts say WHO the footage is of. The name flips the report to
        // third person, groups the office by athlete, and the checkbox is the
        // consent record - required, because it is someone else's data.
        if (p.role === "coach") {
          var ath = $("[data-cx-athlete-block]");
          if (ath) ath.hidden = false;
          var nameEl = $("#cx-athlete");
          try {
            if (nameEl && !nameEl.value)
              nameEl.value = localStorage.getItem("coachio.lastAthlete") || "";
          } catch (e) {}
          var sideQ = $("[data-cx-side-q]");
          if (sideQ) sideQ.textContent = "Which side did your player use?";
        }
      }).catch(function () {});
    }

    var zone = $("[data-cx-dropzone]");
    var title = $("[data-cx-dropzone-title]");
    var hint = $("[data-cx-dropzone-hint]");
    var progWrap = $("[data-cx-upload-progress]");
    var progBar = progWrap && $(".progress__bar", progWrap);

    var input = document.createElement("input");
    input.type = "file";
    input.accept = "video/*";
    input.className = "sr-only";
    document.body.appendChild(input);

    var busy = false;
    function go(file) {
      if (guest) { location.href = "/signin/?signup=1"; return; }
      if (!file || busy) return;
      busy = true;
      var errBox = $("[data-cx-error]");
      if (errBox) errBox.hidden = true;
      if (zone) zone.classList.add("is-busy");
      if (title) title.textContent = "Uploading " + (file.size / 1e6).toFixed(0) + " MB…";
      if (hint) hint.textContent = file.name;
      if (progWrap) progWrap.hidden = false;

      var extra = {
        control_scheme: ($("#cx-controls") || {}).value || "",
        skill_level: ($("#cx-skill") || {}).value || "",
      };
      var athBlock = $("[data-cx-athlete-block]");
      if (athBlock && !athBlock.hidden) {
        var athName = (($("#cx-athlete") || {}).value || "").trim();
        var consent = $("#cx-consent");
        if (athName && !(consent && consent.checked)) {
          busy = false;
          if (zone) zone.classList.remove("is-busy");
          if (progWrap) progWrap.hidden = true;
          return showError("Confirm you have " + athName + "'s permission to analyse their footage.");
        }
        if (athName) {
          extra.athlete = athName;
          extra.athlete_consent = "yes";
          try { localStorage.setItem("coachio.lastAthlete", athName); } catch (e) {}
        }
      }
      createMatch(side, extra)
        .then(function (m) {
          return uploadSource(m.match_id, file, function (frac) {
            if (progBar) progBar.style.width = Math.round(frac * 100) + "%";
            if (title && frac >= 1) title.textContent = "Finishing upload…";
          }).then(function () { return m; });
        })
        .then(function (m) { return completeMatch(m.match_id).then(function () { return m; }); })
        .then(function (m) { location.href = "/analyzing/?id=" + m.match_id; })
        .catch(function (e) {
          busy = false;
          if (zone) zone.classList.remove("is-busy");
          if (progWrap) progWrap.hidden = true;
          if (progBar) progBar.style.width = "0%";  // don't resume from the failed attempt
          if (title) title.textContent = "Drop your match video here";
          if (hint) hint.textContent = "or browse for a file - 720p or better";
          showError(e.message);
        });
    }

    input.addEventListener("change", function () { go(input.files[0]); });

    if (zone) {
      zone.addEventListener("click", function () {
        if (guest) { location.href = "/signin/?signup=1"; return; }
        input.click();
      });
      zone.addEventListener("keydown", function (e) {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); input.click(); }
      });
      zone.addEventListener("dragover", function (e) {
        e.preventDefault();
        zone.classList.add("is-over");
      });
      zone.addEventListener("dragleave", function () { zone.classList.remove("is-over"); });
      zone.addEventListener("drop", function (e) {
        e.preventDefault();
        zone.classList.remove("is-over");
        go(e.dataTransfer.files[0]);
      });
    }

    loadRecent(guest);  // the banner above already carries the sign-up buttons
  }

  // ---- ANALYZING page ------------------------------------------------------
  function stepPct(stage, status, detail) {
    var t = ((status || "") + " " + (detail || "") + " " + (stage || "")).toLowerCase();
    // Only the pipeline finishing means 100%. Stages emit "done" for their own
    // step, which used to slam the bar to 100% and label it "Done" mid-run.
    if (/done|complete/.test(t)) return stage === "pipeline" ? 100 : 96;
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
  // How long a typical match takes end to end. Only used to pace the bar - the
  // real outcome always comes from the pipeline, never from this clock.
  var EXPECTED_MS = 165000;   // ~2:45
  var CREEP_CAP = 95;         // never reach 100 on a guess

  // Elapsed-time position: rises fast at first, then ever more slowly, and
  // approaches the cap without arriving. Tying it to the clock rather than to the
  // next milestone is what keeps it moving during the Gemini watch call, which
  // emits nothing for minutes at a time.
  function creepTarget(elapsedMs) {
    return CREEP_CAP * (1 - Math.exp(-1.6 * (elapsedMs / EXPECTED_MS)));
  }

  function labelFor(pct) {
    if (pct >= 100) return "Done";
    if (pct >= 92) return "Learning";
    if (pct >= 80) return "Writing your coaching report";
    if (pct >= 60) return "Cross-checking and reading your squad";
    if (pct >= 42) return "Watching your match";
    if (pct >= 16) return "Uploading to the coach";
    return "Starting up";
  }
  // Drive the visible stepper off the same percentage, so the dots never
  // disagree with the bar.
  function setSteps(pct) {
    var bounds = { upload: [0, 26], watch: [26, 60], score: [60, 80], write: [80, 100] };
    Object.keys(bounds).forEach(function (key) {
      var el = $('[data-step="' + key + '"]');
      if (!el) return;
      var lo = bounds[key][0], hi = bounds[key][1];
      el.classList.toggle("is-done", pct >= hi);
      el.classList.toggle("is-active", pct >= lo && pct < hi);
    });
  }

  function initAnalyzing() {
    var id = qs("id");
    if (!id) { location.href = "/upload/"; return; }
    if (!identity()) { location.href = "/signin/"; return; }

    var pctEl = $("[data-cx-pct]"), barEl = $("[data-cx-bar]"),
        stepEl = $("[data-cx-step]"), headEl = $("[data-cx-heading]");
    var pct = 0, terminal = { complete: 1, failed: 1, over_budget: 1 }, finished = false;

    function set(p, lbl) {
      p = Math.max(pct, Math.min(100, p));
      pct = p;
      if (pctEl) pctEl.textContent = Math.round(p) + "%";
      if (barEl) barEl.style.width = p + "%";
      if (stepEl) stepEl.textContent = lbl || labelFor(p);
      setSteps(p);
    }
    function fail(heading, message) {
      if (finished) return;
      finished = true;
      clearInterval(creep);
      if (headEl) headEl.textContent = heading;
      if (stepEl) stepEl.textContent = "";
      showError(message);
    }
    // Takes the whole match, not just the status: the pipeline records WHY it
    // failed in `warnings`, and a generic "didn't finish" sends you digging
    // through worker logs for something the server already knew.
    function done(m) {
      var status = (m && m.status) || m;
      if (finished) return;
      if (status === "complete") {
        finished = true;
        clearInterval(creep);
        set(100, "Done");
        setTimeout(function () { location.href = "/report/?id=" + id; }, 400);
        return;
      }
      // Second line of defence: only the match's own terminal status may end this
      // screen. If the stream closes early while the run is still going, keep
      // polling rather than announcing a failure that hasn't happened.
      if (!terminal[status]) return;
      var why = ((m && m.warnings) || []).filter(Boolean).join(" ");
      fail(status === "over_budget" ? "Over budget" : "Analysis failed",
        why || (status === "over_budget"
          ? "This match would cost more than the configured budget cap, so it was stopped."
          : "The analysis didn't finish. You can try uploading the match again."));
    }

    set(0);

    // Continuous, clock-driven creep. The Gemini watch is a single call that
    // emits nothing for minutes, so the bar used to park at 42% - looking hung -
    // and then snap to 100%. Pacing it off elapsed time keeps it visibly moving
    // the whole way; real pipeline events still jump it forward because set() is
    // monotonic. It approaches 95% and stops: only the pipeline says 100%.
    var startedAt = Date.now();
    var creep = setInterval(function () {
      if (finished) return;
      var target = creepTarget(Date.now() - startedAt);
      if (target > pct) set(target);      // set() is monotonic, so events still win
    }, 600);

    try {
      // EventSource cannot set headers, so identity rides in the query string.
      var es = new EventSource(API + "/api/matches/" + id + "/progress" + authQuery());
      es.addEventListener("progress", function (e) {
        var d = JSON.parse(e.data);
        var p = stepPct(d.stage, d.status, d.detail);
        if (p) set(p, d.detail || labelFor(p));
      });
      es.addEventListener("done", function () {
        es.close();
        // If this read fails, say nothing - the poll below is still running and
        // will reach the real outcome.
        getMatch(id).then(done).catch(function () {});
      });
      es.addEventListener("error", function () { es.close(); });
    } catch (_) {}

    // Polling safety net - also creeps the bar so it never looks frozen.
    // Poll failures must surface: this used to swallow every error, so a deleted
    // or mistyped match id left the page showing "Starting up / 0%" forever.
    var tries = 0, misses = 0;
    (function poll() {
      getMatch(id).then(function (m) {
        misses = 0;
        if (terminal[m.status]) return done(m);
      }).catch(function (e) {
        if (e && e.status === 404) {
          return fail("Match not found",
            "We couldn't find that match - it may have been deleted. Try uploading it again.");
        }
        if (++misses >= 5) {
          fail("Connection lost",
            "We lost contact with the server while analysing. The report is saved if it finished - check your reports.");
        }
      });
      if (!finished && ++tries < 200) setTimeout(poll, 3000);
    })();
  }

  // ---- REPORT page ---------------------------------------------------------
  function chip(time, note, tone) {
    return '<button type="button" class="chip time-chip ' + (tone || "")
      + '" data-t="' + esc(time) + '" data-c="' + esc(note || "") + '">'
      + icon("play_circle") + esc(time) + "</button>";
  }

  function pointsCard(title, items, tone, iconName) {
    if (!items || !items.length) return "";
    var lis = items.map(function (it) {
      var t = times(it);
      var chips = t.chips.map(function (c) { return chip(c, t.text); }).join("");
      return "<li><span>" + esc(t.text) + "</span>"
        + (chips ? '<span class="chip-row">' + chips + "</span>" : "") + "</li>";
    }).join("");
    return '<div class="card"><div class="card__head">'
      + '<span class="tile ' + tone + '">' + icon(iconName) + "</span>"
      + '<h3 class="card__title">' + esc(title) + "</h3></div>"
      + '<ul class="points">' + lis + "</ul></div>";
  }

  // ---- report template sections -------------------------------------------
  // Every builder returns "" when its data is absent, so a report written before
  // the template existed renders exactly as it used to instead of showing a page
  // of empty headings.

  // Some fields legitimately come back as "not measurable from this footage" or
  // "not visible in this footage" - the model is instructed to say so rather than
  // invent. Render those as an explicit, muted gap: it is information, and
  // dressing it up as a normal value would hide that the footage fell short.
  var _GAP = /^\s*(not measurable|not visible|unknown|n\/a|none)\b/i;
  function gapAware(v) {
    var t = String(v == null ? "" : v).trim();
    if (!t) return "";
    return _GAP.test(t) ? '<span class="faint">' + esc(t) + "</span>" : esc(t);
  }

  function kvRows(obj, fields) {
    if (!obj) return "";
    var rows = fields.map(function (f) {
      var v = gapAware(obj[f[0]]);
      return v ? '<div class="kv"><dt>' + esc(f[1]) + "</dt><dd>" + v + "</dd></div>" : "";
    }).join("");
    return rows ? '<dl class="kv-list">' + rows + "</dl>" : "";
  }

  function sectionCard(title, iconName, tone, body) {
    if (!body) return "";
    return '<section class="section"><div class="card"><div class="card__head">'
      + '<span class="tile ' + (tone || "") + '">' + icon(iconName) + "</span>"
      + '<h2 class="card__title">' + esc(title) + "</h2></div>" + body + "</div></section>";
  }

  function diagnosisBlock(d) {
    var body = kvRows(d, [
      ["biggest_strength", "Biggest strength"],
      ["biggest_repeatable_mistake", "Biggest repeatable mistake"],
      ["highest_value_habit", "Highest-value habit to fix"],
      ["main_tactical_problem", "Main tactical problem"],
      ["main_mechanical_problem", "Main mechanical problem"],
    ]);
    return sectionCard("Executive diagnosis", "target", "tile--accent", body);
  }

  function contextBlock(c) {
    var body = kvRows(c, [
      ["mode", "Mode"], ["my_formation", "My formation"],
      ["opponent_formation", "Opponent formation"], ["result", "Result"],
      ["score_by_phase", "Score by phase"], ["technical_issues", "Technical issues"],
      ["sample_quality", "Sample quality"], ["confidence", "Confidence"],
    ]);
    return sectionCard("Match context", "info", "tile--info", body);
  }

  var _SEV = { high: "badge--loss", medium: "badge--warn", low: "badge--neutral" };
  function eventLogBlock(events, matchId) {
    if (!events || !events.length) return "";
    var rows = events.map(function (e) {
      var sev = String(e.severity || "").toLowerCase();
      var repeat = String(e.repeat_count || "").trim();
      return '<div class="ev">'
        + '<div class="ev__head">'
        + (e.time ? chip(e.time, String(e.what_i_did || ""), sev === "high" ? "chip--bad" : "") : "")
        + (e.phase ? '<span class="pill">' + esc(e.phase) + "</span>" : "")
        + (sev ? '<span class="badge ' + (_SEV[sev] || "badge--neutral") + '">' + esc(sev) + "</span>" : "")
        + (repeat && repeat !== "1" ? '<span class="faint t-xs">×' + esc(repeat) + " in this match</span>" : "")
        + "</div>"
        + '<div class="ev__body">'
        + kvRows(e, [
            ["ball_location", "Ball"], ["selected_player", "You controlled"],
            ["what_i_did", "What you did"], ["best_option", "Better option"],
            ["why", "Why it went that way"], ["correction", "Correction"],
          ])
        + "</div></div>";
    }).join("");
    return '<section class="section"><div class="section__head">'
      + '<h2 class="t-section">Event log</h2>'
      + '</div>'
      + '<div class="stack-s reveal-list">' + rows + "</div></section>";
  }


  function attackingBlock(a) {
    return sectionCard("Attacking analysis", "sports_soccer", "tile--accent", kvRows(a, [
      ["build_up_angles", "Build-up angles"], ["use_of_width", "Use of width"],
      ["half_space_occupation", "Half-spaces"], ["third_man_runs", "Third-man runs"],
      ["striker_movement", "Striker movement"], ["cam_movement", "CAM movement"],
      ["overlaps_underlaps", "Overlaps / underlaps"], ["cutback_creation", "Cutback creation"],
      ["shot_selection", "Shot selection"], ["rushes_final_action", "Rushing the final action"],
    ]));
  }

  function defendingBlock(d) {
    return sectionCard("Defensive analysis", "shield", "tile--danger", kvRows(d, [
      ["shape", "Shape"], ["cdm_positioning", "CDM positioning"],
      ["centre_back_movement", "Centre-back movement"], ["player_switching", "Player switching"],
      ["jockey_and_sprint_usage", "Jockey / sprint usage"], ["pressing_angles", "Pressing angles"],
      ["through_ball_prevention", "Through-ball prevention"],
      ["cutback_prevention", "Cutback prevention"],
      ["recovery_after_losing_possession", "Recovery after losing it"],
      ["fullback_exposure", "Fullback exposure"],
    ]));
  }

  function eliteBlock(e) {
    if (!e) return "";
    function list(items, label, cls) {
      if (!items || !items.length) return "";
      return '<p class="eyebrow" style="margin-top:12px">' + label + "</p><ul class=\"points\">"
        + items.map(function (i) { return '<li><span class="' + cls + '">' + esc(i) + "</span></li>"; }).join("")
        + "</ul>";
    }
    var body = list(e.habits_already_shown, "Habits you already show", "")
      + list(e.habits_missing, "Habits you lack", "")
      + kvRows(e, [["smallest_next_step", "Smallest thing to practise first"],
                   ["reference_gaps", "Missing reference data"]]);
    return sectionCard("Elite comparison", "military_tech", "tile--warn", body);
  }

  function practicePlanBlock(plan) {
    if (!plan || !plan.length) return "";
    var cards = plan.map(function (pr, i) {
      return '<div class="card prio"><div class="card__head">'
        + '<span class="tile tile--accent prio__n">' + (i + 1) + "</span>"
        + '<h3 class="card__title">' + esc(pr.problem || "Priority " + (i + 1)) + "</h3></div>"
        + kvRows(pr, [
            ["drill", "Drill"], ["reps", "Repetitions"],
            ["success_metric", "Success metric"], ["common_mistake", "Common mistake"],
          ])
        + (pr.correction_phrase
            ? '<p class="prio__say">' + icon("record_voice_over") + esc(pr.correction_phrase) + "</p>"
            : "")
        + "</div>";
    }).join("");
    return '<section class="section"><div class="section__head">'
      + '<h2 class="t-section">Practice plan</h2>'
      + '</div>'
      + '<div class="grid grid-3 reveal-list">' + cards + "</div></section>";
  }


  function tacticalBlock(changes) {
    // An empty list is a real answer here, not missing data: the coach is told to
    // change nothing unless the video proves the tactic contributed. Saying so is
    // more useful than hiding the section.
    if (!changes) return "";
    if (!changes.length) {
      return sectionCard("Tactical recommendation", "tune", "",
        '<p class="muted">Nothing in this match pointed at your tactics. The problems '
        + "above are habits, not settings, and changing the setup now would add a new "
        + "weakness without fixing anything.</p>");
    }
    var rows = changes.map(function (c) {
      return '<div class="well" style="margin-top:12px">'
        + '<p class="t-sm"><strong>' + esc(c.current_setting || "") + "</strong>"
        + '<span class="faint"> &rarr; </span><strong>' + esc(c.new_setting || "") + "</strong></p>"
        + kvRows(c, [["problem_it_solves", "Solves"],
                     ["new_weakness_created", "New weakness this creates"],
                     ["reverse_when", "Reverse it when"]])
        + "</div>";
    }).join("");
    return sectionCard("Tactical recommendation", "tune", "", rows);
  }

  function nextVideoBlock(n) {
    var body = kvRows(n, [
      ["match_type", "Match type"], ["formation", "Formation"],
      ["behaviour_to_practise", "Practise this"],
      ["behaviour_not_to_change", "Do NOT change this"],
      ["metrics_to_compare", "Compare these metrics"],
      ["minimum_sample_size", "Minimum sample"],
    ]);
    return sectionCard("Next video to record", "videocam", "", body);
  }

  function renderReport(m) {
    var main = $("main");
    var rep = (m.insights || []).filter(function (i) { return i.kind === "coaching_report"; })[0];
    if (!rep) {
      main.innerHTML = emptyState("description", "No coaching report yet",
        "This match finished without producing a report.",
        '<a class="btn btn--primary btn--sm" href="/upload/">Analyse another match</a>');
      return;
    }
    var p = rep.payload || {}, o = m.outcome || {};

    // Goal by goal
    var goals = (p.goals || []).map(function (g) {
      var scored = String(g.type || "").toLowerCase().indexOf("scor") === 0;
      var deep = g.deep && g.deep.root_cause ? g.deep : null;
      var note = g.summary + (g.fix ? "  ·  Fix: " + g.fix : "");
      var deepHtml = "";
      if (deep) {
        deepHtml = '<div class="well" style="margin-top:12px">'
          + '<p class="eyebrow" style="margin-bottom:6px">Deep read'
          + (deep.defender ? " · " + esc(deep.defender) : "") + "</p>"
          + (deep.what_happened ? '<p class="t-sm">' + esc(deep.what_happened) + "</p>" : "")
          + (deep.root_cause ? '<p class="t-sm muted" style="margin-top:6px"><strong>Root cause:</strong> ' + esc(deep.root_cause) + "</p>" : "")
          + (deep.fix ? '<p class="t-sm muted" style="margin-top:4px"><strong>Fix:</strong> ' + esc(deep.fix) + "</p>" : "")
          + "</div>";
      }
      return '<div class="goal"><div class="goal__side">'
        + '<span class="badge ' + (scored ? "badge--win" : "badge--loss") + '">'
        + (scored ? "Goal" : "Conceded") + "</span>"
        + chip(g.time, note, scored ? "chip--good" : "chip--bad")
        + '</div><div class="goal__body"><p>' + esc(g.summary) + "</p>"
        + (!scored && g.fix && !deep ? '<p class="goal__fix"><strong>Fix:</strong> ' + esc(g.fix) + "</p>" : "")
        + deepHtml + "</div></div>";
    }).join("");


    // Evidence log
    var ev = (p.evidence_log || []).map(function (e) {
      var tm = (String(e).match(/\[([^\]]+)\]/) || [])[1] || "";
      return '<div class="log__item"><span class="log__time">' + esc(tm) + "</span>"
        + '<span class="log__text">' + esc(String(e).replace(/^\s*\[[^\]]*\]\s*/, "")) + "</span></div>";
    }).join("");

    var youAre = p.your_team && p.your_team.abbrev ? " · You: " + esc(p.your_team.abbrev) : "";

    main.innerHTML =
      scoreHeader(o, "Match report" + youAre,
        // A plain link, not fetch(): letting the browser navigate is what makes
        // the download work with the filename the server sets. Navigation sends
        // no custom headers, so the identity rides in ?u= like the video and the
        // progress stream do.
        // Wrapped: .page-head is space-between, so two bare children push to
        // opposite ends instead of sitting together as one action group.
        '<div class="row wrap">'
        + '<a class="btn btn--secondary" href="/api/matches/' + esc(m.id) + '/report.pdf'
        + (identity() ? "?u=" + encodeURIComponent(identity()) : "") + '" download>'
        + icon("download") + "Download PDF</a>"
        + '<a class="btn btn--secondary" href="/moment/?id=' + esc(m.id) + '">'
        + icon("play_circle") + "Watch the moments</a></div>")

      + '<section class="section reveal"><div class="card">'
      + '<div class="card__head"><span class="tile tile--accent">' + icon("smart_toy") + "</span>"
      + '<h2 class="card__title">Coach\'s summary</h2></div>'
      + "<p>" + esc(rep.summary) + "</p></div></section>"

      + diagnosisBlock(p.diagnosis)
      + contextBlock(p.match_context)

      // Kept alongside the template: these two carry the clickable timestamps and
      // are the only lists in the report. positioning_issues / decision_patterns /
      // practice_drills render ONLY for reports written before the template - new
      // ones get the attacking/defensive analysis and practice plan instead.
      + '<section class="section"><div class="grid grid-2 grid-fill reveal-list">'
      + pointsCard("What you did well", p.strengths, "tile--accent", "check_circle")
      + pointsCard("Recurring mistakes", p.recurring_mistakes, "tile--danger", "warning")
      + pointsCard("Positioning", p.positioning_issues, "tile--warn", "map")
      + pointsCard("Decision-making", p.decision_patterns, "tile--info", "lightbulb")
      + pointsCard("Practice focus", p.practice_drills, "", "target")
      + "</div></section>"

      + eventLogBlock(p.event_log, m.id)
      + practicePlanBlock(p.practice_plan)
      + attackingBlock(p.attacking)
      + defendingBlock(p.defending)
      + eliteBlock(p.elite_comparison)
      + tacticalBlock(p.tactical_changes)
      + nextVideoBlock(p.next_video_test)

      + (goals
        ? '<section class="section"><div class="section__head"><h2 class="t-section">Goal by goal</h2></div>'
          + '<div class="stack-s">' + goals + "</div></section>"
        : "")


      + (ev
        ? '<section class="section"><details class="disclosure"><summary>'
          + icon("receipt_long") + "Evidence log"
          + '<span class="material-symbols-outlined disclosure__chevron">expand_more</span></summary>'
          + '<div class="disclosure__body"><div class="log">' + ev + "</div></div></details></section>"
        : "");

    wireChips(m.id);
  }

  function wireChips(matchId) {
    $$(".time-chip").forEach(function (c) {
      c.addEventListener("click", function () {
        location.href = "/moment/?id=" + matchId
          + "&t=" + encodeURIComponent(c.getAttribute("data-t") || "")
          + "&c=" + encodeURIComponent(c.getAttribute("data-c") || "");
      });
    });
  }

  // A coach arrives at /report/?id=X&client=Y. `/api/matches/{id}` is owner-only
  // and would 404 for them, so the shared-report endpoint is used instead - it
  // re-checks the link AND the sharing grant server-side.
  function loadReportFor(id, clientId) {
    return j("/api/clients/" + encodeURIComponent(clientId) + "/report/" + encodeURIComponent(id));
  }

  function loadOne(id) {
    return id ? getMatch(id) : listMatches().then(function (ms) {
      var done = (ms || []).filter(function (m) { return m.status === "complete"; });
      done.sort(function (a, b) { return (b.created_at || "").localeCompare(a.created_at || ""); });
      return done[0] ? getMatch(done[0].id) : null;
    });
  }

  function initReport() {
    var clientId = qs("client");
    if (clientId && qs("id")) {
      var main0 = $("main");
      loadReportFor(qs("id"), clientId).then(function (m) {
        renderReport(m);
        // renderReport builds the owner-only PDF link; a coach must use the
        // shared route or the button 404s on the report they can plainly read.
        var dl = $('a[href*="/report.pdf"]');
        if (dl) dl.href = "/api/clients/" + encodeURIComponent(clientId)
          + "/report/" + encodeURIComponent(qs("id")) + ".pdf"
          + (identity() ? "?u=" + encodeURIComponent(identity()) : "");
        var head = $(".page-head");
        if (head) {
          var note = document.createElement("p");
          note.className = "faint t-xs";
          note.style.marginTop = "8px";
          note.textContent = "Shared with you by your client. They can revoke this at any time.";
          head.appendChild(note);
        }
      }).catch(function (e) {
        main0.innerHTML = errorState(e.status === 403
          ? "This player has not shared their full reports with you."
          : e.message);
      });
      return;
    }
    var main = $("main");
    if (!identity()) {
      main.innerHTML = signInPrompt("Sign in to see your report",
        "Reports belong to the account that uploaded the match.");
      return;
    }
    loadOne(qs("id")).then(function (m) {
      if (m) renderReport(m);
      else main.innerHTML = emptyState("analytics", "No reports yet",
        "Upload a match and your coaching report will appear here.",
        '<a class="btn btn--primary btn--sm" href="/upload/">Upload a match</a>');
    }).catch(function (e) { main.innerHTML = errorState(e.message); });
  }

  // ---- MOMENT viewer -------------------------------------------------------
  // Small nudge so playback doesn't start mid-frame on the exact timestamp.
  // Goal times are ALREADY backed off at the source (GOAL_READ_LEAD_S in
  // core/pipeline/stages.py) because the scoreboard lags the goal - don't add a
  // second big offset here or goals start half a minute early.
  var SEEK_LEAD_S = 2;

  function toSeconds(t) {
    if (!t) return 0;
    var p = String(t).split(":").map(Number);
    if (p.length === 2) return p[0] * 60 + p[1];
    if (p.length === 3) return p[0] * 3600 + p[1] * 60 + p[2];
    return Number(t) || 0;
  }
  function buildMoments(m) {
    var rep = (m.insights || []).filter(function (i) { return i.kind === "coaching_report"; })[0];
    if (!rep) return [];
    var p = rep.payload || {}, out = [];

    // The event log carries an explicit time, phase and severity, so it is a far
    // better moment source than regex-scraping timestamps out of prose. Reports
    // written before the template have no event_log and fall through to the lists
    // below, which is why both paths stay.
    (p.event_log || []).forEach(function (e) {
      if (!e.time) return;
      var sev = String(e.severity || "").toLowerCase();
      var note = [e.what_i_did, e.why, e.correction ? "Correction: " + e.correction : ""]
        .filter(Boolean).join("  \u00b7  ");
      out.push({
        t: e.time, secs: toSeconds(e.time),
        kind: e.phase ? String(e.phase) : "Event",
        tone: sev === "high" ? "k-bad" : sev === "medium" ? "k-warn" : "k-info",
        note: note || String(e.correction || ""),
      });
    });

    (p.goals || []).forEach(function (g) {
      var scored = String(g.type || "").toLowerCase().indexOf("scor") === 0;
      var note = String(g.summary || "") + (g.fix ? "  ·  Fix: " + g.fix : "");
      if (g.time) {
        out.push({
          t: g.time, secs: toSeconds(g.time), kind: scored ? "Goal" : "Conceded",
          tone: scored ? "k-good" : "k-bad", note: note,
        });
      }
    });
    [["strengths", "Strength", "k-good"], ["recurring_mistakes", "Mistake", "k-bad"],
     ["positioning_issues", "Positioning", "k-warn"], ["decision_patterns", "Decision", "k-info"]]
      .forEach(function (s) {
        (p[s[0]] || []).forEach(function (item) {
          var parsed = times(item);
          parsed.chips.forEach(function (c) {
            out.push({ t: c, secs: toSeconds(c), kind: s[1], tone: s[2], note: parsed.text });
          });
        });
      });
    out.sort(function (a, b) { return a.secs - b.secs; });
    return out;
  }

  function initMoment() {
    var main = $("main");
    if (!identity()) {
      main.innerHTML = signInPrompt("Sign in to review your moments",
        "Every coaching point links to the moment it happened in your own match video.");
      return;
    }
    loadOne(qs("id")).then(function (m) {
      if (!m) {
        main.innerHTML = emptyState("play_circle", "No analysed matches yet",
          "Once a match is analysed, every coaching point becomes a clip you can jump to.",
          '<a class="btn btn--primary btn--sm" href="/upload/">Upload a match</a>');
        return;
      }
      renderMoments(m, qs("t"), qs("c"));
    }).catch(function (e) { main.innerHTML = errorState(e.message); });
  }

  function renderMoments(m, startT, startC) {
    var main = $("main");
    var o = m.outcome || {};
    var moments = buildMoments(m);
    var cur = -1;

    if (startT) {
      var target = toSeconds(startT), bestD = 1e9;
      moments.forEach(function (mo, i) {
        var d = Math.abs(mo.secs - target);
        if (d < bestD) { bestD = d; cur = i; }
      });
    } else if (moments.length) cur = 0;

    var listHtml = moments.map(function (mo, i) {
      return '<button type="button" data-i="' + i + '" class="cx-mom moment-item">'
        + '<span class="moment-item__time">' + esc(mo.t) + "</span>"
        + '<span class="moment-item__body">'
        + '<span class="moment-item__kind ' + mo.tone + '">' + esc(mo.kind) + "</span>"
        + '<span class="moment-item__note">' + esc(mo.note) + "</span></span></button>";
    }).join("");

    main.innerHTML =
      scoreHeader(o, "Moments · " + moments.length + " to review",
        '<a class="btn btn--secondary" href="/report/?id=' + esc(m.id) + '">'
        + icon("receipt_long") + "Full report</a>")

      + '<div class="grid split">'
      + '<div class="reveal">'
      // Matching header row so the player's top edge lines up with the timeline
      // LIST, not with the word "Timeline" - without it the right column's
      // heading pushed its card down and the two columns started at different
      // heights.
      + '<div class="section__head"><h2 class="t-section">Match video</h2></div>'
      + '<div class="video-frame"><video id="cx-video" controls playsinline preload="metadata"></video></div>'
      + '<div data-cx-note style="margin-top:16px"></div>'
      + '<div class="row-between" style="margin-top:16px">'
      + '<button type="button" class="btn btn--secondary" data-cx-prev>' + icon("arrow_back") + "Previous</button>"
      + '<span class="mono t-sm faint" data-cx-count></span>'
      + '<button type="button" class="btn btn--secondary" data-cx-next>Next' + icon("arrow_forward") + "</button>"
      + "</div></div>"

      + '<aside><div class="section__head"><h2 class="t-section">Timeline</h2>'
      + '<span class="faint t-xs">' + moments.length + "</span></div>"
      + '<div class="card card--flush scroll-y reveal-list" style="max-height:70vh;padding:6px">'
      + (listHtml || '<p class="muted t-sm" style="padding:16px">No timestamped moments in this report.</p>')
      + "</div></aside></div>";

    var v = $("#cx-video");
    // <video> cannot set headers either - same query-string fallback.
    v.src = API + "/api/matches/" + m.id + "/video" + authQuery();
    var pending = startT ? toSeconds(startT) : (cur >= 0 ? moments[cur].secs : 0);
    var ready = false;
    function seekTo(secs) {
      pending = secs;
      if (!ready) return;
      // Start the clip SEEK_LEAD_S before the moment so you see the build-up, not
      // just the outcome. Goal times in particular are read off the scoreboard,
      // which only changes once the ball is already in - landing exactly on the
      // timestamp means watching the celebration.
      try { v.currentTime = Math.max(0, secs - SEEK_LEAD_S); } catch (e) {}
      v.play().catch(function () {});
    }
    function armed() { if (ready) return; ready = true; if (pending) seekTo(pending); }
    v.addEventListener("loadedmetadata", armed);
    v.addEventListener("canplay", armed);

    var noteBox = $("[data-cx-note]"), countBox = $("[data-cx-count]");
    function noteCard(label, tone, text) {
      return '<div class="card"><div class="card__head">'
        + '<span class="tile">' + icon("smart_toy") + "</span>"
        + '<h3 class="card__title">Coach\'s note <span class="' + tone + '">· ' + esc(label) + "</span></h3></div>"
        + "<p>" + esc(text) + "</p></div>";
    }

    var listBtns = $$(".cx-mom");
    function select(i, seek) {
      if (i < 0 || i >= moments.length) return;
      cur = i;
      var mo = moments[i];
      listBtns.forEach(function (btn, k) {
        btn.setAttribute("aria-current", String(k === i));
      });
      if (listBtns[i] && listBtns[i].scrollIntoView) listBtns[i].scrollIntoView({ block: "nearest" });
      noteBox.innerHTML = noteCard(mo.kind + " · " + mo.t, mo.tone, mo.note);
      countBox.textContent = (i + 1) + " / " + moments.length;
      if (seek !== false) seekTo(mo.secs);
    }
    listBtns.forEach(function (btn) {
      btn.addEventListener("click", function () { select(parseInt(btn.getAttribute("data-i"), 10)); });
    });
    $("[data-cx-prev]").addEventListener("click", function () { select(cur - 1); });
    $("[data-cx-next]").addEventListener("click", function () { select(cur + 1); });

    if (cur >= 0) select(cur, false);
    else if (startC) noteBox.innerHTML = noteCard("Moment", "", startC);
    countBox.textContent = moments.length ? ((cur + 1) + " / " + moments.length) : "0 / 0";
  }

  // ---- TRENDS page ---------------------------------------------------------
  // Previously this page was hardcoded demo content with no API wiring at all.
  // Surplus/deficit area chart. The baseline is YOUR average for the selected
  // range; the area between it and the line is filled green where you were on
  // the good side of your own form and red where you were not.
  //
  // Both fills are the SAME closed path drawn twice, each clipped to one half of
  // the track. That is what makes the colour break exactly at the baseline even
  // mid-segment, where a fill-per-point approach would step at the points.
  //
  // Which half is "good" depends on the metric: for goals against, BELOW average
  // is the good side. `hib` (higher_is_better) carries that; when it is unknown
  // both halves stay neutral rather than guess.
  var _chartId = 0;
  function miniChart(points, byId, unit, hib) {
    var pts = (points || []).filter(function (p) { return typeof p.value === "number"; });
    if (!pts.length) return "";
    var n = pts.length, u = unit || "", id = "cx-ch" + (++_chartId);
    var avg = pts.reduce(function (a, p) { return a + p.value; }, 0) / n;
    var devs = pts.map(function (p) { return p.value - avg; });
    var maxDev = Math.max.apply(null, devs.map(Math.abs).concat([0]));

    // viewBox is 0 0 100 100 with the baseline at y=50; deviation spans +-40 so
    // the extremes never touch the edge of the box.
    var AX = 50, SPAN = 40;
    var read = pts.map(function (p, i) {
      var dev = devs[i];
      if (Math.abs(dev) < 0.05) dev = 0;   // float noise must not tint a flat run
      var meta = [];
      if (p.created_at) meta.push(fmtDate(p.created_at));
      var m = byId[p.match_id];
      if (m) {
        var r = resultOf(m.outcome);
        if (r) meta.push((badge(r)[0][0] + " " + ((m.outcome || {}).score || "")).trim());
      }
      return {
        v: p.value,
        x: n === 1 ? 50 : (i / (n - 1)) * 100,
        y: AX - (maxDev ? (dev / maxDev) * SPAN : 0),
        dev: dev === 0 ? "average" : (dev > 0 ? "+" : "") + (Math.round(dev * 10) / 10) + " vs avg",
        meta: meta.join(" \u00b7 "),
      };
    });

    var alt = "Compared with your average of " + (Math.round(avg * 10) / 10) + u + ": "
      + read.map(function (x) {
          return x.v + u + " (" + x.dev + (x.meta ? ", " + x.meta : "") + ")";
        }).join(", ");

    var line = read.map(function (x) { return x.x.toFixed(2) + "," + x.y.toFixed(2); }).join(" ");
    var area = "M" + read.map(function (x) { return x.x.toFixed(2) + "," + x.y.toFixed(2); }).join(" L")
      + " L100," + AX + " L0," + AX + " Z";
    var aboveCls = hib === true ? "is-good" : hib === false ? "is-bad" : "is-flat";
    var belowCls = hib === true ? "is-bad" : hib === false ? "is-good" : "is-flat";

    var svg = n < 2 ? "" :
      '<svg class="dev__svg" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">'
      + '<defs><clipPath id="' + id + 'a"><rect x="0" y="0" width="100" height="' + AX + '"/></clipPath>'
      + '<clipPath id="' + id + 'b"><rect x="0" y="' + AX + '" width="100" height="' + AX + '"/></clipPath></defs>'
      + '<path class="dev__fill ' + aboveCls + '" d="' + area + '" clip-path="url(#' + id + 'a)"/>'
      + '<path class="dev__fill ' + belowCls + '" d="' + area + '" clip-path="url(#' + id + 'b)"/>'
      + '<polyline class="dev__line ' + aboveCls + '" points="' + line + '" vector-effect="non-scaling-stroke" clip-path="url(#' + id + 'a)"/>'
      + '<polyline class="dev__line ' + belowCls + '" points="' + line + '" vector-effect="non-scaling-stroke" clip-path="url(#' + id + 'b)"/>'
      + "</svg>";

    // Hover zones are centred on their point, so the first and last are half
    // width - the outer points sit on the chart's edges, not in column centres.
    var half = n === 1 ? 50 : 50 / (n - 1);
    var zones = read.map(function (x, i) {
      var l = Math.max(0, x.x - half), r = Math.min(100, x.x + half);
      // Explicit edge classes: :first-of-type/:first-child both mis-target here,
      // because the SVG and the axis span are siblings of the hover zones.
      return '<span class="dev__pt' + (i === 0 ? " dev__pt--first" : "")
        + (i === n - 1 ? " dev__pt--last dev__pt--now" : "") + '"'
        + ' style="left:' + l.toFixed(2) + "%;width:" + (r - l).toFixed(2) + "%;--y:" + x.y.toFixed(2) + "%;"
        + "--cx:" + (((x.x - l) / (r - l)) * 100).toFixed(2) + '%">'
        + '<span class="dev__dot"></span>'
        + '<span class="dev__tip"><b>' + esc(String(x.v) + u) + "</b>"
        + "<i>" + esc(x.dev) + "</i>"
        + (x.meta ? "<i>" + esc(x.meta) + "</i>" : "") + "</span></span>";
    }).join("");

    return '<div class="dev" role="img" aria-label="' + esc(alt) + '">'
      + svg + '<span class="dev__axis"></span>' + zones + "</div>";
  }

  function trendCard(t, byId) {
    var val = t.latest;
    if (typeof val !== "number") return "";
    var unit = t.unit || "";
    var delta = t.delta;
    var deltaHtml = "";
    if (typeof delta === "number" && delta !== 0) {
      var good = t.improving === true, bad = t.improving === false;
      var cls = good ? "stat__delta--up" : bad ? "stat__delta--down" : "stat__delta--flat";
      deltaHtml = '<span class="stat__delta ' + cls + '">'
        + icon(delta > 0 ? "arrow_upward" : "arrow_downward")
        + (delta > 0 ? "+" : "") + (Math.round(delta * 100) / 100) + "</span>";
    } else {
      deltaHtml = '<span class="stat__delta stat__delta--flat">No change</span>';
    }
    var n = (t.points || []).length;
    // Just the average. The window is already stated by the range picker at the
    // top of the page, and repeating "over 5 matches" on all six cards was five
    // extra words each to say something the control above already says.
    var avg = typeof t.average === "number"
      ? "avg " + (Math.round(t.average * 10) / 10) + esc(unit)
      : n + " match" + (n === 1 ? "" : "es");
    return '<div class="stat"><div class="stat__label">' + esc(t.label || t.key) + "</div>"
      + '<div class="row" style="gap:10px;align-items:baseline">'
      + '<span class="stat__value">' + (Math.round(val * 100) / 100) + esc(unit) + "</span>"
      + deltaHtml + "</div>"
      + '<div class="faint t-xs" style="margin-top:2px">' + avg + "</div>"
      + miniChart(t.points, byId || {}, unit, t.higher_is_better) + "</div>";
  }

  // Range the whole page is scoped to. `null` = every match.
  var STATS_RANGES = [[5, "Last 5"], [10, "Last 10"], [null, "All"]];
  var statsRange = 5;

  function formStrip(done) {
    // Oldest -> newest, so it reads the same direction as the metric charts.
    var seq = done.slice().reverse();
    var wins = seq.filter(function (m) { return resultOf(m.outcome) === "win"; }).length;
    var rate = seq.length ? Math.round((wins / seq.length) * 100) : 0;
    return '<div class="section__head"><h2 class="t-section">Form</h2>'
      + '</div>'
      + '<div class="card"><div class="row wrap" style="gap:20px">'
      + '<div class="form-strip reveal-list">'
      + seq.map(function (m) {
          var b = badge(resultOf(m.outcome));
          return '<span class="result ' + b[2] + '" title="' + esc((m.outcome || {}).score || "")
            + " \u00b7 " + esc(fmtDate(m.created_at)) + '">' + b[0][0] + "</span>";
        }).join("")
      + "</div>"
      + '<div><span class="stat__value" style="font-size:22px">' + rate + "%</span>"
      + '<span class="faint t-xs" style="margin-left:8px">win rate over '
      + seq.length + " match" + (seq.length === 1 ? "" : "es") + "</span></div>"
      + "</div></div>";
  }

  function patternsCard(data) {
    var issues = (data && data.issues) || [];
    if (!issues.length) return "";
    var total = data.matches || 1;
    return '<div class="section__head"><h2 class="t-section">What keeps costing you</h2></div>'
      + '<div class="card">'
      + issues.map(function (i) {
          var pct = Math.round((i.count / total) * 100);
          return '<div class="pattern">'
            + '<span class="pattern__label">' + esc(i.label) + "</span>"
            + '<span class="pattern__meter"><span class="pattern__fill" style="width:'
            + pct + '%"></span></span>'
            + '<span class="pattern__count">' + i.count + " / " + total + "</span></div>";
        }).join("")
      + "</div>";
  }

  function initTrends() {
    var metricsHost = $("[data-cx-metrics]"), historyHost = $("[data-cx-history]");
    if (!identity()) {
      metricsHost.innerHTML = signInPrompt("Sign in to see your statistics",
        "Statistics compare your metrics across every match you've analysed.");
      historyHost.innerHTML = "";
      return;
    }

    function rangePicker() {
      return '<div class="segmented" role="group" aria-label="How many matches to include">'
        + STATS_RANGES.map(function (r) {
            return '<button type="button" class="segmented__btn" data-cx-range="' + (r[0] || "")
              + '" aria-pressed="' + (r[0] === statsRange) + '">' + r[1] + "</button>";
          }).join("")
        + "</div>";
    }

    function load() {
      Promise.all([getTrends(statsRange), listMatches(), getPatterns(statsRange)])
        .then(function (res) {
          var trends = res[0] || [], matches = res[1] || [], patterns = res[2] || {};
          var done = matches.filter(function (m) { return m.status === "complete"; });
          done.sort(function (a, b) { return (b.created_at || "").localeCompare(a.created_at || ""); });
          if (statsRange) done = done.slice(0, statsRange);

          if (!done.length) {
            metricsHost.innerHTML = rangeWrap("");
            historyHost.innerHTML = emptyState("history", "No matches yet",
              "Your analysed matches will be listed here with the coach's takeaway.",
              '<a class="btn btn--primary btn--sm" href="/upload/">Upload a match</a>');
            wireRange();
            return;
          }

          // Oldest first: every block on this page has time as its axis, and a
          // history that reads right to left is a puzzle, not a chart.
          var byId = {};
          done.forEach(function (m) { byId[m.id] = m; });
          var cards = trends.map(function (t) { return trendCard(t, byId); })
            .filter(Boolean).slice(0, 6).join("");
          var metricsBlock = cards
            ? '<div class="section__head"><h2 class="t-section">Key metrics</h2>'
              + '</div>'
              + '<div class="grid grid-3 reveal-list">' + cards + "</div>"
              // Goals are scoreboard-derived; the rest is the model's count of what
              // it noticed. Say so rather than let them look equally authoritative.
              + '<p class="faint t-xs" style="margin-top:12px">'
              + "Goals come from the scoreboard. Shots, big chances and errors are the coach's "
              + "count of what it saw in the video - treat them as estimates.</p>"
            : '<div class="section__head"><h2 class="t-section">Key metrics</h2></div>'
              + emptyState("query_stats", "Not enough data yet",
                "Metrics appear once you've analysed at least two matches.");

          // Key metrics first - it is what the page is called Statistics for.
          // Form and the recurring patterns sit beneath as the interpretation.
          metricsHost.innerHTML = rangeWrap(
            '<section class="section">' + metricsBlock + "</section>"
            + '<section class="section">' + formStrip(done) + "</section>"
            + (patternsCard(patterns) ? '<section class="section">' + patternsCard(patterns) + "</section>" : "")
          );

          var rows = done.map(function (m) {
            var b = badge(resultOf(m.outcome));
            var rep = (m.insights || []).filter(function (i) { return i.kind === "coaching_report"; })[0];
            var takeaway = rep && rep.summary ? rep.summary : "-";
            return '<tr class="is-clickable" data-id="' + esc(m.id) + '">'
              + '<td><span class="result ' + b[2] + '">' + b[0][0] + "</span></td>"
              + '<td class="num">' + esc((m.outcome && m.outcome.score) || "-") + "</td>"
              + '<td class="num muted">' + esc(fmtDate(m.created_at)) + "</td>"
              + '<td class="muted cell-truncate"><span title="' + esc(takeaway) + '">'
              + esc(takeaway) + "</span></td></tr>";
          }).join("");

          historyHost.innerHTML = '<div class="table-wrap reveal"><div class="table-scroll">'
            + '<table class="table"><thead><tr>'
            + "<th>Result</th><th>Score</th><th>Date</th><th>Coach's takeaway</th>"
            + "</tr></thead><tbody>" + rows + "</tbody></table></div></div>";

          $$("tr.is-clickable", historyHost).forEach(function (tr) {
            tr.addEventListener("click", function () {
              location.href = "/report/?id=" + tr.getAttribute("data-id");
            });
          });
          wireRange();
        }).catch(function (e) {
          metricsHost.innerHTML = errorState(e.message);
          historyHost.innerHTML = "";
        });
    }

    function rangeWrap(inner) {
      return '<div class="row" style="justify-content:flex-end;margin-bottom:24px">'
        + rangePicker() + "</div>" + inner;
    }
    function wireRange() {
      $$("[data-cx-range]").forEach(function (b) {
        b.addEventListener("click", function () {
          var v = b.getAttribute("data-cx-range");
          statsRange = v ? parseInt(v, 10) : null;
          load();
        });
      });
    }

    load();
  }

  // ---- ACCOUNT page --------------------------------------------------------
  var getAccount = function () { return j("/api/account"); };
  var saveAccount = function (patch) {
    return j("/api/account", {
      method: "PATCH", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    });
  };

  function skillOption(lvl, current) {
    var on = lvl.value === current;
    return '<button type="button" class="match-row" data-cx-skill="' + esc(lvl.value) + '"'
      + ' aria-pressed="' + on + '"'
      + (on ? ' style="border-color:var(--accent-line);background:var(--surface-2)"' : "")
      + '><span class="result ' + (on ? "result--win" : "result--draw") + '">'
      + (on ? icon("check") : "") + "</span>"
      + '<span class="match-row__main"><span class="match-row__title" style="font-family:var(--font)">'
      + esc(lvl.label) + "</span>"
      + '<span class="match-row__meta" style="white-space:normal">' + esc(lvl.blurb) + "</span></span></button>";
  }

  function renderAccount(data) {
    var main = $("main"), p = data.profile, u = data.usage;
    var pct = u.limit ? Math.min(100, Math.round((u.used / u.limit) * 100)) : 0;
    // A generous limit makes real usage round to zero. "0%" next to a used count
    // reads as broken, so say what is actually true.
    var pctText = (pct === 0 && u.used > 0) ? "<1%" : pct + "%";
    var role = p.role || "player";
    var levelLabel = (data.skill_levels || []).filter(function (l) {
      return l.value === p.skill_level;
    })[0];

    var cp = p.coach_profile || {};
    var memberSince = "";
    if (p.created_at) {
      var _d = new Date(p.created_at);
      if (!isNaN(_d)) memberSince = _d.toLocaleDateString(undefined, { month: "short", year: "numeric" });
    }

    // Usage ring. r=42 on a 100 box, so the circumference is fixed and the
    // dash offset is just a percentage of it.
    var R = 42, C = 2 * Math.PI * R;

    function ffield(id, label, inner) {
      return '<label class="ffield" for="' + id + '">'
        + '<span class="ffield__label">' + esc(label) + "</span>" + inner + "</label>";
    }

    main.innerHTML =
      // --- hero -------------------------------------------------------------
      '<div class="phero reveal">'
      + '<div class="phero__pic">'
      + avatarHtml(p, "avatar--xl")
      + '<button type="button" class="phero__picbtn" data-cx-pic'
      + ' aria-label="' + (p.avatar_url ? "Change" : "Add") + ' profile picture">'
      + icon("photo_camera") + "</button>"
      + (p.avatar_url
          ? '<button type="button" class="link t-xs phero__picdel" data-cx-picdel>Remove</button>'
          : "")
      + "</div>"
      + '<div class="phero__id">'
      + '<h1 class="phero__name">' + esc(accountLabel(p)) + "</h1>"
      + '<p class="phero__mail">'
      + (p.email ? esc(p.email) : "No email yet - add one below to sign in elsewhere")
      + "</p>"
      + '<div class="phero__meta">'
      + '<span class="badge badge--info">' + esc(role === "coach" ? "Coach" : "Player") + "</span>"
      + (levelLabel ? '<span class="badge badge--win">' + esc(levelLabel.label) + "</span>" : "")
      + (memberSince
          ? '<span class="phero__since">' + icon("calendar_month") + "Member since " + esc(memberSince) + "</span>"
          : "")
      + "</div></div>"
      + '<div class="phero__actions">'
      + '<a class="btn btn--secondary" href="' + (role === "coach" ? "/office/" : "/locker/") + '">'
      + icon(role === "coach" ? "business_center" : "checklist")
      + (role === "coach" ? "My office" : "My locker") + "</a>"
      + '<button class="btn btn--secondary" data-cx-signout>' + icon("logout") + "Sign out</button>"
      + "</div></div>"

      // --- 2x2 grid ---------------------------------------------------------
      + '<div class="grid grid-2 reveal-list" style="margin-top:var(--s5);align-items:stretch">'

      // Coaching level
      + '<div class="pcard"><div class="pcard__head">'
      + '<span class="pcard__icon">' + icon("bar_chart") + "</span><div>"
      + '<h2 class="pcard__title">Coaching level</h2>'
      + '<p class="pcard__sub">Choose the level that best describes you.</p></div></div>'
      + '<div data-cx-skills>'
      + (data.skill_levels || []).map(function (l) {
          var on = l.value === p.skill_level;
          return '<button type="button" class="lvl" data-cx-skill="' + esc(l.value) + '"'
            + ' aria-pressed="' + on + '">'
            + '<span class="lvl__radio">' + icon("check") + "</span><span>"
            + '<span class="lvl__name">' + esc(l.label) + "</span>"
            + '<span class="lvl__blurb">' + esc(l.blurb) + "</span></span></button>";
        }).join("")
      + "</div></div>"

      // Profile details
      + '<div class="pcard"><div class="pcard__head">'
      + '<span class="pcard__icon">' + icon("person") + "</span><div>"
      + '<h2 class="pcard__title">Profile details</h2>'
      + '<p class="pcard__sub">Manage your personal information.</p></div></div>'
      + (p.email ? "" :
          '<div class="alert alert--warn" style="margin-bottom:10px">' + icon("warning")
          + '<div class="alert__body"><p class="alert__title">No email on this account</p>'
          + '<p class="muted t-sm">Add one so you can sign back in from another device.</p></div></div>')
      + ffield("cx-name", "Display name",
          '<input class="ffield__input" id="cx-name" value="' + esc(p.display_name || "")
          + '" placeholder="Your name">')
      + ffield("cx-email", "Email",
          '<input class="ffield__input" id="cx-email" type="email" inputmode="email" value="'
          + esc(p.email || "") + '" placeholder="you@example.com">')
      + ffield("cx-ctrl", "Control scheme",
          '<select id="cx-ctrl">'
          + ["Classic", "Alternate"].map(function (c) {
              return '<option' + (c === p.control_scheme ? " selected" : "") + ">" + c + "</option>";
            }).join("")
          + "</select>" + icon("expand_more", "ffield__chev"))
      + '<div class="pcard__foot">'
      + '<button class="btn btn--primary" data-cx-save>Save changes</button></div>'
      + "</div>"

      // Coach profile - the copy players actually choose from. Only rendered for
      // coach accounts; a player has nobody to advertise to.
      + (role === "coach"
          ? '<div class="pcard" style="grid-column:1/-1"><div class="pcard__head">'
            + '<span class="pcard__icon">' + icon("badge") + "</span><div>"
            + '<h2 class="pcard__title">Your coach profile</h2>'
            + '<p class="pcard__sub">This is what players see before they ask you to coach them.</p>'
            + "</div></div>"
            + '<div class="grid grid-2" style="gap:10px">'
            + '<label class="ffield" for="cx-headline"><span class="ffield__label">Headline</span>'
            + '<input class="ffield__input" id="cx-headline" maxlength="90" placeholder="e.g. Elite coach - defending and transitions" value="'
            + esc(cp.headline || "") + '"></label>'
            + '<label class="ffield" for="cx-exp"><span class="ffield__label">Experience</span>'
            + '<input class="ffield__input" id="cx-exp" maxlength="90" placeholder="e.g. Elite division, coaching since 2024" value="'
            + esc(cp.experience || "") + '"></label>'
            + "</div>"
            + '<label class="ffield" for="cx-bio" style="margin-top:10px">'
            + '<span class="ffield__label">About you</span>'
            + '<textarea class="ffield__input" id="cx-bio" rows="4" maxlength="900"'
            + ' placeholder="Who you coach, what you focus on, how you work.">'
            + esc(cp.bio || "") + "</textarea></label>"
            + '<label class="ffield" for="cx-spec" style="margin-top:10px">'
            + '<span class="ffield__label">Specialities - comma separated, up to 6</span>'
            + '<input class="ffield__input" id="cx-spec" placeholder="Defending, Build-up, Transitions" value="'
            + esc((cp.specialties || []).join(", ")) + '"></label>'

            + '<hr class="divider" style="margin:18px 0">'
            + '<p class="t-sm" style="font-weight:600">What you offer</p>'
            + '<p class="faint t-xs" style="margin-bottom:10px">Coachfio does not take '
            + "payment. The price is shown to players so they know what you charge - "
            + "you arrange it between yourselves.</p>"
            + '<div class="grid grid-3" style="gap:10px">'
            + '<label class="ffield" for="cx-price"><span class="ffield__label">Price</span>'
            + '<input class="ffield__input" id="cx-price" inputmode="decimal" placeholder="30" value="'
            + esc(cp.price || "") + '"></label>'
            + '<label class="ffield" for="cx-cur"><span class="ffield__label">Currency</span>'
            + '<select id="cx-cur">'
            + ["EUR", "USD", "GBP"].map(function (c) {
                return '<option' + (c === (cp.currency || "EUR") ? " selected" : "") + ">" + c + "</option>";
              }).join("")
            + "</select>" + icon("expand_more", "ffield__chev") + "</label>"
            + '<label class="ffield" for="cx-pkg"><span class="ffield__label">Package name</span>'
            + '<input class="ffield__input" id="cx-pkg" maxlength="90" placeholder="e.g. 3 matches + a 1v1" value="'
            + esc(cp.package_title || "") + '"></label>'
            + "</div>"
            + '<label class="ffield" for="cx-inc" style="margin-top:10px">'
            + '<span class="ffield__label">What they get - one per line, up to 8</span>'
            + '<textarea class="ffield__input" id="cx-inc" rows="6"'
            + ' placeholder="I analyse 3 of your matches in full&#10;We watch them back together and I show you the mistakes&#10;A 1v1 against me where I imitate your opponent">'
            + esc((cp.includes || []).join("\n")) + "</textarea></label>"
            + '<div class="pcard__foot">'
            + '<a class="btn btn--secondary" href="/coach/?id=' + esc(p.user_id) + '">'
            + icon("visibility") + "Preview</a>"
            + '<button class="btn btn--primary" data-cx-savecoach style="margin-left:8px">Save profile</button>'
            + "</div></div>"
          : "")

      // Where you play
      + ((data.skill_survey || []).length
          ? '<div class="pcard"><div class="pcard__head">'
            + '<span class="pcard__icon">' + icon("stadia_controller") + "</span><div>"
            + '<h2 class="pcard__title">Where you play</h2>'
            + '<p class="pcard__sub">Tell us about your usual gameplay.</p></div></div>'
            + '<div class="grid grid-2" style="gap:14px;align-items:start">'
            + data.skill_survey.map(function (q) { return surveyField(q, p.skill_survey || {}); }).join("")
            + "</div>"
            + (data.suggestion
                ? '<div class="hint">' + icon("lightbulb") + "<span>Suggests <strong>"
                  + esc(data.suggestion.level) + "</strong> - " + esc(data.suggestion.reason)
                  + '<br><span class="muted">Your choice above still wins.</span></span></div>'
                : "")
            + "</div>"
          : "")

      // Usage
      + '<div class="pcard"><div class="pcard__head">'
      + '<span class="pcard__icon">' + icon("trending_up") + "</span><div>"
      + '<h2 class="pcard__title">Usage &amp; progress</h2>'
      + '<p class="pcard__sub">See how much you\'ve used Coachfio.</p></div></div>'
      + '<div class="usage">'
      + "<div><p class=\"muted t-sm\">Matches remaining</p>"
      + '<p class="usage__big">' + Math.max(0, u.remaining).toLocaleString() + "</p>"
      + '<p class="faint t-xs">of ' + u.limit.toLocaleString() + "</p></div>"
      + '<div class="usage__ring"><svg viewBox="0 0 100 100" width="92" height="92">'
      + '<circle class="usage__ring-track" cx="50" cy="50" r="' + R + '"/>'
      + '<circle class="usage__ring-val" cx="50" cy="50" r="' + R + '"'
      + ' stroke-dasharray="' + C.toFixed(1) + '"'
      + ' stroke-dashoffset="' + (C * (1 - pct / 100)).toFixed(1) + '"/></svg>'
      + '<span class="usage__pct">' + pctText + "</span></div>"
      + '<div class="usage__rest"><p class="muted t-sm">' + u.used.toLocaleString()
      + " of " + u.limit.toLocaleString() + " used</p>"
      + '<div class="progress" style="margin-top:8px"><div class="progress__bar" style="width:'
      + pct + '%"></div></div></div>'
      + "</div></div>"

      + "</div>"

      // --- help strip -------------------------------------------------------
      + '<div class="helpbar reveal">'
      + '<span class="helpbar__icon">' + icon("help") + "</span>"
      + "<div><p style=\"font-weight:600\">Need help?</p>"
      + '<p class="muted t-sm">Something not working, or a report that looks wrong? Tell us.</p></div>'
      + '<div class="helpbar__actions">'
      + '<a class="btn btn--secondary" href="mailto:support@coachfio.app">' + icon("mail") + "Contact support</a>"
      + "</div></div>"

      + '<div data-cx-error hidden style="margin-top:16px"></div>';

    // skill level - saves immediately, because it is the whole point of the page
    $$("[data-cx-skill]").forEach(function (b) {
      b.addEventListener("click", function () {
        saveAccount({ skill_level: b.getAttribute("data-cx-skill") })
          .then(renderAccount)
          .catch(function (e) { showError(e.message); });
      });
    });
    $("[data-cx-save]").addEventListener("click", function () {
      saveAccount({
        display_name: $("#cx-name").value,
        email: $("#cx-email").value,
        control_scheme: $("#cx-ctrl").value,
      }).then(renderAccount).catch(function (e) { showError(e.message); });
    });
    // Survey answers save on change and re-render, so the suggestion updates live.
    $$("[data-cx-q]").forEach(function (sel) {
      sel.addEventListener("change", function () {
        var next = Object.assign({}, p.skill_survey || {});
        next[sel.getAttribute("data-cx-q")] = sel.value;
        (data.skill_survey || []).forEach(function (q) {
          if (questionLocked(q, next)) next[q.key] = "";   // drop now-irrelevant answers
        });
        saveAccount({ skill_survey: next }).then(renderAccount)
          .catch(function (e) { showError(e.message); });
      });
    });
    var saveCoach = $("[data-cx-savecoach]");
    if (saveCoach) saveCoach.addEventListener("click", function () {
      saveAccount({
        coach_profile: {
          headline: $("#cx-headline").value,
          experience: $("#cx-exp").value,
          bio: $("#cx-bio").value,
          specialties: ($("#cx-spec").value || "").split(",").map(function (x) {
            return x.trim();
          }).filter(Boolean),
          price: $("#cx-price").value,
          currency: $("#cx-cur").value,
          package_title: $("#cx-pkg").value,
          includes: ($("#cx-inc").value || "").split("\n").map(function (x) {
            return x.trim();
          }).filter(Boolean),
        },
      }).then(renderAccount).catch(function (e) { showError(e.message); });
    });

    // Picture upload. Optional throughout: no account needs one.
    var picInput = document.createElement("input");
    picInput.type = "file";
    picInput.accept = "image/*";
    picInput.className = "sr-only";
    document.body.appendChild(picInput);
    $("[data-cx-pic]").addEventListener("click", function () { picInput.click(); });
    picInput.addEventListener("change", function () {
      var f = picInput.files && picInput.files[0];
      if (!f) return;
      var fd = new FormData();
      fd.append("file", f, f.name);
      var url = API + "/api/account/avatar"
        + (identity() ? "?u=" + encodeURIComponent(identity()) : "");
      fetch(url, { method: "POST", body: fd })
        .then(function (r) {
          if (!r.ok) return r.json().then(function (e) { throw new Error(e.detail || "Upload failed"); });
          return r.json();
        })
        .then(function () { return getAccount(); })
        .then(renderAccount)
        .then(loadAccountNav)          // the bar shows it immediately too
        .catch(function (e) { showError(e.message); });
    });
    var del = $("[data-cx-picdel]");
    if (del) del.addEventListener("click", function () {
      confirmDialog({
        danger: true,
        icon: "no_photography",
        title: "Remove your profile picture?",
        text: "Your initials will be shown instead. You can upload a new one any time.",
        confirmLabel: "Remove",
      }).then(function (ok) {
        if (!ok) return;
        j("/api/account/avatar", { method: "DELETE" })
          .then(getAccount).then(renderAccount).then(loadAccountNav)
          .catch(function (e) { showError(e.message); });
      });
    });

    $("[data-cx-signout]").addEventListener("click", function () {
      clearIdentity();
      location.href = "/signin/";
    });
  }

  var CURRENCY_SIGN = { EUR: "\u20ac", USD: "$", GBP: "\u00a3" };
  function priceLabel(c) {
    if (!c || !c.price) return "";
    return (CURRENCY_SIGN[c.currency] || "") + c.price;
  }

  // The account avatar: uploaded picture if there is one, initials otherwise.
  // A picture is never required - `initialsOf` always resolves to something, so
  // this can be rendered before the profile has loaded, or for an account that
  // simply never uploaded one.
  function avatarHtml(p, cls) {
    var c = "avatar" + (cls ? " " + cls : "");
    return (p && p.avatar_url)
      ? '<span class="' + c + ' avatar--img"><img src="' + esc(p.avatar_url)
        + '" alt="" loading="lazy"></span>'
      : '<span class="' + c + '">' + esc(initialsOf(p)) + "</span>";
  }

  // One or two letters for the avatar. Falls back through the same chain as the
  // nav label so it never renders a slice of the opaque account id.
  function initialsOf(p) {
    var n = accountLabel(p).replace(/[^A-Za-z0-9 ]/g, " ").trim();
    if (!n) return "?";
    var parts = n.split(/\s+/);
    return (parts.length > 1 ? parts[0][0] + parts[1][0] : n.slice(0, 2)).toUpperCase();
  }

  function initAccount() {
    var main = $("main");
    getAccount().then(renderAccount).catch(function (e) {
      main.innerHTML = errorState(e.message);
    });
  }

  // ---- SIGN IN / SIGN UP ---------------------------------------------------
  // Real account records keyed by email; no password, because credential
  // handling belongs to the auth provider that plugs into `current_user`.
  var LEVELS = [
    ["amateur", "Amateur", "New or casual. Reports explain the basics in plain language."],
    ["intermediate", "Intermediate", "You know the fundamentals. Reports focus on habits and decisions."],
    ["pro", "Pro", "Competitive. Dense, meta-aware, skips what you already know."],
  ];

  // The survey (Division Rivals tier, Champs record) and the level it implies are
  // both game-specific, so they come from the adapter via the API - this file only
  // renders whatever questions it is handed.
  var suggestLevel = function (answers) {
    return j("/api/account/suggest-level", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ game_id: GAME.game_id, edition: GAME.edition, answers: answers }),
    }).then(function (r) { return r.suggestion; }).catch(function () { return null; });
  };

  // A question is locked while another answer sits in its `locked_by.values`
  // (Champs is meaningless in the bottom divisions).
  function questionLocked(q, answers) {
    var lb = q.locked_by;
    if (!lb) return null;
    var current = (answers || {})[lb.key];
    return current && (lb.values || []).indexOf(current) !== -1 ? (lb.reason || "Not applicable.") : null;
  }

  function surveyField(q, answers) {
    var lockedReason = questionLocked(q, answers);
    var val = (answers || {})[q.key] || "";
    return '<div class="field"><label class="field__label" for="cx-q-' + esc(q.key) + '">'
      + esc(q.label) + "</label>"
      + '<select class="select" id="cx-q-' + esc(q.key) + '" data-cx-q="' + esc(q.key) + '"'
      + (lockedReason ? " disabled" : "") + ">"
      + '<option value="">' + (lockedReason ? "Not applicable" : "Select…") + "</option>"
      + (q.options || []).map(function (o) {
          return '<option value="' + esc(o.value) + '"'
            + (o.value === val ? " selected" : "") + ">" + esc(o.label) + "</option>";
        }).join("")
      + "</select>"
      + '<span class="faint t-xs">' + esc(lockedReason || q.help || "") + "</span></div>";
  }

  function initAuth() {
    var host = $("[data-cx-auth]");
    var mode = /signup/.test(location.search) ? "up" : "in";
    var level = "intermediate";
    // Arriving from "I'm a coach" must open the form on Coach. It used to be
    // hardcoded to player, so the button announced you were a coach and then the
    // form quietly assumed you were not, and you had to notice and fix it.
    var role = /[?&]role=coach\b/.test(location.search) ? "coach" : "player";
    var survey = [], answers = {}, suggestion = null, levelTouched = false;
    // Every survey change re-renders the card, which would blow away anything
    // already typed. Keep the text fields in state and write them back.
    var form = { name: "", email: "" };
    function captureForm() {
      var n = $("#cx-name"), e = $("#cx-email");
      if (n) form.name = n.value;
      if (e) form.email = e.value;
    }

    // Fetch the questions once, whenever sign-up is first shown. This has to be
    // callable from the mode switch too: arriving via "Create one" from the
    // sign-in tab used to leave the survey empty, because it was only ever
    // fetched on page load.
    var surveyLoading = false;
    function loadSurvey() {
      if (survey.length || surveyLoading) return;
      surveyLoading = true;
      j("/api/account/skill-survey").then(function (d) {
        survey = d.skill_survey || [];
        surveyLoading = false;
        if (survey.length) { captureForm(); render(); }
      }).catch(function () { surveyLoading = false; });
    }

    function render() {
      var up = mode === "up";
      // Sign-up needs the extra width for its level cards; sign-in is one field.
      var shell = $("#main");
      if (shell) shell.classList.toggle("auth--wide", up);

      // Who is this account for? A coach reviews other people's footage, so the
      // Rivals/Champs survey and the level cards are meaningless to them - the
      // whole right-hand column disappears when "coach" is picked.
      var rolePick = !up ? "" :
        '<div class="role-pick" role="group" aria-label="Account type">'
        + [["player", "sports_esports", "Player", "Analyse your own matches and level up."],
           ["coach", "supervisor_account", "Coach", "Review players' footage and track their progress."]]
          .map(function (r) {
            return '<button type="button" class="role-pick__opt" data-cx-role="' + r[0] + '"'
              + ' aria-pressed="' + (role === r[0]) + '">'
              + icon(r[1]) + "<span><strong>" + r[2] + "</strong>"
              + '<span class="level-pick__blurb">' + r[3] + "</span></span></button>";
          }).join("")
        + "</div>";

      var fields =
        '<div class="stack-s">'
        + (up
            ? '<div class="field"><label class="field__label" for="cx-name">Display name</label>'
              + '<input class="input" id="cx-name" placeholder="Your name" autocomplete="name"'
              + ' value="' + esc(form.name) + '"></div>'
            : "")
        + '<div class="field"><label class="field__label" for="cx-email">Email</label>'
        + '<input class="input" id="cx-email" type="email" placeholder="you@example.com"'
        + ' autocomplete="email" inputmode="email" value="' + esc(form.email) + '"></div>'
        // Sits under the fields in the two-column layout: it balances the column
        // AND lands right next to the email box it is actually about.
        + (up ? '<p class="faint t-xs" style="margin-top:4px">'
            + "No password yet - your account is found by email until the hosted "
            + "sign-in provider is connected. Your matches stay private to it.</p>" : "")
        + "</div>";

      var levels =
        '<div style="margin-top:24px"><p class="field__label" style="margin-bottom:8px">Your level</p>'
        + '<div class="level-pick level-pick--row">'
        + LEVELS.map(function (l) {
            var isSuggested = suggestion && suggestion.level === l[0];
            return '<button type="button" class="level-pick__opt" data-cx-level="' + l[0] + '"'
              + ' aria-pressed="' + (l[0] === level) + '">'
              + '<span class="level-pick__dot"></span><span>'
              + '<span class="level-pick__name">' + esc(l[1])
              + (isSuggested ? ' <span class="badge badge--win" style="margin-left:6px">Suggested</span>' : "")
              + "</span>"
              + '<span class="level-pick__blurb">' + esc(l[2]) + "</span></span></button>";
          }).join("")
        + "</div>"
        + (suggestion
            ? '<p class="faint t-xs" style="margin-top:8px">' + esc(suggestion.reason)
              + " - but pick whatever suits you.</p>"
            : '<p class="faint t-xs" style="margin-top:8px">You can change this any time.</p>')
        + "</div>";

      // Right-hand column: the questions that produce the suggestion.
      var surveyHtml = survey.length
        ? '<div class="stack-s">'
          + survey.map(function (q) { return surveyField(q, answers); }).join("")
          + "</div>"
        : '<div class="faint t-xs">Loading…</div>';

      host.innerHTML =
        '<div class="card">'
        + '<h1 class="auth__title">' + (up ? "Create your account" : "Welcome back") + "</h1>"
        + '<p class="auth__sub">' + (up
            ? (role === "coach"
                ? "Review your players' footage, track their habits across matches, and chat with them - the AI does the first watch."
                : "Pick your level and the coach writes every report for it - an amateur report and a pro report on the same match read completely differently.")
            : "Sign in to pick up your reports, moments and progress where you left off.")
          + "</p>"

        // Two equal columns (you | where you play), then the level picker spanning
        // the full width beneath - symmetrical, no orphaned half-row.
        + '<div style="margin-top:24px">'
        + (up ? rolePick : "")
        + (up
            ? (role === "coach"
                ? fields
                : '<div class="auth__grid">' + fields + surveyHtml + "</div>" + levels)
            : fields)
        + "</div>"

        + '<button class="btn btn--primary btn--block btn--lg" data-cx-submit style="margin-top:24px">'
        + (up ? "Create account" : "Continue") + icon("arrow_forward") + "</button>"
        + '<div data-cx-error hidden style="margin-top:14px"></div>'
        + "</div>"

        + '<p class="auth__switch">' + (up ? "Already have an account?" : "New to Coachfio?")
        + ' <button class="link" type="button" data-cx-switch>'
        + (up ? "Sign in" : "Create one") + "</button></p>"

        + (up ? "" : '<p class="faint t-xs text-center" style="margin-top:20px">'
            + "No password yet - accounts are keyed by email until the hosted sign-in "
            + "provider is connected.</p>");

      $$("[data-cx-role]").forEach(function (b) {
        b.addEventListener("click", function () {
          captureForm();
          role = b.getAttribute("data-cx-role");
          render();
        });
      });

      $$("[data-cx-level]").forEach(function (b) {
        b.addEventListener("click", function () {
          level = b.getAttribute("data-cx-level");
          levelTouched = true;   // stop the suggestion overriding a deliberate choice
          $$("[data-cx-level]").forEach(function (x) {
            x.setAttribute("aria-pressed", String(x === b));
          });
        });
      });

      $$("[data-cx-q]").forEach(function (sel) {
        sel.addEventListener("change", function () {
          captureForm();   // keep whatever is already typed before re-rendering
          var key = sel.getAttribute("data-cx-q");
          answers[key] = sel.value;
          // Clear any answer this one now locks, so a Division 9 player can't keep
          // a stale Champs result that no longer applies.
          survey.forEach(function (q) {
            if (questionLocked(q, answers)) answers[q.key] = "";
          });
          suggestLevel(answers).then(function (s) {
            suggestion = s;
            if (s && !levelTouched) level = s.level;   // pre-select, never force
            render();
          });
        });
      });
      $("[data-cx-switch]").addEventListener("click", function () {
        captureForm();   // carry the email across the sign-in / sign-up toggle
        mode = up ? "in" : "up";
        if (mode === "up") loadSurvey();
        render();
      });
      $("[data-cx-submit]").addEventListener("click", submit);
      $("#cx-email").addEventListener("keydown", function (e) {
        if (e.key === "Enter") submit();
      });
      $("#cx-email").focus();
    }

    function submit() {
      var btn = $("[data-cx-submit]");
      var email = ($("#cx-email").value || "").trim();
      var errBox = $("[data-cx-error]");
      if (errBox) errBox.hidden = true;
      if (!email) return showError("Enter your email address.");

      btn.disabled = true;
      var body = mode === "up"
        ? (role === "coach"
            ? { email: email, display_name: ($("#cx-name") || {}).value || "", role: "coach" }
            : { email: email, display_name: ($("#cx-name") || {}).value || "",
                skill_level: level, skill_survey: answers, role: "player" })
        : { email: email };

      j("/api/auth/" + (mode === "up" ? "signup" : "signin"), {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      }).then(function (r) {
        setIdentity(r.user_id);
        if (mode !== "up") { location.href = "/"; return; }
        // New account: the welcome takes the whole screen. Replace the auth shell
        // rather than rendering inside it, so the card and its width cap go away.
        var shell = $("#main");
        shell.className = "welcome-screen";
        shell.innerHTML =
          '<div class="welcome">'
          + '<img class="welcome__logo" src="/fc27.png" alt="EA Sports FC 27">'
          + '<span class="material-symbols-outlined welcome__pad">stadia_controller</span>'
          + '<p class="welcome__title">Welcome to Coachfio</p>'
          + '<p class="welcome__sub">Be ready to level up your game.</p>'
          + "</div>";
        setTimeout(function () { location.href = "/"; }, 2100);
      }).catch(function (e) {
        btn.disabled = false;
        showError(e.detail || e.message);
      });
    }

    if (mode === "up") loadSurvey();
    render();
  }

  // Best available human label for the signed-in account.
  function accountLabel(p) {
    if (!p) return "Account";
    var n = String(p.display_name || "").trim();
    if (!n && p.email) n = String(p.email).split("@")[0];   // "ilija@…" -> "ilija"
    if (!n) n = String(p.user_id || "").slice(0, 8);        // opaque id, last resort
    return n.length > 18 ? n.slice(0, 17) + "…" : n;
  }

  // Fills the top bar: usage counter, and the account link shows WHO you are
  // rather than the word "Account" - useful when switching between accounts.
  function loadAccountNav() {
    if (!identity()) return;
    // The match-count readout used to sit in the app bar; it was noise on every
    // page. The limit is still enforced server side (402 when exceeded).
    var linkEl = $('.appbar__end a[href="/account/"]');
    getAccount().then(function (d) {
      // Picture in the bar, name only as the accessible label - the name was
      // eating horizontal space that the nav needs on narrow screens.
      if (linkEl) {
        linkEl.classList.add("nav__avatar");
        linkEl.innerHTML = avatarHtml(d.profile, "avatar--sm");
        linkEl.setAttribute("aria-label", accountLabel(d.profile) + " - account");
        linkEl.setAttribute("title", accountLabel(d.profile));
      }
      // Role decides the fifth nav item: coaches get the office, players the
      // locker. Injected here (this runs on every page) instead of editing
      // seven HTML files - and re-marked, because markNav ran before this.
      var role = (d.profile || {}).role || "player";
      var target = role === "coach"
        ? { href: "/office/", label: "Office", ic: "business_center" }
        : { href: "/locker/", label: "My locker", ic: "checklist" };
      var nav = $(".nav");
      if (nav && !$('[data-nav="' + target.href + '"]', nav)) {
        var a = document.createElement("a");
        a.className = "nav__link"; a.href = target.href;
        a.setAttribute("data-nav", target.href);
        a.innerHTML = icon(target.ic) + target.label;
        nav.appendChild(a);
      }
      var tab = $(".tabbar");
      if (tab && !$('[data-nav="' + target.href + '"]', tab)) {
        var t = document.createElement("a");
        t.className = "tabbar__item"; t.href = target.href;
        t.setAttribute("data-nav", target.href);
        t.innerHTML = icon(target.ic) + target.label;
        var acct = $('[data-nav="/account/"]', tab);
        tab.insertBefore(t, acct || null);
      }
      markNav();
    }).catch(function () {});
  }

  // ---- guest mode ----------------------------------------------------------
  // You can browse the whole app signed out. Anything that would show YOUR data
  // shows a sign-in prompt instead - never a redirect, and never someone else's
  // matches (a shared "anonymous" account would leak between guests).
  //
  // `quiet` drops the buttons, for when the screen already shows a sign-up CTA -
  // two identical button pairs on one page just dilutes both.
  function signInPrompt(title, text, quiet) {
    return '<div class="empty"><div class="empty__icon">' + icon("lock") + "</div>"
      + '<p class="empty__title">' + esc(title) + "</p>"
      + '<p class="empty__text">' + esc(text) + "</p>"
      + (quiet ? "" : '<div class="empty__actions">'
          + '<a class="btn btn--primary btn--sm" href="/signin/?signup=1">Create account</a>'
          + '<a class="btn btn--secondary btn--sm" href="/signin/">Sign in</a>'
          + "</div>")
      + "</div>";
  }

  // The app bar is static markup in every page, so swap its right-hand side here
  // rather than maintaining two copies of the header in seven files.
  // The "Want a real coach too?" band on the home page. The markup ships in the
  // GUEST state (both doors), so this only has to correct it for someone signed
  // in. "I'm a coach" previously pointed at the auth screen for everyone, and the
  // router bounces a signed-in user off that screen straight back to "/" - click,
  // flicker, nothing, which reads as a dead button.
  function initHomeBand() {
    var band = $("[data-cx-coach-band]");
    if (!band || !identity()) return;          // guest: shipped markup is correct
    var find = $("[data-cx-find-coach]"), cta = $("[data-cx-coach-cta]");
    // Hide the pair until the role is known. Showing both and then removing one
    // is worse than a short gap: the wrong door is briefly clickable.
    band.hidden = true;
    getAccount().then(function (d) {
      var isCoach = ((d.profile || {}).role === "coach");
      if (cta) {
        cta.hidden = !isCoach;
        if (isCoach) cta.href = "/office/";     // their office, not a second account
      }
      if (find) find.hidden = isCoach;
    }).catch(function () {
      // Profile unavailable: fall back to the guest pair rather than a band with
      // no way out of it.
    }).then(function () { band.hidden = false; });
  }

  function renderAuthNav() {
    var end = $(".appbar__end");
    if (!end || identity()) return;
    end.innerHTML =
      '<a class="btn btn--secondary btn--sm" href="/signin/">Sign in</a>'
      + '<a class="btn btn--primary btn--sm" href="/signin/?signup=1">Create account</a>';
  }

  // ---- chat (shared by office + locker) ------------------------------------
  // Plain polling. The interval is cleared before every re-render, otherwise
  // navigating between threads stacks timers and the API gets hit N times per
  // tick. `chatTimer` is the single owner of that interval.
  var chatTimer = null;

  function chatPanel(host, peerId, peerName) {
    if (chatTimer) { clearInterval(chatTimer); chatTimer = null; }

    // Collapsed state is remembered per conversation: someone who minimises the
    // chat does not want it reopening on every page load.
    var KEY = "coachio.chatMin." + peerId;
    var collapsed = false;
    try { collapsed = localStorage.getItem(KEY) === "1"; } catch (e) {}
    // Messages already present when it was collapsed. Anything past this count
    // is new, and gets a badge on the header so minimising never hides the fact
    // that someone replied.
    var seenCount = null;

    function msgHtml(m) {
      return '<div class="msg' + (m.mine ? " msg--mine" : "") + '">'
        + '<span class="msg__body">' + esc(m.body) + "</span>"
        + '<span class="msg__time">' + esc(fmtTime(m.created_at)) + "</span></div>";
    }
    function fmtTime(iso) {
      var d = new Date(iso);
      return isNaN(d) ? "" : d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    }

    host.innerHTML =
      '<div class="chat' + (collapsed ? " is-collapsed" : "") + '" data-cx-chat>'
      + '<button type="button" class="chat__head" data-cx-chat-toggle'
      + ' aria-expanded="' + (!collapsed) + '">'
      + '<span class="tile tile--accent">' + icon("chat") + "</span>"
      + "<strong>" + esc(peerName) + "</strong>"
      + '<span class="badge badge--win chat__unread" hidden></span>'
      + icon("expand_more", "chat__chev") + "</button>"
      // Wrapper exists purely so collapsing can animate: you cannot transition
      // display:none, so the body's height is what moves.
      + '<div class="chat__body">'
      + '<div class="chat__scroll" data-cx-msgs><div class="faint t-xs">Loading...</div></div>'
      + '<form class="chat__composer" data-cx-chat-form>'
      + '<input class="input" data-cx-chat-input placeholder="Write a message..." autocomplete="off" maxlength="2000">'
      + '<button class="btn btn--primary" type="submit">' + icon("send") + "</button></form>"
      + "</div></div>";

    var panel = $("[data-cx-chat]", host);
    var scroll = $("[data-cx-msgs]", host);
    var unreadEl = $(".chat__unread", host);
    var lastCount = -1;

    function paintUnread(total) {
      if (!collapsed || seenCount === null) { unreadEl.hidden = true; return; }
      var n = Math.max(0, total - seenCount);
      unreadEl.hidden = n === 0;
      unreadEl.textContent = n + " new";
    }

    function load() {
      j("/api/chat/" + encodeURIComponent(peerId)).then(function (d) {
        var msgs = d.messages || [];
        paintUnread(msgs.length);
        // Skip the DOM work while collapsed - nothing is visible, and rewriting
        // the list would also reset the scroll position for when it reopens.
        if (collapsed || msgs.length === lastCount) return;
        lastCount = msgs.length;
        scroll.innerHTML = msgs.length
          ? msgs.map(msgHtml).join("")
          : '<div class="faint t-sm" style="padding:16px">No messages yet - say hello.</div>';
        scroll.scrollTop = scroll.scrollHeight;
      }).catch(function () {});
    }

    $("[data-cx-chat-toggle]", host).addEventListener("click", function () {
      collapsed = !collapsed;
      panel.classList.toggle("is-collapsed", collapsed);
      this.setAttribute("aria-expanded", String(!collapsed));
      try { localStorage.setItem(KEY, collapsed ? "1" : "0"); } catch (e) {}
      if (collapsed) {
        seenCount = lastCount < 0 ? 0 : lastCount;
      } else {
        seenCount = null;
        unreadEl.hidden = true;
        lastCount = -1;   // force a repaint of whatever arrived while it was shut
        load();
      }
    });

    $("[data-cx-chat-form]", host).addEventListener("submit", function (e) {
      e.preventDefault();
      var input = $("[data-cx-chat-input]", host);
      var text = (input.value || "").trim();
      if (!text) return;
      input.value = "";
      j("/api/chat/" + encodeURIComponent(peerId), {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ body: text }),
      }).then(load).catch(function () { input.value = text; });
    });

    load();
    // Polling continues while collapsed, otherwise the "N new" badge - the whole
    // point of minimising rather than closing - would never appear.
    chatTimer = setInterval(function () {
      if (!document.hidden) load();
    }, 5000);
  }

  // ---- OFFICE (coach home) --------------------------------------------------
  function initOffice() {
    var clientsHost = $("[data-cx-clients]"), detailHost = $("[data-cx-office-chat]");
    var overviewHost = $("[data-cx-office-overview]");
    if (!identity()) {
      clientsHost.innerHTML = signInPrompt("Sign in to open your office",
        "Your clients, their progress and your conversations live here.");
      if (detailHost) detailHost.innerHTML = "";
      return;
    }

    var clients = [], mine = [], requests = [], openId = null;

    // The list is a scannable index, not a dashboard: name, form, unread. Every
    // detail moved into the panel, because six expanded cards meant scrolling
    // past three players to reach the fourth.
    function clientRow(c) {
      var forms = (c.matches || []).slice(0, 5).reverse().map(function (m) {
        var b = badge(resultOf({ result: m.result }));
        return '<span class="result ' + b[2] + '">' + b[0][0] + "</span>";
      }).join("");
      return '<button type="button" class="client-row' + (c.user_id === openId ? " is-active" : "")
        + '" data-cx-client="' + esc(c.user_id) + '">'
        + '<span class="tile tile--accent">' + icon("person") + "</span>"
        + '<span class="client-row__main"><strong>' + esc(c.display_name) + "</strong>"
        + '<span class="faint t-xs">' + esc(c.skill_level || "") + " · " + c.analysed
        + " analysed"
        + (lastActive(c) ? " · last " + esc(fmtDate(lastActive(c))) : " · nothing yet")
        + "</span></span>"
        + (forms ? '<span class="form-strip client-row__form">' + forms + "</span>" : "")
        + (c.unread ? '<span class="badge badge--win">' + c.unread + "</span>" : "")
        + icon("chevron_right")
        + "</button>";
    }

    // Tabs, not one long scroll. Progress, matches and the conversation are
    // three different jobs; stacking them meant the chat sat below everything
    // and you had to scroll past a client's whole history to answer them.
    var tab = "progress";

    function progressTab(c) {
      var issues = (c.issues || []).map(function (i) {
        var pct = c.analysed ? Math.round((i.count / c.analysed) * 100) : 0;
        return '<div class="pattern"><span class="pattern__label">' + esc(i.label || i.tag) + "</span>"
          + '<span class="pattern__meter"><span class="pattern__fill" style="width:' + pct + '%"></span></span>'
          + '<span class="pattern__count">' + i.count + " / " + c.analysed + "</span></div>";
      }).join("");
      var forms = (c.matches || []).slice(0, 8).reverse().map(function (m) {
        var b = badge(resultOf({ result: m.result }));
        return '<span class="result ' + b[2] + '" title="' + esc(m.score || "") + '">' + b[0][0] + "</span>";
      }).join("");
      return (forms ? '<p class="eyebrow">Recent form</p>'
                + '<div class="form-strip" style="margin-bottom:20px">' + forms + "</div>" : "")
        + (issues
            ? '<p class="eyebrow">What keeps costing them</p><div class="card">' + issues + "</div>"
            : '<p class="muted t-sm">Nothing recurring flagged yet.</p>');
    }

    function matchesTab(c) {
      var rows = (c.matches || []).map(function (m) {
        var b = badge(resultOf({ result: m.result }));
        return '<div class="cmatch">'
          + '<div class="row" style="gap:10px">'
          + '<span class="result ' + b[2] + '">' + b[0][0] + "</span>"
          + '<span class="mono t-sm">' + esc(m.score || "-") + "</span>"
          + '<span class="faint t-xs">' + esc(fmtDate(m.created_at)) + "</span>"
          + (c.reports_shared
              ? '<a class="link t-xs" style="margin-left:auto" href="/report/?id=' + esc(m.id)
                + "&client=" + esc(c.user_id) + '">Open report</a>'
              : "")
          + "</div>"
          + (m.takeaway ? '<p class="muted t-sm" style="margin-top:6px">' + esc(m.takeaway) + "</p>" : "")
          + "</div>";
      }).join("");
      return (rows ? '<div class="stack-s">' + rows + "</div>"
                   : '<p class="muted t-sm">Nothing analysed yet.</p>')
        + (c.reports_shared ? ""
            : '<p class="faint t-xs" style="margin-top:12px">' + esc(c.display_name)
              + " hasn't shared their full reports with you. They can turn that on "
              + "from their locker.</p>");
    }

    function detail(c) {
      detailHost.innerHTML =
        panelHead("Details", c.analysed + " match" + (c.analysed === 1 ? "" : "es") + " analysed")
        + '<div class="panel">'
        + '<div class="panel__head"><div class="row">'
        + avatarHtml(c, "")
        + '<div><strong>' + esc(c.display_name) + "</strong>"
        + '<div class="faint t-xs">' + esc(c.skill_level || "") + "</div></div></div>"
        + '<button class="icon-btn" data-cx-close-detail aria-label="Close">'
        + icon("close") + "</button></div>"

        + '<div class="tabs" role="tablist">'
        + [["progress", "Progress", 0], ["matches", "Matches", 0], ["chat", "Chat", c.unread || 0]]
            .map(function (t) {
              return '<button type="button" class="tabs__btn" role="tab" data-cx-tab="' + t[0] + '"'
                + ' aria-selected="' + (tab === t[0]) + '">' + t[1]
                + (t[2] ? '<span class="badge badge--win">' + t[2] + "</span>" : "")
                + "</button>";
            }).join("")
        + "</div>"

        + '<div class="panel__body" data-cx-tabbody></div></div>';

      $("[data-cx-close-detail]").addEventListener("click", closeDetail);
      $$("[data-cx-tab]").forEach(function (b) {
        b.addEventListener("click", function () {
          tab = b.getAttribute("data-cx-tab");
          detail(c);
        });
      });
      paintTab(c);
    }

    function paintTab(c) {
      var body = $("[data-cx-tabbody]", detailHost);
      // Leaving the chat tab must stop its poller, or every client you have ever
      // opened keeps hitting the API for the rest of the session.
      if (tab !== "chat" && chatTimer) { clearInterval(chatTimer); chatTimer = null; }
      if (tab === "progress") body.innerHTML = progressTab(c);
      else if (tab === "matches") body.innerHTML = matchesTab(c);
      else { body.innerHTML = ""; chatPanel(body, c.user_id, c.display_name); }
    }

    function closeDetail() {
      // Stop the poller with the panel - otherwise a closed conversation keeps
      // hitting the API every 5s for the rest of the session.
      if (chatTimer) { clearInterval(chatTimer); chatTimer = null; }
      openId = null;
      detailHost.innerHTML = placeholder();
      renderList();
    }

    // Both columns open with a .section__head so their headings sit on the same
    // baseline - without one on this side the panel started ~30px high and the
    // two columns read as unrelated blocks rather than one row.
    function panelHead(label, sub) {
      return '<div class="section__head"><h2 class="t-section">' + esc(label) + "</h2>"
        + (sub ? '<span class="faint t-xs">' + esc(sub) + "</span>" : "") + "</div>";
    }

    function placeholder() {
      if (!clients.length && !requests.length) return "";
      var shared = sharedHabits();
      if (!shared.length) {
        return panelHead("Details")
          + '<div class="panel panel--empty">'
          + '<span class="material-symbols-outlined">touch_app</span>'
          + "<p>Pick a client to see their progress, matches and chat.</p></div>";
      }
      // An empty panel is wasted space. This is the one thing a coach can only
      // see by holding several players' reports side by side.
      return panelHead("Across your clients",
                       shared.length + " shared habit" + (shared.length === 1 ? "" : "s"))
        + '<div class="panel"><div class="panel__body">'
        + '<p class="muted t-sm" style="margin-bottom:var(--s4)">Habits more than one '
        + "of your players keeps repeating. Worth a session for the group rather "
        + "than the same conversation four times.</p>"
        + shared.map(function (h) {
            return '<div class="shared">'
              + '<div class="row-between" style="gap:12px;align-items:flex-start">'
              + '<span class="t-sm" style="font-weight:550">' + esc(h.label) + "</span>"
              + '<span class="badge badge--warn" style="flex:none">' + h.who.length
              + " players</span></div>"
              + '<div class="catch-row" style="margin-top:8px">'
              + h.who.map(function (n) {
                  return '<span class="catch catch--sm">' + esc(n) + "</span>";
                }).join("")
              + "</div></div>";
          }).join("")
        + '<p class="faint t-xs" style="margin-top:var(--s4)">Pick a client on the left '
        + "for their individual progress and chat.</p>"
        + "</div></div>";
    }

    function athleteGroups() {
      var groups = {};
      mine.forEach(function (m) {
        var a = ((m.capture || {}).athlete || "").trim();
        if (!a || m.status !== "complete") return;
        (groups[a] = groups[a] || []).push(m);
      });
      var names = Object.keys(groups).sort();
      if (!names.length) return "";
      return '<div class="section__head" style="margin-top:32px"><h2 class="t-section">Your uploads by athlete</h2>'
        + "</div>"
        + '<div class="stack-s">'
        + names.map(function (n) {
            var ms = groups[n];
            var forms = ms.slice(0, 8).reverse().map(function (m) {
              var b = badge(resultOf(m.outcome));
              return '<span class="result ' + b[2] + '">' + b[0][0] + "</span>";
            }).join("");
            return '<a class="client-row" href="/report/?id=' + esc(ms[0].id) + '">'
              + '<span class="tile tile--info">' + icon("videocam") + "</span>"
              + '<span class="client-row__main"><strong>' + esc(n) + "</strong>"
              + '<span class="faint t-xs">' + ms.length + " match"
              + (ms.length === 1 ? "" : "es") + "</span></span>"
              + '<span class="form-strip client-row__form">' + forms + "</span>"
              + icon("chevron_right") + "</a>";
          }).join("")
        + "</div>";
    }

    // Pending requests sit ABOVE the client list: they are the only thing on this
    // page that needs a decision, and a request buried under twelve clients is a
    // player waiting days for an answer.
    function requestsBlock() {
      if (!requests.length) return "";
      return '<div class="section__head"><h2 class="t-section">Requests</h2>'
        + '<span class="faint t-xs">' + requests.length + " waiting</span></div>"
        + '<div class="stack-s" style="margin-bottom:28px">'
        + requests.map(function (r) {
            return '<div class="client-row" style="cursor:default">'
              + '<span class="tile tile--warn">' + icon("person_add") + "</span>"
              + '<span class="client-row__main"><strong>' + esc(r.display_name) + "</strong>"
              + '<span class="faint t-xs">' + esc(r.skill_level || "") + " · wants you as their coach</span></span>"
              + '<span class="row" style="gap:6px;flex:none">'
              + '<button class="btn btn--primary btn--sm" data-cx-accept="' + esc(r.user_id) + '">Accept</button>'
              + '<button class="btn btn--ghost btn--sm" data-cx-decline="' + esc(r.user_id) + '">Decline</button>'
              + "</span></div>";
          }).join("")
        + "</div>";
    }

    function respond(playerId, decision, btn) {
      var go = decision === "accept"
        ? Promise.resolve(true)
        : confirmDialog({
            danger: true,
            icon: "person_remove",
            title: "Decline this request?",
            text: "The request disappears and nothing is shared. They can ask you again later.",
            confirmLabel: "Decline",
          });
      go.then(function (ok) {
        if (!ok) return;
        btn.disabled = true;
        j("/api/requests/" + encodeURIComponent(playerId) + "/" + decision, { method: "POST" })
          .then(load)
          .catch(function (e) { btn.disabled = false; showError(e.message); });
      });
    }

    // Overview strip. The page used to open on a bare list against a large empty
    // panel; these are the numbers a coach actually opens the office to check.
    function overview() {
      var unread = clients.reduce(function (n, c) { return n + (c.unread || 0); }, 0);
      var analysed = clients.reduce(function (n, c) { return n + (c.analysed || 0); }, 0);
      var athletes = {};
      mine.forEach(function (m) {
        var a = ((m.capture || {}).athlete || "").trim();
        if (a && m.status === "complete") athletes[a] = 1;
      });
      function tile(label, value, note, tone) {
        return '<div class="stat"><div class="stat__label">' + esc(label) + "</div>"
          + '<div class="stat__value stat__value--tile' + (tone ? " " + tone : "") + '">'
          + esc(String(value)) + "</div>"
          + (note ? '<div class="faint t-xs" style="margin-top:2px">' + esc(note) + "</div>" : "")
          + "</div>";
      }
      return '<div class="grid grid-4" style="margin-bottom:var(--s6)">'
        + tile("Clients", clients.length, clients.length ? "connected" : "none yet")
        + tile("Requests", requests.length,
               requests.length ? "waiting on you" : "none waiting",
               requests.length ? "k-warn" : "")
        + tile("Unread", unread, unread ? "new messages" : "all caught up",
               unread ? "k-good" : "")
        + tile("Matches analysed", analysed + Object.keys(athletes).length,
               "clients + your uploads")
        + "</div>";
    }

    // Who needs you first: unread messages, then whoever has gone longest
    // without an analysed match. A coach with ten clients should not have to
    // work out the order themselves.
    function lastActive(c) {
      var m = (c.matches || [])[0];
      return m && m.created_at ? m.created_at : "";
    }
    function byPriority(a, b) {
      if ((b.unread || 0) !== (a.unread || 0)) return (b.unread || 0) - (a.unread || 0);
      return String(lastActive(b)).localeCompare(String(lastActive(a)));
    }

    // Habits more than one client shares. This is the view a coach cannot get
    // anywhere else - it says what to teach everyone rather than one person.
    function sharedHabits() {
      var byTag = {};
      clients.forEach(function (c) {
        (c.issues || []).forEach(function (i) {
          var k = i.tag || i.label;
          if (!k) return;
          (byTag[k] = byTag[k] || { label: i.label || i.tag, who: [] }).who.push(c.display_name);
        });
      });
      return Object.keys(byTag).map(function (k) { return byTag[k]; })
        .filter(function (x) { return x.who.length > 1; })
        .sort(function (a, b) { return b.who.length - a.who.length; })
        .slice(0, 4);
    }

    function renderList() {
      if (overviewHost) overviewHost.innerHTML = overview();
      clientsHost.innerHTML = requestsBlock() +
        (clients.length
          ? '<div class="section__head"><h2 class="t-section">Clients</h2>'
            + '<span class="faint t-xs">' + clients.length + " connected</span></div>"
            + '<div class="stack-s reveal-list">'
            + clients.slice().sort(byPriority).map(clientRow).join("") + "</div>"
          : emptyState("supervisor_account", "No clients yet",
              "Players connect to you from their locker - share Coachfio with them and "
              + "they pick you from the coach directory. Their progress and chat appear here."))
        + athleteGroups();

      $$("[data-cx-accept]").forEach(function (b) {
        b.addEventListener("click", function () { respond(b.getAttribute("data-cx-accept"), "accept", b); });
      });
      $$("[data-cx-decline]").forEach(function (b) {
        b.addEventListener("click", function () { respond(b.getAttribute("data-cx-decline"), "decline", b); });
      });

      $$("[data-cx-client]").forEach(function (row) {
        row.addEventListener("click", function () {
          var id = row.getAttribute("data-cx-client");
          if (id === openId) { closeDetail(); return; }   // click again to close
          openId = id;
          renderList();
          detail(clients.filter(function (c) { return c.user_id === id; })[0]);
          if (window.innerWidth < 900) detailHost.scrollIntoView({ behavior: "smooth" });
        });
      });
    }

    function load() {
      Promise.all([j("/api/clients"), listMatches(), j("/api/requests")]).then(function (res) {
        clients = (res[0] || {}).clients || [];
        mine = res[1] || [];
        requests = (res[2] || {}).requests || [];
        renderList();
        if (detailHost && !openId) detailHost.innerHTML = placeholder();
      }).catch(function (e) { clientsHost.innerHTML = errorState(e.message); });
    }
    load();
  }

  // ---- LOCKER (player home) -------------------------------------------------
  function initLocker() {
    var progHost = $("[data-cx-locker-progress]"), planHost = $("[data-cx-locker-plan]");
    var coachHost = $("[data-cx-locker-coach]");
    if (!identity()) {
      progHost.innerHTML = signInPrompt("Sign in to open your locker",
        "Your progress, practice plan and coach conversations live here.");
      planHost.innerHTML = ""; coachHost.innerHTML = "";
      return;
    }

    // slug for checklist keys: stable across visits as long as the drill text is.
    function planKey(text) {
      return String(text || "").toLowerCase().replace(/[^a-z0-9]+/g, "-").slice(0, 80);
    }

    function renderPlan(items, checks) {
      if (!items.length) {
        planHost.innerHTML = emptyState("checklist", "No plan yet",
          "Analyse a match and the coach's practice plan lands here as a checklist.");
        return;
      }
      planHost.innerHTML =
        '<div class="section__head"><h2 class="t-section">Practice checkboard</h2>'
        + "</div>"
        + '<div class="card"><div class="stack-s">'
        + items.map(function (it) {
            var st = checks[it.key] || {};
            return '<label class="check check--row">'
              + '<input type="checkbox" data-cx-check="' + esc(it.key) + '"' + (st.done ? " checked" : "") + ">"
              + '<span><strong class="t-sm">' + esc(it.title) + "</strong>"
              + (it.detail ? '<span class="muted t-sm" style="display:block">' + esc(it.detail) + "</span>" : "")
              + "</span></label>";
          }).join("")
        + "</div></div>";
      $$("[data-cx-check]").forEach(function (box) {
        box.addEventListener("change", function () {
          j("/api/checklist", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ key: box.getAttribute("data-cx-check"), done: box.checked }),
          }).catch(function () { box.checked = !box.checked; });
        });
      });
    }

    function renderCoach(threads) {
      if (threads.length) {
        var c = threads[0];   // one coach is the overwhelmingly common case
        coachHost.innerHTML =
          '<div class="section__head"><h2 class="t-section">Your coach</h2>'
          + (threads.length > 1 ? '<span class="faint t-xs">' + threads.length + " connected</span>" : "")
          + "</div><div data-cx-chat-slot></div>"
          // Connecting shares the summary; the FULL report is a second, explicit
          // grant - it names your players and reads your mistakes back in detail.
          + '<div class="card" style="margin-top:12px">'
          + '<label class="check"><input type="checkbox" data-cx-share'
          + (c.share_reports ? " checked" : "") + ">"
          + '<span><strong class="t-sm">Share my full reports with ' + esc(c.display_name) + "</strong>"
          + '<span class="muted t-xs" style="display:block">They can already see your form and '
          + "recurring issues. This also lets them open the whole AI report for each match.</span>"
          + "</span></label></div>"
          + '<button class="link t-xs" data-cx-disconnect style="margin-top:8px">Disconnect from '
          + esc(c.display_name) + "</button>";
        chatPanel($("[data-cx-chat-slot]", coachHost), c.user_id, c.display_name);
        $("[data-cx-share]").addEventListener("change", function () {
          var box = this;
          j("/api/coaches/" + encodeURIComponent(c.user_id) + "/sharing", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ share_reports: box.checked }),
          }).catch(function () { box.checked = !box.checked; });
        });
        $("[data-cx-disconnect]").addEventListener("click", function () {
          confirmDialog({
            danger: true,
            icon: "link_off",
            title: "Disconnect from " + c.display_name + "?",
            text: "They immediately lose access to your form, your recurring issues and "
              + "your reports, and the conversation closes. Your matches and messages "
              + "are not deleted - you can connect again later.",
            confirmLabel: "Disconnect",
          }).then(function (ok) {
            if (!ok) return;
            j("/api/coaches/" + encodeURIComponent(c.user_id) + "/disconnect", { method: "POST" })
              .then(loadCoachSection);
          });
        });
        return;
      }
      j("/api/coaches").then(function (d) {
        var coaches = (d.coaches || []);
        var waiting = coaches.filter(function (c) { return c.status === "pending"; });
        coachHost.innerHTML =
          '<div class="section__head"><h2 class="t-section">'
          + (waiting.length ? "Your request" : "Find a coach") + "</h2></div>"
          + (coaches.length
              // The whole row opens the coach's page: choosing a coach off a
              // bare name is not a choice, so browsing has to come first.
              ? '<div class="stack-s">' + coaches.map(function (c) {
                  var pending = c.status === "pending";
                  return '<a class="client-row" href="/coach/?id=' + esc(c.user_id) + '">'
                    + avatarHtml(c, "")
                    + '<span class="client-row__main"><strong>' + esc(c.display_name) + "</strong>"
                    + '<span class="faint t-xs">'
                    + esc(pending ? "Waiting for them to accept"
                                  : (c.headline || (c.specialties || []).join(" \u00b7 ")
                                     || "View profile"))
                    + "</span></span>"
                    + (pending ? '<span class="badge badge--warn">Requested</span>'
                        : (priceLabel(c)
                            ? '<span class="badge badge--neutral">' + esc(priceLabel(c)) + "</span>"
                            : ""))
                    + icon("chevron_right") + "</a>";
                }).join("") + "</div>"
                + '<p class="faint t-xs" style="margin-top:10px">The coach has to accept before '
                + "anything is shared. Once they do, they can see your form, recurring issues and "
                + "match summaries, and you can chat. You can disconnect any time.</p>"
              : emptyState("supervisor_account", "No coaches here yet",
                  "When a coach creates a Coachfio account, they appear here and you can "
                  + "ask them to coach you."));
      }).catch(function (e) { coachHost.innerHTML = errorState(e.message); });
    }

    function loadCoachSection() {
      j("/api/chat/threads").then(function (d) { renderCoach(d.threads || []); })
        .catch(function (e) { coachHost.innerHTML = errorState(e.message); });
    }

    Promise.all([listMatches(), getPatterns(5), j("/api/checklist")]).then(function (res) {
      var done = (res[0] || []).filter(function (m) { return m.status === "complete"; });
      done.sort(function (a, b) { return (b.created_at || "").localeCompare(a.created_at || ""); });
      var patterns = res[1] || {}, checks = (res[2] || {}).checklist || {};

      // "One thing to work on": the diagnosis headline if the latest report has
      // one, else the most frequent recurring issue. ONE, on purpose.
      var latest = done[0];
      var rep = latest && (latest.insights || []).filter(function (i) { return i.kind === "coaching_report"; })[0];
      var pay = (rep && rep.payload) || {};
      var focus = (pay.diagnosis || {}).highest_value_habit
        || ((patterns.issues || [])[0] || {}).label || "";

      progHost.innerHTML =
        (focus
          ? '<section class="section reveal"><div class="card focus-card">'
            + '<p class="eyebrow">One thing to work on</p>'
            + '<p class="focus-card__text">' + esc(focus) + "</p>"
            + (latest ? '<a class="link t-sm" href="/report/?id=' + esc(latest.id) + '">From your latest report</a>' : "")
            + "</div></section>"
          : "")
        + (done.length
            ? '<section class="section">' + formStrip(done.slice(0, 5)) + "</section>"
              + (patternsCard(patterns) ? '<section class="section">' + patternsCard(patterns) + "</section>" : "")
            : emptyState("analytics", "Nothing analysed yet",
                "Upload a match and your progress starts building here.",
                '<a class="btn btn--primary btn--sm" href="/upload/">Upload a match</a>'));

      // Plan items: template practice_plan first, legacy practice_drills as the
      // fallback. Walk BACK through matches until one actually has a plan - the
      // newest report can legitimately lack one, and an empty checkboard when
      // last week's plan exists would look like lost data.
      var items = [];
      for (var di = 0; di < done.length && !items.length; di++) {
        var r2 = (done[di].insights || []).filter(function (i) { return i.kind === "coaching_report"; })[0];
        var p2 = (r2 && r2.payload) || {};
        (p2.practice_plan || []).forEach(function (pr) {
          if (pr && (pr.problem || pr.drill)) items.push({
            key: planKey(pr.problem || pr.drill),
            title: pr.problem || "Priority", detail: pr.drill || "",
          });
        });
        if (!items.length) (p2.practice_drills || []).forEach(function (d) {
          var t = (d && typeof d === "object") ? d.point : d;
          if (t) items.push({ key: planKey(t), title: String(t), detail: "" });
        });
      }
      renderPlan(items, checks);
      loadCoachSection();
    }).catch(function (e) { progHost.innerHTML = errorState(e.message); });
  }

  // ---- a coach's public page ------------------------------------------------
  function initCoachPage() {
    var main = $("main"), id = qs("id");
    if (!identity()) {
      main.innerHTML = signInPrompt("Sign in to view coaches",
        "Create an account to browse coaches and ask one to work with you.");
      return;
    }
    if (!id) { main.innerHTML = errorState("No coach specified."); return; }

    function render(c) {
      var pending = c.status === "pending", linked = c.status === "accepted";
      main.innerHTML =
        '<p style="margin-bottom:var(--s4)"><a class="link t-sm" href="/locker/">'
        + icon("arrow_back") + "Back to your locker</a></p>"

        + '<div class="phero reveal">'
        + '<div class="phero__pic">' + avatarHtml(c, "avatar--xl") + "</div>"
        + '<div class="phero__id">'
        + '<h1 class="phero__name">' + esc(c.display_name) + "</h1>"
        + (c.headline ? '<p class="phero__mail">' + esc(c.headline) + "</p>" : "")
        + '<div class="phero__meta">'
        + '<span class="badge badge--info">Coach</span>'
        + (c.experience ? '<span class="phero__since">' + icon("military_tech")
            + esc(c.experience) + "</span>" : "")
        + '<span class="phero__since">' + icon("group")
        + c.clients + " player" + (c.clients === 1 ? "" : "s") + " coached</span>"
        + "</div></div>"
        + '<div class="phero__actions">'
        + (linked
            ? '<span class="badge badge--win">Your coach</span>'
            : pending
              ? '<span class="badge badge--warn">Request sent</span>'
              : '<button class="btn btn--primary" data-cx-ask>' + icon("person_add")
                + "Ask to coach me</button>")
        + "</div></div>"

        + '<div class="grid grid-2 reveal-list" style="margin-top:var(--s5);align-items:stretch">'
        + '<div class="pcard"><div class="pcard__head">'
        + '<span class="pcard__icon">' + icon("person") + "</span><div>"
        + '<h2 class="pcard__title">About</h2></div></div>'
        + (c.bio
            ? '<p style="white-space:pre-wrap;line-height:1.65">' + esc(c.bio) + "</p>"
            : '<p class="muted t-sm">This coach has not written a bio yet.</p>')
        + "</div>"

        + '<div class="pcard"><div class="pcard__head">'
        + '<span class="pcard__icon">' + icon("target") + "</span><div>"
        + '<h2 class="pcard__title">Specialities</h2></div></div>'
        + ((c.specialties || []).length
            ? '<div class="catch-row">' + c.specialties.map(function (x) {
                return '<span class="catch">' + icon("check") + esc(x) + "</span>";
              }).join("") + "</div>"
            : '<p class="muted t-sm">None listed.</p>')
        + '<p class="faint t-xs" style="margin-top:auto;padding-top:var(--s4)">'
        + "Asking sends a request. Nothing is shared until they accept, and you "
        + "choose separately whether to share your full reports.</p>"
        + "</div>"

        // What you get, and for how much. Full width: this is the section the
        // decision actually turns on.
        + ((c.includes || []).length || c.price
            ? '<div class="pcard" style="grid-column:1/-1"><div class="pcard__head">'
              + '<span class="pcard__icon">' + icon("workspace_premium") + "</span><div>"
              + '<h2 class="pcard__title">' + esc(c.package_title || "What you get") + "</h2>"
              + '<p class="pcard__sub">Everything included when you work with this coach.</p>'
              + "</div>"
              + (c.price
                  ? '<div class="pricetag"><span class="pricetag__amt">' + esc(priceLabel(c))
                    + "</span></div>"
                  : "")
              + "</div>"
              + ((c.includes || []).length
                  ? '<div class="checklist">' + c.includes.map(function (x) {
                      return '<div class="checklist__item">' + icon("check_circle")
                        + "<span>" + esc(x) + "</span></div>";
                    }).join("") + "</div>"
                  : '<p class="muted t-sm">This coach has not listed what is included yet.</p>')
              + '<p class="faint t-xs" style="margin-top:var(--s3)">'
              + "Coachfio does not handle payment. Arrange it directly with the coach "
              + "after they accept your request.</p>"
              + "</div>"
            : "")
        + "</div>";

      var ask = $("[data-cx-ask]");
      if (ask) ask.addEventListener("click", function () {
        ask.disabled = true;
        j("/api/coaches/" + encodeURIComponent(id) + "/connect", { method: "POST" })
          .then(load).catch(function (e) { ask.disabled = false; showError(e.message); });
      });
    }

    function load() {
      j("/api/coaches/" + encodeURIComponent(id)).then(render)
        .catch(function (e) { main.innerHTML = errorState(e.message); });
    }
    load();
  }

  // ---- nav -----------------------------------------------------------------
  function markNav() {
    var path = location.pathname.replace(/index\.html$/, "");
    if (path.length > 1 && path.slice(-1) !== "/") path += "/";
    $$("[data-nav]").forEach(function (a) {
      if (a.getAttribute("data-nav") === path) a.setAttribute("aria-current", "page");
    });
  }

  // ---- router --------------------------------------------------------------
  // The game comes from the hostname, so it has to be known before any page
  // builds a /trends/{game}/{edition} URL. One same-origin call, and a failure
  // falls through to the built-in default rather than blocking the whole app on
  // it: a page that renders against the wrong game is recoverable, a page that
  // never renders is not.
  function bootSite() {
    return j("/api/site").then(function (d) {
      SITE = d || SITE;
      if (d && d.game) GAME = { game_id: d.game.game_id, edition: d.game.edition };
    }).catch(function () {});
  }

  document.addEventListener("DOMContentLoaded", function () {
    // The root domain is a landing page, not the app: no match hosts to render
    // into and no game to render them about, so it takes its own path.
    if (document.body.hasAttribute("data-cx-root")) {
      bootSite().then(renderGameTiles);
      return;
    }
    bootSite().then(startRouter);
  });

  function renderGameTiles() {
    var host = $("[data-cx-game-list]");
    if (!host) return;
    var sites = SITE.sites || [];
    if (!sites.length) {
      host.innerHTML = emptyState("sports_esports", "No games yet",
        "Games appear here as they are added.");
      return;
    }
    // SITE.root comes from the server, which is the only place that knows which
    // label is a subdomain. Deriving it here is what produced fifa.fifa.localhost.
    var root = SITE.root || location.hostname;
    var port = location.port ? ":" + location.port : "";
    host.innerHTML = '<div class="hub-games">' + sites.map(function (s) {
      var url = location.protocol + "//" + s.label + "." + root + port + "/";
      return '<a class="hub-game" href="' + url + '">'
        + '<span class="hub-game__mark">' + icon("sports_esports") + "</span>"
        + "<span><span class='hub-game__name'>" + esc(s.display_name) + "</span><br>"
        + "<span class='hub-game__host'>" + esc(s.label + "." + root) + "</span></span>"
        + '<span class="hub-game__go">' + icon("arrow_forward") + "</span></a>";
    }).join("") + "</div>";
  }


  function startRouter() {
    markNav();
    var path = location.pathname.replace(/index\.html$/, "");
    var onAuth = /\/signin\/?$/.test(path);

    // Signed in, the auth screen has nothing to offer.
    if (onAuth) {
      if (identity()) { location.href = "/"; return; }
      initAuth();
      return;
    }
    // The account page is the ONE screen with nothing to show a guest.
    if (!identity() && /\/account\/?$/.test(path)) {
      location.href = "/signin/";
      return;
    }

    renderAuthNav();
    loadAccountNav();

    if (/\/account\/?$/.test(path)) initAccount();
    else if (/\/upload\/?$/.test(path)) initUpload();
    else if (/\/analyzing\/?$/.test(path)) initAnalyzing();
    else if (/\/report\/?$/.test(path)) initReport();
    else if (/\/moment\/?$/.test(path)) initMoment();
    else if (/\/statistics\/?$/.test(path)) initTrends();
    else if (/\/office\/?$/.test(path)) initOffice();
    else if (/\/coach\/?$/.test(path)) initCoachPage();
    else if (/\/locker\/?$/.test(path)) initLocker();
    else { applySite(); loadRecent(); initHomeBand(); }  // home
  }

  // The home page ships the FC wording because that is the only live site today.
  // On the root domain there is no game, so the hero says so and offers the
  // sites instead of pretending to be one of them.
  function applySite() {
    var eyebrow = $("[data-cx-game]");
    if (eyebrow && SITE.game) eyebrow.textContent = SITE.game.display_name;
    if (SITE.game || !(SITE.sites || []).length) return;

    if (eyebrow) eyebrow.textContent = "Choose your game";
    var port = location.port ? ":" + location.port : "";
    // The server computes the root. This used to count dots here, which treated
    // fifa.localhost as a bare domain and linked on to fifa.fifa.localhost.
    // Knowing which label is a subdomain is the server's job, and duplicating the
    // rule in two languages is what let the two disagree.
    var root = SITE.root || location.hostname;
    var row = $(".row");
    if (!row) return;
    row.innerHTML = SITE.sites.map(function (s, i) {
      return '<a class="btn btn--' + (i ? "secondary" : "primary") + ' btn--lg" href="'
        + location.protocol + "//" + s.label + "." + root + port + '/">'
        + esc(s.display_name) + "</a>";
    }).join("");
  }
})();
