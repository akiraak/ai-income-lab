# titan で動かした vibeboard を外（tailnet）から見る（tailscale serve ＋ Tailscale Services）

作成: 2026-09-10（同日、サービス名の URL を追記。dashboard は公開しないと決定）／ 完了: 2026-09-10（archive へ移動）

## 目的・背景

Sx360 から `ssh titan` で入り、titan の `ai-income-lab` で `./run-vibeboard.sh`（開発管理画面、3010）を動かしたとき、そのページを Sx360（や自分の他の端末）のブラウザで開けるようにする。URL は `http://titan-income-vibeboard` のように**サービス名が入る形**にする。

利用者の指示（2026-09-10）: **titan に client から ssh 接続して run-vibeboard.sh を動かしたときに外部からページが見れるようにする** ／ 同日追記: **アクセスする URL は `http://titan-income` のようなサービス名が URL に入る形にできないか検討する** → サービス名は **`titan-income-vibeboard`** に決定 ／ **`run-server.sh`（dashboard）は危険なので公開しない**（下の「対象外」）

派生元は「自宅の外から SSH で GPU 機（WSL2）に入れるようにする（Tailscale）」（2026-09-10 完了。[運用メモ](remote-ssh-tailscale.md)）。SSH の経路はできているので、**その上に HTTP を 1 本通す**のがこのタスク。

2026-09-10 に確認した現状:

| 項目 | 状態 |
|---|---|
| vibeboard の待ち受け | `127.0.0.1` に固定（`vibeboard/src/config.ts` の `host: '127.0.0.1'`）。`--host` も `vibeboard.config.json` の `host` も無い。`run-vibeboard.sh` は `--port` しか通さない |
| Tasks タブの登録先 | `vibeboard/scripts/session-hook.mjs` が `127.0.0.1:<VIBEBOARD_PORT → config の port → 3010>` に POST する。**ポートを変えると Claude セッション側にも `VIBEBOARD_PORT` が要る** |
| ポートの取り合い | 同じ root の vibeboard を起動し直すと portGuard が前のプロセスを止めて置き換える。別 root なら止まる |
| titan のネットワーク | WSL2 mirrored（Windows と同一ポート空間）。Windows 側の Tailscale は `--unattended` 済み。Tailscale の面は WSL に `eth2` として映る |
| titan への到達 | tailnet `titan.tail061b58.ts.net`（100.82.194.13）／ LAN `titan.lan`（10.0.1.81）。22 番は Windows FW と Hyper-V FW で許可済み（**Hyper-V FW の既定 inbound は Block**） |
| WezTerm | `wezterm connect titan` の mux ドメイン。ペインの中のプロセスは切断しても titan に残る |
| vibeboard の出自 | upstream `akiraak/vibeboard` の vendor。改造は upstream に入れて `vibeboard update --from` で配る |

### 対象外: dashboard（`run-server.sh`）は公開しない（2026-09-10 決定）

dashboard は接続元 IP で面を決めていて（`dashboard/app/access.py`）、ループバックなら無条件にローカル面（cert の発注・解除・鍵・開発）を開く。`tailscale serve` は titan の tailscaled が接続を終端して**ループバックから**アプリに繋ぐので、tailnet のどの端末から来ても全部ループバックに見え、**serve の先に置いた瞬間に発注の面が無認証で開く**。公開面を別に作れば出せるが（`Tailscale-User-Login` を照合する 4 つ目のモード）、**利用者の判断で公開しない**ことにした。

- ⚠ **titan の tailscaled に 3012 の serve も Services の端点も作らない**（テスト T14 で「無いこと」を確認する）
- 外から dashboard を見たいときは `ssh -L 3013:127.0.0.1:3012 titan` で手元の `http://localhost:3013` を開く（ssh の鍵の内側なのでローカル面が出てよい）

## 対応方針

**経路は `tailscale serve`、名前は Tailscale Services で付ける。** titan の Windows 側 tailscaled が tailnet からの接続を終端し、WSL の `127.0.0.1:3010` へ渡す。vibeboard と `run-vibeboard.sh` は**変更しない**。

### 2-1. 経路の 3 候補（TODO の候補をここで 1 つに絞った）

| 候補 | コード変更 | FW 変更 | 見える範囲 | ssh を切ると | 判定 |
|---|---|---|---|---|---|
| (a) `ssh -L` で手元に引く | なし | なし | その ssh を張った端末だけ | 消える。WezTerm の mux ドメインは `-L` を張れない【推測】ので別の `ssh -N` が常に要る | **逃げ道**（最初の 1 分で使える） |
| (b) vibeboard に `--host` を足して Tailscale の面に bind | upstream 改造 ＋ 2 プロジェクトへ配布 | Hyper-V FW に 3010 の許可 | bind 先しだい（100.x に限れば tailnet） | 残る | **落とす**。(c) に無い利点が無く、手数だけ多い |
| (c) `tailscale serve` | なし | なし（tailscaled が終端して loopback へ渡す） | tailnet の全端末（LAN には出ない） | 残る。titan の再起動もまたぐ | **採る** |

【公表値】`tailscale serve --bg` は無効化するまで常駐し、**端末の再起動や `tailscale down` / `up` のあとも自動で再開する**。`--http=<port>` は平文 HTTP を張る指定で、`http://<ノード名>` の短い MagicDNS 名で開ける。制限があるのは macOS（App Store 版）だけで Windows の記載はない。serve は tailnet の中だけに出る（インターネットに出すのは Funnel）（出典: https://tailscale.com/kb/1242/tailscale-serve 、2026-09-10 取得）

