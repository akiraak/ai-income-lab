import re, subprocess, sys, collections
repo = sys.argv[1]
files = subprocess.run(["git","ls-files"],cwd=repo,capture_output=True,text=True).stdout.split("\n")
def scope(f):
    if f in ("DONE.md",) or f.startswith("docs/plans/archive/"): return "Z 履歴(DONE・archive)"
    if f in ("CLAUDE.md","TODO.md","README.md","AGENTS.md"): return "A 直下の文書"
    if f.startswith("docs/plans/"): return "B 進行中のプラン"
    if f.startswith("docs/specs/experiments/dev-loop/"): return "D2 開発ループの生成物(log・register等)"
    if f.startswith("docs/specs/experiments/feature-discovery/"): return "C1 rules.md ほか"
    if f.startswith("docs/specs/experiments/"): return "D1 実験の記録"
    if f.startswith("docs/specs/"): return "C2 仕様書(specs直下)"
    if "/devloop/prompts/" in f: return "F 開発ループのプロンプト"
    if f.startswith("dashboard/") and f.endswith(".toml") and f.count("/")==1: return "E1 外部のタブのTOML"
    if "/templates/" in f or f.endswith((".html",".js")): return "E2 管理画面のテンプレート"
    if "/tests/" in f or "/test_" in f: return "H テスト"
    if "/config/ideas/" in f: return "G2 レジストリ(config/ideas)"
    if f.endswith(".toml"): return "G1 設定のTOML"
    if f.endswith(".py"): return "I Python(コメント・メッセージ)"
    if f.endswith((".sh",".service",".timer")): return "J シェル・systemd"
    if f.endswith(".md"): return "K その他のmd(README等)"
    return None
terms = collections.OrderedDict([
 ("砂場", r"砂場"),
 ("台帳", r"台帳"),
 ("運転手", r"運転手"),
 ("関門", r"関門"),
 ("門(単独)", r"(?<![関専部入名校正城水])門(?![前番外限下家])"),
 ("門前", r"門前"),
 ("錠", r"錠"),
 ("段 N", r"段 ?[0-9０-９]"),
 ("選ぶ段等", r"(選ぶ|作る|回す|土曜の|の)段(?![階落取])"),
 ("口(機能)", r"(?<![入出窓人利悪])口(?![座調])"),
 ("器(単独)", r"(?<![執行機検知武楽食容計])器"),
 ("札", r"札"),
 ("印(単独)", r"(?<![目矢刻])印(?![刷象])"),
 ("登録簿", r"登録簿"),
 ("日誌", r"日誌"),
 ("置き場", r"置き場"),
 ("道(パス)", r"(?<![軌報筋近鉄水国歩])道(?![具路筋])"),
 ("控え", r"控え(?!る|て|め)"),
 ("写し", r"写し(?!て|た|ま)"),
 ("吐き直", r"吐き直"),
 ("指紋", r"指紋"),
 ("黄金", r"黄金"),
 ("雛形", r"雛形"),
 ("偽の", r"偽の"),
 ("見張", r"見張"),
 ("機械", r"機械(?!学習|で|的)"),
 ("作業ツリー", r"作業ツリー"),
 ("木(単独)", r"(?<![水金])木(?![曜）)])"),
 ("物差し", r"物差し"),
 ("突き合わせ", r"突き合わせ"),
 ("人の手", r"人の手"),
 ("執行器", r"執行器"),
 ("検知器", r"検知器"),
 ("土台", r"土台"),
 ("棚卸し", r"棚卸し"),
 ("名簿", r"名簿"),
 ("識別項目", r"識別項目"),
 ("検証結果一覧", r"検証結果一覧"),
 ("売買基準値", r"売買基準値"),
 ("識別名", r"識別名"),
 ("許可", r"許可"),
 ("資格情報", r"資格情報"),
 ("売買履歴", r"売買履歴"),
 ("手じまい", r"手じまい"),
 ("帯", r"(?<![時携連])帯"),
 ("束", r"(?<![約拘結])束"),
 ("枝", r"枝"),
 ("種(seed)", r"種 ?[0-9]|種を"),
 ("尻", r"尻"),
 ("頭", r"の頭"),
 ("切れ目", r"切れ目"),
 ("較正", r"較正"),
 ("取り込", r"取り込"),
 ("写す", r"写(す|して|した)"),
 ("打ち切", r"打ち切"),
 ("作り置き", r"作り置き"),
 ("通し運転", r"通し運転"),
 ("試運転", r"試運転"),
 ("筋書き", r"筋書き"),
 ("帳尻", r"帳尻"),
 ("番人", r"番人"),
 ("綴り", r"綴り"),
 ("棚", r"(?<!卸)棚(?!卸)"),
 ("系統", r"系統"),
 ("注記", r"注記"),
])
cnt = collections.defaultdict(lambda: collections.Counter())
nfiles = collections.Counter()
for f in files:
    s = scope(f) if f else None
    if not s: continue
    try: txt = open(f"{repo}/{f}",encoding="utf-8").read()
    except Exception: continue
    nfiles[s]+=1
    for name,pat in terms.items():
        n = len(re.findall(pat,txt))
        if n: cnt[name][s]+=n
scopes = sorted(nfiles)
print("scope files:"); 
for s in scopes: print(f"  {s}: {nfiles[s]}")
print()
print("term\t"+"\t".join(s.split()[0] for s in scopes)+"\ttotal(除Z)")
for name in terms:
    row=[cnt[name][s] for s in scopes]
    tot=sum(c for s,c in zip(scopes,row) if not s.startswith("Z"))
    print(name+"\t"+"\t".join(str(c) for c in row)+f"\t{tot}")
