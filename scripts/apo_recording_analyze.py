#!/usr/bin/env python3
"""アポ録音（30分以内の短い録音。Botに送られて登録済みのもの）をGeminiで分析し、
・ライブラリ（Botの「ベスト録音」）に出す解説（場面・要約・真似したいところ・聞きどころ）
・録音を送った本人だけに届くフィードバック（良かった点・次に直す点）
を作る。2026-11-18 今川さん指示（ベスト録音ライブラリ＋チケット）。

使い方:
  python3 scripts/apo_recording_analyze.py            # 未分析のアポ録音をまとめて分析→本人へフィードバック
  python3 scripts/apo_recording_analyze.py --dry-run  # 対象の一覧だけ表示
  python3 scripts/apo_recording_analyze.py --limit 5

・結果は deal_recordings.notion_url のJSON（analysis）に保存する（新しいテーブルは使わない）。
・フィードバックは録音を送った本人にだけ、1:1のLINEで送る（グループ・他の人には送らない）。深夜（23:30〜6:00）は送らず、次の実行で送る。
・お客様の名前・住所・電話番号は、解説にもフィードバックにも書かせない（プロンプトで禁止＋後で機械的に伏せる）。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import deal_analysis as DA  # noqa: E402
from quiet_hours import in_quiet_hours  # noqa: E402

BUCKET = "deal-recordings"

PROMPT = """あなたは訪問営業（工事のご挨拶から掃除などの無料サービスを案内し、商談のアポイントを取る仕事）のトークコーチです。
次の音声は、アポインターがお客様と話した短い録音です。聞き取れた内容だけをもとに、次のJSONだけを出力してください（前後に文章やコードブロックは付けない）。

{{
  "scene": "インターホン／対面／断りの切り返し／日程切り／その他 のうち、この録音でいちばん聞きどころになる場面を1つ",
  "summary": "この録音は何をしていて、どんな流れか。2文以内（60〜100字）",
  "good": ["真似したい・うまいと思った点を具体的に（言い回しや間の取り方など）。最大3つ"],
  "improve": ["次に直すとよい点を、すぐ試せる行動の形で。最大2つ"],
  "listen": [{{"t": "mm:ss", "note": "ここを聞いてほしい、という聞きどころを一言で"}}]
}}

