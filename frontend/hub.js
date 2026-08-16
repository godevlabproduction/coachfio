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

  var ID_KEY = "coachio.user";
  var $ = function (sel, root) { return (root || document).querySelector(sel); };

  function identity() {
    try { return localStorage.getItem(ID_KEY) || ""; } catch (e) { return ""; }
  }
  function setIdentity(v) { try { localStorage.setItem(ID_KEY, v); } catch (e) {} }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }
  function api(path, opts) {
    opts = opts || {};
    opts.headers = opts.headers || {};
    if (identity()) opts.headers["X-User-Id"] = identity();
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

  function icon(name) { return '<span class="material-symbols-outlined">' + name + "</span>"; }

  function renderGames(host, d, opts) {
    opts = opts || {};
    var sites = d.sites || [];
    if (!sites.length) { host.innerHTML = ""; return; }
    // Genre and blurb are presentation, and the adapter does not declare them
    // yet, so they are keyed off the game id here. This is exactly what the
    // adapter UI manifest is for; until that lands it is one lookup in one file
    // rather than a branch scattered through the app.
    // `art` is key art dropped into frontend/art/. `focus` is the object-position
    // used to crop it: the source images are 16:9 and the card is nearly square,
    // so the default centre crop would cut the subject. When the file is absent
    // the card falls back to its gradient rather than showing a broken image.
    var META = {
      "ea-fc": {
        genre: "Sports", blurb: "Tactical analysis, decision-making and player positioning.",
        art: "/art/fc27.jpg", focus: "62% 42%", fallback: "",
      },
      cs2: {
        genre: "FPS", blurb: "Crosshair placement, utility usage and round economy.",
        art: "/art/cs2.png", focus: "38% 50%", fallback: " hub-gcard__art--b",
      },
    };
    var cards = sites.map(function (s) {
      var m = META[s.game_id] || { genre: "Game", blurb: "", fallback: "" };
      return '<a class="hub-gcard" href="' + gameUrl(s, d.root) + '">'
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
      cards.push('<div class="hub-gsoon">'
        + '<span class="hub-gsoon__ring">' + icon("sports_esports") + "</span>"
        + '<h3 class="hub-h3" style="color:var(--h-ink)">More games coming soon</h3>'
        + '<p class="hub-small" style="margin-top:4px;max-width:32ch">Each new title is a plugin on '
        + "the same engine, so support arrives without rebuilding the coach.</p></div>");
    }
    host.innerHTML = '<div class="hub-games">' + cards.join("") + "</div>";
  }

  // ---- pages ---------------------------------------------------------------
  function initLanding() {
    var host = $("[data-hub-games]");
    if (host) loadSites().then(function (d) { renderGames(host, d); });
  }

  function initHub() {
    var host = $("[data-hub-games]");
    if (!identity()) { location.replace("/signin/"); return; }
    loadSites().then(function (d) { if (host) renderGames(host, d, { soon: true }); });
    api("/api/account").then(function (d) {
      var p = (d && d.profile) || {};
      var icon = $("[data-hub-signout]");
      var name = p.display_name || p.email || "";
      if (icon) {
        icon.title = name ? "Signed in as " + name + " - sign out" : "Sign out";
        icon.setAttribute("aria-label", icon.title);
      }
    }).catch(function () {});
    var out = $("[data-hub-signout]");
    if (out) {
      out.addEventListener("click", function (e) {
        e.preventDefault();
        try { localStorage.removeItem(ID_KEY); } catch (err) {}
        location.href = "/signin/";
      });
    }
    api("/api/usage").then(function (u) {
      var chip = $("[data-hub-usage]");
      if (!chip || !u) return;
      // A countdown only means something when the quota actually constrains. The
      // dev limit is 100000, and "99988 reports left" reads as a broken number
      // rather than as reassurance, so a large allowance just says Free plan.
      var left = (typeof u.remaining === "number") ? u.remaining : null;
      var limit = (typeof u.limit === "number") ? u.limit : 0;
      chip.hidden = false;
      chip.querySelector("[data-hub-usage-text]").textContent =
        (left === null || limit >= 1000)
          ? "Free plan"
          : left + " report" + (left === 1 ? "" : "s") + " left";
    }).catch(function () {});
  }

  function initAuth() {
    var form = $("[data-hub-auth]");
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
      var body = mode === "up"
        ? { email: email, display_name: name, role: "player" }
        : { email: email };
      api("/api/auth/" + (mode === "up" ? "signup" : "signin"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      }).then(function (r) {
        setIdentity(r.user_id);
        location.href = "/hub/";
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
  }

  document.addEventListener("DOMContentLoaded", function () {
    var page = document.body.getAttribute("data-hub-page");
    if (page === "hub") initHub();
    else if (page === "auth") initAuth();
    else initLanding();
  });
})();
