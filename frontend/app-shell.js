/* Glass shell for the game-site app pages (upload, report, moments, stats,
   account, coach, locker, office, analyzing).

   Each page keeps its own content and coach.js untouched; this script wraps
   that content in the SAME shell the hub and the game home use - animated
   sky, glass sidebar with the game switcher and nav, profile row with theme
   and background pickers - so moving hub -> home -> any page never changes
   the frame. Runs synchronously before coach.js, which still finds the bits
   it expects: a `.nav` to add the role item to, `[data-nav]` links to mark,
   and a (hidden) `.appbar__end` account anchor. */
(function () {
  "use strict";
  var body = document.body;
  var gameAttr = body.getAttribute("data-game-id") || "ea-fc";

  // Saved theme + background, applied before anything paints. `?theme=dark|light`
  // overrides and sticks: it makes a page shareable in a known theme (support
  // links, embeds, screenshots) without asking the reader to toggle first.
  try {
    var q = new URLSearchParams(location.search).get("theme");
    if (q === "dark" || q === "light") localStorage.setItem("coachio.hubTheme", q);
  } catch (e) {}
  try {
    if (localStorage.getItem("coachio.hubTheme") === "dark") {
      body.classList.remove("hub--glass"); body.classList.add("hub--dark");
    }
  } catch (e) {}
  try {
    var saved = localStorage.getItem("coachio.sky." + gameAttr);
    /* pre-generator values (fc1..fc5) are dropped: keys are "<game>:<slug>" now */
  if (saved && saved.indexOf(":") > 0) body.setAttribute("data-sky", saved);
  } catch (e) {}

  var main = document.getElementById("main") || document.querySelector("main");
  if (!main) return;

  function icon(n) { return '<span class="material-symbols-outlined">' + n + "</span>"; }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }
  var ART = { "ea-fc": "/art/fc27-card.jpg", cs2: "/art/cs2.png" };
  // Role item (My locker / My office) sits ABOVE Account. Painted from the
  // cached role (same key coach.js keeps) so it is there before coach.js
  // confirms it; coach.js then only swaps it if the role changed.
  var role = "player";
  try { role = localStorage.getItem("coachio.role") === "coach" ? "coach" : "player"; } catch (e) {}
  var ROLE = role === "coach"
    ? ["/office/", "business_center", "My office"]
    : ["/locker/", "checklist", "My locker"];
  var NAV = [
    ["/home/", "home", "Home"], ["/upload/", "upload", "Upload"],
    ["/report/", "description", "Reports"], ["/moment/", "movie", "Moments"],
    ["/statistics/", "trending_up", "Statistics"], ROLE, ["/account/", "settings", "Account"],
  ];


  // Sky behind everything.
  var PATH = location.pathname.replace(/index\.html$/, "");
  if (PATH.length > 1 && PATH.slice(-1) !== "/") PATH += "/";

  var sky = document.createElement("div");
  sky.className = "hub-sky"; sky.setAttribute("aria-hidden", "true");
  sky.innerHTML = "<i></i><i></i><i></i><i></i>";
  body.insertBefore(sky, body.firstChild);

  // Shell: sidebar + content column; the page's <main> moves into the column.
  var shell = document.createElement("div");
  shell.className = "hub-shell";
  shell.innerHTML =
    '<aside class="hub-side hub-glass">'
    + '<a class="hub-logo" href="/home/"><span class="hub-logo__mark">' + icon("insights") + "</span>Coachfio</a>"
    + '<a class="hub-game" href="/hub/" data-shell-hub title="Back to all games">'
    + '<img src="' + (ART[gameAttr] || "") + '" alt="" data-shell-art>'
    + '<span class="hub-game__t"><b data-shell-game>Your game</b><span>Change game</span></span>'
    + icon("swap_horiz") + "</a>"
    + '<nav class="hub-side__nav nav" aria-label="Primary">'
    + NAV.map(function (n) {
        var on = n[0] === PATH;
        return '<a class="hub-side__link' + (on ? " is-active" : "") + '" href="' + n[0] + '" data-nav="' + n[0] + '"'
          + (on ? ' aria-current="page"' : "") + ">" + icon(n[1]) + n[2] + "</a>";
      }).join("")
    + "</nav>"
    + '<div class="hub-side__foot">'
    + '<div class="hub-skypick" data-shell-skypick hidden></div>' 
    + '<div class="hub-side__me-row">'
    + '<div class="hub-me" data-shell-me hidden><span class="hub-me__av" data-shell-av></span>'
    + '<span class="hub-me__id"><b data-shell-name></b></span></div>'
    + '<button type="button" class="hub-theme" data-shell-sky-btn aria-expanded="false" aria-label="Choose background style" title="Background">' + icon("palette") + "</button>"
    + '<button type="button" class="hub-theme" data-shell-theme aria-label="Switch theme" title="Switch theme">' + icon("light_mode") + "</button>"
    + "</div>"
    + '<a class="hub-side__link" href="/signin/" data-shell-signout>' + icon("logout") + "Sign out</a>"
    + "</div></aside>"
    + '<div class="hub-content"></div>'
    // coach.js looks here for the account link; the bar itself is gone.
    + '<div class="appbar__end" hidden><a href="/account/"></a></div>';
  main.parentNode.insertBefore(shell, main);
  shell.querySelector(".hub-content").appendChild(main);


  // coach.js appends a role item when it swaps roles; keep Account last.
  var navEl = shell.querySelector(".hub-side__nav");
  new MutationObserver(function () {
    var acc = navEl.querySelector('a[href="/account/"]');
    if (acc && acc !== navEl.lastElementChild) navEl.appendChild(acc);
  }).observe(navEl, { childList: true });

  function $(sel) { return shell.querySelector(sel); }

  // Theme toggle (shared key with the hub and the game home).
  var tbtn = $("[data-shell-theme]");
  function paintTheme() {
    var dark = body.classList.contains("hub--dark");
    tbtn.querySelector(".material-symbols-outlined").textContent = dark ? "light_mode" : "dark_mode";
    tbtn.setAttribute("aria-label", dark ? "Switch to light theme" : "Switch to dark theme");
  }
  tbtn.addEventListener("click", function () {
    var toLight = body.classList.contains("hub--dark");
    body.classList.toggle("hub--dark", !toLight);
    body.classList.toggle("hub--glass", toLight);
    try { localStorage.setItem("coachio.hubTheme", toLight ? "light" : "dark"); } catch (e) {}
    paintTheme();
  });
  paintTheme();

  // Background picker (saved per game).
  var sbtn = $("[data-shell-sky-btn]"), pick = $("[data-shell-skypick]");
  function mark() {
    var cur = body.getAttribute("data-sky") || "fc1";
    pick.querySelectorAll("[data-sky-opt]").forEach(function (o) {
      o.classList.toggle("is-on", o.getAttribute("data-sky-opt") === cur);
    });
  }
  sbtn.addEventListener("click", function (e) {
    e.stopPropagation();
    pick.hidden = !pick.hidden;
    sbtn.setAttribute("aria-expanded", String(!pick.hidden));
    if (!pick.hidden) mark();
  });
  pick.addEventListener("click", function (e) { e.stopPropagation(); });
  document.addEventListener("click", function () {
    if (!pick.hidden) { pick.hidden = true; sbtn.setAttribute("aria-expanded", "false"); }
  });
  function wireOpts() {
    pick.querySelectorAll("[data-sky-opt]").forEach(function (o) {
      o.addEventListener("click", function () {
        var v = o.getAttribute("data-sky-opt");
        body.setAttribute("data-sky", v);
        try { localStorage.setItem("coachio.sky." + (body.getAttribute("data-game-id") || gameAttr), v); } catch (e) {}
        mark();
      });
    });
  }

  // Palettes are generated from each game's key art (tools/extract_palette.py)
  // and served as a manifest, so the picker never hard-codes a game's colours.
  fetch("/palettes.json").then(function (r) { return r.ok ? r.json() : null; }).then(function (m) {
    var list = (m || {})[body.getAttribute("data-game-id") || gameAttr] || [];
    if (!list.length) { pick.hidden = true; sbtn.hidden = true; return; }
    pick.innerHTML = "<p>Background</p>" + list.map(function (o) {
      return '<button type="button" data-sky-opt="' + esc(o.key) + '">'
        + '<i style="background:linear-gradient(135deg,' + esc(o.swatch.join(",")) + ')"></i>'
        + esc(o.name) + "</button>";
    }).join("");
    if (!body.getAttribute("data-sky")) body.setAttribute("data-sky", list[0].key);
    wireOpts(); mark();
  }).catch(function () {});

  // Sign out: the cookie is HttpOnly, so the server drops it; navigate after.
  $("[data-shell-signout]").addEventListener("click", function (e) {
    e.preventDefault();
    fetch("/api/auth/signout", { method: "POST" }).catch(function () {})
      .then(function () { location.href = "/signin/"; });
  });

  // Which game this host serves, and the way back to the hub.
  fetch("/api/site").then(function (r) { return r.ok ? r.json() : null; }).then(function (d) {
    if (!d) return;
    if (d.game && d.game.game_id) {
      body.setAttribute("data-game-id", d.game.game_id);
      $("[data-shell-game]").textContent = d.game.display_name || d.game.game_id;
      var art = ART[d.game.game_id];
      var img = $("[data-shell-art]");
      if (art && img) img.src = art;
    }
    var port = location.port ? ":" + location.port : "";
    $("[data-shell-hub]").href = location.protocol + "//" + (d.root || location.hostname) + port + "/hub/";
  }).catch(function () {});

  // Who is signed in (profile chip).
  fetch("/api/account").then(function (r) { return r.ok ? r.json() : null; }).then(function (d) {
    var p = (d && d.profile) || {};
    var name = p.display_name || p.email || "";
    if (!name) return;
    $("[data-shell-me]").hidden = false;
    var av = $("[data-shell-av]");
    if (p.avatar_url) {
      av.classList.add("hub-me__av--img");
      av.innerHTML = '<img src="' + esc(p.avatar_url) + '" alt="">';
    } else {
      av.classList.remove("hub-me__av--img");
      av.textContent = name.replace(/@.*/, "").slice(0, 2).toUpperCase();
    }
    $("[data-shell-name]").textContent = name;
  }).catch(function () {});

  /* ==== playback: stop paying for glass while a video runs ================
     The material costs GPU on every composited frame: four animated 46vw
     blurred fields behind the page, plus a backdrop-filter on every panel.
     Idle, that is nearly free; with video playing the compositor redoes all of
     it per decoded frame - which is why playback is choppy in this design and
     was not in the flat one. So while something plays the page goes calm (the
     field freezes, panels turn solid) and it all returns on pause.
     Media events do not bubble, hence the capture-phase listeners. */
  var playing = 0;
  function setPlaying(delta) {
    playing = Math.max(0, playing + delta);
    body.classList.toggle("is-playing", playing > 0);
  }
  document.addEventListener("play", function () { setPlaying(1); }, true);
  document.addEventListener("pause", function () { setPlaying(-1); }, true);
  document.addEventListener("ended", function () { setPlaying(-1); }, true);
  addEventListener("pagehide", function () { playing = 0; body.classList.remove("is-playing"); });
})();
