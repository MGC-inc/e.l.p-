"""使い方: python publish_supabase.py 出力フォルダ(out/public配下のPDFをアップロード)

生成したPDF（out/public/*.pdf）を、既存のSupabase Storage（deal-recordingsバケットと同じ
プロジェクト）に新規バケット「weekly-pdca」を作ってアップロードする（Public）。
line-webhookのPython関数は外部ライブラリ依存ゼロという方針（README参照）を崩さないため、
PDF生成（reportlab/matplotlib）が必要なこの用途ではVercelではなくSupabase Storageを使う
（商談分析運用.md 8章「実行方式」参照）。

ファイル名はbuild_all.py側で既にランダム文字列（推測不可）になっているため、そのままの
ファイル名でアップロードするだけでよい（バケット名を変える以外、URLの当てずっぽうを防ぐ工夫は
build_all.py任せ）。

出力: 標準出力に "PUBLIC_BASE_URL=<値>" を1行印字する（GitHub Actions側で後続ステップに渡す）。

環境変数: ELP_SUPABASE_URL / ELP_SUPABASE_SERVICE_ROLE_KEY
"""
import os
import sys
import json
import urllib.error
import urllib.request

BUCKET = "weekly-pdca"


def _req(method: str, path: str, data: bytes | None = None, headers: dict | None = None):
    url = os.environ["ELP_SUPABASE_URL"].rstrip("/") + path
    key = os.environ["ELP_SUPABASE_SERVICE_ROLE_KEY"]
    h = {"apikey": key, "Authorization": f"Bearer {key}"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=data, method=method, headers=h)
    return urllib.request.urlopen(req, timeout=60)


def ensure_bucket() -> None:
    body = json.dumps({"id": BUCKET, "name": BUCKET, "public": True}).encode()
    try:
        _req("POST", "/storage/v1/bucket", body, {"Content-Type": "application/json"})
        print(f"バケット {BUCKET} を新規作成しました。")
    except urllib.error.HTTPError as e:
        if e.code in (400, 409):
            pass  # 既に存在する場合はそのまま使う
        else:
            raise


def upload(local_path: str, filename: str) -> None:
    with open(local_path, "rb") as f:
        data = f.read()
    try:
        _req("POST", f"/storage/v1/object/{BUCKET}/{filename}", data,
             {"Content-Type": "application/pdf", "x-upsert": "true"})
    except urllib.error.HTTPError as e:
        sys.exit(f"アップロード失敗: {filename} ({e.code} {e.read().decode()[:200]})")


def main(out_dir: str) -> None:
    ensure_bucket()
    pub_dir = os.path.join(out_dir, "public")
    files = [f for f in os.listdir(pub_dir) if f.endswith(".pdf")]
    for f in files:
        upload(os.path.join(pub_dir, f), f)
        print(f"公開: {f}")
    base_url = os.environ["ELP_SUPABASE_URL"].rstrip("/") + f"/storage/v1/object/public/{BUCKET}"
    print(f"{len(files)}件公開。 PUBLIC_BASE_URL={base_url}")
    print(f"PUBLIC_BASE_URL={base_url}")


if __name__ == "__main__":
    main(sys.argv[1])
