/* Hub behaviour (coachfio.com only). Deliberately separate from coach.js: the
   hub loads none of the app's match/report/chat code, and the app loads none of
   this. Shared state between them is the session, not JavaScript.

   Auth note: this talks to the EXISTING /api/auth endpoints behind the
   `current_user` seam. It does not invent a token format. The shared
   cross-subdomain session (JWT cookie on .coachfio.com) is a backend decision
   that has not been made yet, so today the hub stores the identity the same way
   a game site does and cannot hand it across origins. That limitation is real
   and is called out in the UI rather than papered over. */
(function () {
  "use strict";

  var ID_KEY = "coachio.user";       // legacy; cleared on sign-out
  var $ = function (sel, root) { return (root || document).querySelector(sel); };

  // The session is an HttpOnly cookie, not a value this file can read. `_me`
  // caches what /api/auth/session answered, resolved once before anything
  // renders. See the note in coach.js for why the localStorage id had to go.
  var _me = "";
  function identity() { return _me; }
  function setIdentity(v) { _me = v || ""; }
  function bootSession() {
    return api("/api/auth/session").then(function (d) {
      _me = (d && d.signed_in && d.profile && d.profile.user_id) || "";
    }).catch(function () { _me = ""; });
  }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }
  function api(path, opts) {
    opts = opts || {};
    opts.headers = opts.headers || {};
    // Same origin, so the session cookie rides along on its own.
    return fetch(path, opts).then(function (r) {
      if (!r.ok) return r.text().then(function (t) { throw new Error(t || r.statusText); });
      return r.status === 204 ? null : r.json();
    });
  }

  // Sites come from /api/site, which resolves them from SITE_HOSTS. Adding a
  // game stays a config line: nothing here needs to know a game exists.
  function loadSites() { return api("/api/site").catch(function () { return { sites: [] }; }); }


  function gameUrl(site, root) {
    var port = location.port ? ":" + location.port : "";
    return location.protocol + "//" + site.label + "." + (root || location.hostname) + port + "/";
  }

  // Entering a game is a jump to another ORIGIN, and a cookie set on
  // coachfio.com is not sent to fifa.coachfio.com unless it is scoped to the
  // parent domain. Locally it cannot be (browsers disagree about
  // `Domain=.localhost`), so the session is handed over explicitly: a 60-second,
  // single-purpose token in the URL FRAGMENT, which the game site swaps for its
  // own cookie on arrival.
  //
  // This is also what fixes signing in as a different account and finding the
  // game still on the previous one - the game origin is told who is signed in
  // NOW, instead of trusting whatever it saw last.
  //
  // Only a plain left click is intercepted, so cmd/ctrl/middle-click and "open
  // in new tab" keep working; those get the plain URL and pick the session up
  // from the shared cookie in production.
  function enterGame(e, card) {
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    if (!identity()) return;              // signed out: nothing to hand over
    e.preventDefault();
    var href = card.getAttribute("href");
    api("/api/auth/handoff", { method: "POST" })
      .then(function (r) {
        location.href = r && r.token ? href + "#h=" + encodeURIComponent(r.token) : href;
      })
      .catch(function () { location.href = href; });
  }

  function icon(name) { return '<span class="material-symbols-outlined">' + name + "</span>"; }

  // Genre and blurb are presentation, and the adapter does not declare them
  // yet, so they are keyed off the game id here. This is exactly what the
  // adapter UI manifest is for; until that lands it is one lookup in one file
  // rather than a branch scattered through the app. Everything that RENDERS
  // (cards, hero, feed) works off core entities and reads this table only for
  // decoration - an unknown game id still renders, on the gradient fallback.
  // `art` is key art dropped into frontend/art/. `focus` is the object-position
  // used to crop it: the source images are 16:9 and the card is nearly square,
  // so the default centre crop would cut the subject. When the file is absent
  // the card falls back to its gradient rather than showing a broken image.
  var GAME_META = {
    "ea-fc": {
      genre: "Sports", blurb: "Tactical analysis, decision-making and player positioning.",
      // fc27-card.jpg is a pre-cropped cut of the key art: the full 16:9 sheet
      // has the "STANDARD EDITION / FC27" lockup mid-frame, so any near-square
      // object-fit crop slices the lettering (and the edition text contradicts
      // the card's own title). Cropping the file once beats fighting
      // object-position per breakpoint.
      art: "/art/fc27-card.jpg", focus: "50% 30%", fallback: "",
    },
    cs2: {
      genre: "FPS", blurb: "Crosshair placement, utility usage and round economy.",
      art: "/art/cs2.png", focus: "38% 50%", fallback: " hub-gcard__art--b",
    },
  };
  function gameMeta(gameId) { return GAME_META[gameId] || { genre: "Game", blurb: "", fallback: " hub-gcard__art--b" }; }

  function renderGames(host, d, opts) {
    opts = opts || {};
    var sites = d.sites || [];
    if (!sites.length) { host.innerHTML = ""; return; }
    var META = GAME_META;
    // No hero, no featured slot: every game gets the same card, same size,
    // same treatment. The hub is game-agnostic on purpose (core rule: FC 26
    // is a plugin, not the product) - picking one as "featured" would just
    // be FC 26 every time, since it is first in the site config.
    var cards = sites.map(function (s) {
      var m = META[s.game_id] || { genre: "Game", blurb: "", fallback: "" };
      return '<a class="hub-gcard" href="' + gameUrl(s, d.root) + '"'
        + ' data-search="' + esc((s.display_name + " " + m.genre).toLowerCase()) + '">'
        + '<span class="hub-gcard__art' + (m.fallback || "") + '">'
        + (m.art
          ? '<img class="hub-gcard__img" src="' + esc(m.art) + '" alt="" loading="lazy"'
            + ' style="object-position:' + esc(m.focus || "center") + '"'
            + ' onerror="this.remove()">'
          : "")
        + "</span>"
        + '<span class="hub-gcard__scrim' + (m.art ? " hub-gcard__scrim--art" : "") + '"></span>'
        + '<span class="hub-gcard__in">'
        + '<span class="hub-gcard__tags">'
        + '<span class="hub-gtag">' + esc(m.genre) + "</span>"
        + '<span class="hub-gtag hub-gtag--active">Active</span></span>'
        + '<h2 class="hub-gcard__name">' + esc(s.display_name) + "</h2>"
        + '<p class="hub-gcard__desc">' + esc(m.blurb) + "</p></span></a>";
    });
    if (opts.soon) {
      cards.push('<div class="hub-gsoon" data-search="">'
        + '<span class="hub-gsoon__ring">' + icon("sports_esports") + "</span>"
        + '<h3 class="hub-h3" style="color:var(--h-ink)">More games coming soon</h3>'
        + '<p class="hub-small" style="margin-top:4px;max-width:32ch">Each new title is a plugin on '
        + "the same engine, so support arrives without rebuilding the coach.</p></div>");
    }

    host.innerHTML = '<div class="hub-games" data-hub-games-grid>' + cards.join("") + "</div>";

    // Hand the CURRENT session to the game origin on the way in.
    Array.prototype.forEach.call(host.querySelectorAll(".hub-gcard"), function (card) {
      card.addEventListener("click", function (e) { enterGame(e, card); });
    });

    // Search filters the grid by name/genre - real functionality over a
    // small dataset beats a decorative input that does nothing.
    var search = $("[data-hub-search]");
    if (search) {
      search.addEventListener("input", function () {
        var q = search.value.trim().toLowerCase();
        host.querySelectorAll("[data-hub-games-grid] > *").forEach(function (el) {
          var s = el.getAttribute("data-search");
          el.hidden = q.length > 0 && s !== "" && s.indexOf(q) === -1;
        });
      });
    }
  }

  // ---- pages ---------------------------------------------------------------
  function initLanding() {
    var host = $("[data-hub-games]");
    if (host) loadSites().then(function (d) { renderGames(host, d); });
  }

  // ---- dashboard helpers ---------------------------------------------------
  function relTime(iso) {
    var t = Date.parse(iso || "");
    if (!t) return "";
    var s = (Date.now() - t) / 1000;
    if (s < 90) return "just now";
    if (s < 5400) return Math.round(s / 60) + " min ago";
    if (s < 129600) return Math.round(s / 3600) + "h ago";
    return Math.round(s / 86400) + "d ago";
  }
  function firstInsight(m) {
    var list = m.insights || [];
    for (var i = 0; i < list.length; i++) {
      if (list[i] && list[i].summary) return list[i].summary;
    }
    return "";
  }
  function clipCount(m) {
    return (m.events || []).filter(function (e) { return e && e.payload && e.payload.clip; }).length;
  }
  function resultWord(m) {
    var r = ((m.outcome || {}).result || "").toLowerCase();
    return r ? r.charAt(0).toUpperCase() + r.slice(1) : "";
  }
  // The art slot shared by hero and report cards - same fallback rule as the
  // game cards: no art file, no broken image, just the gradient.
  function artHtml(meta, cls) {
    return '<span class="' + cls + (meta.art ? "" : " hub-gcard__art--b") + '">'
      + (meta.art
        ? '<img src="' + esc(meta.art) + '" alt="" loading="lazy"'
          + ' style="object-position:' + esc(meta.focus || "center") + '" onerror="this.remove()">'
        : "")
      + "</span>";
  }

  // Light/dark toggle for the hub. The saved value is applied pre-paint by an
  // inline snippet in the page; this just flips classes and keeps the icon and
  // label describing the switch's DESTINATION, not its current state.
  function wireTheme() {
    var btn = $("[data-dash-theme]");
    if (!btn) return;
    function paint() {
      var dark = document.body.classList.contains("hub--dark");
      btn.querySelector(".material-symbols-outlined").textContent = dark ? "light_mode" : "dark_mode";
      btn.setAttribute("aria-label", dark ? "Switch to light theme" : "Switch to dark theme");
    }
    btn.addEventListener("click", function () {
      var toLight = document.body.classList.contains("hub--dark");
      document.body.classList.toggle("hub--dark", !toLight);
      document.body.classList.toggle("hub--glass", toLight);
      try { localStorage.setItem("coachio.hubTheme", toLight ? "light" : "dark"); } catch (e) {}
      paint();
    });
    paint();
  }

  // Background palette picker in the profile row. The saved value is applied
  // pre-paint by the page's inline snippet; this wires the popover.
  function wireSky() {
    var btn = $("[data-dash-sky-btn]");
    var pick = $("[data-dash-skypick]");
    if (!btn || !pick) return;
    function mark() {
      var cur = document.body.getAttribute("data-sky") || "1";
      pick.querySelectorAll("[data-sky-opt]").forEach(function (o) {
        o.classList.toggle("is-on", o.getAttribute("data-sky-opt") === cur);
      });
    }
    btn.addEventListener("click", function (e) {
      e.stopPropagation();
      pick.hidden = !pick.hidden;
      btn.setAttribute("aria-expanded", String(!pick.hidden));
      if (!pick.hidden) mark();
    });
    pick.addEventListener("click", function (e) { e.stopPropagation(); });
    document.addEventListener("click", function () {
      if (!pick.hidden) { pick.hidden = true; btn.setAttribute("aria-expanded", "false"); }
    });
    pick.querySelectorAll("[data-sky-opt]").forEach(function (o) {
      o.addEventListener("click", function () {
        var v = o.getAttribute("data-sky-opt");
        document.body.setAttribute("data-sky", v);
        try { localStorage.setItem("coachio.hubSky", v); } catch (e) {}
        mark();   // stays open so palettes can be compared live
      });
    });
  }

  function initHub() {
    if (!identity()) { location.replace("/signin/"); return; }
    wireTheme();
    wireSky();

    var out = $("[data-hub-signout]");
    if (out) {
      out.addEventListener("click", function (e) {
        e.preventDefault();
        try { localStorage.removeItem(ID_KEY); } catch (err) {}
        // HttpOnly: only the server can drop the cookie, and the navigation must
        // wait or it cancels the request and the session survives.
        api("/api/auth/signout", { method: "POST" })
          .catch(function () {})
          .then(function () { location.href = "/signin/"; });
      });
    }

    // Sites and matches decide the whole main column, so they load together;
    // account and usage only decorate the rails and arrive when they arrive.
    Promise.all([
      loadSites(),
      api("/api/matches").catch(function () { return []; }),
    ]).then(function (r) { renderDashboard(r[0], r[1] || []); });

    api("/api/account").then(function (d) {
      var p = (d && d.profile) || {};
      var name = p.display_name || p.email || "";
      var box = $("[data-dash-me]");
      if (box && name) {
        box.hidden = false;
        $("[data-dash-me-av]").textContent = name.replace(/@.*/, "").slice(0, 2).toUpperCase();
        $("[data-dash-me-name]").textContent = name;
      }
      if (out) {
        out.title = name ? "Signed in as " + name + " - sign out" : "Sign out";
        out.setAttribute("aria-label", out.title);
      }
    }).catch(function () {});

    api("/api/usage").then(function (u) {
      var card = $("[data-dash-plan]");
      if (!card || !u) return;
      var left = (typeof u.remaining === "number") ? u.remaining : null;
      var limit = (typeof u.limit === "number") ? u.limit : 0;
      card.hidden = false;
      // A countdown only means something when the quota actually constrains. The
      // dev limit is 100000, and "99988 reports left" reads as a broken number
      // rather than as reassurance, so a large allowance just says Free plan.
      $("[data-dash-plan-text]").textContent =
        (left === null || limit >= 1000)
          ? "Analyse matches across all your games"
          : left + " of " + limit + " report" + (limit === 1 ? "" : "s") + " left";
    }).catch(function () {});
  }

  function renderDashboard(d, matches) {
    var sites = d.sites || [];
    var byGame = {};
    sites.forEach(function (s) { byGame[s.game_id] = s; });

    // Newest first. The hero is a game-blind rule - "your latest completed
    // report, whatever the game" - never a featured/pinned game, which with a
    // config-ordered site list would just mean game #1 every time.
    var done = matches.filter(function (m) { return m.status === "complete"; })
      .sort(function (a, b) { return (b.created_at || "").localeCompare(a.created_at || ""); });

    function reportHref(m) {
      var site = byGame[m.game_id];
      return site ? gameUrl(site, d.root) + "report/?id=" + encodeURIComponent(m.id) : null;
    }
    function gameName(m) {
      var site = byGame[m.game_id];
      return site ? site.display_name : m.game_id;
    }

    renderHero($("[data-dash-hero]"), done, reportHref, gameName);
    renderReports($("[data-dash-reports]"), done, reportHref, gameName);
    renderActivity($("[data-dash-activity]"), done, gameName);

    var host = $("[data-hub-games]");
    if (host) renderGames(host, d, { soon: true });

    // Search filters BOTH grids (reports + games) by the data-search text each
    // card carries. renderGames wires the games grid itself; this covers the
    // report cards with the same contract.
    var search = $("[data-hub-search]");
    if (search) {
      search.addEventListener("input", function () {
        var q = search.value.trim().toLowerCase();
        document.querySelectorAll("[data-dash-reports-grid] > *").forEach(function (el) {
          var s = el.getAttribute("data-search") || "";
          el.hidden = q.length > 0 && s.indexOf(q) === -1;
        });
      });
    }

    // Entering a report is entering a game origin: hand the session over the
    // same way the game cards do. Scoped to the reports grid - the hero wires
    // its own links because the carousel re-paints them on every step.
    document.querySelectorAll("[data-dash-reports-grid] [data-dash-go]").forEach(function (a) {
      a.addEventListener("click", function (e) { enterGame(e, a); });
    });

    // The reports pager scrolls the row - a page is most of the visible width.
    var grid = $("[data-dash-reports-grid]");
    document.querySelectorAll("[data-dash-page]").forEach(function (b) {
      b.addEventListener("click", function () {
        if (grid) grid.scrollBy({ left: grid.clientWidth * 0.8 * Number(b.getAttribute("data-dash-page")), behavior: "smooth" });
      });
    });
  }

  // Hero carousel over the most recent completed reports (up to 5): the arrows
  // are the reference design's, but they page through YOUR matches - a live
  // control, not chrome. Each step re-paints, so the hero wires its own links.
  function renderHero(host, done, reportHref, gameName) {
    if (!host) return;
    host.hidden = false;
    var list = done.slice(0, 5).filter(function (m) { return reportHref(m); });
    if (!list.length) {
      // Nothing analysed yet: the hero sells the first upload, pointing at the
      // game grid below - the games ARE the doors to the upload flow.
      host.className = "hub-dhero hub-dhero--empty hub-glass";
      host.innerHTML =
        '<div class="hub-dhero__plate hub-dhero__plate--empty">'
        + '<span class="hub-dhero__ring">' + icon("sports") + "</span>"
        + '<h1 class="hub-dhero__name">Your coach is ready for the first match</h1>'
        + '<p class="hub-dhero__ins">Pick a game below and upload a recording - '
        + "you get a full report with timestamps, moments and coaching points.</p>"
        + '<div class="hub-dhero__cta"><a class="hub-btn hub-btn--primary" href="#games">'
        + icon("upload") + "Choose a game</a></div></div>";
      return;
    }
    var i = 0;
    function paint() {
      var m = list[i];
      var meta = gameMeta(m.game_id);
      var score = (m.outcome || {}).score || "";
      var result = resultWord(m);
      var ins = firstInsight(m);
      var clips = clipCount(m);
      var href = reportHref(m);
      host.className = "hub-dhero";
      host.innerHTML =
        artHtml(meta, "hub-dhero__art")
        + '<span class="hub-dhero__scrim"></span>'
        + '<div class="hub-gcard__tags hub-dhero__tags">'
        + '<span class="hub-gtag">' + esc(gameName(m)) + "</span>"
        + (result ? '<span class="hub-gtag">' + esc(result) + "</span>" : "")
        + '<span class="hub-gtag hub-gtag--active">' + (i === 0 ? "Latest report" : esc(relTime(m.created_at))) + "</span></div>"
        + (list.length > 1
          ? '<button type="button" class="hub-dhero__arrow hub-dhero__arrow--l" data-step="-1" aria-label="Previous report">' + icon("chevron_left") + "</button>"
            + '<button type="button" class="hub-dhero__arrow hub-dhero__arrow--r" data-step="1" aria-label="Next report">' + icon("chevron_right") + "</button>"
          : "")
        + '<div class="hub-dhero__plate"><div class="hub-dhero__bottom">'
        + '<div><h1 class="hub-dhero__name">' + (score ? esc(score) : "Match report")
        + ' <span class="hub-dhero__when">&middot; ' + esc(relTime(m.created_at)) + "</span></h1>"
        + (ins ? '<p class="hub-dhero__ins">' + esc(ins.slice(0, 200)) + "</p>" : "")
        + "</div>"
        + '<div class="hub-dhero__cta">'
        + '<a class="hub-buy" data-dash-go href="' + esc(href) + '">'
        + '<span class="hub-buy__label">Open report</span>'
        + '<span class="hub-buy__score">' + (score ? esc(score) : "View") + "</span></a>"
        + (clips ? '<a class="hub-ico" data-dash-go href="' + esc(href) + '" title="'
          + clips + " moment" + (clips === 1 ? "" : "s") + ' clipped">' + icon("movie") + "</a>" : "")
        + "</div></div></div>";
      host.querySelectorAll("[data-step]").forEach(function (b) {
        b.addEventListener("click", function () {
          i = (i + Number(b.getAttribute("data-step")) + list.length) % list.length;
          paint();
        });
      });
      host.querySelectorAll("[data-dash-go]").forEach(function (a) {
        a.addEventListener("click", function (e) { enterGame(e, a); });
      });
    }
    paint();
  }

  function renderReports(section, done, reportHref, gameName) {
    if (!section || !done.length) return;
    section.hidden = false;
    $("[data-dash-reports-grid]", section).innerHTML = done.slice(0, 8).map(function (m, idx) {
      var meta = gameMeta(m.game_id);
      var score = (m.outcome || {}).score || "";
      var result = resultWord(m);
      var ins = firstInsight(m);
      var href = reportHref(m);
      var name = gameName(m);
      if (!href) return "";
      return '<a class="hub-dcard' + (idx === 0 ? " hub-dcard--hi" : "") + '" data-dash-go href="' + esc(href) + '"'
        + ' data-search="' + esc((name + " " + result + " " + score + " " + ins).toLowerCase()) + '">'
        + '<span class="hub-dcard__art">' + artHtml(meta, "hub-dcard__artin")
        + (score ? '<span class="hub-dcard__score">' + esc(score) + "</span>" : "")
        + "</span>"
        + '<span class="hub-dcard__in">'
        + '<span class="hub-dcard__meta"><span class="hub-gbadge">' + esc(name) + "</span>"
        + "<time>" + esc(relTime(m.created_at)) + "</time></span>"
        + "<b>" + esc(result ? result + (score ? " " + score : "") : "Match report") + "</b>"
        + (ins ? "<p>" + esc(ins.slice(0, 110)) + "</p>" : "")
        + "</span></a>";
    }).join("");
  }

  function renderActivity(card, done, gameName) {
    if (!card || !done.length) return;
    card.hidden = false;
    var feed = done.slice(0, 4).map(function (m) {
      var score = (m.outcome || {}).score;
      return '<div class="hub-rp__i">' + icon("description")
        + "<span><b>Report ready</b><span>" + esc(gameName(m))
        + (score ? " &middot; " + esc(score) : "")
        + " &middot; " + esc(relTime(m.created_at)) + "</span></span></div>";
    });
    var clips = clipCount(done[0]);
    if (clips) {
      feed.splice(1, 0, '<div class="hub-rp__i">' + icon("movie")
        + "<span><b>" + clips + " moment" + (clips === 1 ? "" : "s") + " clipped</b><span>"
        + esc(gameName(done[0])) + " &middot; " + esc(relTime(done[0].created_at)) + "</span></span></div>");
    }
    $("[data-dash-activity-feed]", card).innerHTML = feed.slice(0, 5).join("");
  }

  // The backend builds the Supabase authorize URL, so the redirect target is
  // decided server-side from the requesting origin rather than trusted from the
  // page. A provider that is not enabled in Supabase yet answers 503, and the
  // button says so instead of bouncing the user somewhere broken.
  function initOauthButtons() {
    var note = $("[data-hub-oauth-note]");
    var LABEL = { google: "Google", discord: "Discord" };
    Array.prototype.forEach.call(document.querySelectorAll("[data-hub-oauth]"), function (b) {
      b.addEventListener("click", function () {
        var name = LABEL[b.getAttribute("data-hub-oauth")] || "That provider";
        b.disabled = true;
        api("/api/auth/oauth/" + b.getAttribute("data-hub-oauth"))
          .then(function (r) { location.href = r.url; })
          .catch(function () {
            b.disabled = false;
            if (!note) return;
            note.textContent = name + " sign-in is not switched on yet. "
              + "Use your email below for now.";
            note.hidden = false;
          });
      });
    });
  }

  function initAuth() {
    var form = $("[data-hub-auth]");
    initOauthButtons();
    if (!form) return;
    if (identity()) { location.replace("/hub/"); return; }

    var mode = /signup/.test(location.search) ? "up" : "in";
    var nameField = $("[data-hub-name-field]", form);
    var submit = $("[data-hub-submit]", form);
    var errBox = $("[data-hub-error]", form);
    var subtitle = $("[data-hub-subtitle]");
    var swap = $("[data-hub-swap]");
    var swapText = $("[data-hub-swap-text]");

    function paint() {
      if (nameField) nameField.hidden = mode !== "up";
      submit.textContent = mode === "up" ? "Create account" : "Sign in";
      if (subtitle) subtitle.textContent = mode === "up" ? "Create your account" : "Sign in to your account";
      if (swapText) swapText.textContent = mode === "up" ? "Already have an account?" : "New here?";
      if (swap) swap.textContent = mode === "up" ? "Sign in" : "Create an account";
    }
    if (swap) {
      swap.addEventListener("click", function (e) {
        e.preventDefault();
        mode = mode === "up" ? "in" : "up";
        if (errBox) errBox.hidden = true;
        paint();
      });
    }
    paint();

    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var email = ($("#hub-email", form).value || "").trim();
      var name = nameField ? ($("#hub-name", form).value || "").trim() : "";
      if (errBox) errBox.hidden = true;
      if (!email) { return showError("Enter your email address."); }

      submit.disabled = true;
      // One flow for both modes. A magic link signs you in if you have an
      // account and creates one if you do not, so "sign in" and "sign up" stop
      // being different actions - and there is no password to forget, reset or
      // leak. Supabase answers identically either way, so this cannot be used to
      // find out which addresses are registered.
      api("/api/auth/magic-link", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: email, name: name }),
      }).then(function () {
        sent(email);
      }).catch(function (err) {
        submit.disabled = false;
        showError(String(err.message || err).slice(0, 200));
      });
    });

    function showError(msg) {
      if (!errBox) return;
      errBox.textContent = msg;
      errBox.hidden = false;
    }

    // Replace the form with a "check your email" state. The address is echoed
    // back because a typo is the single most common reason a link never arrives,
    // and it is the one thing the person can check themselves.
    function sent(email) {
      var card = form.parentNode;
      if (!card) return;
      card.innerHTML =
        '<div style="text-align:center">'
        + '<span class="material-symbols-outlined" style="font-size:40px;color:var(--h-violet)">'
        + "mark_email_read</span>"
        + '<h1 class="hub-h3" style="margin-top:12px">Check your email</h1>'
        + '<p class="hub-body" style="margin-top:8px">We sent a sign-in link to '
        + "<strong>" + esc(email) + "</strong>. It is good for one use.</p>"
        + '<p class="hub-small" style="margin-top:16px">Wrong address, or nothing after '
        + 'a minute? <a class="hub-textlink" href="/signin/" '
        + 'style="color:var(--h-violet-2)">Try again</a>.</p></div>';
    }
  }

  // Finish a Supabase sign-in that landed on this page.
  //
  // Supabase only redirects to URLs on its allow-list and SILENTLY falls back to
  // the project's Site URL otherwise - so a missing entry does not error, it just
  // drops you on the home page with a valid token in the fragment and nobody to
  // read it. Any hub page therefore completes the exchange rather than only
  // /auth/callback.
  //
  // The backend sets an HttpOnly session cookie; nothing is stored here.
  function adoptProviderToken() {
    var frag = new URLSearchParams((location.hash || "").replace(/^#/, ""));
    var token = frag.get("access_token");
    if (!token) return Promise.resolve();
    try { history.replaceState(null, "", location.pathname + location.search); } catch (e) {}
    return api("/api/auth/provider-session", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ access_token: token }),
    }).then(function (r) {
      if (r && r.user_id) setIdentity(r.user_id);   // in-memory only
      // Signing in should land you IN the product, not wherever Supabase
      // happened to drop the token. When the redirect allow-list is set this
      // never runs - the callback page handles it - but a fallback landing on
      // the marketing page and stopping there is not a sign-in, it just looks
      // like one failed.
      if (document.body.getAttribute("data-hub-page") !== "hub") {
        location.replace("/hub/");
        // Never resolves, so the router does not paint the page we are leaving.
        return new Promise(function () {});
      }
    }).catch(function () {});   // expired or already spent: carry on as a guest
  }

  document.addEventListener("DOMContentLoaded", function () {
    // Adopt any token in the fragment, THEN ask the server who we are, and only
    // then decide what to render - each page branches on identity() immediately.
    adoptProviderToken().then(bootSession).then(function () {
      var page = document.body.getAttribute("data-hub-page");
      if (page === "hub") initHub();
      else if (page === "auth") initAuth();
      else initLanding();
    });
  });
})();
