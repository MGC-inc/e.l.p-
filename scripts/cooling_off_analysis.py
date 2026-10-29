#!/usr/bin/env python3
"""契約→クーリングオフとなった商談の録音を再分析し、原因と対策をあぶり出す。

scripts/deal_analysis.py（商談全体の総合評価・構造化データ抽出）とは別物。
こちらは「なぜこの契約が後で解約されたか」に絞った振り返り専用の分析で、
クーリングオフ検知Routine（商談分析運用.md セクション6-4）から呼ばれる。
Gemini呼び出し・アップロード処理はdeal_analysis.pyのものをそのまま再利用する。

使い方:
  python3 scripts/cooling_off_analysis.py 録音.mp3 --closer 今川 --customer 山田様

出力:
  標準出力に分析結果のMarkdown本文をそのまま出力する（Notion追記・LINE通知の要約用）
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from deal_analysis import DEFAULT_MODEL, ENV_PATH, generate, load_env, log, upload_audio  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
PROMPT_PATH = os.path.join(HERE, "cooling_off_analysis_prompt.txt")


def main():
    ap = argparse.ArgumentParser(description="クーリングオフとなった契約の録音から原因と対策を分析する")
    ap.add_argument("audio", help="元の契約時の録音ファイル（mp3/wav/m4a等）")
    ap.add_argument("--closer", required=True, help="担当クローザー名")
    ap.add_argument("--customer", required=True, help="顧客名（例: 山田様）")
    ap.add_argument("--model", default=DEFAULT_MODEL, help=f"Geminiモデル（既定: {DEFAULT_MODEL}）")
    args = ap.parse_args()

    if not os.path.exists(args.audio):
        sys.exit(f"ファイルが見つかりません: {args.audio}")
    env = load_env(ENV_PATH)
    key = env.get("GEMINI_API_KEY") or os.environ.get("GEMINI_API_KEY")
    if not key:
        sys.exit("GEMINI_API_KEY が .env にありません（API一覧.md の保管場所参照）")

    prompt = open(PROMPT_PATH, encoding="utf-8").read()
    meta = f"\n\n# この商談の既知メタ情報\n- 担当クローザー: {args.closer}\n- 顧客: {args.customer}\n"

    uri, mime = upload_audio(key, args.audio)
    log(f"file_uri={uri}")
    log(f"{args.model} でクーリングオフ原因分析中...")
    text = generate(key, args.model, prompt + meta, uri, mime)
    print(text.strip())


if __name__ == "__main__":
    main()
