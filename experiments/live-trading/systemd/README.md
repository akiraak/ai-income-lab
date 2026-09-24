# 毎日の自動起動（systemd の user timer）— D16。⚠ 入れるのも鍵を置くのも利用者

✅ 2026-09-20 の利用者決定 D4: **timer は水曜（9/23）から**。火曜は手で起動して 1 回成功を見てから入れる。→ ⚠ 手動の初日が 9/23（水）になったので **timer は木曜（9/24）から**（2026-09-22）。✅ 9/23 の手動の投入は成功（10 本 Filled・差 0）＝ 入れてよい。⚠ **まず dry-run のまま 1 日**。

| timer | いつ | 何を |
| --- | --- | --- |
| `ail-live-prepare.timer` | 平日 09:00 ET | `run-live.sh --prepare`（日足 ＋ 外部系列。発注しない・鍵は要らない） |
| `ail-live-run.timer` | 平日 15:40 ET | `run-live.sh … --wait`（15:50 ET まで待ってから 日足 → 予測 → 執行器） |

```bash
mkdir -p ~/.config/systemd/user ~/.config/ai-income-lab
cp experiments/live-trading/systemd/ail-live-*.service experiments/live-trading/systemd/ail-live-*.timer ~/.config/systemd/user/
cp experiments/live-trading/systemd/live.env.example ~/.config/ai-income-lab/live.env && chmod 600 ~/.config/ai-income-lab/live.env
$EDITOR ~/.config/ai-income-lab/live.env          # ⚠ まず dry-run のまま 1 日回す。発注に切り替えるのは利用者
systemctl --user daemon-reload
systemctl --user enable --now ail-live-prepare.timer ail-live-run.timer
loginctl enable-linger "$USER"                    # ログインしていなくても動かす
systemctl --user list-timers | grep ail-live      # 次に起きる時刻
tail -f experiments/live-trading/out/timer-run.log
```

- 止める: `systemctl --user disable --now ail-live-run.timer`。⚠ **本番を 13500t へ切り替える日（Phase 4）は、止めたあと `live.env` から発注の許可を外し、`notprod.py set` で「本番の機械ではない」印を置く**（手順は [live-trading.md §0-14](../../../docs/specs/experiments/live-trading.md)。印があれば timer が残っていても本番の発注は rc=7 で拒まれる）。⚠ **急ぐときは管理画面の停止ボタン（`HALT`）が先**（timer が起こしても執行器は発注しない）
- 休場日・半日立会は執行器が自分で拒否して `events.jsonl` に `out_of_window` を残す（暦は `nyse_calendar.py`。⚠ 年に 1 度、次の年を足す）
- ⚠ **titan は WSL2**: Windows を再起動したら WSL が起きているか（`systemctl --user list-timers` が返るか）を確かめる。寝ていた時刻の回は実行されない（`Persistent=false` ＝ 窓を過ぎてから起きても発注させない）
- ⚠ シミュレーションモード（`MODE` が sim）の機械では、執行器が鍵があっても起動を拒否する（rc=5）
- systemd が使えない環境では cron でも同じ（`CRON_TZ=America/New_York` ／ `40 15 * * 1-5 cd ~/ai-income-lab && set -a && . ~/.config/ai-income-lab/live.env && set +a && ./run-live.sh …`）
