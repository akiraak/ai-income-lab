"""的中率 ／ IC ／ 粗利 bp ／ 純利 bp（コスト後）。

⚠ **粗利だけを見てはいけない。** E8 §1-4 の通り往復 5〜10bp を引いて、
⚠ **正でなければその手法は使えない。**
"""

from __future__ import annotations

import numpy as np


def score(pred: np.ndarray, y: np.ndarray, cost_bp: float) -> dict:
    hit = float(np.mean(np.sign(pred) == np.sign(y)))
    ic = float(np.corrcoef(pred, y)[0, 1]) if np.std(pred) > 0 else 0.0
    gross = float(np.mean(np.sign(pred) * y))       # 1 往復あたりの粗利（対数）
    return {"的中率": hit, "IC": ic, "粗利bp": gross * 1e4, "純利bp": gross * 1e4 - cost_bp}