ポートは **3010 のまま**にする。理由は hook の既定が 3010 で、変えると Claude セッション側の設定も要るから。同じ root の起動し直しは portGuard が前のを置き換えるが、それを叩くのは利用者自身なので問題にしない。TODO のメモにある「3011 など別ポート」は、**逃げ道 (a) で Sx360 の手元ポートを選ぶときだけ**の話（Sx360 の 3010 は Sx360 自身の vibeboard が使う）。

### 2-2. URL にサービス名を入れる: Tailscale Services

`http://titan:3010` の形は「ノード名 ＋ ポート」で、サービス名は入らない。サービス名を入れる方法を 4 つ比べた:

| 方法 | URL の形 | 仕組み | 判定 |
|---|---|---|---|
| ノード名 ＋ ポート | `http://titan:3010` | serve `--http=3010` | Phase 1〜2 の**足場**（名前が付くまでの形） |
| ノード名 ＋ パス | `http://titan/vibeboard` | serve `--set-path` | **落とす**。vibeboard は `/api/...` `/files` の絶対パスで組んであり、パスの下に置くと壊れる |
| 自前 DNS ＋ 逆 proxy | `http://titan-income-vibeboard` | tailnet の DNS 設定に自前のネームサーバ、WSL に Caddy 等で Host 振り分け | **落とす**。serve は Host ヘッダで振り分けないので常駐が 2 つ増える |
| **Tailscale Services** | `http://titan-income-vibeboard.tail061b58.ts.net`（短い `http://titan-income-vibeboard` は【要実測】） | サービスに **MagicDNS 名と専用の仮想 IP** が付く。titan が `tailscale serve --service=svc:titan-income-vibeboard --http=80 3010` で広告し、管理画面で承認 | **採る** |

【公表値】Tailscale Services は「MagicDNS 名・TailVIP（仮想 IP）・リソース定義・1 つ以上のホスト」の組。**管理画面でサービスを先に定義**し、ホストは `tailscale serve --service=svc:web-server --http=80 3000` の形で端点を広告する（**自動で background モード**になる）。ホストは **v1.86.0 以上**、アクセスする側は **v1.94 以上なら `accept-routes` が不要**。Layer 3 の端点だけ Linux 限定で、Layer 4 / 7（serve）に OS の制限は書かれていない。TCP のみ。方針ファイルの `grants` で `"dst": ["svc:web-server"]` を許し、`autoApprovers.services` で承認を自動化できる（出典: https://tailscale.com/kb/1552/tailscale-services 、2026-09-10 取得）

- サービス名は **`titan-income-vibeboard`**（利用者の指定、2026-09-10）。端点は `tcp:80` の 1 つで、URL にポートを入れない
- ⚠ **短い名前 `http://titan-income-vibeboard` で引けるかは docs に記載がない**（例は全部 FQDN）。ノードは検索ドメインで短い名前が引けるので同じ zone のサービスも引ける【推測】→ Phase 3 で実測し、引けなければ FQDN で使う
- Services が使えない（版が古い・承認が通らない）場合は、ノード名 ＋ ポートの形（Phase 1〜2）で運用する

経路は tailnet → Windows の tailscaled が終端 → loopback → WSL の vibeboard で、**vibeboard は 127.0.0.1 から動かさず、dashboard は serve の先に置かない**:

```mermaid
flowchart LR
    B["Sx360 のブラウザ<br>http://titan-income-vibeboard"]
    subgraph win["titan / Windows 11"]
        T["tailscaled<br>serve（svc:titan-income-vibeboard）"]
    end
    subgraph wsl["titan / WSL2 Ubuntu（mirrored）"]
        V["vibeboard<br>127.0.0.1:3010（変更なし）"]
        R["docs / TODO.md"]
        C["Claude Code セッション<br>（Tasks の投函先）"]
        D["dashboard<br>127.0.0.1:3012（公開しない）"]
    end
    L["LAN の端末<br>http://titan.lan:3010"]
    B -- "WireGuard（tailnet）" --> T
    T -- "loopback（mirrored で共有【要実測】）" --> V
    V --> R
    V --> C
    D -. "serve の先に置かない" .- T
    L -. "拒否（serve は tailnet の面だけ）" .-> T
```

⚠ **分岐点は「Windows 側の loopback から WSL の 127.0.0.1:3010 に届くか」**（Phase 1 Step 1-2）。mirrored モードで Windows → Linux の localhost が通るのは 0.0.0.0 に bind した場合が確実で、127.0.0.1 だけに bind した場合は【推測】。届かなければ「失敗時の代替」に落とす。

## 影響範囲

- **このリポジトリのコードは変更しない**（`vibeboard/`・`run-vibeboard.sh`・`dashboard/`・`run-server.sh` とも触らない）。文書だけ: `CLAUDE.md` の vibeboard 節に 1 行、`DONE.md`、このプランの archive 移動
- titan の Windows: tailscaled の serve 設定（再起動をまたぐ）
- titan の WSL: 何も変えない（vibeboard を起動するだけ）
- Sx360: 何も変えない（ブラウザで開くだけ。逃げ道 (a) のときだけ ssh の `-L`）
- Tailscale の管理画面（利用者）: Service を 1 つ定義・ホストの承認・`grants` に 1 項目。HTTPS 証明書は有効化しない（今回は平文 HTTP。tailnet 内は WireGuard で暗号化済み）

