"""
历史记录（SQLite）
==================
每次生成方案存一条记录；打样后可补充「实际结果」和「修正建议」。
"""

import sqlite3
import json
from datetime import datetime

DB_PATH = "data/history.db"


def init_db(db_path=DB_PATH):
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS records (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at   TEXT NOT NULL,
            target_hex   TEXT NOT NULL,
            material     TEXT,
            total_weight REAL,
            recipe_json  TEXT,          -- 推荐方案的配比（JSON）
            mixed_hex    TEXT,
            delta_e      REAL,
            note         TEXT,          -- 备注
            actual_result TEXT,         -- 实际打样结果，如 " 偏黑 "
            fix_suggestion TEXT         -- 修正建议
        )
    """)
    conn.commit()
    conn.close()


def save_record(target_hex, material, total_weight, plan,
                note="", db_path=DB_PATH):
    """保存一条方案记录，返回新记录 id。"""
    init_db(db_path)
    recipe = [
        {"色号": c["色号"], "HEX": c.get("HEX", ""), "比例": c["比例"],
         "用量kg": c.get("用量kg")}
        for c in plan["components"]
    ]
    conn = sqlite3.connect(db_path)
    cur = conn.execute(
        """INSERT INTO records
           (created_at, target_hex, material, total_weight,
            recipe_json, mixed_hex, delta_e, note,
            actual_result, fix_suggestion)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
         target_hex, material, total_weight,
         json.dumps(recipe, ensure_ascii=False),
         plan["mixed_hex"], plan["delta_e"], note, "", ""),
    )
    conn.commit()
    rid = cur.lastrowid
    conn.close()
    return rid


def update_result(record_id, actual_result, fix_suggestion, db_path=DB_PATH):
    """打样后补充实际结果和修正建议。"""
    conn = sqlite3.connect(db_path)
    conn.execute(
        "UPDATE records SET actual_result=?, fix_suggestion=? WHERE id=?",
        (actual_result, fix_suggestion, record_id),
    )
    conn.commit()
    conn.close()


def delete_record(record_id, db_path=DB_PATH):
    """删除单条历史记录。"""
    conn = sqlite3.connect(db_path)
    conn.execute("DELETE FROM records WHERE id=?", (record_id,))
    conn.commit()
    conn.close()


def delete_records(record_ids, db_path=DB_PATH):
    """批量删除历史记录。record_ids: 可迭代的 id 列表。"""
    if not record_ids:
        return 0
    conn = sqlite3.connect(db_path)
    qmarks = ",".join("?" for _ in record_ids)
    cur = conn.execute(f"DELETE FROM records WHERE id IN ({qmarks})",
                       list(record_ids))
    conn.commit()
    n = cur.rowcount
    conn.close()
    return n


def get_records(db_path=DB_PATH):
    """返回所有记录（新的在前），list[dict]。"""
    init_db(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM records ORDER BY id DESC"
    ).fetchall()
    conn.close()
    result = []
    for r in rows:
        d = dict(r)
        try:
            d["recipe"] = json.loads(d["recipe_json"]) if d["recipe_json"] else []
        except Exception:
            d["recipe"] = []
        result.append(d)
    return result


if __name__ == "__main__":
    from ratio_search import search_recipe
    res = search_recipe("#07166B", 100, "EPDM", n_colors=4)
    rid = save_record("#07166B", "EPDM", 100, res["recommended"], note="测试记录")
    print("已保存记录 id =", rid)
    update_result(rid, "偏黑", "减少 FK-022，增加 FK-021")
    for rec in get_records():
        print(rec["id"], rec["created_at"], rec["target_hex"],
              rec["mixed_hex"], "ΔE=" + str(rec["delta_e"]),
              "| 实际:", rec["actual_result"], "| 建议:", rec["fix_suggestion"])
