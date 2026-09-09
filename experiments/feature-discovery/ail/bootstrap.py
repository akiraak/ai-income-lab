"""登録を全部走らせる。⚠ **`@register` は import されて初めて効く**ので、1 か所で束ねる。

⚠ **ここに import を足し忘れると「registry に無い」で止まる。** 手法を足したら 1 行足す。
"""

from ail.data.sources import tastytrade      # noqa: F401
# 外部の日次系列（rules.md 1 章の層に載せる。⚠ **足とは形が違う**）
from ail.data.sources import ecb, noaa, treasury, usgs   # noqa: F401
# ⚠ **社会にインパクトを与えるもの（本命）。** 偽薬（気象・地震）とは枠が別
from ail.data.sources import epu, ncei_storm            # noqa: F401
from ail.features import own, cross, relative, leadlag, exog   # noqa: F401
from ail.selectors import filter as _filter, wrapper, embedded  # noqa: F401
from ail.models import baselines, linear     # noqa: F401
from ail.validation import splits            # noqa: F401
