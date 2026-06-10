# E.L.P 組織管理リポジトリ

株式会社E.L.P（株式会社クリーンマン）の組織情報を一元管理する **単一情報源（Single Source of Truth）** リポジトリ。

**入口は [E.L.P.md](./E.L.P.md)** — 何をどこで探すかはすべてそこに書いてある。

## 構成

```
E.L.P.md        # 大元仕様書（エントリポイント・ドキュメントマップ）
├─ 事業内容.md   # 会社概要・サービス内容
├─ KPI.md       # KPI定義・KPI→Howの落とし込み
├─ 従業員.md     # メンバー・担当マップ
├─ イノベラ.md   # 電話録音の要約・LINE通知フロー
├─ API一覧.md    # 外部サービス台帳（キー実体は置かない）
└─ 議事録/       # MTG議事録（YYYY/YYYY-MM-DD_相手_テーマ.md）
   ├─ README.md     # 議事録の作り方
   └─ _template.md  # テンプレート
```

## 運用

- Claude Code から自然言語で参照・更新する（ルールは [CLAUDE.md](./CLAUDE.md)）
- コミットは Conventional Commits 形式: `docs(KPI): 6月目標を更新`
- **シークレット（APIキー・パスワード）は絶対にコミットしない**
