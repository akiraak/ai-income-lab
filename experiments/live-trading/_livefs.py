"""記録の置き場 `livefs`（experiments/tastytrade-api-sample/livefs.py）を import するための 1 か所。

    from _livefs import livefs

⚠ 記録（`out/`・`state/`・`mode.log`・シミュレーションの木の記録）はここを通して DB に入る（プラン db-model-facts.md §11）。
⚠ `HALT`・`MODE`・`run.lock`・`control.json` はファイルのまま（通さない）。
"""

import os
import sys

_SAMPLE_DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tastytrade-api-sample"))
if _SAMPLE_DIR not in sys.path:
    sys.path.insert(0, _SAMPLE_DIR)

import livefs  # noqa: E402,F401
