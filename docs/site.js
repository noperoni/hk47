/* Two behaviours, no dependencies.
   1. Copy-to-clipboard on the command rows.
   2. The voice comparison toggle.
   Both degrade to something readable with JavaScript off: the commands are
   selectable text, and the transcript is rendered in the HK-47 voice on load. */

(function () {
  "use strict";

  // ── 1. copy buttons ──────────────────────────────────────
  document.querySelectorAll(".cmd[data-copy]").forEach(function (row) {
    var btn = row.querySelector(".copy");
    var code = row.querySelector("code");
    if (!btn || !code) return;

    btn.addEventListener("click", function () {
      var text = code.textContent;
      var done = function () {
        btn.textContent = "copied";
        btn.classList.add("done");
        setTimeout(function () {
          btn.textContent = "copy";
          btn.classList.remove("done");
        }, 1400);
      };

      if (navigator.clipboard && window.isSecureContext) {
        navigator.clipboard.writeText(text).then(done, fallback);
      } else {
        fallback();
      }

      // execCommand is deprecated but it is the only path on a page served
      // over plain http, which a local preview of this site is.
      function fallback() {
        var ta = document.createElement("textarea");
        ta.value = text;
        ta.setAttribute("readonly", "");
        ta.style.position = "fixed";
        ta.style.opacity = "0";
        document.body.appendChild(ta);
        ta.select();
        try { document.execCommand("copy"); done(); } catch (e) { /* nothing useful to do */ }
        document.body.removeChild(ta);
      }
    });
  });

  // ── 2. voice toggle ──────────────────────────────────────
  var transcript = document.getElementById("transcript");
  var toggles = document.querySelectorAll(".tog[data-voice]");
  if (!transcript || !toggles.length) return;

  function render(voice) {
    transcript.querySelectorAll(".say").forEach(function (p) {
      p.textContent = p.getAttribute("data-" + voice) || "";
    });
    transcript.classList.toggle("is-hk47", voice === "hk47");
    transcript.classList.toggle("is-plain", voice === "plain");
    toggles.forEach(function (t) {
      t.classList.toggle("is-on", t.getAttribute("data-voice") === voice);
    });
  }

  toggles.forEach(function (t) {
    t.addEventListener("click", function () {
      render(t.getAttribute("data-voice"));
    });
  });

  render("hk47");
})();
