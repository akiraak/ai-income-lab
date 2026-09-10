# TODO

- [ ] 社会にインパクトを与えそうなデータを増やす（気象・地震の延長） [plan](docs/plans/impact-data.md)
  利用者の指示（2026-09-09）: **気象と地震のような社会にインパクトを与えそうなデータを増やす**
  派生元: 「予想モデルに使うデータを広く収集する」（2026-09-09 完了。[記録 §9](docs/specs/experiments/daily-data-sources.md)）
  ⚠ **着手前に決めることが 2 つある。どちらも決めずに集めると、集めた分だけ無駄になる**
  - [x] ⚠ **決めごと 1: 気象と地震は「偽薬」のまま残す**（2026-09-09。[plan §2-1](docs/plans/impact-data.md)）
    ⚠ **移さない。** ⚠ **結果を見てから枠を移すのは後付け**で、[§9](docs/specs/experiments/daily-data-sources.md) の「雑音だった」という結論を自分で崩す
    ⚠ **移すと物差しを失う。** ⚠ **偽薬が無いと「本命が効いた」を検証できない**（§9 で効いた唯一の仕掛け）
    ⚠ **新しく取るものを本命として足し、仮説を取得の前に 1 行で書く**
  - [ ] ⚠ **決めごと 2: 全銘柄で同じ値にしないこと**（⚠ **これが効かなかった一番の理由**）
    ⚠ **[§9-4](docs/specs/experiments/daily-data-sources.md) の判定**: `ex_` 層は全銘柄で同じ値になるので、⚠ **市場全体の方向にしか効きようがなく、銘柄の選択には効かない**
    ⚠ **災害は地域と業種に割り当てれば銘柄を区別できる**（ハリケーン → 保険・メキシコ湾の精製 ／ 日本の地震 → EWJ）。⚠ **割り当てを持たないなら、また同じ結果になる**
  - [x] Phase 1: 規約の確認（2026-09-09。[記録 §10-1](docs/specs/experiments/daily-data-sources.md)）
    ⚠ **NOAA SPC は `robots.txt` が `Disallow: /` なので採らない**（Stooq と同じ扱い）。⚠ **同じ事象は NCEI（公式の保管庫）から取れる**
    ⚠ **ついでに §3-4 で使った NCEI の `/access/services/` が禁止に含まれないことも確認した**
  - [x] Phase 2: ⚠ **取得して層に置いた**（2026-09-09。`python3 -m cli.fetch --exog impact_daily`。[記録 §10-2](docs/specs/experiments/daily-data-sources.md)）
    ⚠ **本命 14 系列**: NCEI Storm Events 13（全国 3 ／ 種類 6 ／ ⚠ **地域 4 の被害額**）＋ EPU 日次 1
    ⚠ **仮説を取得の前に書いた**（災害＝保険と操業停止 ／ EPU＝投資を控える）。⚠ **書けないものは本命にしない**
    ⚠ **一番大きい発見: Storm Events は公表が 101 日遅れる**。⚠ **一律 1 日ずらしだと先読みになる**ので、⚠ **ずらし幅を取得元ごとに変えられるようにした**（Storm Events は 120 日）
    ⚠ **災害も地震と同じ形**（行が無い日 = 0 件。竜巻は 1,607 日が 0）。前方埋め禁止
    ⚠ **偽薬は §9 のまま動かしていない。** 外部系列は 本命 28 ／ 偽薬 11 の計 39 本になった
  - [x] Phase 2-2: ⚠ **リアルタイムの経路を探した**（2026-09-09。[記録 §11](docs/specs/experiments/daily-data-sources.md)）
    ⚠ **「今の値が取れる」と「検証に使える」は別物だった**。⚠ **NWS の警報 API はリアルタイムだが遡れるのは 7〜14 日**
    ⚠ **検証には保管庫が要る**: IEM（`mesonet.agron.iastate.edu`）が数十年ぶん持つ。⚠ **ただし `Crawl-delay: 120`（2 分間隔）**
    ⚠ **NWS の API は `robots.txt` が `Disallow: /` だが規約が「公開データ・あらゆる目的に自由」と明記**（非商用の縛りも無い）。Open-Meteo と同じ立て方で採る
    ⚠ **USGS 地震は配信と保管庫が同じ経路にある稀な例**
  - [~] Phase 2-3: ⚠ **IEM から警報の保管庫を取る**（2026-09-09 に取得は完了。[記録 §12](docs/specs/experiments/daily-data-sources.md)）
    ✅ **取れた**: `python3 -m cli.fetch --exog impact_warnings` → 34 系列・30,977 行（130,820 事象・2018-01-01〜2026-09-08）。⚠ **公表の遅れは 1 日**（NCEI は 101 日）
    ⚠ **年ごとに `raw/iem/events/<年>.jsonl` へ保管して再開できる形にした**（7 年目でサーバに切られて 6 年ぶん 20 分を失ったため）
    ⚠ **⚠ `data/` は git 管理外。別の環境では取り直しになる**（9 回の要求 × `Crawl-delay: 120` ＝ 約 30 分）。⚠ **手で `data/raw/iem/` を持っていけば省ける**
    - [ ] ⚠ **熱の取り直し**: NWS が 2024-10 に `EH` → `XH` へ替えたので熱の系列が 2024-10-24 で止まっていた。コードは足した。⚠ **`raw/iem/events/2025-01-01_2026-01-01.jsonl` を消してから `cli.fetch --exog impact_warnings`**（2 回の要求・約 5 分。検証に使う 12 系列には影響しない）
    - [ ] ⚠ **突き合わせ**: `python3 -m cli.crosscheck --day <7 日以内の日>`（NWS の API と IEM を同じ日で比べる。⚠ **IEM への要求は直前の要求から 120 秒空ける**）。コードは書いてあり、NWS 側は動くことを確認済み。⚠ **結果は未取得**
  - [~] Phase 3: ⚠ **地域・業種への割り当て**（決めごと 2）。⚠ **コードは済み、検証は未実施**
    ✅ `ail/features/impact.py`（`im_` 層）＋ `config/exposure/us63.toml`（6 経路・仮説つき・⚠ **重みは全部【推測】・後知恵あり**）＋ `tests/test_impact.py` 17 件
    ⚠ **重みは変換の「後」に掛ける**（先に掛けると `z20` で消えて全銘柄が同じ値に戻る）。⚠ **曝露 0 は 0 で欠損にしない**
    ⚠ **偽薬は「割り当ての入れ替え」**（`im_scramble`。並べ替えなので重みの分布は同じ、付き先だけ撹乱）。`im_with_placebo` で同じ表に `im_pb_` として並べられる
  - [ ] Phase 4: 検証。⚠ **新しい偽薬を必ず併置する**（設定 5 本は済み。⚠ **回すのはこれから**）
    ⚠ **順番**: (1) 熱の取り直し → (2) `cli.build` を `impact_ex_2018` `impact_2018` `impact_placebo_2018` `impact_both_2018` の 4 本（⚠ **行数が土台 `own_impact_2018` の 131,250 と揃うこと**。揃わなければ `start_date` を合わせる）→ (3) `cli.run` を 5 本（1 本 約 10 分）→ (4) `runs/<実行>/selected.csv` で `im_` 対 `im_pb_` の選ばれ方（偽発見率）→ (5) 記録 §12-4 以降・§13、台帳の吐き直し
    ✅ 土台は済み: `own_impact_2018`（`runs/2026-09-09T12-22-33_own_impact_2018`。最良は F3-3 の −0.77bp、基準の「常に上」＋0.02bp を超える手法なし）。⚠ **`runs/` も git 管理外**なので別の環境では回し直す
  ⚠ **やり直さなくてよいこと**: 取得の作りは済んでいる（`ail/data/sources/` に 1 ファイル足すだけ ／ `--exog` で層に載る ／ 1 日ずらしと 0 埋めの規約は `ail/features/exog.py`）
  ⚠ **資金は動かさない**（2026-08-27 の方針）。⚠ **数字は【実測】/【公表値】/【推測】を明示する**
  関連: [daily-data-sources.md](docs/specs/experiments/daily-data-sources.md) ／ [rules.md](docs/specs/experiments/feature-discovery/rules.md) ／ [market-data-availability.md](docs/specs/market-data-availability.md)

