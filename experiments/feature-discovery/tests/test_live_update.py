"""`live_update.sh` の段の流れ（2026-11-03 docs/plans/exog-failure-continue.md）。

外部系列の取得元が 1 つ失敗しても、ほかの取得元・調整・検査・「どこまで入ったか」まで進み、
rc は「どこまで入ったか」（live_status）が決めることを、python を偽物に差し替えて確かめる（ネットワークなし）。
"""
import os
import subprocess
from pathlib import Path

import pytest

FD = Path(__file__).resolve().parents[1]
SCRIPT = FD / "live_update.sh"

FAKE = r"""#!/usr/bin/env bash
# 偽の python: 呼ばれた引数を 1 行ずつ残し、決めた取得元だけ失敗する
echo "$*" >> "$CALLS"
case "$*" in
  *"--source $FAIL_SOURCE"*) echo "requests.exceptions.HTTPError: 500 Server Error" >&2; exit 1 ;;
  *cli.live_status*) exit "${STATUS_RC:-0}" ;;
esac
exit 0
"""


def _run(tmp_path, fail_source="ecb", status_rc=0, *args):
    fake = tmp_path / "fakepy"
    fake.write_text(FAKE)
    fake.chmod(0o755)
    live = tmp_path / "live"
    live.mkdir()
    (live / "seed.json").write_text("{}")
    calls = tmp_path / "calls.txt"
    env = {**os.environ, "AIL_LIVE_UPDATE_PY": str(fake), "AIL_LIVE_DATA_DIR": str(live),
           "CALLS": str(calls), "FAIL_SOURCE": fail_source, "STATUS_RC": str(status_rc)}
    p = subprocess.run(["bash", str(SCRIPT), *args], env=env, capture_output=True, text=True, timeout=60)
    return p, calls.read_text().splitlines()


def test_one_exog_source_failing_does_not_skip_adjust(tmp_path):
    p, calls = _run(tmp_path, fail_source="ecb")
    assert p.returncode == 0, p.stdout + p.stderr
    sources = [c.split("--source ")[1] for c in calls if "--exog exog_live --source" in c]
    assert sources == ["ecb", "treasury", "noaa", "usgs"]          # 残りの取得元も取る
    assert any("cli.adjust" in c for c in calls)                   # 調整を飛ばさない
    assert any("cli.check" in c for c in calls)
    assert "cli.live_status" in calls[-1]
    assert "外部 ecb の取得に失敗" in p.stdout
    assert "取得に失敗した外部系列: ecb" in p.stdout


def test_rc_comes_from_live_status(tmp_path):
    p, _ = _run(tmp_path, fail_source="ecb", status_rc=2)          # 古すぎる ＝ live_status の rc 2 をそのまま返す
    assert p.returncode == 2


def test_no_failure_no_warning(tmp_path):
    p, calls = _run(tmp_path, fail_source="none")
    assert p.returncode == 0
    assert "取得に失敗" not in p.stdout


def test_no_exog_skips_exog(tmp_path):
    p, calls = _run(tmp_path, "ecb", 0, "--no-exog")
    assert p.returncode == 0
    assert not any("--exog exog_live --source" in c for c in calls)
    assert any("cli.adjust" in c for c in calls)


@pytest.mark.parametrize("step", ["cli.adjust"])
def test_adjust_failure_still_stops(tmp_path, step):
    """調整の失敗は今までどおり止まる（rc 1）＝ 緩めたのは外部系列の取得だけ。"""
    fake = tmp_path / "fakepy"
    fake.write_text('#!/usr/bin/env bash\ncase "$*" in *cli.adjust*) exit 1 ;; esac\nexit 0\n')
    fake.chmod(0o755)
    live = tmp_path / "live"
    live.mkdir()
    (live / "seed.json").write_text("{}")
    env = {**os.environ, "AIL_LIVE_UPDATE_PY": str(fake), "AIL_LIVE_DATA_DIR": str(live)}
    p = subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=60)
    assert p.returncode == 1
