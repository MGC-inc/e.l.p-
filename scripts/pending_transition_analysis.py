#!/usr/bin/env python3
"""保留→契約／保留→失注となった商談の録音を再分析し、要因と対策をあぶり出す。

scripts/deal_analysis.py（商談全体の総合評価・構造化データ抽出）とは別物。
こちらは「なぜこの商談が保留から契約／失注に転じたか」に絞った振り返り専用の
分析で、保留転換検知Routine（商談分析運用.md参照）から呼ばれる。
Gemini呼び出し・アップロード処理はdeal_analysis.pyのものをそのまま再利用する。

使い方:
  python3 scripts/pending_transition_analysis.py 録音.mp3 --closer 今川 --customer 山田様 \
    --final-result 契約 --pending-date 2026-09-01 --transition-date 2026-09-19

出力:
  標準出力に、保留→結果の経緯（経過日数）＋分析結果のMarkdown本文を出力する
  （Notion追記・LINE通知の要約用）。--pending-date/--transition-dateを渡すと、
  音声からは分からない「何日後に転じたか」を冒頭に確定情報として明記する。
"""
import argparse
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from deal_analysis import DEFAULT_MODEL, ENV_PATH, generate, load_env, log, upload_audio  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
PROMPT_PATH = os.path.join(HERE, "pending_transition_analysis_prompt.txt")


def main():
    ap = argparse.ArgumentParser(description="保留→契約／保留→失注となった商談の録音から要因と対策を分析する")
    ap.add_argument("audio", help="当初「保留」となった商談時の録音ファイル（mp3/wav/m4a等）")
    ap.add_argument("--closer", required=True, help="担当クローザー名")
    ap.add_argument("--customer", required=True, help="お客様名（例: 山田様）")
    ap.add_argument("--final-result", required=True, choices=["契約", "失注"], help="最終的に転じた結果")
    ap.add_argument("--pending-date", help="保留となった商談日 YYYY-MM-DD（Notionの商談日時）")
    ap.add_argument("--transition-date",
                     help="結果が切り替わったと推測される日 YYYY-MM-DD（NotionページのLast edited time等）")
    ap.add_argument("--model", default=DEFAULT_MODEL, help=f"Geminiモデル（既定: {DEFAULT_MODEL}）")
    args = ap.parse_args()

    if not os.path.exists(args.audio):
        sys.exit(f"ファイルが見つかりません: {args.audio}")
    env = load_env(ENV_PATH)
    key = env.get("GEMINI_API_KEY") or os.environ.get("GEMINI_API_KEY")
    if not key:
        sys.exit("GEMINI_API_KEY が .env にありません（API一覧.md の保管場所参照）")

    # 経過日数は音声から推測できる情報ではないため、Notionの日付から機械的に計算し、
    # Geminiの出力とは別に確定情報として先頭に付ける（LLMに日付計算を委ねない）。
    elapsed_header = ""
    elapsed_note = ""
    if args.pending_date and args.transition_date:
        pending = dt.date.fromisoformat(args.pending_date)
        transition = dt.date.fromisoformat(args.transition_date)
        days = (transition - pending).days
        elapsed_header = (
            f"## ■0. 保留→{args.final_result}の経緯\n"
            f"- 保留となった商談日: {args.pending_date}\n"
            f"- {args.final_result}に切り替わったと推測される日（Notion最終更新日時ベース）: {args.transition_date}\n"
            f"- 経過日数: 約{days}日\n\n"
        )
        elapsed_note = (
            f"\n- 保留となった商談日から{args.final_result}に切り替わるまで、約{days}日かかっています。"
            f"この期間の長さも踏まえて分析してください（短期間なら当日〜数日以内の即決に近い追客、"
            f"長期間なら時間をかけた検討・再アプローチがあったと考えられます）。\n"
        )

    prompt = open(PROMPT_PATH, encoding="utf-8").read()
    meta = (
        f"\n\n# この商談の既知メタ情報\n"
        f"- 担当クローザー: {args.closer}\n"
        f"- お客様: {args.customer}\n"
        f"- 最終的に転じた結果: {args.final_result}"
        f"（この商談は当初「保留」でしたが、その後の追客の結果、最終的に{args.final_result}となりました）"
        f"{elapsed_note}"
    )

    uri, mime = upload_audio(key, args.audio)
    log(f"file_uri={uri}")
    log(f"{args.model} で保留転換要因分析中...")
    text = generate(key, args.model, prompt + meta, uri, mime)
    print(elapsed_header + text.strip())


if __name__ == "__main__":
    main()
