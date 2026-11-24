"""Write-back layer: a stand-in for the customer's system of record (SAP PM, DMS, permit tracker).
Nothing is written until an engineer approves. Every approval leaves an audit entry."""
import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(os.getenv("INFRA_AGENT_DB", Path(__file__).resolve().parent.parent / "system_of_record.db"))


def _con():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.executescript("""
    CREATE TABLE IF NOT EXISTS master_data (
        pack TEXT, item_key TEXT, field TEXT, value TEXT, set_by TEXT, updated_at TEXT,
        PRIMARY KEY (pack, item_key, field));
    CREATE TABLE IF NOT EXISTS work_orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT, pack TEXT, item_key TEXT, title TEXT,
        priority TEXT, document_md TEXT, status TEXT, created_at TEXT);
    CREATE TABLE IF NOT EXISTS decision_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT, work_order_id INTEGER, pack TEXT, item_key TEXT,
        approver TEXT, decision TEXT, details_json TEXT, document_sha256 TEXT, created_at TEXT);
    """)
    return con


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def approve(pack: str, item: dict, document_md: str, resolved: dict, findings: list[dict],
            approver: str) -> int:
    if not approver.strip():
        raise ValueError("An approver name is required.")
    if any(f["severity"] == "blocker" for f in findings):
        raise ValueError("Blockers are open – nothing can be filed.")
    con = _con()
    for field, choice in resolved.items():
        con.execute("INSERT OR REPLACE INTO master_data VALUES (?,?,?,?,?,?)",
                    (pack, item["key"], field, json.dumps(choice["value"]), approver, _now()))
    priority = "high" if any(f["severity"] == "high" for f in findings) else "normal"
    wo = con.execute(
        "INSERT INTO work_orders (pack,item_key,title,priority,document_md,status,created_at) VALUES (?,?,?,?,?,?,?)",
        (pack, item["key"], item["title"], priority, document_md, "released", _now())).lastrowid
    con.execute(
        "INSERT INTO decision_log (work_order_id,pack,item_key,approver,decision,details_json,document_sha256,created_at)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (wo, pack, item["key"], approver, "approved",
         json.dumps({"resolved_conflicts": resolved,
                     "findings": [f"{f['rule']} [{f['severity']}] {f['message']}" for f in findings]},
                    ensure_ascii=False, default=str),
         hashlib.sha256(document_md.encode()).hexdigest(), _now()))
    con.commit()
    return wo


def table(name: str, limit: int = 50) -> list[dict]:
    return [dict(r) for r in _con().execute(f"SELECT * FROM {name} ORDER BY rowid DESC LIMIT ?", (limit,))]
