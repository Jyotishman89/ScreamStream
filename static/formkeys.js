/* Make the Enter key finish the current form everywhere — no need to click the
 * submit button. Standard forms already do this, but this guarantees it for
 * every field/device (including phones where Enter on a non-final field can do
 * nothing) and gives mobile keyboards a proper "Go/Search/Send" action key.
 *
 * It never double-submits: on Enter we cancel the browser's own implicit submit
 * and drive submission ourselves, running validation and firing the normal
 * submit event (so confirmation prompts still work). Textareas are left alone so
 * multi-line fields keep inserting newlines.
 */
(function () {
  // Input types where Enter should NOT submit (or that can't be typed into).
  var SKIP_TYPES = {
    button: 1, submit: 1, reset: 1, image: 1, file: 1,
    checkbox: 1, radio: 1, range: 1, color: 1, hidden: 1
  };

  function submitForm(form) {
    // Prefer the form's own submit button so its name/value is included and
    // constraint validation runs; fall back to requestSubmit()/submit().
    var btn = form.querySelector(
      'button[type="submit"], input[type="submit"], button:not([type])');
    if (typeof form.requestSubmit === "function") {
      form.requestSubmit(btn || undefined);
    } else if (btn) {
      btn.click();
    } else {
      form.submit();
    }
  }

  document.addEventListener("keydown", function (e) {
    if (e.key !== "Enter" || e.isComposing || e.keyCode === 229) return;
    // Let shortcuts like Ctrl/Cmd+Enter and Shift+Enter behave normally.
    if (e.ctrlKey || e.metaKey || e.altKey || e.shiftKey) return;
    if (e.defaultPrevented) return;

    var el = e.target;
    if (!el || el.tagName !== "INPUT") return;            // ignore textareas etc.
    var type = (el.getAttribute("type") || "text").toLowerCase();
    if (SKIP_TYPES[type]) return;

    var form = el.form;
    if (!form) return;

    e.preventDefault();   // cancel the browser's implicit submit → no duplicates
    submitForm(form);
  });

  // Give on-screen keyboards a sensible action key ("Go"/"Search") instead of a
  // plain return, so pressing it clearly finishes the step. (Enter always tries
  // to submit; if a required field is still empty the browser focuses it, which
  // naturally moves you along the form.)
  function hintFor(input) {
    var type = (input.getAttribute("type") || "text").toLowerCase();
    if (type === "search" || input.name === "q" || input.name === "tmdb_q") {
      return "search";
    }
    return "go";
  }

  var inputs = document.querySelectorAll("input");
  for (var i = 0; i < inputs.length; i++) {
    if (!inputs[i].hasAttribute("enterkeyhint")) {
      inputs[i].setAttribute("enterkeyhint", hintFor(inputs[i]));
    }
  }
})();