## Phase

### Phase 0: 決めごと（このプランで決定。利用者の確認だけ）

- 経路は (c)、名前は Tailscale Services で `titan-income-vibeboard`、ポートは 3010 のまま、**dashboard は公開しない**、**Funnel（インターネット公開）は使わない**、HTTPS は見送り
- ⚠ **vibeboard は Files の編集と Tasks の投函を持つ ＝ titan で任意の作業を走らせられる面**。tailnet に出すと、自分の Tailscale アカウントに入っている全端末から鍵なしで届く。SSH と同じ信頼境界（アカウントは 2 段階認証済み）だが、この点を利用者が了解してから Phase 1 に進む

### Phase 1: vibeboard を titan のノード名で出す（Claude が `ssh titan` で実行）

⚠ 先に `SSH_AUTH_SOCK=~/.ssh/agent.sock ssh-add -l` で鍵が載っているかを見る（無ければ利用者に `ssh-add`。[運用メモ](remote-ssh-tailscale.md)）

- Step 1-1: titan で vibeboard を起動し、WSL の loopback で 200 を取る
  - `cd ~/ai-income-lab && nohup ./run-vibeboard.sh > /tmp/vibeboard-3010.log 2>&1 &`（利用者が WezTerm のペインで叩く形でもよい。mux ドメインなので切断しても残る）
  - `curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:3010/` → `200`
  - ⚠ titan に別 root の vibeboard が 3010 を掴んでいると起動が止まる。`ss -ltnp 'sport = :3010'` で見て、**ポートから引いたプロセスを止める**（`pgrep -f` は自分に当たる）
- Step 1-2: **分岐点**。Windows 側の loopback から WSL の vibeboard に届くか
  - `/mnt/c/Windows/System32/curl.exe -s -o NUL -w "%{http_code}" http://127.0.0.1:3010/` → `200` なら先へ
  - ❌（接続拒否・タイムアウト）なら「失敗時の代替」の 2 か 3 へ
- Step 1-3: serve を張る（Windows の CLI を WSL の interop から叩く。`tailscale set --unattended` を通した経路と同じ）
  - `"/mnt/c/Program Files/Tailscale/tailscale.exe" serve --bg --http=3010 http://127.0.0.1:3010`
    - ⚠ **2026-09-10 に判明: 外向きのポートを 3010 にしてはいけない**（下の「進捗」）。mirrored では serve が押さえた Windows 側の listener（`100.82.194.13:3010`）が WSL の 3010 の bind を `EADDRINUSE` にし、以後 `run-vibeboard.sh` が起動できない。**外向きは 80**（`serve --bg --http=80 http://127.0.0.1:3010` → `http://titan/`）にして、後ろの 3010 は変えない
  - `tailscale.exe serve status` に `http://titan.tail061b58.ts.net:3010 → http://127.0.0.1:3010` が出る
  - ⚠ 権限で弾かれたら、利用者が Windows の PowerShell で同じコマンドを叩く
  - ⚠ **`funnel` は使わない**。`tailscale.exe funnel status` が空のままであること
- Step 1-4: titan の中から面ごとに確認
  - tailnet の面: `curl -s -o /dev/null -w '%{http_code}\n' http://100.82.194.13:3010/` → `200`
  - LAN の面: `curl -m 3 -s -o /dev/null -w '%{http_code}\n' http://10.0.1.81:3010/` → 接続拒否（vibeboard は loopback だけ、serve は tailnet の面だけ）

### Phase 2: Sx360 からの接続試験（curl は Claude、ブラウザと外の回線は利用者）

- Step 2-1: Sx360 の WSL から `curl -s -o /dev/null -w '%{http_code}\n' http://titan:3010/` → `200`。`http://titan.lan:3010/` → 接続拒否
- Step 2-2: ブラウザで `http://titan:3010`。docs / Files / Tasks の各タブが出る
  - SSE の確認: titan 側で `touch ~/ai-income-lab/TODO.md` → 画面が即時に更新される（serve が `text/event-stream` をそのまま通すか【要実測】。通らなければ 2 秒ポーリングに落ちるだけで壊れはしない）
- Step 2-3: Tasks タブに titan の Claude セッションが出る（`ssh titan` で `claude` を起動しておく。hook が `127.0.0.1:3010` に登録する）
  - ⚠ 投函は実際の作業を走らせるので、試すのは「説明」だけにする
- Step 2-4: 外の回線（スマホのテザリング）から同じ URL（利用者）。⚠ 繋ぎ始めは DERP 中継で遅い（前回【実測】: 往復 200ms 超 → 数往復で直結 78ms）

### Phase 3: 名前を付ける（Tailscale Services）

