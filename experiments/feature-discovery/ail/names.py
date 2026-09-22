"""予測モデルの名前（rules.md 10-2）。⚠ **台帳の鍵から機械で作る。人が付けるのは「型」（解説のページ）だけ。**

    from ail import names
    names.model_name(row)    # 'own.all.ridge.shared'      台帳の鍵から θ を除いた 11 列 ＝ 予測モデル 1 つ
    names.trial_name(row)    # 'own.all.ridge.shared@50'   台帳の鍵の 12 列 ＝ 台帳の 1 行 ＝ n_trials の 1

名前の形: `<入力データ>.<数字の選び方・作り方>.<学習器>.<学習範囲>[~<既定から外れた列>…][@<θ>]`
（台帳の列では 特徴量の層 ／ 鍵〔手法〕／ モデル ／ 形式。⚠ 言葉は rules.md 10-2 の対応の一覧で読む）

  - ⚠ **既存の名前（実験名・モデル名・選び方や作り方の登録名）は 1 つも変えない。** ここで作るのは別名
  - ⚠ **台帳の鍵が違えば名前も必ず違う**（`tests/test_names.py` が台帳の全行で確かめる）
  - ⚠ **既定の値は書かない**（rules.md 10-1 の「水準」と同じ）。既定は `config/names.toml` の `[defaults]`
  - ⚠ 綴りの一覧（`config/names.toml`）に無い選び方・作り方・学習器は止める（名前を付けてから回す）
  - ⚠ **名前は n_trials を動かさない**（数え方は `catalog.is_trial` のまま）
  - pandas を import しない（標準ライブラリだけ ＝ 読み手を選ばない）
"""

from __future__ import annotations

import functools
import os
import re
import tomllib

ROOT = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))
TABLE = os.path.join(ROOT, "config", "names.toml")

# 台帳の鍵（`catalog.KEY`）のうち θ を除いた 11 列 ＝ 予測モデル 1 つ
MODEL_KEY = ("鍵", "モデル", "粒度", "地平", "特徴量の層", "層", "期間", "銘柄", "検証方式", "形式", "較正")

_ID = re.compile(r"^F\d-\d+[a-z]?$")                                    # catalog._ID と同じ形（全体一致）
_PAIR = re.compile(r"^入口([A-Z]\d)\(\d+\)×出口([A-Z]\d)\(\d+\)（[^）]+）$")  # rules.md 16 章の入口 × 出口
_TOPK_BASE = re.compile(r"^([^〔]+)〔(.+)・上位(\d+)・([^・〕]+)〕$")      # ボラ上位〔<選び方・作り方>・上位3・端数〕
_TOPK = re.compile(r"〔上位(\d+)・([^〕]+)〕$")                           # <選び方・作り方>〔上位3・端数〕
_LEARNER = re.compile(r"^([^+(]+)((?:\+[^+(]+)*)(?:\(([^)]+)\))?$")      # 基底 +増強… (水準)
_HORIZON = re.compile(r"^(\d+) 本")
_GRAN = re.compile(r"^(\d+) 分足$")
_TOKEN = re.compile(r"^[a-z0-9][a-z0-9-]*$")


@functools.lru_cache(maxsize=None)
def table(path: str = TABLE) -> dict:
    with open(path, "rb") as f:
        return tomllib.load(f)


def _stop(what: str, value: str) -> None:
    raise SystemExit(f"⚠ 名前の綴りが決まっていない{what}: {value!r}"
                     f"（{os.path.relpath(TABLE, ROOT)} に 1 行足す。rules.md 10-2）")


def _check(token: str, what: str, value: str) -> str:
    if not _TOKEN.match(token):
        raise SystemExit(f"⚠ 名前に使えない文字がある{what}: {value!r} → {token!r}（英小文字・数字・`-` だけ）")
    return token


