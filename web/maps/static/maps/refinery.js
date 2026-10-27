// Блок «Обработка данных» (ТЗ, 4.5): в паре несовместимых флажков выставленный снимает
// другой; флажок, которому нужен «вкл», без него недоступен. Это удобство формы: сами
// правила проверяет сервер.
(function () {
  "use strict";
  var block = document.getElementById("refinery-params");
  if (!block) { return; }

  function box(name) { return block.querySelector('input[type="checkbox"][name="' + name + '"]'); }
  function on(name) { var b = box(name); return !!b && b.checked; }
  function allow(name, allowed) {
    var b = box(name);
    if (!b) { return; }
    if (!allowed) { b.checked = false; }
    b.disabled = !allowed;
    var label = b.closest("label");
    if (label) { label.classList.toggle("disabled", !allowed); }
  }

  // пары, где выставить можно только один флажок: второй снимается сам
  var PAIRS = [
    ["utzd", "utzdc"],
    ["grlu", "grluc"], ["grld", "grldc"],
    ["grru", "grruc"], ["grrd", "grrdc"]
  ];

  function switchPair(name) {
    if (!on(name)) { return; }
    PAIRS.forEach(function (pair) {
      var i = pair.indexOf(name);
      if (i !== -1) { var other = box(pair[1 - i]); if (other) { other.checked = false; } }
    });
  }

  function update() {
    allow("utzdc", on("utz"));
    allow("grldc", on("grlu") || on("grluc"));
    allow("grrdc", on("grru") || on("grruc"));
  }

  block.addEventListener("change", function (event) {
    if (event.target && event.target.name) { switchPair(event.target.name); }
    update();
  });
  update();
})();