- Step 3-0: ⚠ **titan を tag 付きノードにする**（2026-09-10 に判明。「進捗」の (A) の手順。利用者の判断で進む）
- Step 3-1: 版の確認。titan: `tailscale.exe version` が **1.86.0 以上**（2026-09-09 に winget で入れたので満たす見込み【推測】）。Sx360: v1.102.3（≥ 1.94 ✅）
- Step 3-2（利用者・管理画面）: Services に `titan-income-vibeboard`（端点 `tcp:80`）を定義。方針ファイルに `{"src": ["autogroup:member"], "dst": ["svc:titan-income-vibeboard"], "ip": ["80"]}` を足す。⚠ `autoApprovers` は tag 付きノード向けなので、titan（利用者所有）は手で承認する
- Step 3-3（titan）: `tailscale.exe serve --service=svc:titan-income-vibeboard --http=80 http://127.0.0.1:3010`。管理画面に「承認待ち」が出るので利用者が承認
- Step 3-4（Sx360）: `curl -s -o /dev/null -w '%{http_code}\n' http://titan-income-vibeboard.tail061b58.ts.net/` → `200`。**短い名前** `http://titan-income-vibeboard/` も試す（【要実測】。引けなければ FQDN で運用し、CLAUDE.md にはそう書く）。ブラウザで Step 2-2 / 2-3 をもう一度（SSE と Tasks が Services の proxy でも動くこと）
- Step 3-5: 名前で通ったら、ノード名 ＋ ポートの serve（3010）は **外す**（`tailscale.exe serve --http=3010 off`）。扉を 2 重に開けておかない

### Phase 4: 仕上げ

- Step 4-1: 再起動をまたぐ確認。titan を再起動 → `tailscale.exe serve status` に Service が残っている → `ssh titan` で入って `./run-vibeboard.sh` → `http://titan-income-vibeboard` が開く
  - vibeboard 自体は再起動で消える。**ssh で入って叩き直すのが今回の要件**なので、無人起動（`wsl-autostart` に足す）は別タスクにする
- Step 4-2: `CLAUDE.md` の vibeboard 節に「titan では `http://titan-income-vibeboard`（Tailscale Services、Funnel なし。dashboard は出さない）」を 1 行。切り分けの型をこのプランの「運用メモ」に書き、`DONE.md` へ移して archive する

## テスト方針

各面で 200 と拒否の両方を取る。**拒否の確認（LAN・Funnel・dashboard が出ていないこと）を省かない**。

| # | どこから | 何を | 期待 |
|---|---|---|---|
| T1 | titan の WSL | `http://127.0.0.1:3010/` | 200 |
| T2 | titan の Windows（curl.exe） | `http://127.0.0.1:3010/` | 200（**分岐点**） |
| T3 | titan の WSL | `http://100.82.194.13:3010/` | 200 |
| T4 | titan の WSL | `http://10.0.1.81:3010/` | 接続拒否 |
| T5 | Sx360 の WSL | `http://titan/`（当初は `:3010`。衝突が分かって 80 に変更） | 200 |
| T6 | Sx360 の WSL | `http://titan.lan:3010/` | 接続拒否 |
| T7 | titan | `tailscale.exe funnel status` | 空 |
| T8 | Sx360 のブラウザ | TODO.md を titan で touch | 画面が即時更新（SSE） |
| T9 | Sx360 のブラウザ | Tasks タブ | titan の Claude セッションが並ぶ |
| T10 | 外の回線 | `http://titan-income-vibeboard` | 開く（利用者） |
| T11 | Sx360 の WSL | `http://titan-income-vibeboard.tail061b58.ts.net/` と `http://titan-income-vibeboard/` | 200（短い名前は【要実測】） |
| T12 | Sx360 の WSL | Phase 3-5 のあと `http://titan/` | 接続拒否（ノード名の serve は外した） |
| T13 | Sx360 の WSL | `http://titan:3012/` | 接続拒否（dashboard は出していない） |
| T14 | titan | `tailscale.exe serve status` | 3012 の serve も端点も無い |

## セキュリティ上の注意

- **Funnel は使わない**（T7）。serve も Services も tailnet の中にしか出ない
- **dashboard（3012）は serve の先に置かない**（T13 / T14）。serve 経由は全部ループバックに見えるので、置いた瞬間に発注の面が無認証で開く
- tailnet に出す ＝ 自分の Tailscale アカウントの全端末から**鍵なし**で届く。端末を失くしたら Tailscale の管理画面でその端末を外す
- 他人の端末を tailnet に共有する日が来たら、`grants` の `src` を `sx360` 等に絞る（Services なら `dst` がサービス名なので絞りやすい）
- vibeboard の `/files` は dotfiles 403 だけの守り（`.env` は読めない）。それ以外のリポジトリ内容は見える ＝ ssh で入れる範囲と同じ
- HTTPS にするなら serve / Services とも `--https=443` ＋ 管理画面で HTTPS Certificates。⚠ Let's Encrypt の証明書なので **ホスト名が公開の CT ログに載る**。今回は見送る

## 失敗時の代替

1. **すぐ凌ぐ: (a) `ssh -L`**。Sx360 で `ssh -N -L 3011:127.0.0.1:3010 titan` → `http://localhost:3011`。⚠ Sx360 の 3010 は Sx360 自身の vibeboard が使うので手元は 3011。`~/.ssh/config` の `Host titan` に `LocalForward 3011 127.0.0.1:3010` を足せば `ssh titan` のたびに張れる（WezTerm の mux ドメインは張らない【推測】。`ssh -N` を別に回す）。dashboard を見たいときも同じ形（`-L 3013:127.0.0.1:3012`）で、これが dashboard の唯一の外からの経路
2. **Step 1-2 で loopback が届かない場合: vibeboard を `0.0.0.0` に bind して serve に渡す**。upstream に `--host` / `VIBEBOARD_HOST` を足し（(b) の改造）、`run-vibeboard.sh` から `VIBEBOARD_HOST=0.0.0.0` で起動。Windows → Linux の localhost は 0.0.0.0 bind なら通る。**Hyper-V FW の既定 inbound が Block なので、LAN と tailnet の面には直接は届かず serve 経由だけになる**【推測。T4 / T6 で確かめる】
3. **コードを触らずに 2 と同じことをする: WSL 内の `socat`** で `TCP-LISTEN:3010,bind=100.82.194.13,fork TCP:127.0.0.1:3010` を回し、Hyper-V FW に 3010 の許可を足す（22 番と同じ形）。常駐プロセスが 1 つ増えるので 2 より劣る
4. Services が使えない（titan の版が古い・承認が通らない・短い名前が引けない）: ノード名 ＋ ポート（`http://titan:3010`）のまま運用し、名前は次の機会に回す
5. serve の権限で弾かれる: 利用者が Windows の PowerShell で同じコマンドを叩く