def method_slug(key: str) -> str:
    """台帳の「鍵」列（数字の選び方・作り方。カタログの選び方は ID）→ 綴り。"""
    t = table()
    if _ID.match(key):
        return key.lower()
    if key in t["method"]:
        return t["method"][key]
    if (m := _TOPK_BASE.match(key)) and m.group(1) in t["topk"]:
        return _check(f"{t['topk'][m.group(1)]}-{method_slug(m.group(2))}-top{m.group(3)}-{_holding(m.group(4), key)}",
                      "数字の選び方・作り方", key)
    if m := _TOPK.search(key):
        return _check(f"{method_slug(key[:m.start()])}-top{m.group(1)}-{_holding(m.group(2), key)}", "数字の選び方・作り方", key)
    if m := _PAIR.match(key):
        return f"in-{m.group(1).lower()}-out-{m.group(2).lower()}"
    _stop("数字の選び方・作り方", key)


def _holding(word: str, key: str) -> str:
    t = table()["holding"]
    if word not in t:
        _stop("上位 K の買い方", key)
    return t[word]


def learner_slug(model: str) -> str:
    """台帳の「モデル」列（学習器）→ 綴り。`LightGBM+GAN増強(batch16k)` → `lgbm-gan-16k`。"""
    t = table()
    if model in t["learner"]:
        return t["learner"][model]
    m = _LEARNER.match(model)
    if not m or m.group(1) not in t["learner"]:
        _stop("学習器", model)
    parts = [t["learner"][m.group(1)]]
    for aug in filter(None, m.group(2).split("+")):
        if aug not in t["augment"]:
            _stop("増強", model)
        parts.append(t["augment"][aug])
    if m.group(3):
        parts.append(re.sub(r"^batch", "", m.group(3)).lower())
    return _check("-".join(parts), "学習器", model)


def layers_slug(layers: str) -> str:
    """台帳の「特徴量の層」列（空白区切り）→ `-` でつないだ綴り。"""
    parts = str(layers or "").split()
    for p in parts:
        _check(p, "特徴量の層", layers)
    return "-".join(parts) or "none"


def _options(row: dict) -> list[str]:
    """既定から外れた列だけを、決まった順に並べる。⚠ **順を変えると名前が変わる。**"""
    d = table()["defaults"]
    out = []
    style = row["検証方式"]
    if style == "毎日往復":
        out.append("rt")
    elif style != d["style"]:
        _stop("検証方式", style)
    gran = row["粒度"]
    if gran != d["granularity"]:
        m = _GRAN.match(gran)
        out.append(f"{m.group(1)}m") if m else _stop("粒度", gran)
    m = _HORIZON.match(row["地平"])
    if not m:
        _stop("地平", row["地平"])
    if m.group(1) != d["horizon"]:
        out.append(f"h{m.group(1)}")      # ⚠ 本数だけで足りる（地平の見せ方は 本数 × 粒度 で決まる。catalog._horizon）
    if row["層"] != d["layer"]:
        out.append(_check({"?": "layer-unknown"}.get(row["層"], row["層"]), "層", row["層"]))
    if row["期間"] != d["period"]:
        out.append("p-na" if row["期間"] == "—" else _check(f"p{row['期間']}", "期間", row["期間"]))
    if row["銘柄"] != d["symbols"]:
        out.append("n-na" if row["銘柄"] == "—" else _check(f"n{row['銘柄']}", "銘柄", row["銘柄"]))
    cal = row["較正"]
    if cal != (d["calibration"] if style == d["style"] else "—"):
        out.append({"旧": "cal-old"}.get(cal) or _check(f"cal-{cal}", "較正", cal))
    return out


def model_name(row: dict) -> str:
    """予測モデル名 ＝ 台帳の鍵から θ を除いた 11 列。例 `own-seq.t3-quant60.ridge.shared`（`60` ＝ 観測期間 60 日）。"""
    form = table()["form"].get(row["形式"]) or _stop("学習範囲（台帳の列「形式」）", row["形式"])
    base = ".".join((layers_slug(row["特徴量の層"]), method_slug(row["鍵"]), learner_slug(row["モデル"]), form))
    return base + "".join("~" + o for o in _options(row))


def trial_name(row: dict) -> str:
    """試行名 ＝ 予測モデル名 ＋ θ。⚠ **θ が「—」の行（毎日往復・門前）は予測モデル名と同じ。**"""
    th = str(row.get("閾値") or "—")
    return model_name(row) + ("" if th == "—" else f"@{th}")