- [ ] データの取得元を広げる（判断待ちの 2 件）
  ⚠ **「予想モデルに使うデータを広く収集する」から切り出した**（2026-09-09 に本体は完了。[DONE](DONE.md)）
  ⚠ **どちらも私には決められない。** ⚠ **取れないのではなく、本プロジェクトをどう位置づけるかで決まる**
  - [ ] ⚠ **(1) FRED の無料 API キーを取るか**（読み取り専用の登録。売買口座の登録とは別物）
    ⚠ **取らなくても金利と商品は揃っている**（イールドは米財務省から、商品は ETF で取得済み）
    ⚠ **FRED でしか手軽に取れないのは 3 つ**: 信用スプレッド ／ 商品の現物価格（ETF とは別物）／ 1962 年からの長い履歴
    ⚠ **4 つ目が増えた（2026-09-09）: 雇用統計・CPI の発表日**（FRED の releases API）。⚠ **BLS 直接は robots.txt 自体が 403 で採れない**（[記録 §1](docs/specs/experiments/econ-calendar.md)）
  - [ ] ⚠ **(2) 本プロジェクトを「非商用」と言えるか**（目的が「収入を稼ぐ方法の体系化」なので言い切れない）
    ⚠ **効くのは Open-Meteo と SILSO（太陽黒点 76,214 行・1818 年〜）。** ⚠ **天気は NOAA で代替済み**
  - [ ] 判断が付いたら: 連邦準備 H.10 の URL 確定 ／ 地磁気の長期 ／ 黒点の公有経路 ／ GDELT の規約
  関連: [記録 §4](docs/specs/experiments/daily-data-sources.md) ／ [market-data-availability.md](docs/specs/market-data-availability.md)

