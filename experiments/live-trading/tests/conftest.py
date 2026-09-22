import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLE = os.path.normpath(os.path.join(HERE, "..", "tastytrade-api-sample"))
for p in (HERE, SAMPLE):
    if p not in sys.path:
        sys.path.insert(0, p)

import pytest  # noqa: E402

import livefs  # noqa: E402


@pytest.fixture(autouse=True)
def _record_db(tmp_path):
    """⚠ 記録は DB（プラン db-model-facts.md §11）。テストの一時置き場に自分の DB を作る ＝ 本物の live.sqlite に書かない。
    （子プロセスの run_day・simrun も、道から親へたどってこの DB を見つける）"""
    livefs.init(tmp_path, "live")
    yield
    livefs.forget()
