#!/usr/bin/env python3
"""イノベラ通話 → Gemini文字起こし+19項目抽出 → Supabase call_logs 反映。

対象回線:
  circuit_id 1 = 車トラブル相談窓口 (05088960087)
  circuit_id 2 = 車トラブル受付センター (05088906176)

フロー:
  1. CDR検索 (?ckey=cdr&akey=search) で対象回線の録音あり通話を取得
  2. 録音URL検索 (?ckey=cdr&akey=record, cdr_id=...) でWAVのURLを取得
  3. WAVをダウンロード → Gemini Files API へアップロード
  4. scripts/gemini_prompt.txt のプロンプト + 通話メタ情報で generateContent
  5. 出力をパース（文字起こし/要約/19項目TSV）
  6. Supabase call_logs に upsert (innovera_uniqid キー)

使い方:
  python3 scripts/innovera_pipeline.py --limit 1
  python3 scripts/innovera_pipeline.py --start "2026-06-10 00:00:00" --end "2026-06-10 23:59:59" --limit 5
  python3 scripts/innovera_pipeline.py --limit 1 --dry-run   # Gemini/DB保存をスキップ
"""
import argparse
import datetime as dt
import json
import os
import sys
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(HERE, "..", ".env")
PROMPT_PATH = os.path.join(HERE, "gemini_prompt.txt")
GEMINI_MODEL = "gemini-2.5-flash"
TARGET_CIRCUITS = {"1": "車トラブル相談窓口", "2": "車トラブル受付センター"}
GEMINI_BASE = "https://generativelanguage.googleapis.com"

# A〜S列 → call_logs カラム名
TSV_COLUMNS = [
    "sheet_timestamp", "agency", "inquiry_date_raw", "inquiry_time", "area",
    "address1", "address2", "customer_name", "gender", "customer_phone",
    "call_result", "ng_reason", "vehicle_type", "trouble_type", "insurer",
    "hours_told", "self_resolve_method", "out_of_area_region", "quoted_amount",
]


def log(msg):
    print(f"[{dt.datetime.now():%H:%M:%S}] {msg}", flush=True)


def load_env(path):
    env = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


def http(url, data=None, headers=None, method=None, raw=False, timeout=120):
    if isinstance(data, (dict,)):
        data = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read()
        return (body if raw else json.loads(body.decode("utf-8", "replace"), strict=False),
                dict(r.getheaders()))


# ---------- Innovera ----------

def cdr_search(host, key, start, end):
    url = f"https://{host}/pbx/api/front/index/?ckey=cdr&akey=search"
    d, _ = http(url, data={"api_key": key, "start": start, "end": end},
                headers={"Content-Type": "application/x-www-form-urlencoded"})
    return d.get("data") or []


def record_url(host, key, cdr_id):
    url = f"https://{host}/pbx/api/front/index/?ckey=cdr&akey=record"
    d, _ = http(url, data={"api_key": key, "cdr_id": str(cdr_id)},
                headers={"Content-Type": "application/x-www-form-urlencoded"})
    return d


def talk_to_seconds(hhmmss):
    try:
        h, m, s = (hhmmss or "00:00:00").split(":")
        return int(h) * 3600 + int(m) * 60 + int(s)
    except Exception:
        return None


# ---------- Gemini ----------

def gemini_upload_file(key, data, mime, display_name):
    """Files API (resumable) でアップロードし file_uri を返す。"""
    start_url = f"{GEMINI_BASE}/upload/v1beta/files?key={key}"
    meta = json.dumps({"file": {"display_name": display_name}}).encode()
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
    res, _ = http(upload_url, data=data, headers={
        "Content-Length": str(len(data)),
        "X-Goog-Upload-Offset": "0",
        "X-Goog-Upload-Command": "upload, finalize",
    })
    f = res["file"]
    name, uri = f["name"], f["uri"]
    # ACTIVE になるまで待つ
    for _ in range(30):
        st, _ = http(f"{GEMINI_BASE}/v1beta/{name}?key={key}")
        if st.get("state") == "ACTIVE":
            return uri, f.get("mimeType", mime)
        if st.get("state") == "FAILED":
            raise RuntimeError("file processing FAILED")
        time.sleep(2)
    return uri, f.get("mimeType", mime)


