#!/usr/bin/env python3
"""保留→契約／保留→失注となった商談の録音を再分析し、要因と対策をあぶり出す。

scripts/deal_analysis.py（商談全体の総合評価・構造化データ抽出）とは別物。
こちらは「なぜこの商談が保留から契約／失注に転じたか」に絞った振り返り専用の
分析で、保留転換検知Routine（商談分析運用.md参照）から呼ばれる。
Gemini呼び出し・アップロード処理はdeal_analysis.pyのものをそのまま再利用する。

使い方:
  python3 scripts/pending_transition_analysis.py 録音.mp3 --closer 今川 --customer 山田様 --final-result 契約

出力:
  標準出力に分析結果のMarkdown本文をそのまま出力する（Notion追記・LINE通知の要約用）
"""
import argparse
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
    ap.add_argument("--model", default=DEFAULT_MODEL, help=f"Geminiモデル（既定: {DEFAULT_MODEL}）")
    args = ap.parse_args()

    if not os.path.exists(args.audio):
        sys.exit(f"ファイルが見つかりません: {args.audio}")
    env = load_env(ENV_PATH)
    key = env.get("GEMINI_API_KEY") or os.environ.get("GEMINI_API_KEY")
    if not key:
        sys.exit("GEMINI_API_KEY が .env にありません（API一覧.md の保管場所参照）")

    prompt = open(PROMPT_PATH, encoding="utf-8").read()
    meta = (
        f"\n\n# この商談の既知メタ情報\n"
        f"- 担当クローザー: {args.closer}\n"
        f"- お客様: {args.customer}\n"
        f"- 最終的に転じた結果: {args.final_result}"
        f"（この商談は当初「保留」でしたが、その後の追客の結果、最終的に{args.final_result}となりました）\n"
    )

    uri, mime = upload_audio(key, args.audio)
    log(f"file_uri={uri}")
    log(f"{args.model} で保留転換要因分析中...")
    text = generate(key, args.model, prompt + meta, uri, mime)
    print(text.strip())


if __name__ == "__main__":
    main()