## 進捗（2026-09-10【実測】）

Phase 1 の Step 1-1・1-2 まで Claude が `ssh titan` で実施。**Step 1-3（serve を張る）は Claude からの実行が権限（分類器）で弾かれた**ので、「失敗時の代替 5」のとおり利用者が叩く。vibeboard は titan で起動したまま（pid は `ss -ltnp 'sport = :3010'` で引く）。

| # | どこから | 何を | 結果 |
|---|---|---|---|
| 前提 | Sx360 | `ssh-add -l`・`ssh titan hostname` | 鍵あり・`titan` ✅ |
| 前提 | titan | `tailscale.exe version` | **1.102.3**（Phase 3 の 1.86 以上を満たす ✅） |
| 前提 | titan | `serve status` / `funnel status` | どちらも `No serve config`（張る前） |
| T1 | titan の WSL | `http://127.0.0.1:3010/` | **200** ✅ |
| T2 | titan の Windows（curl.exe） | `http://127.0.0.1:3010/` | **200** ✅（**分岐点を通過**。mirrored で 127.0.0.1 bind のままでも Windows の loopback から届く） |
| T5 基準線 | Sx360 の WSL | `http://titan:3010/`（serve 前） | 接続拒否（curl 7。tailscaled が何も張っていない） |
| T6 基準線 | Sx360 の WSL | `http://10.0.1.81:3010/`・`http://titan.lan:3010/` | タイムアウト（curl 28。Hyper-V FW の既定 Block） |
| T13 基準線 | Sx360 の WSL | `http://titan:3012/` | タイムアウト（curl 28） |
| serve 後 | titan | `serve status` | `http://titan:3010 (tailnet only)` → `proxy http://127.0.0.1:3010`。3012 の行は無い（T14 ✅） |
| T7 | titan | `funnel status` | Funnel の行は無い（serve と同じ内容が `(tailnet only)` で出るだけ）✅ |
| T3 | titan の WSL | `http://100.82.194.13:3010/` | **接続拒否**（curl 7）。⚠ **期待が誤り**: serve は tunnel から来た接続だけを受け、自分宛ては通らない。判定は T5 で行う |
| T4 | titan の WSL | `http://10.0.1.81:3010/` | 接続拒否 ✅ |
| T5 | Sx360 の WSL | `http://titan:3010/`・`http://titan.tail061b58.ts.net:3010/` | **200**（往復 26ms）✅ |
| T6 | Sx360 の WSL | `http://10.0.1.81:3010/`・`http://titan.lan:3010/` | タイムアウト ✅（LAN には出ない） |
| T13 | Sx360 の WSL | `http://titan:3012/` | タイムアウト ✅（dashboard は出ていない） |
| API | Sx360 の WSL | `/api/docs`・`/api/tree`・`/api/tasks/windows` | 200（proxy 越しで API が通る） |
| T8 | Sx360 の WSL（curl） | `/api/files/watch?watch=TODO.md` を張ったまま titan で `touch TODO.md` | **`event: change` が約 0.5 秒で届く**（serve は SSE を素通しする）✅。⚠ `?watch=` を付けないと何も流れない設計（接続を保つだけ） |

### ⚠ 発見: serve の外向きポートを vibeboard と同じ 3010 にすると、以後 vibeboard が起動できない

Tasks に `claude が PATH にありません` が出たので vibeboard を起こし直したところ、portGuard が「ポート 3010 は vibeboard 以外に使われています: 不明」で止まった。WSL の `ss` には誰もいないのに `127.0.0.1:3010` も `0.0.0.0:3010` も bind が `EADDRINUSE`（3011・3012 は通る）。Windows の `netstat.exe` に **`100.82.194.13:3010 LISTENING`（tailscaled）** がある。**mirrored モードは Windows 側の listener があるポートを WSL に bind させない**ので、serve の外向きポート ＝ vibeboard のポート にすると、serve が残っている限り（再起動をまたいで残る）`run-vibeboard.sh` が二度と上がらない。最初の起動が通ったのは serve を張る前だったから。

- 直し方: **外向きは 80、後ろは 3010 のまま**。`tailscale serve --http=3010 off` → `tailscale serve --bg --http=80 http://127.0.0.1:3010`。URL は `http://titan/`（ポートなし）
- Phase 3 の Services も端点は `tcp:80`（仮想 IP 側）なので、この形と揃う
- ⚠ 3011・3012 は WSL で bind できることを確認済み。dashboard（3012）には影響なし
- 経路の図の「loopback（mirrored で共有【要実測】）」は実測で ✅（Windows → WSL の 127.0.0.1 は届く）。ただし **共有はポート空間にも及ぶ**のが今回の落とし穴

### 次に利用者が叩くもの（Step 1-3 の張り直し）