- [ ] tastytrade で、実際の API 取引のサンプルプログラムを動かす [plan](docs/plans/tastytrade-api-sample.md)
  - 対象は [docs/specs/service-trust-assessment.md](docs/specs/service-trust-assessment.md) の判定「高」で、[docs/specs/trading-fee-comparison.md](docs/specs/trading-fee-comparison.md) §4 で株 $0・API プレミアム $0、常駐プロセス不要の tastytrade 1 社。moomoo・IBKR は 2026-09-04 に対象から外した（再開条件はプラン §1-2）
  - 動かす範囲: 認証 → 口座照会 → 現在値 → 指値・取消 → 約定・反対売買 → ストリーミング、の 6 手順を cert（sandbox）環境で
  - **2026-09-05: 方針は (c)（sandbox ＋ 本口座 ＋ 入金 ＋ 本番で 1 株）に決定**。CLAUDE.md にこの 1 手法だけの例外として追記済み。⚠ 口座開設・入金・本番発注を実行するのは利用者（Claude は手順とコードまで）
  - ✅ 2026-09-05: sandbox（$100,000）と本口座の資格情報が揃い、`.env` に設定済み。**待ちは市場時間だけ**
  - [x] Phase 0: 方針の決定と cert 環境の入口の確認（OAuth パネルは実装済みと確認。公式 SDK は archived で OpenAPI 直叩きに決定。入金なしの quote token と米国内承認日数は未解決のまま残す）
  - [x] Phase 1: 環境と記録形式（venv・JSONL・`.env` と `.gitignore`・cert / prod の取り違え防止・モックによる自己検査）
  - [x] Phase 2: 認証（OAuth2。cert・prod とも交換できた。`expires_in` 900 / JWT 954 秒）
  - [x] Phase 3: REST の 4 手順（2026-09-08 に手順 5 の約定・建玉・反対売買まで本物の cert で通した）
  - [x] Phase 4: ストリーミングとレート制限（口座ストリーマ・DXLink・429 ＋ 認証寿命 900 秒の実測まで済み）
  - [x] **9/8（火）の市場時間に回した**（[記録 §0-4](docs/specs/experiments/tastytrade-api-sample.md)）— **6 観点のうち 5 つが ✅**。残るは A（営業日を 2 日跨ぐ交換）だけ
    - [x] `--step cleanup` → `--step 4` / `--step 5`: ✅ **通った**（14:52〜14:53 ET。`final_Cancelled` と `buy_Filled/sell_Filled`、SPY 1 株を $766.47 で建てて解消）。⚠ **その 25 分前は `Session offline` で拒否された**（同時刻の `market-time` は `Open`、余力 $200,000）。一時的な状態がある
    - [x] `--step 3` で気配の遅延: ✅ **サーバの時計で −0.12 秒**（12 回の中央値。ばらつき 0.24 秒）。⚠ こちらの時計では −1.14〜+0.65 秒と 1.8 秒揺れる（**WSL2 の時計**）。`sample.py` が `delay_corrected_s` を記録し、判定 C はそちらを優先するようにした。DXLink 229 イベント
    - [x] `--step 6 --seconds 60`: ✅ ack 123 ms、60 秒で 5 メッセージ（Order 通知 2）
    - [x] `--step rate`: ✅ 60 回/分・30 連射とも 429 なし、中央値 133.9 ms
    - [x] `--step 1 --verify-expiry`: ✅ **920 秒待って 401** → **900 秒で失効**。⚠ 「954 秒」は `exp − iat` の読み違いだった（`iat` は grant 作成時刻の固定値。2 本のトークンで同じ値）
    - [x] 9/5 に取った refresh token がそのまま使えるか / cert の 24 時間リセット: ✅ **どちらも残った**（同じ口座・残高は $100,000 にリセット）
    - [x] ⚠ `Session offline` は**一時的**と確認（25 分後に成功）。自動売買では「注文の中身が悪い」と読まず時間をおいて再送する設計が要る
  - [x] Phase 5: 記録と判定（2026-09-08。[記録](docs/specs/experiments/tastytrade-api-sample.md) の 6 観点・訂正候補 15 件・未実測 4 件、overview §4/§6 と CLAUDE.md への反映）
    - ⚠ 観点 A だけ ⏳。g3plus の管理画面が sandbox に繋いで監視を回しているので、**翌営業日に自動で ✅ になる**。9/9 に `/judge` を見る
  - [ ] **着金の確認（$1,000 / SoFi → tastytrade、2026-09-05 送金指示）**
    - 着いたら `sample.py --step probe` をもう 1 回回し、着金前（[記録 §0](docs/specs/experiments/tastytrade-api-sample.md)）との差分を取る
    - 見るもの: `cash-balance` が 0.0 → 1000.0 になるか、`pending-cash` が消えるか、`cash-available-to-withdraw` がいつ立つか（＝ ACH の保留期間の実測）、`available-trading-funds` が 0.0 のままか
    - ⚠ 着金前の状態はもう測れない。⚠ 9/7 は Labor Day のため、着金は 9/8（火）以降の見込み
  - [ ] Phase 6（方針 (c)）: 本番口座で 1 株（入金と発注は利用者が行う）
    - ⚠ 2026-09-05 の dry-run で **着金前でも 1 株は通る**ことが分かっている（買付余力 1000.0 が効き、`available-trading-funds` 0.0 は効かない）。着金を待つ必要は無い

