"""設定。環境変数 → dashboard/.env → サンプルの .env の順に読む（先に見つかった値が勝つ）。

面（公開 / ローカル）は AIL_AUTH_MODE で決まる。**既定は最も厳しい loopback**（ループバック以外は全部 403）。
cloudflare を選ぶときは CF_ACCESS_TEAM / CF_ACCESS_AUD / CF_ACCESS_EMAIL が全部そろわないと起動しない
（discord-manager と同じ fail-safe。プラン §2-1）。
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

DASHBOARD_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = DASHBOARD_DIR.parent
VERSION = "0.1.0"
AUTH_MODES = ("loopback", "local", "cloudflare")
# モックサーバ（experiments/tastytrade-api-sample/mock_server.py）のポート。開発画面とデモが共用する
MOCK_PORTS = (8765, 8766, 8767)


class ConfigError(RuntimeError):
    pass


def read_env_file(path: Path) -> dict[str, str]:
    """KEY=VALUE の .env を読む（ttclient.load_env と同じ規則。依存を増やさない）。"""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip().strip("'\"")
    return values


@dataclass
class Settings:
    auth_mode: str
    cf_team: str | None
    cf_aud: str | None
    cf_email: str | None
    bind: str
    port: int
    data_dir: Path
    records_dir: Path
    sample_dir: Path
    runs_dir: Path
    sample_python: str
    symbol: str
    poll_seconds: float
    tt: dict[str, str] = field(default_factory=dict)
    version: str = VERSION
    # デモ: 資格情報が無い（または AIL_DEMO=1）とき、本物には繋がずモックのデータで全画面を出す
    demo: bool = False

    # ---- 派生 ----
    @property
    def face(self) -> str:
        """公開面（cloudflare）かローカル面か。操作・開発の経路はローカル面でしか出さない。"""
        return "public" if self.auth_mode == "cloudflare" else "local"

    @property
    def mock_rest_base(self) -> str:
        return f"http://127.0.0.1:{MOCK_PORTS[0]}"

    @property
    def mock_account_streamer(self) -> str:
        return f"ws://127.0.0.1:{MOCK_PORTS[1]}"

    def demo_credentials(self, env: str) -> dict:
        """デモの監視が使う資格情報。値はモック用の印で、本物の口座には一切届かない。"""
        return {
            "client_id": None,
            "client_secret": f"DEMO-{env.upper()}-CLIENT-SECRET",
            "refresh_token": f"DEMO-{env.upper()}-REFRESH-TOKEN",
            "rest_base": self.mock_rest_base,
            "account_streamer": self.mock_account_streamer,
            "account_number": None,
        }

    @property
    def monitor_dir(self) -> Path:
        return self.data_dir / "monitor"

    @property
    def jobs_dir(self) -> Path:
        return self.data_dir / "jobs"

    @property
    def ops_dir(self) -> Path:
        return self.data_dir / "ops"

    @property
    def halt_file(self) -> Path:
        # sample.py が見る場所と同じ（TT_HALT_FILE の既定 = 記録ディレクトリの HALT）
        return self.records_dir / "HALT"

    @property
    def allow_prod_dry_run(self) -> bool:
        return self.tt.get("TT_ALLOW_PROD_DRY_RUN") == "1"

    @property
    def allow_prod_orders(self) -> bool:
        return self.tt.get("TT_ALLOW_PROD_ORDERS") == "1"

    def credentials(self, env: str) -> dict | None:
        """env（cert / prod）に使う資格情報。無ければ None（その環境は監視しない）。

        サンプルの .env と同じ意味: TT_* は TT_ENV（既定 cert）の資格情報、TT_PROD_* は本番の読み取り用。
        """
        tt = self.tt
        primary_env = tt.get("TT_ENV", "cert") or "cert"
        if env == "prod" and tt.get("TT_PROD_CLIENT_SECRET") and tt.get("TT_PROD_REFRESH_TOKEN"):
            return {
                "client_id": tt.get("TT_PROD_CLIENT_ID") or None,
                "client_secret": tt["TT_PROD_CLIENT_SECRET"],
                "refresh_token": tt["TT_PROD_REFRESH_TOKEN"],
                "rest_base": tt.get("TT_PROD_REST_BASE") or None,
                "account_streamer": tt.get("TT_PROD_ACCOUNT_STREAMER") or None,
                "account_number": tt.get("TT_PROD_ACCOUNT_NUMBER") or None,
            }
        if env == primary_env and tt.get("TT_CLIENT_SECRET") and tt.get("TT_REFRESH_TOKEN"):
            return {
                "client_id": tt.get("TT_CLIENT_ID") or None,
                "client_secret": tt["TT_CLIENT_SECRET"],
                "refresh_token": tt["TT_REFRESH_TOKEN"],
                "rest_base": tt.get("TT_REST_BASE") or None,
                "account_streamer": tt.get("TT_ACCOUNT_STREAMER") or None,
                "account_number": tt.get("TT_ACCOUNT_NUMBER") or None,
            }
        return None

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.records_dir, self.monitor_dir, self.jobs_dir, self.ops_dir):
            d.mkdir(parents=True, exist_ok=True)


def load_settings(environ: dict | None = None) -> Settings:
    env = dict(os.environ if environ is None else environ)
    env_file = Path(env.get("AIL_ENV_FILE") or DASHBOARD_DIR / ".env")
    for key, value in read_env_file(env_file).items():
        env.setdefault(key, value)

    sample_dir = Path(env.get("AIL_SAMPLE_DIR") or REPO_ROOT / "experiments" / "tastytrade-api-sample").resolve()
    # 開発機ではサンプルの .env（資格情報）をそのまま使う。コピーを増やさない
    tt_env_file = Path(env.get("AIL_TT_ENV_FILE") or sample_dir / ".env")
    for key, value in read_env_file(tt_env_file).items():
        env.setdefault(key, value)

    auth_mode = (env.get("AIL_AUTH_MODE") or "loopback").strip().lower()
    if auth_mode not in AUTH_MODES:
        raise ConfigError(f"AIL_AUTH_MODE は {'/'.join(AUTH_MODES)} のどれか: {auth_mode!r}")
    cf = {k: (env.get(k) or "").strip() or None for k in ("CF_ACCESS_TEAM", "CF_ACCESS_AUD", "CF_ACCESS_EMAIL")}
    if auth_mode == "cloudflare" and not all(cf.values()):
        missing = [k for k, v in cf.items() if not v]
        raise ConfigError(f"AIL_AUTH_MODE=cloudflare には {', '.join(missing)} が要る（3 つ全部そろえる）")
    if auth_mode != "cloudflare" and any(cf.values()):
        # 中途半端な設定は素通りより起動失敗のほうが安全
        raise ConfigError("CF_ACCESS_* が設定されているのに AIL_AUTH_MODE が cloudflare でない")

    data_dir = Path(env.get("AIL_DATA_DIR") or DASHBOARD_DIR / "data").resolve()
    # 記録の既定: 開発機ではサンプルの out/（sample.py の既定と同じ場所）、Docker では data/records
    records_default = sample_dir / "out" if env.get("AIL_DATA_DIR") is None else data_dir / "records"
    records_dir = Path(env.get("AIL_RECORDS_DIR") or records_default).resolve()

    # 検証（特徴量の発見手法）の実行記録。⚠ **git 管理外なので、別環境では空でよい**
    runs_dir = Path(env.get("AIL_RUNS_DIR")
                    or REPO_ROOT / "experiments" / "feature-discovery" / "runs").resolve()

    venv_python = sample_dir / ".venv" / "bin" / "python"
    sample_python = env.get("AIL_SAMPLE_PYTHON") or (str(venv_python) if venv_python.exists() else sys.executable)

    tt = {k: v for k, v in env.items() if k.startswith("TT_")}

    # デモ: AIL_DEMO で強制（1 / 0）。未指定なら「資格情報が 1 つも無い」ときに自動でデモ
    has_creds = bool((tt.get("TT_PROD_CLIENT_SECRET") and tt.get("TT_PROD_REFRESH_TOKEN")) or (tt.get("TT_CLIENT_SECRET") and tt.get("TT_REFRESH_TOKEN")))
    demo_flag = (env.get("AIL_DEMO") or "").strip().lower()
    if demo_flag in ("1", "true", "yes", "on"):
        demo = True
    elif demo_flag in ("0", "false", "no", "off"):
        demo = False
    else:
        demo = not has_creds
    if demo:
        # デモのデータ（記録・監視ログ・ジョブ・操作履歴）は本物と混ぜない
        data_dir = data_dir / "demo"
        records_dir = Path(env.get("AIL_RECORDS_DIR") or data_dir / "records").resolve()

    settings = Settings(
        auth_mode=auth_mode,
        cf_team=cf["CF_ACCESS_TEAM"],
        cf_aud=cf["CF_ACCESS_AUD"],
        cf_email=cf["CF_ACCESS_EMAIL"],
        bind=env.get("AIL_BIND") or "127.0.0.1",
        port=int(env.get("AIL_PORT") or 3012),
        data_dir=data_dir,
        records_dir=records_dir,
        sample_dir=sample_dir,
        runs_dir=runs_dir,
        sample_python=sample_python,
        symbol=(env.get("AIL_SYMBOL") or "SPY").upper(),
        poll_seconds=float(env.get("AIL_POLL_SECONDS") or 30),
        tt=tt,
        demo=demo,
    )
    return settings


def import_sample(sample_dir: Path) -> None:
    """experiments/tastytrade-api-sample の ttclient / record を import できるようにする（コピーしない）。"""
    path = str(sample_dir)
    if path not in sys.path:
        sys.path.insert(0, path)
