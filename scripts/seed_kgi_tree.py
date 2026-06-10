#!/usr/bin/env python3
"""KGI → 事業部 → KPI → タスク(大/中/小) のダミー階層を投入する。
作業者(assignee_id)と指示者(director_id)も割り当てる。
kgis が空のときだけ実行（再実行で重複しない）。
"""
import json
import os
import urllib.parse
import urllib.request

ENV_PATH = os.path.join(os.path.dirname(__file__), "..", ".env")


def load_env():
    env = {}
    for line in open(ENV_PATH, encoding="utf-8"):
        line = line.rstrip("\n")
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


env = load_env()
URL = env["ELP_SUPABASE_URL"]
KEY = env["ELP_SUPABASE_SERVICE_ROLE_KEY"]
H = {"apikey": KEY, "Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}


def get(path):
    return json.load(urllib.request.urlopen(urllib.request.Request(f"{URL}/rest/v1/{path}", headers=H), timeout=30))


def post(tbl, rows):
    req = urllib.request.Request(f"{URL}/rest/v1/{tbl}", data=json.dumps(rows, ensure_ascii=False).encode(),
                                 method="POST", headers={**H, "Prefer": "return=representation"})
    return json.load(urllib.request.urlopen(req, timeout=30))


def emp(name, ext, role):
    found = get(f"employees?select=id&name=eq.{urllib.parse.quote(name)}")
    if found:
        return found[0]["id"]
    return post("employees", [{"name": name, "extension_num": ext, "role": role}])[0]["id"]


def task(title, status, level, kpi_id, parent_id, worker, director):
    return post("tasks", [{
        "title": title, "status": status, "level": level, "kpi_id": kpi_id,
        "parent_task_id": parent_id, "assignee_id": worker, "director_id": director,
        "source_type": "その他",
    }])[0]["id"]


def main():
    if get("kgis?select=id&limit=1"):
        print("already seeded (kgis not empty). skip.")
        return

    matsuo = emp("松尾", None, "代表取締役")
    azuma = emp("東", "1010", "車トラブル事業 リーダー")
    ikeda = emp("池田", "1003", "清掃事業 リーダー")
    suzuki = emp("鈴木", "1006", "オペレーター")
    fujita = emp("藤田", "1007", "オペレーター")

    kgi = post("kgis", [{
        "name": "車トラブル受付を全国モデルへ・売上2億円",
        "target": "年間売上 2億円", "period": "2026年度", "director_id": matsuo,
    }])[0]["id"]

    dA = post("divisions", [{"kgi_id": kgi, "name": "車トラブル受付センター事業", "lead_id": azuma, "sort": 1,
                             "description": "24時間の車トラブル受付・レッカー手配"}])[0]["id"]
    dB = post("divisions", [{"kgi_id": kgi, "name": "清掃事業", "lead_id": ikeda, "sort": 2,
                             "description": "ビル・施設の定期清掃"}])[0]["id"]
    dC = post("divisions", [{"kgi_id": kgi, "name": "業務基盤・DX", "lead_id": matsuo, "sort": 3,
                             "description": "顧客管理・社内システムの整備"}])[0]["id"]

    k_close = post("kpis", [{"division_id": dA, "name": "成約率", "target": "40%", "current": "35%", "sort": 1}])[0]["id"]
    k_lead = post("kpis", [{"division_id": dA, "name": "リード数（着信）", "target": "月1,500件", "current": "1,200件", "sort": 2}])[0]["id"]
    k_keep = post("kpis", [{"division_id": dB, "name": "契約継続率", "target": "95%", "current": "92%", "sort": 1}])[0]["id"]
    k_dx = post("kpis", [{"division_id": dC, "name": "顧客管理の一元化", "target": "Q3稼働", "current": "構築中", "sort": 1}])[0]["id"]

    big1 = task("受付フローの改善", "進行中", "大", k_close, None, azuma, matsuo)
    mid1 = task("夜間対応マニュアル整備", "進行中", "中", k_close, big1, suzuki, azuma)
    task("深夜帯の一次対応スクリプト作成", "未着手", "小", k_close, mid1, suzuki, azuma)
    task("受付フローのKPI集計", "確認待ち", "中", k_close, big1, fujita, azuma)
    big2 = task("提携先の拡大", "進行中", "大", k_lead, None, azuma, matsuo)
    task("あさひ自動車販売へ協定書送付", "進行中", "中", k_lead, big2, azuma, azuma)
    task("レッカー業者の新規契約", "未着手", "中", k_lead, big2, ikeda, azuma)
    big3 = task("既存顧客の維持・拡大", "進行中", "大", k_keep, None, ikeda, matsuo)
    task("グリーンビル契約更新交渉", "進行中", "中", k_keep, big3, ikeda, ikeda)
    task("清掃スタッフのシフト表作成", "未着手", "中", k_keep, big3, fujita, ikeda)
    big4 = task("顧客管理システム刷新", "進行中", "大", k_dx, None, azuma, matsuo)
    task("ベンダー選定", "進行中", "中", k_dx, big4, ikeda, matsuo)
    task("既存データ移行計画", "未着手", "中", k_dx, big4, suzuki, azuma)

    print("seeded: 1 KGI / 3 divisions / 4 KPIs / 13 tasks (大中小) with workers & directors")


if __name__ == "__main__":
    main()