- [ ] 自宅の外から SSH で GPU 機（WSL2）に入れるようにする（Tailscale） [plan](docs/plans/remote-ssh-tailscale.md)
  利用者の指示（2026-09-09）: **WSL2 の GPU 機に家の外から SSH で接続して作業したい。Tailscale で進める**
  ⚠ リポジトリのコードには触れない（変更先は WSL の sshd 設定・Windows のスタートアップ・電源設定・Tailscale 導入）
  ⚠ 調査済み（2026-09-09）: mirrored モード・ssh.socket 待受・FW 許可まで整っている。残りは経路・認証・常時稼働の 3 つ
  📌 **引き継ぎ（2026-09-09 夜・Sx360 → titan）**: ノート PC `Sx360` 側の作業は全部済み（Tailscale ログイン・鍵・ssh config・WezTerm・外の回線からの実測）。残りは下の未チェック 4 つで、**titan の Windows / WSL で行うのは「vbs をスタートアップへ」と「未使用の鍵を外す」**、利用者がどこからでも行えるのが「パスフレーズ」（⚠ これだけは Sx360 で）と「Google の 2 段階認証」。4 つが済んだら親タスクを DONE へ移し、プランを archive へ
  📌 2026-09-10 08:46 titan で確認: 残りは「vbs をスタートアップへ」（titan）と「パスフレーズ付与」（Sx360）の 2 つで、**どちらも利用者の作業**（スタートアップへの書き込みは 2026-09-09 に権限でブロック済みのため Claude は行わない）。`shell:startup` に vbs は未配置を実測（DeepL のみ）。受け側は健在（`ssh.socket` active・tailnet に `titan` / `sx360` の 2 台）
  📌 2026-09-10 追記: 「ログオン不要で電源オンだけで上がる」タスクスケジューラ版を Phase 2 に追加。利用者の指示: **先にログオンする vbs 版を通し、その後こちらへ切り替える**
  - [x] Phase 1: WSL 側の受け入れ（2026-09-09）
    ✅ 鍵 `~/.ssh/remote-client-ed25519` を生成し `authorized_keys` へ登録。`sshd_config.d/60-remote-access.conf` で鍵認証のみに固定
    ✅ ループバックで実測: 鍵で通過・鍵なしは `Permission denied (publickey)`・SSH セッションから CUDA 動作（torch `cuda: True`）
    ⚠ **SSH セッションには WSL の PATH（`/usr/lib/wsl/lib`）が乗らない**。`nvidia-smi` は `/usr/local/bin` へ symlink で解決済み
  - [~] Phase 2: Windows 側の常時稼働
    ✅ スリープは設定済みだった（AC/DC とも「なし」を実測）
    ⚠ **スタートアップへの書き込みは権限でブロックされた**（自動起動の常駐設定は利用者が置くべきという趣旨）
    ✅ 利用者が titan のスタートアップに `wsl-autostart.vbs` を置いた。Sx360 から確認（2026-09-10）: `ssh titan` が通り、Windows の起動 09:08:20 → 常駐の `sleep infinity`（PID 641）の開始 09:09:21 で、再起動からログオン直後に vbs が WSL を上げている。titan に触らずに `ssh titan` が通った
      ⚠ ログオンするまで WSL は上がらない。無人で使うなら自動サインイン（`netplwiz`）が別途要る
    - [ ] 利用者: ログインせず電源オンだけで WSL が上がるようにする（タスクスケジューラ）
      依存: 「titan の `C:\Users\akira\wsl-autostart.vbs`（2026-09-09 に scratchpad から退避）を `shell:startup` にコピーし、Windows を再起動して Sx360 から `ssh titan hostname` が通ることを確認」（2026-09-10 に完了。上の ✅）
      方針（2026-09-10・利用者の指示）: 先にログオン前提の vbs 版を通し、通ってからこちらへ切り替える。Tailscale は Windows のサービスなのでログオン前から繋がっており、ログオンを待っているのは WSL（sshd）だけ
      手順（titan のタスクスケジューラ → 「タスクの作成」）:
      1. 全般: 名前 `wsl-autostart`。「**ユーザーがログオンしているかどうかにかかわらず実行する**」を選ぶ（「パスワードを保存しない」はオフのまま）
      2. トリガー: 新規 → 「スタートアップ時」
      3. 操作: 新規 → プログラム `C:\Windows\System32\wsl.exe`、引数 `-d Sandbox24 --exec sleep infinity`
      4. 条件: 「コンピューターを AC 電源で使用している場合のみ〜」のチェックを外す
      5. 設定: ⚠ 「**タスクを停止するまでの時間**」（既定 3 日）のチェックを外す。外さないと 3 日後に常駐が殺されて SSH が落ちる
      6. OK でアカウントの**パスワード**を入力（⚠ PIN 不可。Microsoft アカウントならそのパスワード。パスワードレス運用だと保存できない → その場合の代替は `netplwiz` の自動サインイン）
      確認: 再起動して titan に触らず、Sx360 から `ssh titan hostname`。通ったら「シャットダウン → 電源オン」でも同じ確認（⚠ 高速スタートアップが有効だと電源オフ→オンで「スタートアップ時」トリガーが発火しないことがある → `powercfg /h off` で無効化）
      ⚠ 通ったら `shell:startup` の vbs は外す（二重起動を避けて片方に揃える）
      ⚠ アカウントのパスワードを変えたらタスクに入れ直しが要る
  - [~] Phase 3: Tailscale の導入とログイン
    ✅ winget で v1.102.3 を導入し、アカウント連携も完了（2026-09-09。デバイス `titan` / `100.82.194.13` / `titan.tail061b58.ts.net`）
    - [x] 利用者: ログイン URL をブラウザで開いてアカウント連携（2026-09-09）
    - [x] 利用者: Tailscale アカウントに 2 要素認証を付ける（2026-09-09。Google アカウントの 2 段階認証がオンであること、管理画面の端末が `titan` と `sx360` の 2 台だけであることを利用者が確認）
      ⚠ Tailscale 自体に 2FA は無く、ログインに使った ID プロバイダ（Google `akiraak@gmail.com`）の 2 段階認証がそれに当たる
    - [~] 利用者: 外で使う端末（ノート PC・スマホ）に Tailscale クライアントを入れ同じアカウントでログイン
      ✅ ノート PC `Sx360`（WSL2 mirrored・Windows ユーザー `akira`）に winget で v1.102.3 を導入（2026-09-09）
      ✅ `Sx360` のログイン完了（2026-09-09。`sx360` = `100.119.134.116`。WSL から MagicDNS 名が引け、`tailscale ping titan` は LAN 直通 7ms）
    - [~] 端末側の鍵。⚠ **方針を変えた: titan の `remote-client-ed25519` を運ばず、定石どおり端末側で生成する**（titan は鍵なしを拒否するので、鍵を運ぶ経路が無い）
      ✅ `Sx360` の WSL で `~/.ssh/titan-ed25519` を生成し、WezTerm 用に `C:\Users\akira\.ssh\` にも置いた（2026-09-09）。ssh config に `titan`（tailnet・MagicDNS 名）/ `titan-lan`（LAN 直結の逃げ道）を WSL・Windows 両方に追加
      ✅ 利用者が titan の `authorized_keys` に `Sx360` の公開鍵を追記（2026-09-09）
      - [ ] 利用者: `Sx360` でパスフレーズを付与。3 か所。⚠ 2026-09-09 20:40 時点で 3 つとも未付与を実測
        手順（Sx360 で。同じパスフレーズでよい）:
        1. WSL のターミナルで `ssh-keygen -p -f ~/.ssh/titan-ed25519`
        2. 同じく `ssh-keygen -p -f ~/.ssh/gpu-home-ed25519`
        3. PowerShell で `ssh-keygen -p -f "$env:USERPROFILE\.ssh\titan-ed25519"`（WezTerm の ssh_domains が使う方）
        4. 確認: `ssh-keygen -y -P "" -f ~/.ssh/titan-ed25519` がエラーになれば付いている
        ⚠ 付けたあとは接続のたびに入力を求められる。Claude のセッションから `ssh titan` を使う前に `eval "$(ssh-agent -s)" && ssh-add ~/.ssh/titan-ed25519`
      ✅ titan の未使用の鍵 `remote-client-ed25519` を `authorized_keys` から外し、鍵ファイルも削除（2026-09-09。バックアップ `authorized_keys.bak-20260909`）。残るのは Sx360 の 2 本（`titan-ed25519` = ssh config の `titan` が使う ／ `gpu-home-ed25519` = 利用者が 20:32 に作成）。スマホ用の鍵は使うときにスマホ側で作る
  - [x] Phase 4: 接続試験（2026-09-09。残っていた外の回線からの実測が通った）
    ✅ 同一マシン内から tailnet の IP（`100.82.194.13`）で SSH → GPU まで到達を実測（2026-09-09。mirrored が Tailscale の面を eth2 として WSL に映しており、懸念だった干渉は起きていない）
    ✅ `Sx360` から LAN 経由で 22 番に到達し、鍵なしは `Permission denied (publickey)` を実測（2026-09-09。別ホストから Windows FW を跨いで WSL の sshd に届いている）
    ✅ `Sx360` から tailnet 経由で titan の sshd に到達し、鍵なしは `Permission denied (publickey)` を実測（2026-09-09。**別ホスト → Tailscale → Hyper-V FW → WSL の経路が通っている**。残る差は NAT 越えだけ）
    ✅ `Sx360` から tailnet 経由の `ssh titan` で鍵が通り `nvidia-smi` に RTX 3090 Ti（24564 MiB・ドライバ 610.62）が出た。`titan-lan` も通り、別の鍵は引き続き拒否（2026-09-09）
    ✅ **外の回線から通った**（2026-09-09。`Sx360` をスマホのテザリング `172.20.10.x` にし、自宅 LAN の 22 番に届かないことを確認したうえで `ssh titan` → `nvidia-smi -L` に 3090 Ti。経路は最初 DERP（sea）中継で `tailscale ping` 208〜244ms、`ssh` の往復 2.1s。⚠ **その後 10 往復のうちに直結へ切り替わり 78ms**（titan 側の自宅グローバル IP:58005 へ）。繋ぎ始めの数秒は中継で遅く、以後は直結になる）
    - [x] WezTerm の ssh_domains で接続できること（2026-09-09）
      ✅ deco-tarm に端末ごとの `ssh.local.lua` を読む仕組みを足し（自動生成の `SSH:<host>` は残す）、`Sx360` に `titan` / `titan-lan` を配置。`wezterm connect titan` で開いた窓の `nvidia-smi` が titan 側に記録され、3090 Ti が見えた。⚠ deco-tarm 側は未コミット
      ⚠ 起動中の WezTerm には新しいドメインが出ない（再読み込みでは増えない）。`wezterm connect titan` か起動し直しで出る

- [ ] moomoo・IBKR の実検証（tastytrade と同じ 6 手順・6 観点で横並びにする）
  - 背景: 2026-09-04 に 3 社 → tastytrade 1 社へ絞ったときの**再開条件**（[plan §1-2](docs/plans/tastytrade-api-sample.md)）に当たる。債券・外国株・FX まで同じ口座で試したいなら IBKR、PFOF なしの執行を試したいなら moomoo
  - 使い回せるもの: `experiments/tastytrade-api-sample/` の記録形式は `venue` 列を持ち、`ttclient.py` の関数名は会場に依存しない（`authenticate` / `list_accounts` / `get_quote` / `dry_run_order` / `submit_order` / `cancel_order`）。**同じ名前で別モジュールを書けば `sample.py` の 6 手順はそのまま動く**
  - ⚠ **方針の例外は tastytrade 1 社にしか掛かっていない**（[CLAUDE.md](CLAUDE.md) の 2026-09-05 追記）。口座開設・入金を伴うなら Phase 0 で改めて決める
  - ⚠ **moomoo は Web 規約が robot 禁止（R1）**（[trading-api-availability.md](docs/specs/trading-api-availability.md) 付録）。API 経由の自動売買が許されるかを、着手前に規約の一次情報で確認する。ここが黒なら moomoo は打ち切り
  - ⚠ **費用が同じでない**: IBKR は株 40 往復/月 ＋ API プレミアムで **$84.50 ＋ 残高 $500**【推測】、moomoo は $0（プロモ中）、tastytrade は $0（[trading-fee-comparison.md](docs/specs/trading-fee-comparison.md) §4）。IBKR は「払ってでも広い品揃えを取るか」の判断になる
  - [ ] Phase 0: 方針と入口の確認
    - 口座開設・入金をどこまで許すか（tastytrade と同じ (a)/(b)/(c) の選択）。⚠ IBKR は最低残高の条件があるので、開設だけで済むかを先に確認
    - 各社の一次情報: 発注 API の版と仕様書の所在、公式 SDK の保守状況（最終リリース日）、sandbox / paper 口座の作り方、認証方式と資格情報の作り方
  - [ ] Phase 1: IBKR — **最大の不確実性は「常駐プロセスが WSL2 で動くか」**
    - Gateway / TWS が **Linux ヘッドレスで起動するか**（GUI ログインを迂回できるか）。⚠ ここが動かなければ観点 B は ❌ で、無人運転の前提が崩れる
    - REST（Client Portal 系）と TWS API のどちらを使うか、認証の更新に人手が要るか（tastytrade は refresh token で無人だった）
    - paper 口座は本口座から作る必要があるか
  - [ ] Phase 2: moomoo — **OpenD 常駐と 2 要素**
    - OpenD が Linux ヘッドレスで動くか、SMS ＋ デバイスロックが無人運転を止めないか
    - 模擬取引が OpenD 経由でどこまで再現できるか（tastytrade の cert 相当）
  - [ ] Phase 3: 6 手順を同じ記録形式で回す（認証 → 口座照会 → 現在値 → 指値・取消 → 約定・反対売買 → ストリーミング）
    - ⚠ tastytrade で分かった落とし穴を各社でも確かめる: 認証トークンの実寿命、websocket の認証ヘッダの形、**時間外に発注・約定できるか**、レート制限、エラー本体の残し方
  - [ ] Phase 4: 3 社の横並び判定
    - 観点 A〜F（認証の寿命・常駐・現在値・往復・レート制限・SDK）を 1 表にし、**費用込みで「無人で 1 営業日回る」会場**を選ぶ
    - 品揃え（債券・外国株・FX・先物）と信用判定（IBKR は「中」）を並べ、tastytrade を置き換える理由があるかを判断する
