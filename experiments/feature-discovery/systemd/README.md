# 開発ループの自動起動（systemd の user timer）— ⚠ 入れるのも止めるのも利用者。titan でだけ

毎日 01:00 PDT（K8）に `run-devloop.sh` を起こす。中身は `cli.devloop night`（段 0 門 → 段 1 アイデア出し〔`claude -p`〕→ 段 2 作る〔K3: 試運転は人の手 ＝ `config/devloop.toml` の `[build] mode = "manual"`〕→ 段 3 回す〔`cli.queue`〕→ 段 4 選ぶ → 日誌 `docs/specs/experiments/dev-loop/log/<日付>.md` → commit〔K5〕）。規約は rules.md 31 章。

```bash
mkdir -p ~/.config/systemd/user
cp experiments/feature-discovery/systemd/ail-devloop.service experiments/feature-discovery/systemd/ail-devloop.timer ~/.config/systemd/user/
systemctl --user daemon-reload
# ⚠ まず timer の環境で claude -p が動くかを 1 度確かめる（ログインしていない user セッション ＝ ~/.claude の資格情報が読めるか）
#    ✅ 2026-10-25 titan【実測】: 絶対パスなら動いた（$0.21）。PATH に ~/.local/bin が無いので `run-devloop.sh` が足す
systemd-run --user --wait --pipe --collect env -u CLAUDECODE ~/.local/bin/claude -p "Reply with the single word OK" --output-format json --max-turns 1 --tools ""
systemd-run --user --wait --pipe --collect ~/ai-income-lab/run-devloop.sh --dry-run --stages 0,4   # 運転手そのものが timer の環境で動くか（費用なし）
# ⚠ 初回は段 4 の「上位 3」を写しておく（初回の晩に机上を足さない。cli.devloop は初回を自動で写すが、手で先に写しておいてもよい）
( cd experiments/feature-discovery && .venv/bin/python -m cli.devloop state --seed-top )
systemctl --user enable --now ail-devloop.timer
loginctl enable-linger "$USER"                    # ログインしていなくても動かす
systemctl --user list-timers | grep ail-devloop   # 次に起きる時刻
tail -f experiments/feature-discovery/devloop/logs/timer.log
```

## 報告メール（毎晩の最後に 1 通・止まった晩も。プラン `docs/plans/devloop-mail.md`）

⚠ 宛先とアプリ パスワードはリポジトリ・日誌・メモリに書かない。titan の次のファイルにだけ置く（置くのは利用者）:

```bash
mkdir -p ~/.config/ai-income-lab
cat > ~/.config/ai-income-lab/devloop-mail.env <<'ENV'
AIL_MAIL_HOST=smtp.gmail.com
AIL_MAIL_PORT=587
AIL_MAIL_USER=<送り主の Gmail アドレス>
AIL_MAIL_PASSWORD=<Google のアプリ パスワード 16 文字>
AIL_MAIL_TO=<宛先>
ENV
chmod 600 ~/.config/ai-income-lab/devloop-mail.env
( cd experiments/feature-discovery && .venv/bin/python -m cli.devloop mail-test )   # 試しの 1 通（rc 0 ＝ 送れた）
```

- アプリ パスワードは Google アカウントの「セキュリティ」→ 2 段階認証プロセス → アプリ パスワード で作る（2 段階認証が前提【推測・作るときに Google のヘルプで確かめる】）
- ファイルが無い晩は送らず、`devloop/logs/<日付>.log` に `報告メール: not-sent（設定ファイルが無い …）` の 1 行だけ
- 件名 `[ail devloop] <日付> ✅ 登録 n・n_trials a→b・新顔 k・$x` ／ `⚠ 止まった: …` ／ `⚠ 段 N が落ちた` ／ `⚠ 運転手が落ちた（rc=N）`（Python ごと落ちた晩は `run-devloop.sh` がシステムの python3 で送る）。本文の頭に「朝にやること」、続きに日誌の全文
- HTML の本文（アイデアの札・頭の 1 行・回した結果の札）とテキストの本文の 2 部構成。HTML の見本を送る ＝ `mail-test --sample`（ファイルに書くだけ ＝ `--out <道>`）・HTML をやめる ＝ `[mail] html = false`
- 送れなくてもループは止めない（結果は timer.log と状態 `nights[-1].mail`）。その晩だけ送らない ＝ `night --no-mail`・止める ＝ `config/devloop.toml` の `[mail] enabled = false`

- 止める: `touch experiments/feature-discovery/DEVLOOP_HALT`（段 0 が見る・次の晩から止まる）／ `systemctl --user disable --now ail-devloop.timer`。⚠ 実売買の `HALT` とは別のファイル
- 打ち切り（rules.md 31-4）: 段 2 が 3 晩続けて 1 本も通らない ／ 14 晩続けて登録 0 → 段 0 で止まる。解くのは `cli.devloop state --reset`（利用者）
- 段 0 は作業ツリーが汚れていると止まる（利用者の未コミットと混ぜない）。朝に Claude のセッションで作業するときは、先にコミットするか、その晩は `DEVLOOP_HALT` を置く
- 試運転（プラン 3-7）: 3 晩 ＝ 段 1 だけ自動（`[build] mode = "manual"`。段 2 は朝に手で `devloop/prompts/build.md` の手順）→ 7 晩 ＝ 全段（`mode = "claude"`）
- 費用: 段 1 ≤ $6・段 2 ≤ $5（K4。`config/devloop.toml`）。⚠ `--max-budget-usd` に当たると結果は 1 文字も返らない（費用だけ払う）
- ⚠ titan は WSL2: Windows を再起動したら `systemctl --user list-timers` が返るかを確かめる（`Persistent=false` ＝ 寝ていた晩は回らない）