def gemini_generate(key, prompt, file_uri, file_mime):
    url = f"{GEMINI_BASE}/v1beta/models/{GEMINI_MODEL}:generateContent?key={key}"
    payload = json.dumps({
        "contents": [{"parts": [
            {"text": prompt},
            {"file_data": {"mime_type": file_mime, "file_uri": file_uri}},
        ]}],
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": 8192},
    }).encode()
    res, _ = http(url, data=payload, headers={"Content-Type": "application/json"}, timeout=300)
    cand = (res.get("candidates") or [{}])[0]
    parts = cand.get("content", {}).get("parts", [])
    return "".join(p.get("text", "") for p in parts)


# ---------- パース ----------

def parse_gemini_output(text):
    out = {"raw": text, "transcript": "", "summary": "", "tsv": "", "fields": {}}

    def section(label):
        marker = f"【{label}】"
        if marker not in text:
            return ""
        rest = text.split(marker, 1)[1]
        for nxt in ("【", "```"):
            idx = rest.find(nxt)
            if idx != -1:
                rest = rest[:idx]
        return rest.strip()

    out["transcript"] = section("文字起こし")
    out["summary"] = section("要約")

    # ```text ... ``` の中のTSV行を取る
    tsv = ""
    if "```" in text:
        blocks = text.split("```")
        for b in blocks[1:]:  # コードブロック内
            body = b
            if body.lower().startswith("text"):
                body = body[4:]
            for line in body.splitlines():
                if "\t" in line:
                    tsv = line
                    break
            if tsv:
                break
    out["tsv"] = tsv
    if tsv:
        cells = tsv.split("\t")
        for i, col in enumerate(TSV_COLUMNS):
            out["fields"][col] = cells[i].strip() if i < len(cells) else ""
    return out


def to_iso_date(s):
    s = (s or "").strip().replace("-", "/")
    for fmt in ("%Y/%m/%d",):
        try:
            return dt.datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except Exception:
            pass
    return None


# ---------- Supabase ----------

def supabase_upsert(url, key, row):
    endpoint = f"{url}/rest/v1/call_logs?on_conflict=innovera_uniqid"
    body = json.dumps([row], ensure_ascii=False).encode()
    req = urllib.request.Request(endpoint, data=body, method="POST", headers={
        "apikey": key, "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=representation",
    })
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def build_row(rec, parsed, now):
    f = parsed["fields"]
    # A列タイムスタンプは生成時刻を信頼できる実時刻で必ず上書き（Geminiは現在時刻を知らない）
    ts = now.strftime("%Y/%m/%d %H:%M:%S")
    iso_date = to_iso_date(f.get("inquiry_date_raw")) or rec["start_time"][:10]
    date_disp = iso_date.replace("-", "/") if iso_date else ""
    row = {
        "innovera_uniqid": rec["uniqid"],
        "cdr_id": str(rec["id"]),
        "call_at": rec["start_time"],
        "direction": "着信" if rec.get("call_type") == "1" else "発信",
        "circuit_name": rec.get("circuit_name"),
        "counterpart_num": rec.get("caller_num"),
        "talk_seconds": talk_to_seconds(rec.get("talk_time")),
        "transcript": parsed["transcript"] or None,
        "summary": parsed["summary"] or None,
        "sheet_timestamp": ts,
        "agency": f.get("agency") or rec.get("circuit_name"),
        "inquiry_date": iso_date,
        "inquiry_time": f.get("inquiry_time") or rec["start_time"][11:16],
        "area": f.get("area") or None,
        "address1": f.get("address1") or None,
        "address2": f.get("address2") or None,
        "customer_name": f.get("customer_name") or rec.get("caller_name") or None,
        "gender": f.get("gender") or None,
        "customer_phone": f.get("customer_phone") or rec.get("caller_num"),
        "call_result": f.get("call_result") or None,
        "ng_reason": f.get("ng_reason") or None,
        "vehicle_type": f.get("vehicle_type") or None,
        "trouble_type": f.get("trouble_type") or None,
        "insurer": f.get("insurer") or None,
        "hours_told": f.get("hours_told") or None,
        "self_resolve_method": f.get("self_resolve_method") or None,
        "out_of_area_region": f.get("out_of_area_region") or None,
        "quoted_amount": f.get("quoted_amount") or None,
    }
    # 19項目を確実にA〜S列順で再構築（末尾空欄も保持して必ず19セル）
    cells = [
        ts, row["agency"] or "", date_disp, row["inquiry_time"] or "",
        row["area"] or "", row["address1"] or "", row["address2"] or "",
        row["customer_name"] or "", row["gender"] or "", row["customer_phone"] or "",
        row["call_result"] or "", row["ng_reason"] or "", row["vehicle_type"] or "",
        row["trouble_type"] or "", row["insurer"] or "", row["hours_told"] or "",
        row["self_resolve_method"] or "", row["out_of_area_region"] or "",
        row["quoted_amount"] or "",
    ]
    row["sheet_tsv"] = "\t".join(cells)
    return row


