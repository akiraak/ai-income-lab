"""管理画面の本体（FastAPI）。

画面: 概要（/）・全体の詳細（/overall）・トレーダーの詳細（/traders/<name>）・記録（/records）・判定（/judge）・操作（/ops、ローカル面。停止と解除だけ）。
⚠ 検証・データ・手動の注文・開発の画面は 2026-09-18 に外した（検証とデータは vibeboard のタブ。部品 `experiments.py`・`inventory.py`・`devtools.py` は残る）。
公開面（AIL_AUTH_MODE=cloudflare）では /ops は 404 を返し、POST は停止（/ops/halt）だけ受ける。
すべての応答は Redactor を通す（秘密をブラウザに送らない）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .config import Settings, import_sample, load_settings

log = logging.getLogger("ail")
HERE = Path(__file__).resolve().parent
PT = ZoneInfo("America/Los_Angeles")
ET = ZoneInfo("America/New_York")
CSRF_COOKIE = "ail_csrf"


# ---------------------------------------------------------------- フィルタ


def _parse(iso) -> datetime | None:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def fmt_tz(iso, tz, label: str, fmt: str = "%m-%d %H:%M:%S") -> str:
    dt = _parse(iso)
    return f"{dt.astimezone(tz).strftime(fmt)} {label}" if dt else "—"


def fmt_pt(iso) -> str:
    return fmt_tz(iso, PT, "PT")


def fmt_et(iso) -> str:
    return fmt_tz(iso, ET, "ET")


def fmt_ago(iso) -> str:
    dt = _parse(iso)
    if not dt:
        return "—"
    s = (datetime.now(timezone.utc) - dt).total_seconds()
    if s < 0:
        return "0 秒前"
    if s < 90:
        return f"{int(s)} 秒前"
    if s < 5400:
        return f"{int(s // 60)} 分前"
    if s < 172800:
        return f"{s / 3600:.1f} 時間前"
    return f"{s / 86400:.1f} 日前"


def fmt_secs(v) -> str:
    if v is None:
        return "—"
    try:
        v = float(v)
    except (TypeError, ValueError):
        return str(v)
    if v < 0:
        return "失効"
    m, s = divmod(int(v), 60)
    return f"{m} 分 {s:02d} 秒" if m else f"{s} 秒"


def fmt_num(v) -> str:
    if v is None or v == "":
        return "—"
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    if f.is_integer() and abs(f) >= 1000:
        return f"{int(f):,}"
    return f"{f:,.3f}".rstrip("0").rstrip(".") if abs(f) < 1000 else f"{f:,.2f}"


def fmt_json(v) -> str:
    return json.dumps(v, ensure_ascii=False, indent=2, default=str)


# ---------------------------------------------------------------- ミドルウェア


class SecurityHeaders:
    """Cache-Control: no-store と CSP。CSRF の cookie も無ければここで置く。"""

    def __init__(self, app, csrf: str) -> None:
        self.app = app
        self.csrf = csrf

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        has_cookie = False
        for k, v in scope.get("headers", []):
            if k == b"cookie" and f"{CSRF_COOKIE}={self.csrf}".encode() in v:
                has_cookie = True

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers += [
                    (b"cache-control", b"no-store"),
                    (b"x-content-type-options", b"nosniff"),
                    (b"x-frame-options", b"DENY"),
                    (b"referrer-policy", b"no-referrer"),
                    (b"content-security-policy", b"default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; connect-src 'self'; form-action 'self'; frame-ancestors 'none'"),
                ]
                if not has_cookie:
                    headers.append((b"set-cookie", f"{CSRF_COOKIE}={self.csrf}; Path=/; SameSite=Strict; HttpOnly".encode()))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_wrapper)


# ---------------------------------------------------------------- アプリ


def create_app(settings: Settings | None = None, start_monitors: bool = True) -> FastAPI:
    settings = settings or load_settings()
    import_sample(settings.sample_dir)
    settings.ensure_dirs()

    from .access import AccessGuard
    from .devtools import DevError, DevTools
    from . import live as lv
    from . import simmode
    from .judge import judge as run_judge
    from .masking import Redactor
    from .monitor import EventLog, Monitors
    from .ops import Ops
    from .records import diff_runs, find_run, load_runs

    redactor = Redactor()
    for key, value in settings.tt.items():
        if any(tag in key for tag in ("SECRET", "TOKEN")):
            redactor.secret(value, f"<{key.lower()}:masked>")
    events = EventLog(settings.monitor_dir, redactor)
    monitors = Monitors(settings, redactor, events)
    ops = Ops(settings, monitors, redactor, events)
    dev = DevTools(settings, redactor) if settings.face == "local" else None
    # デモ（資格情報なし / AIL_DEMO=1）は起動時にモックを立てて監視をそこへ繋ぐ。公開面でも同じ（開発画面は出ない）
    from .devtools import MockServer

    mock = dev.mock if dev else (MockServer(settings.sample_dir, settings.sample_python, settings.jobs_dir) if settings.demo else None)
    csrf = secrets.token_urlsafe(24)

    def start_demo() -> None:
        """モックを立て、記録が無ければ 1 回だけ 6 手順を流して記録・約定・通知を作る（ローカル面だけ。ジョブとして開発画面に出る）。"""
        try:
            if not mock.running():
                mock.start(True)
        except DevError as e:
            log.warning("デモ: モックが起動できない: %s", e)
            return
        if dev and not load_runs(settings.records_dir):
            try:
                dev.run_step("cert", "all", 8.0, use_mock=True)
            except DevError as e:
                log.warning("デモ: 記録の種まきに失敗: %s", e)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        log.info("管理画面 起動: mode=%s face=%s records=%s envs=%s demo=%s", settings.auth_mode, settings.face, settings.records_dir, list(monitors.items), settings.demo)
        if settings.demo and start_monitors and mock is not None:
            await asyncio.to_thread(start_demo)
        if start_monitors:
            await monitors.start()
        try:
            yield
        finally:
            await monitors.stop()
            if mock is not None:
                mock.stop()

    app = FastAPI(title="ai-income-lab 管理画面", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.settings = settings
    app.state.redactor = redactor
    app.state.monitors = monitors
    app.state.ops = ops
    app.state.dev = dev
    app.state.events = events
    app.state.csrf = csrf
    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")

    templates = Jinja2Templates(directory=str(HERE / "templates"))
    templates.env.filters.update(pt=fmt_pt, et=fmt_et, ago=fmt_ago, secs=fmt_secs, num=fmt_num, tojson_pretty=fmt_json)
    # 概要・トレーダーの詳細のグラフ（サーバで組む SVG。dashboard.md §15-5）。⚠ テンプレートの中で、Redactor を通した後のデータから描く
    from . import charts

    def _num_or_none(v):
        return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None

    templates.env.globals.update(charts=charts, pct=lambda v: charts.fmt_pct(_num_or_none(v)),
                                 usd=lambda v: charts.fmt_usd(_num_or_none(v)),
                                 # 注文の履歴を月ごとにまとめる（§13-7）。⚠ まとめるのは表示の小計だけ・損益は数え直さない
                                 history=lv.history)
    # i マークのヘルプ（§15-10）。文面の正本は dashboard/glossary.toml（用語タブと同じ 1 本）。templates は info("語") と名前で指すだけ
    from .helptext import HelpBook

    helpbook = HelpBook(Path(os.environ["AIL_GLOSSARY_FILE"]) if os.environ.get("AIL_GLOSSARY_FILE") else None, clean=redactor.text)
    app.state.helpbook = helpbook
    templates.env.globals.update(info=helpbook.mark)
    # シミュレーションモードの間、数字と図の見出しに付ける「仮」の印（§15-8 の .chip.placeholder と同じ扱い）
    from markupsafe import Markup
    SIM_MARK = Markup(' <span class="chip placeholder sim-mark" title="シミュレーション: 仮データ・仮の時計。実売買ではない">仮</span>')
    templates.env.globals.update(simmark=lambda machine: SIM_MARK if (machine or {}).get("mode") == "sim" else "")

    # 外側ほど先に評価される: AccessGuard → SecurityHeaders → ルート
    app.add_middleware(SecurityHeaders, csrf=csrf)
    app.add_middleware(AccessGuard, settings=settings)

    # ---------------- 共通

    def env_badges() -> list[dict]:
        out = []
        for env, mon in monitors.items.items():
            a = mon.state["auth"]
            out.append({"env": env, "label": mon.state["label"], "mock": mon.state["mock"], "scope": a.get("scope"), "ok": a.get("ok"), "suspended": a.get("suspended")})
        return out

    def actor(request: Request) -> str:
        return str(getattr(request.state, "user", None) or getattr(request.state, "client_ip", "") or "unknown")

    # ⚠ 読む記録はモードから決まる（real ＝ 本物 ／ sim ＝ sim/<名前>/。simmode.py）。1 つの画面・1 つの応答に両方を混ぜない。
    #    モードと木が食い違うときは空の木を読ませる ＝ 数字を出さない（帯に理由が出る）
    NO_TREE = Path("/nonexistent/mode-mismatch")

    def live_dir_now(machine: dict | None = None) -> Path:
        machine = machine or settings.machine()
        return NO_TREE if machine["mismatch"] else machine["live_dir"]

    def show_test(tr: list[dict], machine: dict) -> bool:
        # 試験用の人を一覧に出すか（2026-09-23 利用者の指示「テストトレーダーは削除」。規則は live.show_test_traders）
        return lv.show_test_traders(tr, mode=machine["mode"], demo=settings.demo)

    def board_now(days: int | None = lv.DAYS, *, all_traders: bool = False) -> dict:
        # ⚠ 面ごとに期間が違う（dashboard.md §13-7）: 概要・全体の詳細 ＝ 直近 20 営業日（人を横に比べる）／
        #    トレーダーの詳細 ＝ days=None で全期間（1 人を縦に追う）。⚠ 見出しに `b.period.label` を必ず書く
        from datetime import date as _date
        machine = settings.machine()
        today = (machine["sim"] or {}).get("today")           # シミュレーションの「今日」＝ 仮の今日
        b = lv.board(live_dir_now(machine), days=days, today=_date.fromisoformat(today) if today else None)
        show = show_test(b["traders"], machine)
        b["hidden_test"] = 0 if show else sum(1 for t in b["traders"] if t["test"])
        if not show and not all_traders:                       # ⚠ 色（cls）は全員の並びで決めた後に外す ＝ 左ペインと同じ色のまま
            b["traders"] = [t for t in b["traders"] if not t["test"]]
        return b

    def api(payload: dict) -> JSONResponse:
        # `/api/*` は必ずモードを名乗る（シミュレーションの数字を本物と取り違えない）
        return JSONResponse(redactor({**payload, **simmode.public(settings.machine())}))

    def nav_traders(machine: dict) -> list[dict]:
        # 左ペインのトレーダー（設定の順。色は系列の順。§15-6）。名前は 呼び名（識別名）。試験用は本物の人がいれば出さない
        tr = lv.traders(live_dir_now(machine))
        show = show_test(tr, machine)
        return [{"name": t["name"], "label": t["label"], "cls": f"s{i % lv.N_SERIES + 1}", "test": t["test"]}
                for i, t in enumerate(tr) if show or not t["test"]]

    def render(request: Request, name: str, **ctx):
        machine = settings.machine()
        base = {
            "page": "",
            "machine": {k: machine.get(k) for k in ("mode", "name", "since", "mismatch", "sim", "not_production")},
            "nav_traders": nav_traders(machine),
            "face": settings.face,
            "auth_mode": settings.auth_mode,
            "user": getattr(request.state, "user", None),
            "csrf": csrf,
            "halt": ops.halt_status(),
            "envs": env_badges(),
            "flash": request.query_params.get("flash"),
            "now": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "version": settings.version,
            "symbol": settings.symbol,
            "records_dir": str(settings.records_dir),
            # ⚠ 検証・データの画面はデモの対象外（実験側のファイルをそのまま読む）ので、出所を出し分ける
            "live_dir": str(machine["live_dir"]),
            "dev_available": dev is not None,
            "demo": settings.demo,
            "help_ok": helpbook.available(),
        }
        base.update(ctx)
        return templates.TemplateResponse(request, name, redactor(base))

    def redirect(url: str, flash: str | None = None):
        if flash:
            url += ("&" if "?" in url else "?") + "flash=" + quote(redactor.text(flash)[:400])
        return RedirectResponse(url, status_code=303)

    async def form_of(request: Request) -> dict:
        form = await request.form()
        token = form.get("csrf")
        cookie = request.cookies.get(CSRF_COOKIE)
        if not token or not cookie or not secrets.compare_digest(str(token), csrf) or not secrets.compare_digest(cookie, csrf):
            raise HTTPException(403, "CSRF トークンが合わない（ページを開き直す）")
        return {k: (v if isinstance(v, str) else "") for k, v in form.items()}

    def require_local() -> None:
        if settings.face != "local" or dev is None:
            raise HTTPException(404)

    # ---------------- 監視

    def monitor_ctx() -> dict:
        snap = monitors.snapshot()
        n_working = sum(len(m.get("live_orders") or []) for m in snap.values())
        return {"monitors": snap, "events": events.tail(30), "n_working": n_working}

    # ⚠ 概要・全体の詳細・トレーダーの詳細は読むだけ（両面）。発注は画面から出さない。停止は既存の /ops/halt（§13）

    @app.get("/", response_class=HTMLResponse)
    async def overview(request: Request):
        ctx = monitor_ctx()
        if request.query_params.get("partial") == "strip":
            return render(request, "strip_panel.html", page="overview", **ctx)
        if request.query_params.get("partial") == "simclock":
            return render(request, "simclock_panel.html", page="overview")
        return render(request, "overview.html", page="overview", b=board_now(), **ctx)

    @app.get("/overall", response_class=HTMLResponse)
    async def overall(request: Request):
        ctx = monitor_ctx()
        if request.query_params.get("partial") == "monitor":
            return render(request, "monitor_panel.html", page="overall", **ctx)
        return render(request, "overall.html", page="overall", b=board_now(), **ctx)

    @app.get("/traders/{name}", response_class=HTMLResponse)
    async def trader_page(request: Request, name: str):
        b = board_now(days=None, all_traders=True)            # ⚠ この面だけ全期間（§13-7）。試験用も URL で開ける
        t = next((x for x in b["traders"] if x["name"] == name), None)
        if t is None:
            raise HTTPException(404, "そのトレーダーは無い")
        # ⚠ 3 秒ごとに取り直すのは数字と図だけ（注文の履歴は全期間で長いので外す。§13-7）。⚠ GET のみ・POST は増やさない
        if request.query_params.get("partial") == "live":
            return render(request, "trader_live.html", page=f"trader:{name}", b=b, t=t)
        return render(request, "trader.html", page=f"trader:{name}", b=b, t=t)

    @app.get("/api/state")
    async def api_state(request: Request):
        payload = {"now": datetime.now(timezone.utc).isoformat(timespec="seconds"), "face": settings.face, "halt": ops.halt_status(), "monitors": monitors.snapshot(), "events": events.tail(30)}
        return api(payload)

    @app.get("/api/events")
    async def api_events(request: Request, n: int = 100):
        return api({"events": events.tail(max(1, min(n, 1000)))})

    # ---------------- 記録

    @app.get("/records", response_class=HTMLResponse)
    async def records(request: Request):
        runs = load_runs(settings.records_dir)
        return render(request, "records.html", page="records", runs=[r.summary() for r in runs])

    @app.get("/records/diff", response_class=HTMLResponse)
    async def records_diff(request: Request, a: str = "", b: str = ""):
        runs = load_runs(settings.records_dir)
        ra, rb = find_run(runs, a), find_run(runs, b)
        if not ra or not rb:
            raise HTTPException(404, "run が見つからない")
        return render(request, "diff.html", page="records", a=ra.summary(), b=rb.summary(), rows=diff_runs(ra, rb))

    @app.get("/records/{run_id}", response_class=HTMLResponse)
    async def record_detail(request: Request, run_id: str):
        runs = load_runs(settings.records_dir)
        run = find_run(runs, run_id)
        if not run:
            raise HTTPException(404, "run が見つからない")
        same_env = [r.summary() for r in runs if r.env == run.env and r.venue == run.venue and r.run_id != run.run_id][:10]
        return render(request, "record.html", page="records", run=run.summary(), rows=run.rows, siblings=same_env)

    @app.get("/api/records")
    async def api_records(request: Request):
        return api({"runs": [r.summary() for r in load_runs(settings.records_dir)]})

    # ---------------- 判定

    @app.get("/judge", response_class=HTMLResponse)
    async def judge_page(request: Request):
        runs = load_runs(settings.records_dir)
        result = run_judge(runs, events.load(), include_mock=settings.demo)
        return render(request, "judge.html", page="judge", judge=result)

    # ---------------------------------------------------------------- 実売買
    # ⚠ **読むだけ。発注は画面から出さない**（公開面にも出す）。停止は既存の /ops/halt（§13）

    @app.get("/live")
    async def live_page(request: Request):
        # 2026-09-18: 実売買の画面は概要（/）に移した。プランや手順書が /live を名指ししているので経路は残して転送する
        return RedirectResponse("/", status_code=302)

    @app.get("/api/live")
    async def api_live(request: Request):
        machine = settings.machine()
        d = lv.index(live_dir_now(machine))
        if not show_test(d["traders"], machine):
            d["traders"] = [t for t in d["traders"] if not t["test"]]      # `test_traders` はそのまま（居ることは分かる）
        return api(d)

    @app.get("/api/judge")
    async def api_judge(request: Request):
        runs = load_runs(settings.records_dir)
        return api(run_judge(runs, events.load(), include_mock=settings.demo))

    # ---------------- 停止（両面）

    @app.post("/ops/halt")
    async def ops_halt(request: Request):
        form = await form_of(request)
        result = await asyncio.to_thread(ops.halt, actor(request), form.get("reason", ""))
        summary = "; ".join(
            f"{r['env']}: " + (r.get("skipped") or r.get("error") or f"取消 {len(r.get('cancelled', []))} 件")
            for r in result["results"]
        ) or "取り消す環境なし"
        return redirect("/", f"停止した（HALT）。{summary}")

    # ---------------- 操作（ローカル面）

    @app.get("/ops", response_class=HTMLResponse)
    async def ops_page(request: Request):
        require_local()
        return render(request, "ops.html", page="ops", history=ops.history(30))

    @app.post("/ops/resume")
    async def ops_resume(request: Request):
        require_local()
        await form_of(request)
        await asyncio.to_thread(ops.resume, actor(request))
        return redirect("/ops", "停止を解除した")

    @app.post("/ops/retry-auth")
    async def ops_retry_auth(request: Request):
        require_local()
        form = await form_of(request)
        mon = monitors.get(form.get("env", ""))
        if not mon:
            raise HTTPException(404)
        mon.retry_auth()
        return redirect("/overall", f"{mon.env} の認証を再試行する（次の周期）")

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        if exc.status_code == 404 and not request.url.path.startswith("/api/"):
            return HTMLResponse("<h1>404</h1>", status_code=404)
        return JSONResponse({"error": redactor.text(str(exc.detail))}, status_code=exc.status_code)

    return app


def main() -> None:
    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = load_settings()
    app = create_app(settings)
    uvicorn.run(app, host=settings.bind, port=settings.port, log_level="info", proxy_headers=False, server_header=False)


if __name__ == "__main__":
    main()
