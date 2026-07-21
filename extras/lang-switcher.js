// Language switcher: populates a <select> dropdown in the header bar.
// Navigates to the equivalent page in the target language, correctly
// handling GitHub Pages sub-path deployments via window.SITE_ROOT.
//
// window.LANG_CONFIG  = { zh: {label, prefix, default?}, ... }
// window.SITE_ROOT     = "https://phaethix.github.io/ai-agent-book"

(function () {
  "use strict";

  var cfg = window.LANG_CONFIG;
  if (!cfg) return;

  // ── helpers ───────────────────────────────────────────────

  /** Detect active language (longest prefix wins). */
  function detectLang(path) {
    var p = path.replace(/\/$/, "");
    var codes = Object.keys(cfg).sort(function (a, b) {
      return cfg[b].prefix.length - cfg[a].prefix.length;
    });
    for (var i = 0; i < codes.length; i++) {
      if (p.indexOf(cfg[codes[i]].prefix) !== -1) return codes[i];
    }
    for (var c in cfg) {
      if (cfg.hasOwnProperty(c) && cfg[c].default) return c;
    }
    return "zh";
  }

  /** Map clean path (no site base) → target edition clean path (no leading /). */
  function mapUrl(cleanPath, targetCode, currentLang) {
    if (targetCode === currentLang) return null;
    var src = cfg[currentLang];
    var dst = cfg[targetCode];

    // Homepage.
    if (cleanPath === "/" || cleanPath === "/index.html") {
      return dst.prefix + "introduction" + (dst.suffix || "") + "/";
    }

    var pp = cleanPath.replace(/^\//, "");
    var url = pp.replace(src.prefix, dst.prefix);

    if (src.suffix) url = url.split(src.suffix + "/").join("/");
    if (dst.suffix) url = url.replace(/\/$/, dst.suffix + "/");

    return url;
  }

  /** Strip the site's sub-path prefix (e.g. /ai-agent-book/) from a pathname. */
  function siteBasePath() {
    try { return new URL(window.SITE_ROOT).pathname; } catch (_) {}
    // Fallback: compute from current page.
    var p = location.pathname;
    // Heuristic: site root is everything up to the first book/ segment.
    var idx = Math.max(p.indexOf("book-en/"), p.indexOf("book-ta/"), p.indexOf("book-vi/"), p.indexOf("book-zhtw/"), p.indexOf("book/"));
    if (idx === -1) return "/";
    return p.slice(0, idx);
  }

  // ── sidebar rewriting ─────────────────────────────────────

  function rewriteSidebar(targetCode) {
    var target = cfg[targetCode];
    var defCode = null;
    for (var c in cfg) { if (cfg[c].default) { defCode = c; break; } }
    defCode = defCode || "zh";
    var defCfg = cfg[defCode];

    var links = document.querySelectorAll(".md-nav__link");
    for (var i = 0; i < links.length; i++) {
      var el = links[i];
      var href = el.getAttribute("href");
      if (!href || href.indexOf("http") === 0 || href.charAt(0) === "#") continue;
      href = href.replace(/^\//, "");

      var defPrefix = (defCfg.prefix || "").replace(/\/$/, "");
      var tgtPrefix = (target.prefix || "").replace(/\/$/, "");

      if (defPrefix && href.indexOf(defPrefix) === 0) {
        href = tgtPrefix + href.slice(defPrefix.length);
      }
      var defSuf = defCfg.suffix || "";
      var tgtSuf = target.suffix || "";
      if (defSuf) href = href.replace(defSuf + ".html", ".html");
      if (tgtSuf && href.indexOf(".html") !== -1) {
        href = href.replace(/\.html$/, tgtSuf + ".html");
      }
      el.setAttribute("href", "/" + href);
    }
  }

  // ── render ────────────────────────────────────────────────

  function render() {
    var rawPath = location.pathname;
    var basePath = siteBasePath();   // e.g. "/ai-agent-book/"
    var cleanPath = "/" + rawPath.slice(basePath.length).replace(/^\//, "");
    var activeLang = detectLang(cleanPath);
    var siteRoot = window.SITE_ROOT.replace(/\/$/, "") + "/";

    var sel = document.getElementById("lang-selector");
    if (!sel) return;
    if (sel.children.length > 0) return;

    var codes = Object.keys(cfg);
    for (var idx = 0; idx < codes.length; idx++) {
      var code = codes[idx];
      var opt = document.createElement("option");
      opt.value = code;
      opt.textContent = cfg[code].label;
      if (code === activeLang) { opt.selected = true; opt.disabled = true; }
      sel.appendChild(opt);
    }

    sel.addEventListener("change", function () {
      var target = sel.value;
      if (!target || target === activeLang) return;
      var rel = mapUrl(cleanPath, target, activeLang);
      if (rel) location.href = siteRoot + rel;
    });

    // Rewrite sidebar for non-default editions.
    var defCode = null;
    for (var c in cfg) { if (cfg[c].default) { defCode = c; break; } }
    if (activeLang !== (defCode || "zh")) {
      rewriteSidebar(activeLang);
    }
  }

  // ── bootstrap ──────────────────────────────────────────────

  function boot() {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", render);
    } else {
      render();
    }
    document.addEventListener("locationchange", render);
    var _pushState = history.pushState;
    history.pushState = function () {
      _pushState.apply(this, arguments);
      setTimeout(render, 60);
    };
  }

  boot();
})();
