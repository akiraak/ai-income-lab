"""登録を全部走らせる。⚠ **`@register` は import されて初めて効く**ので、1 か所で束ねる。

⚠ **ここに import を足し忘れると「registry に無い」で止まる。** 手法を足したら 1 行足す。
"""

from ail.data.sources import tastytrade      # noqa: F401
# 外部の日次系列（rules.md 1 章の層に載せる。⚠ **足とは形が違う**）
from ail.data.sources import ecb, noaa, treasury, usgs   # noqa: F401
# ⚠ **社会にインパクトを与えるもの（本命）。** 偽薬（気象・地震）とは枠が別
from ail.data.sources import epu, ncei_storm            # noqa: F401
# ⚠ **警報は 2 経路**: IEM は保管庫（検証用）、NWS は配信（運用用）。⚠ **突き合わせは cli/crosscheck.py**
from ail.data.sources import iem, nws                   # noqa: F401
# ⚠ **暦（発表日）。** 値ではなく日付そのものが情報（econ_calendar）
from ail.data.sources import fomc                       # noqa: F401
from ail.features import own, cross, relative, leadlag, exog   # noqa: F401
# ⚠ **`trend` は窓の長い `own_`**（20 / 60 / 200 営業日）。下降トレンドの検知が使う
from ail.features import trend                               # noqa: F401
# ⚠ **`im_` は銘柄ごとに値が変わる外部データ**（`ex_` との違いはそこだけ）
from ail.features import impact                              # noqa: F401
from ail.selectors import filter as _filter, wrapper, embedded  # noqa: F401
# ⚠ **表現学習は `selector` ではなく `transform`**（列そのものを作り替える。
# プラン `plans/selectors-small-four.md` §1-1）
from ail.selectors import representation                        # noqa: F401
from ail.models import baselines, linear     # noqa: F401
# ⚠ **モデルの軸**（plans/archive/gpu-models.md）: 勾配ブースティング・MLP・GAN 増強
from ail.models import trees, deep, gan      # noqa: F401
# ⚠ **検知器（買い% 1 本を返す手法）**。下降トレンドの検知（plans/archive/downtrend-detection.md）
from ail.detectors import scale as _scale    # noqa: F401
# ⚠ **入口と出口を別の窓で持つ検知器**（出力 2 本。rules.md 16 章）
from ail.detectors import pair as _pair      # noqa: F401
# ⚠ **時系列分類器 3 本**（MiniRocket・Hydra・QUANT。plans/tsc-minirocket-hydra-quant.md）。
# ⚠ **入力の窓は `seq` 層**（過去 60 営業日の `own_`）。⚠ **aeon は検知器の関数の中でだけ import する**
from ail.features import seq                 # noqa: F401
from ail.detectors import tsc as _tsc        # noqa: F401
# ⚠ **系列モデル 1 本（PatchTST）**（plans/patchtst-threshold.md）。⚠ **窓は時系列分類器と同じ**。
# ⚠ **モデル `PatchTST` は検知器 `S1 PatchTST（60日窓）` からしか呼べない**（窓の配列が要る）
from ail.models import patchtst as _patchtst  # noqa: F401
from ail.detectors import seqmodel as _seqmodel  # noqa: F401
from ail.validation import splits            # noqa: F401
