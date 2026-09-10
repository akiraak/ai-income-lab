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
# ⚠ **`im_` は銘柄ごとに値が変わる外部データ**（`ex_` との違いはそこだけ）
from ail.features import impact                              # noqa: F401
from ail.selectors import filter as _filter, wrapper, embedded  # noqa: F401
from ail.models import baselines, linear     # noqa: F401
# ⚠ **モデルの軸**（plans/archive/gpu-models.md）: 勾配ブースティング・MLP・GAN 増強
from ail.models import trees, deep, gan      # noqa: F401
from ail.validation import splits            # noqa: F401
