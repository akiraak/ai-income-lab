"""管理画面の本体（FastAPI）。

画面: 監視（/）・記録（/records）・判定（/judge）・検証（/experiments）・操作（/ops、ローカル面）・開発（/dev、ローカル面）。
公開面（AIL_AUTH_MODE=cloudflare）では /ops と /dev は 404 を返し、POST は停止（/ops/halt）だけ受ける。
すべての応答は Redactor を通す（秘密をブラウザに送らない）。
"""

from __future__ import annotations

import asyncio
import json
import logging
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
    from . import experiments as exp
    from .judge import judge as run_judge
    from .masking import Redactor
    from .monitor import EventLog, Monitors
    from .ops import ACTIONS, CONFIRM_PHRASE, ORDER_TYPES, Ops, OpsError
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

    def render(request: Request, name: str, **ctx):
        base = {
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
            # ⚠ 検証の画面はデモの対象外（実験の記録をそのまま読む）ので、出所を出し分ける
            "runs_dir": str(settings.runs_dir),
            "dev_available": dev is not None,
            "demo": settings.demo,
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

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        snap = monitors.snapshot()
        ctx = {"monitors": snap, "events": events.tail(30), "page": "monitor"}
        if request.query_params.get("partial"):
            return render(request, "monitor_panel.html", **ctx)
        return render(request, "index.html", **ctx)

    @app.get("/api/state")
    async def api_state(request: Request):
        payload = {"now": datetime.now(timezone.utc).isoformat(timespec="seconds"), "face": settings.face, "halt": ops.halt_status(), "monitors": monitors.snapshot(), "events": events.tail(30)}
        return JSONResponse(redactor(payload))

    @app.get("/api/events")
    async def api_events(request: Request, n: int = 100):
        return JSONResponse(redactor({"events": events.tail(max(1, min(n, 1000)))}))

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
        return JSONResponse(redactor({"runs": [r.summary() for r in load_runs(settings.records_dir)]}))

    # ---------------- 判定

    @app.get("/judge", response_class=HTMLResponse)
    async def judge_page(request: Request):
        runs = load_runs(settings.records_dir)
        result = run_judge(runs, events.load(), include_mock=settings.demo)
        return render(request, "judge.html", page="judge", judge=result)

    # ---------------------------------------------------------------- 検証
    # ⚠ **読むだけなので公開面にも出す。** 検査は実験側が checks.json に書いたものをそのまま使う

    @app.get("/experiments", response_class=HTMLResponse)
    async def experiments_page(request: Request):
        return render(request, "experiments.html", page="experiments",
                      ex=exp.index(settings.runs_dir))

    @app.get("/experiments/{run_id}", response_class=HTMLResponse)
    async def experiment_page(request: Request, run_id: str):
        run = exp.one(settings.runs_dir, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="その検証は無い")
        return render(request, "experiment.html", page="experiments", run=run)

    @app.get("/api/experiments")
    async def api_experiments(request: Request):
        return JSONResponse(redactor(exp.index(settings.runs_dir)))

    @app.get("/api/judge")
    async def api_judge(request: Request):
        runs = load_runs(settings.records_dir)
        return JSONResponse(redactor(run_judge(runs, events.load(), include_mock=settings.demo)))

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
        return render(
            request,
            "ops.html",
            page="ops",
            monitors=monitors.snapshot(),
            history=ops.history(30),
            actions=ACTIONS,
            order_types=ORDER_TYPES,
            confirm_phrase=CONFIRM_PHRASE,
            allow_prod_dry_run=settings.allow_prod_dry_run,
            allow_prod_orders=settings.allow_prod_orders,
            result=None,
        )

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
        return redirect("/", f"{mon.env} の認証を再試行する（次の周期）")

    async def _op(request: Request, fn, *args):
        try:
            return await asyncio.to_thread(fn, *args), None
        except OpsError as exc:
            return None, str(exc)
        except Exception as exc:  # ApiError など
            return None, f"{type(exc).__name__}: {str(exc)[:300]}"

    @app.post("/ops/dry-run", response_class=HTMLResponse)
    async def ops_dry_run(request: Request):
        require_local()
        form = await form_of(request)
        result, error = await _op(request, ops.dry_run, form.get("env", "cert"), form, actor(request))
        return render(
            request, "ops.html", page="ops", monitors=monitors.snapshot(), history=ops.history(30), actions=ACTIONS, order_types=ORDER_TYPES,
            confirm_phrase=CONFIRM_PHRASE, allow_prod_dry_run=settings.allow_prod_dry_run, allow_prod_orders=settings.allow_prod_orders,
            result={"kind": "dry-run", "data": result, "error": error, "form": form},
        )

    @app.post("/ops/submit")
    async def ops_submit(request: Request):
        require_local()
        form = await form_of(request)
        result, error = await _op(request, ops.submit, form.get("env", "cert"), form, actor(request), form.get("confirm", ""))
        if error:
            return redirect("/ops", f"発注できなかった: {error}")
        o = result["submitted"]
        return redirect("/ops", f"{result['env']} に発注した: id {o.get('id')} / {o.get('status')}")

    @app.post("/ops/cancel")
    async def ops_cancel(request: Request):
        require_local()
        form = await form_of(request)
        result, error = await _op(request, ops.cancel, form.get("env", "cert"), form.get("order_id", ""), actor(request))
        if error:
            return redirect(form.get("back") or "/ops", f"取消できなかった: {error}")
        return redirect(form.get("back") or "/ops", f"取消した: id {result['order']['id']} / {result['order']['status']}")

    @app.post("/ops/cleanup")
    async def ops_cleanup(request: Request):
        require_local()
        form = await form_of(request)
        result, error = await _op(request, ops.cleanup, form.get("env", "cert"), actor(request))
        if error:
            return redirect("/ops", f"後片付けできなかった: {error}")
        return redirect("/ops", f"{result['env']} の働いている注文 {result['working']} 件を取り消した")

    # ---------------- 開発（ローカル面）

    @app.get("/dev", response_class=HTMLResponse)
    async def dev_page(request: Request):
        require_local()
        return render(request, "dev.html", page="dev", dev=dev.status(), prod_available=monitors.get("prod") is not None)

    @app.post("/dev/mock/start")
    async def dev_mock_start(request: Request):
        require_local()
        await form_of(request)
        try:
            await asyncio.to_thread(dev.mock.start, True)
        except DevError as exc:
            return redirect("/dev", f"モックを起動できなかった: {exc}")
        return redirect("/dev", "モックを起動した")

    @app.post("/dev/mock/stop")
    async def dev_mock_stop(request: Request):
        require_local()
        await form_of(request)
        await asyncio.to_thread(dev.mock.stop)
        return redirect("/dev", "モックを止めた")

    @app.post("/dev/selftest")
    async def dev_selftest(request: Request):
        require_local()
        await form_of(request)
        try:
            job = dev.run_selftest()
        except DevError as exc:
            return redirect("/dev", str(exc))
        return redirect(f"/dev/jobs/{job.id}")

    @app.post("/dev/run")
    async def dev_run(request: Request):
        require_local()
        form = await form_of(request)
        try:
            seconds = float(form.get("seconds") or 15)
        except ValueError:
            seconds = 15.0
        try:
            job = dev.run_step(form.get("env", "cert"), form.get("step", "1"), seconds, form.get("use_mock") == "1", form.get("verify_expiry") == "1")
        except DevError as exc:
            return redirect("/dev", str(exc))
        return redirect(f"/dev/jobs/{job.id}")

    @app.get("/dev/jobs/{job_id}", response_class=HTMLResponse)
    async def dev_job(request: Request, job_id: str):
        require_local()
        job = dev.jobs.get(job_id)
        if not job:
            raise HTTPException(404)
        ctx = {"page": "dev", "job": job.brief(), "lines": job.lines[-2000:]}
        if request.query_params.get("partial"):
            return render(request, "job_panel.html", **ctx)
        return render(request, "job.html", **ctx)

    @app.post("/dev/jobs/{job_id}/stop")
    async def dev_job_stop(request: Request, job_id: str):
        require_local()
        await form_of(request)
        dev.jobs.stop(job_id)
        return redirect(f"/dev/jobs/{job_id}", "停止を要求した")

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
