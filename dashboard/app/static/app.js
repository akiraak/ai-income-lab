// 部分更新: data-poll="<url>" を持つ要素を data-interval ミリ秒ごとに取り直して差し替える。
// 認証は cookie（Access）か接続元で決まるので、fetch に特別なヘッダは要らない。
(function () {
  document.querySelectorAll("[data-poll]").forEach(function (el) {
    var url = el.getAttribute("data-poll");
    var interval = parseInt(el.getAttribute("data-interval") || "5000", 10);
    var stopWhen = el.getAttribute("data-stop-when");
    var timer = null;
    function tick() {
      if (document.hidden) { return; }
      if (document.activeElement && el.contains(document.activeElement) && document.activeElement.tagName === "INPUT") { return; }
      fetch(url, { credentials: "same-origin", cache: "no-store" })
        .then(function (r) { if (!r.ok) { throw new Error(r.status); } return r.text(); })
        .then(function (html) {
          el.innerHTML = html;
          if (stopWhen && el.querySelector(stopWhen)) { clearInterval(timer); }
        })
        .catch(function () { /* 次の周期でまた試す */ });
    }
    timer = setInterval(tick, interval);
  });
})();

// 確認ダイアログ: data-confirm="<文面>" を持つ form は、送る前に confirm() で確かめる。断ったら送らない。
// ⚠ templates に onsubmit="…" を書かない（CSP の script-src 'self' に止められ、確かめずに送られる。2026-09-18）。
// ⚠ form ごとではなく document で捕まえる（部分更新で差し替わった要素の中の form にも効く）。
(function () {
  document.addEventListener("submit", function (ev) {
    var form = ev.target;
    if (!form || !form.getAttribute) { return; }
    var text = form.getAttribute("data-confirm");
    if (text && !window.confirm(text)) { ev.preventDefault(); }
  });
})();