```bash
! SSH_AUTH_SOCK=~/.ssh/agent.sock ssh titan '"/mnt/c/Program Files/Tailscale/tailscale.exe" serve --http=3010 off && "/mnt/c/Program Files/Tailscale/tailscale.exe" serve --bg --http=80 http://127.0.0.1:3010 && "/mnt/c/Program Files/Tailscale/tailscale.exe" serve status'
```

張り直せたら Claude が titan で vibeboard を起こし（`~/.local/bin` を PATH に入れて。Tasks が `claude` を引くため）、`http://titan/` で T5 / T6 / T8 / T13 / API を取り直す。

### 張り直し後（2026-09-10【実測】。Phase 1 完了・Phase 2 の curl 分完了）

| # | どこから | 何を | 結果 |
|---|---|---|---|
| serve | titan | `serve status` | `http://titan (tailnet only)` → `proxy http://127.0.0.1:3010`。Windows の listener は `100.82.194.13:80`（tailscaled）に移った |
| bind | titan の WSL | `127.0.0.1:3010` の bind | **OK**（3010 の listener が消えたので通る） |
| T1 / T2 | titan | vibeboard 起動 → WSL / Windows の loopback | 200 / 200（起動 1 秒） |
| T4 | titan の WSL | `http://10.0.1.81/` | 接続拒否 ✅ |
| T7 | titan | `funnel status` | Funnel の行なし ✅ |
| T5 | Sx360 の WSL | **`http://titan/`**・`http://titan.tail061b58.ts.net/` | **200**（33ms）✅ |
| T12 | Sx360 の WSL | `http://titan:3010/` | 接続拒否 ✅（3010 の serve は外れた） |
| T6 | Sx360 の WSL | `http://10.0.1.81/`・`http://titan.lan/` | タイムアウト ✅ |
| T13 | Sx360 の WSL | `http://titan:3012/` | タイムアウト ✅ |
| API | Sx360 の WSL | `/api/docs`・`/api/tree` | 200 |
| T9 の土台 | Sx360 の WSL | `/api/tasks/windows` | `agents.ok: true`（`~/.local/bin` を PATH に入れて起こしたので `claude` が引ける。titan にセッションが無いので一覧は空） |
| T8 | Sx360 の WSL（curl） | `http://titan/api/files/watch?watch=TODO.md` ＋ titan で `touch` | `event: change` が 0.5 秒で届く ✅ |

ssh から titan でスクリプトを流すときの落とし穴（2 つ目）: **`bash -s` の stdin を Windows の interop バイナリ（netstat.exe・curl.exe・tailscale.exe）が食って、残りのスクリプトが実行されない**。`ssh titan 'cat > /tmp/x.sh && bash /tmp/x.sh' < x.sh` で置いてから実行し、interop の呼び出しには `< /dev/null` を付ける。

残り: Phase 2 の Step 2-2〜2-4（ブラウザ・titan の Claude セッション・外の回線）と Phase 3〜4 は利用者の手が要る（TODO の各 Phase に手順を書いた）。

### ⚠ Phase 3 の前提が 1 つ抜けていた: Services のホストは tag 付きノードでないといけない（2026-09-10）

利用者が Step 3-3 の `serve --service=svc:titan-income-vibeboard --http=80 http://127.0.0.1:3010` を叩いたら **`service hosts must be tagged nodes`** で拒否された。一次情報で確認【公表値】（出典: https://tailscale.com/kb/1552/tailscale-services ・ https://tailscale.com/kb/1068/tags ・ https://tailscale.com/kb/1337/acl-syntax 、いずれも 2026-09-10 取得）:

| 項目 | 内容 |
|---|---|
| ホストの条件 | 「Service host にするデバイスは **tag ベースの識別**でなければならない。利用者アカウントで認証したデバイスは使えない」。例外の記載なし |
| tag の付け方 | 管理画面 Machines → デバイス行の右端メニュー → **Edit tags** → Save。**再認証は不要**。先に方針ファイルの `tagOwners` に定義しておく |
| tag を付けると変わること | **利用者ベースの認証が外れる**（識別が tag に置き換わる）／ **鍵の期限が既定で無効**になる／ tag は 1 つ以上必ず残り、**利用者所有に戻すには再認証が要る** |
| ACL への影響 | `autogroup:self`（同じ利用者のデバイス同士）は **tag には効かない**。方針が `autogroup:self` に依っていれば、tag を付けた瞬間に Sx360 → titan の 22 番が落ちる。方針が既定の全許可（`src: ["*"], dst: ["*:*"]`）なら影響なし |

つまり **`http://titan-income-vibeboard` を得るには titan を tag 付き（例 `tag:titan`）にする**しかなく、それは「titan は利用者の端末」から「titan はサーバ」へ識別を変えることを意味する。SSH の到達も方針しだいで変わるので、**利用者の判断**で進める。

| 案 | 得るもの | 変わること | 手順 |
|---|---|---|---|
| **(A) titan に tag を付けて Services に進む** | `http://titan-income-vibeboard.tail061b58.ts.net`（短い名前は【要実測】） | titan の識別が tag に。鍵の期限が無効（無人機には利点）。方針に `tagOwners`・SSH の grant・Service の grant を足す | 下の「(A) の手順」 |
| **(B) Services は見送り、`http://titan/` で運用** | いまの状態そのまま | 何も変えない。URL にサービス名は入らない | Phase 3 を閉じて Phase 4 へ（プランの「失敗時の代替 4」） |

