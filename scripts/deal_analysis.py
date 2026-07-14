#!/usr/bin/env python3
"""商談録音（PLAUD NOTE等のMP3） → Gemini一括分析 → Notion登録用JSONを出力。

方針:
  - 録音は分割しない。商談全体（2〜3時間以上）を1リクエストで分析する
    （Gemini は1プロンプトあたり最大約9.5時間の音声を扱える）
  - 文字の内容に加えて声のトーン・声量・話速・間も分析対象（プロンプト参照）
  - 分析の型・出力フォーマットは Notion「商談分析AI」ページに準拠
    （scripts/deal_analysis_prompt.txt はその内容を音声入力向けに拡張したもの）
  - 保存先はこのリポジトリではなく Notion「DB 商談分析＆アポ分析」データベース。
    このスクリプトは分析結果を作るところまでで、Notionへの書き込みは
    呼び出し側（スキル 商談録音分析）が mcp Notion connector で行う

使い方:
  python3 scripts/deal_analysis.py 録音.mp3 --closer 今川 --customer 山田様 --result 保留
  python3 scripts/deal_analysis.py 録音.mp3 --closer 今川 --customer 山田様 --result 契約 \
      --date 2026-07-11 --appointer 佐藤 --model gemini-2.5-pro
  --dry-run でアップロード確認のみ（Gemini生成はスキップ）

出力:
  標準出力に {"content_markdown": "...", "properties": {...}} のJSONを1行で出力する
  （--out でファイルパスを指定すればそこに書き出す）
"""
import argparse
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
ENV_PATH = os.path.join(REPO, ".env")
PROMPT_PATH = os.path.join(HERE, "deal_analysis_prompt.txt")
GEMINI_BASE = "https://generativelanguage.googleapis.com"
DEFAULT_MODEL = "gemini-2.5-flash"

# Notion「結果」ステータスの選択肢（LINEクイックリプライの文言と一致させる）
VALID_RESULTS = {"契約", "保留", "クーリングオフ", "失注", "審査落ち", "キャンセル", "商談予定", "AC"}

MIME_BY_EXT = {
    ".mp3": "audio/mp3",
    ".wav": "audio/wav",
    ".m4a": "audio/aac",
    ".aac": "audio/aac",
    ".ogg": "audio/ogg",
    ".flac": "audio/flac",
}


def log(msg):
    print(f"[{dt.datetime.now():%H:%M:%S}] {msg}", file=sys.stderr, flush=True)


def load_env(path):
    env = {}
    if not os.path.exists(path):
        return env
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


def http(url, data=None, headers=None, method=None, raw=False, timeout=120):
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read()
        return (body if raw else json.loads(body.decode("utf-8", "replace"), strict=False),
                dict(r.getheaders()))


def upload_audio(key, path):
    """Files API (resumable) でアップロードし (file_uri, mime) を返す。"""
    ext = os.path.splitext(path)[1].lower()
    mime = MIME_BY_EXT.get(ext)
    if not mime:
        sys.exit(f"未対応の拡張子: {ext}（対応: {', '.join(MIME_BY_EXT)}）")
    data = open(path, "rb").read()
    log(f"アップロード開始: {os.path.basename(path)} ({len(data)/1e6:.0f}MB, {mime})")

    start_url = f"{GEMINI_BASE}/upload/v1beta/files?key={key}"
    meta = json.dumps({"file": {"display_name": os.path.basename(path)}}).encode()
    _, hdrs = http(start_url, data=meta, raw=True, headers={
        "X-Goog-Upload-Protocol": "resumable",
        "X-Goog-Upload-Command": "start",
        "X-Goog-Upload-Header-Content-Length": str(len(data)),
        "X-Goog-Upload-Header-Content-Type": mime,
        "Content-Type": "application/json",
    })
    upload_url = hdrs.get("X-Goog-Upload-URL") or hdrs.get("x-goog-upload-url")
    if not upload_url:
        raise RuntimeError(f"no upload url in headers: {list(hdrs)}")
    res, _ = http(upload_url, data=data, timeout=1800, headers={
        "Content-Length": str(len(data)),
        "X-Goog-Upload-Offset": "0",
        "X-Goog-Upload-Command": "upload, finalize",
    })
    f = res["file"]
    name, uri = f["name"], f["uri"]
    # 長時間音声は処理に数分かかるので最大15分待つ
    log("Gemini側の処理待ち（長い録音は数分かかります）...")
    for _ in range(180):
        st, _ = http(f"{GEMINI_BASE}/v1beta/{name}?key={key}")
        if st.get("state") == "ACTIVE":
            return uri, f.get("mimeType", mime)
        if st.get("state") == "FAILED":
            raise RuntimeError("file processing FAILED")
        time.sleep(5)
    raise RuntimeError("file processing timeout (15min)")


