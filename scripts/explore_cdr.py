#!/usr/bin/env python3
"""イノベラCDR検索の構造調査用スクリプト。
対象2番号の録音あり通話を取得してレコード構造を表示する。
.env から認証情報を読む（リポジトリには値を置かない）。
"""
import json
import os
import sys
import urllib.parse
import urllib.request

ENV_PATH = os.path.join(os.path.dirname(__file__), "..", ".env")
TARGET_NUMBERS = ["05088960087", "05088906176"]  # 相談窓口 / 受付センター


def load_env(path):
    env = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


def cdr_search(host, key, start, end):
    url = f"https://{host}/pbx/api/front/index/?ckey=cdr&akey=search"
    body = urllib.parse.urlencode({"api_key": key, "start": start, "end": end}).encode()
    req = urllib.request.Request(url, data=body, headers={
        "Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8", "replace"), strict=False)


def main():
    env = load_env(ENV_PATH)
    host = env["INNOVERA_HOST"]
    key = env["INNOVERA_API_KEY"]
    start = sys.argv[1] if len(sys.argv) > 1 else "2026-06-09 00:00:00"
    end = sys.argv[2] if len(sys.argv) > 2 else "2026-06-10 23:59:59"

    d = cdr_search(host, key, start, end)
    data = d.get("data") or []
    print(f"result={d.get('result')} err={d.get('error_code')} total={len(data)}")
    if not data:
        return
    print("FIELDS:", list(data[0].keys()))
    print("\n--- 1件目の全フィールド ---")
    for k, v in data[0].items():
        print(f"  {k}: {v}")

    # 対象番号でフィルタ（着信先/発信先のどちらかに含まれる想定）
    def matches(rec):
        blob = json.dumps(rec, ensure_ascii=False)
        return any(n in blob for n in TARGET_NUMBERS)

    hit = [r for r in data if matches(r)]
    print(f"\n--- 対象2番号にマッチ: {len(hit)}件 ---")
    for r in hit[:10]:
        print(json.dumps({k: r.get(k) for k in r}, ensure_ascii=False)[:300])


if __name__ == "__main__":
    main()
