// Опрос состояния карты: обновляет текст и полосу прогресса, а когда обработчик закончил
// (задание больше не «в работе») - перезагружает страницу. Если задание долго никто не
// берёт, показывает подсказку «обработчик не запущен». Без внешних библиотек.
(function () {
  "use strict";
  var el = document.getElementById("poller");
  if (!el || !window.fetch) { return; }
  var url = el.getAttribute("data-url");
  var textEl = document.getElementById("status-text");
  var barEl = document.getElementById("progress-bar");
  var hintEl = document.getElementById("stall-hint");
  var delay = 2500;

  function schedule() { window.setTimeout(tick, delay); }

  function tick() {
    fetch(url, { credentials: "same-origin", cache: "no-store" })
      .then(function (r) {
        if (r.status === 403 || r.status === 404) { window.location.reload(); return null; }
        return r.ok ? r.json() : null;
      })
      .then(function (data) {
        if (!data) { schedule(); return; }
        if (textEl) { textEl.textContent = data.status; }
        // до первого готового листа полоса остаётся бегущей (без значения)
        if (barEl && data.progress_total && data.progress_done) {
          barEl.max = data.progress_total;
          barEl.value = data.progress_done;
        }
        if (hintEl) { hintEl.hidden = !data.stalled; }
        if (!data.in_progress) { window.location.reload(); return; }
        schedule();
      })
      .catch(schedule);
  }

  schedule();
})();