def generate(key, model, prompt, file_uri, file_mime):
    url = f"{GEMINI_BASE}/v1beta/models/{model}:generateContent?key={key}"
    payload = json.dumps({
        "contents": [{"parts": [
            {"text": prompt},
            {"file_data": {"mime_type": file_mime, "file_uri": file_uri}},
        ]}],
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": 32768},
    }).encode()
    res, _ = http(url, data=payload, headers={"Content-Type": "application/json"}, timeout=1800)
    cand = (res.get("candidates") or [{}])[0]
    parts = cand.get("content", {}).get("parts", [])
    text = "".join(p.get("text", "") for p in parts)
    if not text:
        raise RuntimeError(f"empty response: {json.dumps(res, ensure_ascii=False)[:300]}")
    return text.strip()


def split_content_and_properties(text):
    """応答末尾の```json ...```ブロックを構造化プロパティとして取り出し、
    残りを本文Markdownとして返す。"""
    matches = list(re.finditer(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL))
    if not matches:
        return text.strip(), {}
    last = matches[-1]
    try:
        properties = json.loads(last.group(1))
    except json.JSONDecodeError as e:
        log(f"警告: 構造化JSONのパースに失敗しました（{e}）。properties は空にします。")
        properties = {}
    content = (text[: last.start()] + text[last.end():]).strip()
    return content, properties


def main():
    ap = argparse.ArgumentParser(description="商談録音MP3をGeminiで一括分析しNotion登録用JSONを出力")
    ap.add_argument("audio", help="録音ファイル（mp3/wav/m4a等）")
    ap.add_argument("--closer", required=True, help="担当クローザー名（Notion「クローザー」選択肢と一致させる）")
    ap.add_argument("--customer", required=True, help="顧客名（例: 山田様。Notionページタイトルになる）")
    ap.add_argument("--result", required=True, choices=sorted(VALID_RESULTS),
                     help="商談結果（LINEクイックリプライで取得した値。Notion「結果」にそのまま使う）")
    ap.add_argument("--date", help="商談日時 YYYY-MM-DD[THH:MM]（省略時はファイル更新日）")
    ap.add_argument("--appointer", default="", help="アポインター名（分かっていれば）")
    ap.add_argument("--model", default=DEFAULT_MODEL, help=f"Geminiモデル（既定: {DEFAULT_MODEL}）")
    ap.add_argument("--out", help="結果JSONの出力先ファイル（省略時は標準出力）")
    ap.add_argument("--dry-run", action="store_true", help="アップロードまでで止める（分析はしない）")
    args = ap.parse_args()

    if not os.path.exists(args.audio):
        sys.exit(f"ファイルが見つかりません: {args.audio}")
    env = load_env(ENV_PATH)
    key = env.get("GEMINI_API_KEY") or os.environ.get("GEMINI_API_KEY")
    if not key:
        sys.exit("GEMINI_API_KEY が .env にありません（API一覧.md の保管場所参照）")

    date = args.date or dt.date.fromtimestamp(os.path.getmtime(args.audio)).isoformat()
    prompt = open(PROMPT_PATH, encoding="utf-8").read()
    meta = (
        f"\n\n# この商談の既知メタ情報（出力の該当欄にそのまま使うこと）\n"
        f"- 商談日時: {date}\n"
        f"- 担当クローザー: {args.closer}\n"
        f"- アポインター: {args.appointer or '未指定（分かれば構造化データのアポインター欄に記入）'}\n"
        f"- 顧客: {args.customer}\n"
        f"- 商談結果: {args.result}\n"
    )

    uri, mime = upload_audio(key, args.audio)
    log(f"file_uri={uri}")
    if args.dry_run:
        log("[dry-run] 分析をスキップ")
        return
    log(f"{args.model} で分析中（商談全体を一括処理。数分かかります）...")
    text = generate(key, args.model, prompt + meta, uri, mime)
    content_markdown, properties = split_content_and_properties(text)

    properties["クローザー"] = args.closer
    properties["お客様名"] = args.customer
    properties["結果"] = args.result
    properties["商談日時"] = date
    if args.appointer:
        properties["アポインター"] = args.appointer
    properties["分析済み"] = True

    result = {"content_markdown": content_markdown, "properties": properties}
    out_text = json.dumps(result, ensure_ascii=False)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(out_text)
        log(f"保存: {args.out}")
    else:
        print(out_text)


if __name__ == "__main__":
    main()