守ること:
- お客様の氏名・住所・電話番号・勤務先など、個人が特定できる情報は書かない（「お客様」「奥様」などと書く）。
- 聞き取れない・自信がない部分は書かない。推測で作り話をしない。
- 良い点は、一般論（笑顔が大切など）ではなく、この録音の中の具体的な言動だけを書く。
- 録音者は {speaker} さん。結果は「{result}」。
- "listen" は最大3つ。時刻は音声の経過時間（分:秒）。"""


def sb(method, path, data=None):
    url = os.environ["ELP_SUPABASE_URL"].strip("（）()").rstrip("/") + "/rest/v1/" + path
    key = os.environ["ELP_SUPABASE_SERVICE_ROLE_KEY"].strip("（）()")
    req = urllib.request.Request(url, method=method, data=json.dumps(data).encode() if data is not None else None,
                                 headers={"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json", "Prefer": "return=representation"})
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
        return json.loads(raw) if raw else None


def download(path: str, dest: str) -> None:
    base = os.environ["ELP_SUPABASE_URL"].strip("（）()").rstrip("/")
    key = os.environ["ELP_SUPABASE_SERVICE_ROLE_KEY"].strip("（）()")
    req = urllib.request.Request(f"{base}/storage/v1/object/{BUCKET}/{urllib.parse.quote(path)}", headers={"apikey": key, "Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=300) as r, open(dest, "wb") as f:
        f.write(r.read())


def meta_of(row: dict) -> dict:
    t = row.get("notion_url") or ""
    try:
        return json.loads(t) if t.startswith("{") else {}
    except Exception:
        return {}


PII = [(re.compile(r"0\d{1,4}[-ー]?\d{1,4}[-ー]?\d{3,4}"), "（電話番号）"), (re.compile(r"〒?\d{3}-\d{4}"), "（郵便番号）")]


def scrub(text: str) -> str:
    for rx, rep in PII:
        text = rx.sub(rep, text)
    return text


def parse_json(text: str) -> dict:
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    a, b = t.find("{"), t.rfind("}")
    return json.loads(t[a:b + 1], strict=False)


def clean(an: dict) -> dict:
    out = {"scene": scrub(str(an.get("scene") or ""))[:12], "summary": scrub(str(an.get("summary") or ""))[:160],
           "good": [scrub(str(x))[:120] for x in (an.get("good") or [])][:3],
           "improve": [scrub(str(x))[:120] for x in (an.get("improve") or [])][:2],
           "listen": [{"t": str(x.get("t", ""))[:8], "note": scrub(str(x.get("note", "")))[:60]} for x in (an.get("listen") or []) if isinstance(x, dict)][:3]}
    return out


def line_push(to: str, text: str) -> None:
    tok = os.environ["DEAL_LINE_CHANNEL_ACCESS_TOKEN"]
    req = urllib.request.Request("https://api.line.me/v2/bot/message/push", method="POST",
                                 data=json.dumps({"to": to, "messages": [{"type": "text", "text": text[:4900]}]}).encode(),
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {tok}"})
    urllib.request.urlopen(req, timeout=20).read()


def feedback_text(row: dict, an: dict) -> str:
    lines = [f"🎙 アポ録音のフィードバック（No.{row['id']}・{row.get('result') or ''}）", ""]
    if an["summary"]:
        lines += [an["summary"], ""]
    if an["good"]:
        lines += ["【良かった点】"] + [f"・{g}" for g in an["good"]] + [""]
    if an["improve"]:
        lines += ["【次に試すこと】"] + [f"・{g}" for g in an["improve"]] + [""]
    if an["listen"]:
        lines += ["【聞き返すなら】"] + [f"・{x['t']} {x['note']}".strip() for x in an["listen"]] + [""]
    lines.append("この内容は、あなたにだけ送っています。録音は「ベスト録音」で他のメンバーが聞けます（お客様のお名前は出ません）。")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--model", default=DA.DEFAULT_MODEL)
    args = ap.parse_args()
    env = {}
    p = os.path.join(os.path.dirname(HERE), ".env")
    if os.path.exists(p):
        for line in open(p, encoding="utf-8"):
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    key = os.environ.get("GEMINI_API_KEY") or env.get("GEMINI_API_KEY")
    rows = sb("GET", "deal_recordings?status=eq.apo_done&storage_path=like.apo%2F*&order=received_at.asc&limit=100&select=*") or []
    todo = [r for r in rows if not (meta_of(r).get("analysis"))][:args.limit]
    # 分析は済んでいるが、深夜でフィードバックを送れなかったもの
    unsent = [r for r in rows if meta_of(r).get("analysis") and not meta_of(r).get("fb_sent")]
    print(f"未分析 {len(todo)} 件 / フィードバック未送信 {len(unsent)} 件")
    if args.dry_run:
        for r in todo:
            print("  分析対象:", r["id"], r.get("appointer"), r.get("result"), r["storage_path"])
        return
    if todo and not key:
        sys.exit("GEMINI_API_KEY がありません。分析できません。")
    send_ok = not in_quiet_hours()
    with tempfile.TemporaryDirectory() as td:
        for r in todo:
            ext = os.path.splitext(r["storage_path"])[1] or ".mp3"
            dest = os.path.join(td, f"{r['id']}{ext}")
            try:
                download(r["storage_path"], dest)
                uri, mime = DA.upload_audio(key, dest)
                text = DA.generate(key, args.model, PROMPT.format(speaker=r.get("appointer") or "不明", result=r.get("result") or "不明"), uri, mime)
                an = clean(parse_json(text))
            except Exception as e:  # 1件の失敗で止めない。次の実行でもう一度試す
                print(f"  失敗 No.{r['id']}: {e}")
                continue
            m = meta_of(r)
            m["analysis"] = an
            sb("PATCH", f"deal_recordings?id=eq.{r['id']}", {"notion_url": json.dumps(m, ensure_ascii=False)})
            r["notion_url"] = json.dumps(m, ensure_ascii=False)
            unsent.append(r)
            print(f"  分析OK No.{r['id']} 「{an['scene']}」{an['summary'][:30]}")
    for r in unsent:
        if not send_ok:
            print("  深夜のためフィードバックは送りません（次の実行で送ります）")
            break
        m = meta_of(r)
        try:
            line_push(r["line_user_id"], feedback_text(r, m["analysis"]))
            m["fb_sent"] = True
            sb("PATCH", f"deal_recordings?id=eq.{r['id']}", {"notion_url": json.dumps(m, ensure_ascii=False)})
            print(f"  フィードバック送信 No.{r['id']}（{r.get('appointer')}さん本人へ）")
        except Exception as e:
            print(f"  フィードバック送信失敗 No.{r['id']}: {e}")


if __name__ == "__main__":
    main()
