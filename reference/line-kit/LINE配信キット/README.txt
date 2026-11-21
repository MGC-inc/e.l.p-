【毎週水曜9:00の自動配信】 GitHub Actions が実行する(.github/workflows/weekly.yml)
流れ: Googleスプレッドシートから数字を取得 → 代理店ごとのPDF生成 → Vercelに公開 → 各メンバーに公式LINEでリンク送信
安全装置: config の data_week_start が「先週の月曜」でなければ中止(古いデータを送らない)。失敗するとGitHubから通知メールが届く。

【毎週やること】 火曜の夜までにスプレッドシートを更新
  results  … 先週の個人別数字(name,visits,home,face,target,talk,appo,meet_own,meet_other,win_own,win_other,cooloff,screen_fail,sales)
           (在宅数/対面数/対象数/対話数/自アポ・他アポ別の商談・契約/クーリングオフ/審査落ち/売上(万円))
  config   … data_week_start(先週月曜 YYYY-MM-DD)、月の目標/実績(target_contracts,target_sales,actual_contracts,actual_sales)、
             週の目標(week_target_contracts,week_target_sales ※空欄なら月目標×7/日数)
  members  … 人の入れ替えがあるときだけ(name,line_user_id,role,agency,is_manager)

【初回セットアップ】
1. このフォルダをGitHubのprivateリポジトリに置く
2. スプレッドシートの各シートを「ウェブに公開→CSV」にして、URLをリポジトリのSecretsに登録
   MEMBERS_URL / RESULTS_URL / CONFIG_URL
3. Vercelでプロジェクトを1つ作り、Secretsに登録
   VERCEL_TOKEN / VERCEL_ORG_ID / VERCEL_PROJECT_ID / PUBLIC_BASE_URL(例 https://xxx.vercel.app)
4. LINE_CHANNEL_ACCESS_TOKEN をSecretsに登録
5. Actionsタブで weekly-pdca を「Run workflow」で手動テスト

【ローカルで試す】 python build_all.py sample out  →  python deliver.py out https://公開URL --dry-run
【注意】 公開するのは out/public のPDFだけ(manifest.csvはLINE IDが入るため公開しない)。
        新しく公開すると先週までのPDFのリンクは消える。URLを知っていれば誰でも開ける。
