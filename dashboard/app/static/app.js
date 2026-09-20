// 行動のマス目が横スクロールのとき（期間が長い ＝ .gridscroll）の見え位置。
// 既定は右端（最新の日）。部分更新で作り直されたときは、その前に見ていた位置を保つ（§15-12）。
function alignGrids(root, keep) {
  (root || document).querySelectorAll(".gridscroll").forEach(function (el) {
    el.scrollLeft = (keep === null || keep === undefined) ? el.scrollWidth : keep;
  });
}
function gridScrollLeft(root) {
  var el = (root || document).querySelector(".gridscroll");
  return el ? el.scrollLeft : null;
}
document.addEventListener("DOMContentLoaded", function () { alignGrids(null, null); });

// 部分更新: data-poll="<url>" を持つ要素を data-interval ミリ秒ごとに取り直して差し替える。
// 認証は cookie（Access）か接続元で決まるので、fetch に特別なヘッダは要らない。
(function () {
  document.querySelectorAll("[data-poll]").forEach(function (el) {
    var url = el.getAttribute("data-poll");
    var interval = parseInt(el.getAttribute("data-interval") || "5000", 10);
    function tick() {
      if (document.hidden) { return; }
      if (document.activeElement && el.contains(document.activeElement) && document.activeElement.tagName === "INPUT") { return; }
      if (el.querySelector("details.help[open]")) { return; }  // 開いているヘルプを差し替えで閉じない（§15-10）
      fetch(url, { credentials: "same-origin", cache: "no-store" })
        .then(function (r) { if (!r.ok) { throw new Error(r.status); } return r.text(); })
        .then(function (html) {
          var keep = gridScrollLeft(el);
          el.innerHTML = html;
          alignGrids(el, keep);
        })
        .catch(function () { /* 次の周期でまた試す */ });
    }
    setInterval(tick, interval);
  });
})();

// シミュレーションモードの概要・詳細（§15-11）: <main data-poll-self="ミリ秒"> は、いまの URL を取り直して main の中身だけ差し替える
// ＝ 仮の時計で流れている途中を、開いたまま眺められる。⚠ 表示だけ。帯は main の外なので差し替わらない（消えない）。
(function () {
  var main = document.querySelector("main[data-poll-self]");
  if (!main) { return; }
  setInterval(function () {
    if (document.hidden || main.querySelector("details.help[open]")) { return; }
    fetch(window.location.href, { credentials: "same-origin", cache: "no-store" })
      .then(function (r) { if (!r.ok) { throw new Error(r.status); } return r.text(); })
      .then(function (html) {
        var next = new DOMParser().parseFromString(html, "text/html").querySelector("main");
        if (next) {
          var keep = gridScrollLeft(main);
          main.innerHTML = next.innerHTML;
          alignGrids(main, keep);
        }
      })
      .catch(function () { /* 次の周期でまた試す */ });
  }, parseInt(main.getAttribute("data-poll-self"), 10) || 3000);
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

// i マークのヘルプ（§15-10）: 開く・閉じるは <details class="help"> の素の動き（スクリプト無しでも開く。Tab → Enter ／ Space）。
// ここは足し算だけ: 1 つだけ開く ／ 外を押すか Escape で閉じる ／ 右端 ／ 下端を越えたら左 ／ 上に倒す（.help-left ／ .help-up。style は書かない）。
// ⚠ document で捕まえる（部分更新で差し替わった要素にも効く）。toggle は泡立たないので capture で取る。
(function () {
  function closeAll(except) {
    document.querySelectorAll("details.help[open]").forEach(function (d) { if (d !== except) { d.removeAttribute("open"); } });
  }
  document.addEventListener("toggle", function (ev) {
    var d = ev.target;
    if (!d || !d.classList || !d.classList.contains("help") || !d.open) { return; }
    closeAll(d);
    d.classList.remove("help-left", "help-up");
    var pop = d.querySelector(".help-pop");
    if (!pop) { return; }
    var r = pop.getBoundingClientRect();
    if (r.right > document.documentElement.clientWidth - 8) { d.classList.add("help-left"); }
    if (r.bottom > document.documentElement.clientHeight - 8 && r.top - r.height > 60) { d.classList.add("help-up"); }
  }, true);
  document.addEventListener("click", function (ev) {
    if (!ev.target.closest || !ev.target.closest("details.help")) { closeAll(null); }
  });
  document.addEventListener("keydown", function (ev) {
    if (ev.key !== "Escape") { return; }
    var d = document.querySelector("details.help[open]");
    if (!d) { return; }
    d.removeAttribute("open");
    var s = d.querySelector("summary");
    if (s) { s.focus(); }
  });
})();
