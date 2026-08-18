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
        // HttpOnly: only the server can drop the cookie, and the navigation must
        // wait or it cancels the request and the session survives.
        api("/api/auth/signout", { method: "POST" })
          .catch(function () {})
          .then(function () { location.href = "/signin/"; });
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
