"""登録を全部走らせる。⚠ **`@register` は import されて初めて効く**ので、1 か所で束ねる。

⚠ **ここに import を足し忘れると「registry に無い」で止まる。** 手法を足したら 1 行足す。
"""

from ail.data.sources import tastytrade      # noqa: F401
from ail.features import own, cross, relative, leadlag   # noqa: F401
from ail.selectors import filter as _filter, wrapper, embedded  # noqa: F401
from ail.models import baselines, linear     # noqa: F401
from ail.validation import splits            # noqa: F401