【実測 2026-09-10】`tailscale debug netmap` で Sx360 と titan の受信フィルタを読むと、どちらも「送信元 ＝ tailnet 全域（100.64.0.0/10 の分割）・宛先 ＝ 全アドレス全ポート」の 1 本だけ ＝ **方針は既定の全許可で、`autogroup:self` には依っていない**。したがって **tag を付けても Sx360 → titan の 22 番は通る**。ssh config は `titan.tail061b58.ts.net` ＋ `titan-ed25519` で、名前・IP・sshd・鍵は tag で変わらない。titan の鍵の期限は 2027-03-09（tag で無期限）。(A) の手順 1 の SSH の保険の grant は不要だが、置いても害はない。

(A) の手順（すべて利用者。Claude は (5) と (7) の実測だけ）:

1. 方針ファイルに tag と grant を足す（`grants` の 2 つ目は方針が `autogroup:self` 依存のときの SSH の保険。全許可ならなくてよい）:
   ```json
   "tagOwners": { "tag:titan": ["autogroup:admin"] },
   "grants": [
     { "src": ["autogroup:member"], "dst": ["svc:titan-income-vibeboard"], "ip": ["80"] },
     { "src": ["autogroup:member"], "dst": ["tag:titan"], "ip": ["22"] }
   ],
   "autoApprovers": { "services": { "svc:titan-income-vibeboard": ["tag:titan"] } }
   ```
2. Services（https://console.tailscale.com/admin/services ）→ **Advertise** → **Define a Service** → 名前 `titan-income-vibeboard`・ポートは `tcp:80`（TCP のみ対応）・Tags は空でよい → **Add service**。⚠ **ホストが広告する前に定義しておく**（KB の順序）【公表値】
3. Machines → titan の行の右端メニュー → **Edit tags** → `tag:titan` → Save（再認証なし）
4. ⚠ **すぐ `ssh titan hostname` が通ることを確かめる**（落ちたら `titan-lan` で入れるうちに方針を直す）
5. Claude: `ssh titan` と `http://titan/` が tag 後も届くこと（既存の serve は残る【推測】）
6. `! SSH_AUTH_SOCK=~/.ssh/agent.sock ssh titan '"/mnt/c/Program Files/Tailscale/tailscale.exe" serve --service=svc:titan-income-vibeboard --http=80 http://127.0.0.1:3010'`。CLI は「approval from an admin is required … available as http://titan-income-vibeboard.<tailnet>.ts.net/」の旨を出す。`autoApprovers.services` があれば承認は自動、無ければ Services → サービス名 → **Service hosts** → **Approve**
7. Claude: Sx360 から FQDN と短い名前の 200、titan で `127.0.0.1:3010` の bind が通ったままか（仮想 IP の 80 なので衝突しない【推測】）、SSE・Tasks
8. 通ったら `serve --http=80 off` でノード名の serve を外す（Step 3-5）

### Phase 3 の実測（2026-09-10）

| # | どこから | 何を | 結果 |
|---|---|---|---|
| 方針 | 管理画面（利用者） | `tagOwners` / grants 2 本 / `autoApprovers.services` を JSON editor に追加、Services で `titan-income-vibeboard`（`tcp:80`）を定義 | Save ✅ |
| tag | 管理画面（利用者） | Machines → titan → **Edit ACL tags...** → `tag:titan` | `tailscale status --json` の Tags が `['tag:titan']`。再認証なし。⚠ 鍵の期限 2027-03-09 は残る（再認証していないため。Disable key expiry で手で切る） |
| SSH | Sx360 | tag 直後の `ssh titan hostname` | `titan` ✅（方針が全許可なので落ちない） |
| 既存 serve | Sx360 | tag 後の `http://titan/` | 200（serve は tag で消えない） |
| 広告 | titan（利用者） | `serve --service=svc:titan-income-vibeboard --http=80 http://127.0.0.1:3010` | 「approval from an admin is required」と出るが、`autoApprovers` で**自動承認**（titan の `service-host` cap に svc と仮想 IP `100.101.131.54`） |
| DNS | Sx360 | `titan-income-vibeboard.tail061b58.ts.net`・短い名前 | どちらも `100.101.131.54`（仮想 IP）に解決 |
| **T11** | Sx360 | `http://titan-income-vibeboard.tail061b58.ts.net/`・**`http://titan-income-vibeboard/`** | **200 / 200**（20ms）。⚠ **短い名前は【実測】で引けた**（検索ドメインが効く） |
| T8 | Sx360（curl） | 名前経由の SSE ＋ titan で `touch` | 0.5 秒で `change` ✅ |
| T9 の土台 | Sx360 | 名前経由の `/api/tasks/windows` | `agents.ok: true` |
| 衝突 | titan | `netstat.exe` の LISTENING | `100.82.194.13:80`（ノード serve）だけ。**Service の仮想 IP は host socket に出ない** ＝ WSL の 3010 と衝突しない |
| 起動 | titan | Service を張ったまま vibeboard を止めて `run-vibeboard.sh` | 1 秒で起動、名前経由で 200 ✅（Phase 4 の「ssh で入って叩き直す」の土台） |
| Phase 2 残り | 利用者 | ブラウザで各タブ・`touch` の即時更新・titan で `claude` を起動して Tasks タブ・外の回線 | すべて確認（2026-09-10、利用者報告） |
| Phase 4 Step 4-1 | 利用者 | titan 再起動 → `ssh titan` → `./run-vibeboard.sh` → 名前で開く | 確認（2026-09-10、利用者報告）。serve は再起動をまたいだ。vibeboard は**手動起動でよい**と決定 |
| Step 3-5 | titan（利用者）→ Sx360 | `serve --http=80 off` → 各面 | `serve status` は Service の 1 本だけ。`http://titan/` タイムアウト・`titan:3010` 拒否（T12）・**名前は 200（24ms）**・`titan:3012` / `titan-income-vibeboard:3012` / `titan.lan` は閉（T13 / T6）・Funnel なし（T7）・Windows に 80 / 3010 / 3012 の listener なし（T14）✅ |


