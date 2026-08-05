(function () {
  var SKIP_TYPES = {
    button: 1, submit: 1, reset: 1, image: 1, file: 1,
    checkbox: 1, radio: 1, range: 1, color: 1, hidden: 1
  };

  function submitForm(form) {
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
    if (e.ctrlKey || e.metaKey || e.altKey || e.shiftKey) return;
    if (e.defaultPrevented) return;

    var el = e.target;
    if (!el || el.tagName !== "INPUT") return;
    var type = (el.getAttribute("type") || "text").toLowerCase();
    if (SKIP_TYPES[type]) return;

    var form = el.form;
    if (!form) return;

    e.preventDefault();
    submitForm(form);
  });

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
