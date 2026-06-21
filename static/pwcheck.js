(function () {
  var pw = document.querySelector('input[name="password"]');
  var meter = document.getElementById("pw-meter");
  var bar = document.getElementById("pw-bar");
  var label = document.getElementById("pw-label");
  if (!pw || !meter || !bar || !label) return;

  var COMMON = [
    "password", "12345678", "123456789", "1234567890", "qwerty", "qwertyuiop",
    "password1", "password123", "11111111", "00000000", "letmein", "iloveyou",
    "admin123", "welcome1", "abc12345", "qwerty123", "1q2w3e4r", "zaq12wsx",
    "football", "baseball", "sunshine", "princess", "dragon123", "monkey12",
    "superman", "trustno1", "passw0rd", "starwars", "whatever", "changeme"
  ];

  function distinct(v) {
    var seen = {};
    for (var i = 0; i < v.length; i++) seen[v[i]] = 1;
    return Object.keys(seen).length;
  }

  function score(v) {
    if (!v) return -1;
    if (v.length < 8) return 0;
    if (COMMON.indexOf(v.toLowerCase()) !== -1) return 0;
    if (distinct(v) <= 2) return 0;
    var s = 1;
    if (v.length >= 12) s++;
    var classes = 0;
    if (/[a-z]/.test(v)) classes++;
    if (/[A-Z]/.test(v)) classes++;
    if (/\d/.test(v)) classes++;
    if (/[^A-Za-z0-9]/.test(v)) classes++;
    if (classes >= 2) s++;
    if (classes >= 3 && v.length >= 10) s++;
    return Math.min(s, 4);
  }

  var LEVELS = [
    { cls: "pw-weak",   text: "Too weak — use at least 8 varied characters." },
    { cls: "pw-weak",   text: "Weak — try 12+ characters with a mix of types." },
    { cls: "pw-fair",   text: "Fair — add another character type to strengthen it." },
    { cls: "pw-good",   text: "Good password." },
    { cls: "pw-strong", text: "Strong password." }
  ];

  function update() {
    var sc = score(pw.value);
    if (sc < 0) { meter.classList.remove("show"); return; }
    meter.classList.add("show");
    var lv = LEVELS[sc];
    bar.className = "pw-bar " + lv.cls;
    label.textContent = lv.text;
  }

  pw.addEventListener("input", update);
})();