起動の落とし穴: **ssh の非対話シェルでは `node` が PATH に無い**（nvm は対話シェルでしか読まれない）。`run-vibeboard.sh` を ssh から叩くときは `export PATH=~/.nvm/versions/node/v24.14.0/bin:$PATH` を先に置く。切断後も残すには `setsid nohup ./run-vibeboard.sh > /tmp/vibeboard-3010.log 2>&1 < /dev/null &`。

### 済: 利用者が叩いたもの（Step 1-3、2026-09-10）— ⚠ 直後に上の衝突が判明し、80 で張り直す

Sx360 のこのプロジェクトの Claude Code セッションで（`!` 付きで打つと利用者の権限で走る）:

```bash
! SSH_AUTH_SOCK=~/.ssh/agent.sock ssh titan '"/mnt/c/Program Files/Tailscale/tailscale.exe" serve --bg --http=3010 http://127.0.0.1:3010'
```

または titan の PowerShell で `tailscale serve --bg --http=3010 http://127.0.0.1:3010`。張れたら Claude が Step 1-4（T3 / T4 / T7）と Phase 2 の curl（T5 / T6 / T13）を続ける。**Funnel は叩かない。**

## 運用メモ（2026-09-10）

### 使い方

1. Sx360 で `ssh titan`（鍵はエージェントに載せておく。[運用メモ](remote-ssh-tailscale.md)）
2. titan で `cd ~/ai-income-lab && ./run-vibeboard.sh`（WezTerm の mux ドメインならペインを閉じても残る。ssh から流すなら `setsid nohup ./run-vibeboard.sh > /tmp/vibeboard-3010.log 2>&1 < /dev/null &`。⚠ 非対話シェルは `node` と `claude` が PATH に無いので `export PATH=~/.nvm/versions/node/v24.14.0/bin:~/.local/bin:$PATH` を先に）
3. 自分のどの端末からでも **`http://titan-income-vibeboard`**（FQDN は `http://titan-income-vibeboard.tail061b58.ts.net`）
4. Tasks タブに titan のセッションを出すには、titan で `claude` を起動しておく（hook が `127.0.0.1:3010` に登録する）

### 何がどこにあるか

| もの | 場所 | 残り方 |
|---|---|---|
| Service の定義・承認・tag | Tailscale 管理画面（Services / Machines / Access controls の JSON editor） | 消すまで残る |
| serve の設定（`svc:titan-income-vibeboard` → `127.0.0.1:3010`） | titan の Windows 側 tailscaled | 再起動をまたいで残る【公表値】 |
| vibeboard のプロセス | titan の WSL、`127.0.0.1:3010` | 再起動で消える。ssh で入って叩き直す |
| dashboard（3012） | titan の WSL | **serve に出さない**。外から見るなら `ssh -L 3013:127.0.0.1:3012 titan` |

### `serve status` の読み方（titan で `"/mnt/c/Program Files/Tailscale/tailscale.exe" serve status`）

正しい状態は次の 1 本だけ。`(tailnet only)` が付いていれば Funnel ではない。`http://titan ...` の行や 3012 の行があれば余計な扉が開いている。

```
http://titan-income-vibeboard.tail061b58.ts.net (tailnet only) (svc:titan-income-vibeboard)
|-- / proxy http://127.0.0.1:3010
```

### 切り分けの型（名前で開けないとき、上から順に）

1. Sx360 で `tailscale status`: titan がオンラインか。落ちていれば SSH の運用メモの型へ（tailnet → LAN の順）
2. Sx360 で `getent hosts titan-income-vibeboard.tail061b58.ts.net`: 仮想 IP（`100.101.131.54`）に解決するか。しなければ管理画面の Services でホスト titan が承認済みか・tag が残っているか
3. titan で `serve status`: 上の 1 本があるか。無ければ利用者が `serve --service=svc:titan-income-vibeboard --http=80 http://127.0.0.1:3010` を叩き直す
4. titan で `ss -ltnp 'sport = :3010'`: vibeboard が居るか。居なければ `./run-vibeboard.sh`。**居ないと名前経由は 502**（serve は生きているが後ろが無い）
5. vibeboard が起動できない（「ポート 3010 は vibeboard 以外に使われています」）: Windows の `netstat.exe -ano -p tcp | findstr :3010` に listener があれば、誰かが serve の外向きを 3010 で張っている。`serve --http=3010 off`

### 変えるときの注意

- serve の外向きポートを **3010 にしない**（mirrored のポート共有で WSL の bind が塞がる）
- Funnel を使わない。HTTPS にするなら証明書のホスト名が CT ログに載る
- 他人の端末を tailnet に入れる日が来たら、`grants` の `svc:titan-income-vibeboard` の `src` を絞る
- titan の鍵の期限（2027-03-09）は tag 付与では消えなかった。切れる前に Machines → titan → Disable key expiry
