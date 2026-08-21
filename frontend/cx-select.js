/* Themed dropdowns, shared by every signed-in page AND the signed-out auth
   pages (which have no app shell, so they cannot get this from app-shell.js).

   A native <select> has two problems in a themed app: nothing on it says "this
   opens" until you click, and the open list is drawn by the OS - CSS cannot
   touch it, so it always looks like a different application.

   So each select is UPGRADED, not replaced: the real element stays in the DOM
   (coach.js keeps reading `.value`, forms keep working, `change` still fires),
   and a button + listbox is drawn on top of it in the page's own material. */
(function () {
  "use strict";
  function esc(v) {
    return String(v == null ? "" : v).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }
  function icon(n) { return '<span class="material-symbols-outlined">' + n + "</span>"; }

/* ==== dropdowns ==========================================================
   A native <select> has two problems here: nothing on it says "this opens"
   until you click, and the open list is drawn by the OS - CSS cannot touch
   it, so it always looks like a different application.

   So each select is UPGRADED, not replaced: the real element stays in the
   DOM (coach.js keeps reading `.value`, forms keep working, `change` still
   fires), and a button + listbox is drawn on top of it in the page's own
   material. Anything rendered later is caught by the observer at the end. */
// Only one list is ever open. Opening a control closes whatever was open
// first - the click that opens B stops propagating, so the document-level
// dismiss never reaches A and both used to stay open.
var openList = null;

function enhanceSelect(sel) {
  if (sel.dataset.cxSel || sel.multiple || sel.size > 1) return;
  sel.dataset.cxSel = "1";
  var bare = !!sel.closest(".ffield");          // inside a filled field: no second frame
  var wrap = document.createElement("div");
  wrap.className = "cx-sel" + (bare ? " cx-sel--bare" : "");
  var btn = document.createElement("button");
  btn.type = "button";
  btn.className = "cx-sel__btn";
  btn.setAttribute("aria-haspopup", "listbox");
  btn.setAttribute("aria-expanded", "false");
  var lab = sel.getAttribute("aria-label")
    || (sel.id && (document.querySelector('label[for="' + sel.id + '"]') || {}).textContent) || "";
  if (lab) btn.setAttribute("aria-label", lab.trim());
  var panel = document.createElement("div");
  panel.className = "cx-sel__panel";
  panel.setAttribute("role", "listbox");
  panel.hidden = true;

  sel.parentNode.insertBefore(wrap, sel);
  wrap.appendChild(sel);
  wrap.appendChild(btn);
  // The panel is parked on <body>, NOT inside the field. Every glass card has
  // backdrop-filter, which starts a stacking context, so a panel rendered
  // inside one can never paint above a later card however high its z-index -
  // that is why the list appeared underneath the panels below it.
  document.body.appendChild(panel);
  sel.setAttribute("aria-hidden", "true");
  sel.tabIndex = -1;

  function options() { return [].slice.call(sel.options); }
  function paintBtn() {
    var o = sel.options[sel.selectedIndex];
    btn.innerHTML = '<span class="cx-sel__val">' + esc(o ? o.textContent : "")
      + "</span>" + icon("expand_more");
  }
  function build() {
    panel.innerHTML = options().map(function (o, i) {
      var hint = o.getAttribute("data-hint");
      return '<button type="button" role="option" class="cx-sel__opt'
        + (i === sel.selectedIndex ? " is-on" : "") + '" data-i="' + i + '"'
        + ' aria-selected="' + (i === sel.selectedIndex) + '"'
        + (o.disabled ? " disabled" : "") + ">"
        + '<span class="cx-sel__tick">' + icon("check") + "</span>"
        + '<span class="cx-sel__txt"><b>' + esc(o.textContent) + "</b>"
        + (hint ? "<span>" + esc(hint) + "</span>" : "") + "</span></button>";
    }).join("");
  }
  // Anchored to the trigger in viewport coordinates, and always downwards:
  // it grows to the room available instead of flipping over the control.
  function place() {
    var r = btn.getBoundingClientRect();
    var room = innerHeight - r.bottom - 16;
    panel.style.left = r.left + "px";
    panel.style.width = r.width + "px";
    panel.style.top = (r.bottom + 8) + "px";
    panel.style.maxHeight = Math.max(140, Math.min(300, room)) + "px";
  }
  function open() {
    if (openList && openList !== close) openList(false);
    build();
    panel.hidden = false;
    place();
    btn.setAttribute("aria-expanded", "true");
    openList = close;
    addEventListener("scroll", place, true);
    addEventListener("resize", place);
    var on = panel.querySelector(".is-on") || panel.firstElementChild;
    if (on) on.focus({ preventScroll: true });
  }
  function close(focus) {
    panel.hidden = true;
    btn.setAttribute("aria-expanded", "false");
    removeEventListener("scroll", place, true);
    removeEventListener("resize", place);
    if (openList === close) openList = null;
    if (focus) btn.focus();
  }
  function pick(i) {
    if (sel.selectedIndex !== i) {
      sel.selectedIndex = i;
      sel.dispatchEvent(new Event("input", { bubbles: true }));
      sel.dispatchEvent(new Event("change", { bubbles: true }));
    }
    paintBtn();
    close(true);
  }

  btn.addEventListener("click", function (e) {
    e.stopPropagation();
    panel.hidden ? open() : close(false);
  });
  panel.addEventListener("click", function (e) {
    var o = e.target.closest("[data-i]");
    if (o && !o.disabled) { e.stopPropagation(); pick(Number(o.getAttribute("data-i"))); }
  });
  [wrap, panel].forEach(function (host) { host.addEventListener("keydown", function (e) {
    var opts = [].slice.call(panel.querySelectorAll("[data-i]:not([disabled])"));
    var here = opts.indexOf(document.activeElement);
    if (e.key === "Escape" && !panel.hidden) { e.preventDefault(); close(true); }
    else if ((e.key === "ArrowDown" || e.key === "ArrowUp") && panel.hidden) { e.preventDefault(); open(); }
    else if (e.key === "ArrowDown" && here > -1) { e.preventDefault(); (opts[here + 1] || opts[0]).focus(); }
    else if (e.key === "ArrowUp" && here > -1) { e.preventDefault(); (opts[here - 1] || opts[opts.length - 1]).focus(); }
    else if (e.key === "Home" && here > -1) { e.preventDefault(); opts[0].focus(); }
    else if (e.key === "End" && here > -1) { e.preventDefault(); opts[opts.length - 1].focus(); }
  }); });
  document.addEventListener("click", function () { if (!panel.hidden) close(false); });
  // Something else changed the value (coach.js prefilling the form, a reset).
  sel.addEventListener("change", paintBtn);
  paintBtn();
}

function enhanceAll(root) {
  (root || document).querySelectorAll("select").forEach(enhanceSelect);
}
enhanceAll();
new MutationObserver(function (muts) {
  muts.forEach(function (m) {
    [].slice.call(m.addedNodes).forEach(function (n) {
      if (n.nodeType !== 1) return;
      if (n.tagName === "SELECT") enhanceSelect(n);
      else enhanceAll(n);
    });
  });
}).observe(document.body, { childList: true, subtree: true });
})();