def pick_recording_url(resp):
    """record APIレスポンスから録音URLを取り出す（形が複数あり得るので総当り）。"""
    if not isinstance(resp, dict):
        return None
    data = resp.get("data", resp)
    candidates = []
    def walk(x):
        if isinstance(x, str) and x.startswith("http") and ".wav" in x:
            candidates.append(x)
        elif isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(data)
    return candidates[0] if candidates else None


def main():
    ap = argparse.ArgumentParser()
    today = "2026-06-10"
    ap.add_argument("--start", default=f"{today} 00:00:00")
    ap.add_argument("--end", default=f"{today} 23:59:59")
    ap.add_argument("--limit", type=int, default=1)
    ap.add_argument("--dry-run", action="store_true", help="Gemini/DB保存をスキップ")
    ap.add_argument("--save-audio", help="WAVをこのパスに保存（デバッグ用）")
    args = ap.parse_args()

    env = load_env(ENV_PATH)
    host, ikey = env["INNOVERA_HOST"], env["INNOVERA_API_KEY"]
    gkey = env.get("GEMINI_API_KEY")
    sb_url, sb_key = env.get("ELP_SUPABASE_URL"), env.get("ELP_SUPABASE_SERVICE_ROLE_KEY")
    prompt = open(PROMPT_PATH, encoding="utf-8").read()

    log(f"CDR検索 {args.start} 〜 {args.end}")
    recs = cdr_search(host, ikey, args.start, args.end)
    # 対象回線・録音あり・応答済み(talk_time>0)を新しい順に
    target = [r for r in recs
              if r.get("circuit_id") in TARGET_CIRCUITS
              and str(r.get("record_file_flg")) == "1"
              and talk_to_seconds(r.get("talk_time"))]
    target.sort(key=lambda r: r.get("start_time", ""), reverse=True)
    log(f"対象通話(録音あり・応答済み): {len(target)}件 → 先頭{args.limit}件を処理")

    for rec in target[: args.limit]:
        log(f"--- cdr_id={rec['id']} {rec['circuit_name']} "
            f"{rec['start_time']} caller={rec['caller_num']} talk={rec['talk_time']} ---")
        rresp = record_url(host, ikey, rec["id"])
        wav_url = pick_recording_url(rresp)
        if not wav_url:
            log(f"  録音URL取得失敗: {json.dumps(rresp, ensure_ascii=False)[:200]}")
            continue
        log(f"  録音URL: {wav_url[:90]}...")
        wav, _ = http(wav_url, raw=True, timeout=120)
        log(f"  WAV {len(wav)} bytes")
        if args.save_audio:
            open(args.save_audio, "wb").write(wav)
            log(f"  saved -> {args.save_audio}")
        if args.dry_run:
            log("  [dry-run] Gemini/DB をスキップ")
            continue

        meta_ctx = (
            f"\n\n# この録音の既知メタ情報（抽出時の補助に使うこと）\n"
            f"- 案件業者(B列): {rec['circuit_name']}\n"
            f"- 着信日時: {rec['start_time']}（C列=問い合せ日, D列=問合せ時間はこれを使う）\n"
            f"- お客様電話番号(J列): {rec['caller_num']}\n"
            f"- 対応オペレーター: {rec.get('callee_name','')}\n"
            f"音声は左右チャンネルで話者が分かれている場合があります。"
        )
        log("  Geminiへアップロード中...")
        uri, mime = gemini_upload_file(gkey, wav, "audio/wav", f"call_{rec['id']}.wav")
        log(f"  file_uri={uri}")
        log("  Gemini生成中...")
        text = gemini_generate(gkey, prompt + meta_ctx, uri, mime)
        parsed = parse_gemini_output(text)
        log(f"  要約: {parsed['summary'][:80]}")
        log(f"  TSV列数: {len(parsed['tsv'].split(chr(9))) if parsed['tsv'] else 0}")

        if sb_url and sb_key:
            row = build_row(rec, parsed, dt.datetime.now())
            res = supabase_upsert(sb_url, sb_key, row)
            log(f"  Supabase保存OK id={res[0].get('id') if res else '?'}")
        # 全文出力（確認用）
        print("\n========== Gemini出力 ==========\n")
        print(text)
        print("\n================================\n")


if __name__ == "__main__":
    main()
