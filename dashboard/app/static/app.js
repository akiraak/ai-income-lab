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
