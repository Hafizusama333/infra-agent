"""HTTP API – the same engine, callable from n8n, SAP workflows or any other system.
Run:  uvicorn api:app --port 8000        Docs: http://localhost:8000/docs

Every call names a pack and an item:
  - input packs (telecom_permit):  send "inputs" (the site form)
  - document packs (bridges_ni):  send "key" (e.g. "2726506-1")
"resolved" maps a conflict field to the index of the option the engineer chose, e.g. {"area_m2": 0}.
"""
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from engine import packs

app = FastAPI(title="Infra Agent API",
              description="One engine for infrastructure workflows: cited facts, rule checks, "
                          "AI drafting, human approval, write-back.")


class ItemRef(BaseModel):
    inputs: dict | None = None
    key: str | None = None
    resolved: dict[str, int] = {}


class Approval(ItemRef):
    document_md: str
    approver: str


def _pack(pid):
    if pid not in packs.available():
        raise HTTPException(404, f"Unknown pack '{pid}'. Available: {packs.available()}")
    return packs.get(pid)


_items_cache = {}


def _item(pack, ref: ItemRef):
    if pack.meta.get("input_form"):
        return pack.items(ref.inputs or {})[0]
    if pack.id not in _items_cache:
        _items_cache[pack.id] = {i["key"]: i for i in pack.items()}
    item = _items_cache[pack.id].get(ref.key or "")
    if not item:
        raise HTTPException(404, f"No item '{ref.key}' in pack '{pack.id}'.")
    return item


def _resolved(item, choices: dict[str, int]) -> dict:
    out = {}
    for c in item.get("conflicts", []):
        if c["field"] in choices:
            out[c["field"]] = c["options"][int(choices[c["field"]])]
    return out


def _assess(pid, ref):
    pack = _pack(pid)
    item = _item(pack, ref)
    resolved = _resolved(item, ref.resolved)
    findings = pack.assess(item, resolved)
    return pack, item, resolved, findings


@app.get("/packs")
def list_packs():
    return [{"id": p, "name": packs.get(p).meta["name"], "vertical": packs.get(p).meta["vertical"],
             "rules": len(packs.get(p).rules), "unverified_rule_citations": packs.get(p).unverified_rules}
            for p in packs.available()]


@app.post("/packs/{pid}/assess")
def assess(pid: str, ref: ItemRef):
    _, item, _, findings = _assess(pid, ref)
    blockers = [f for f in findings if f["severity"] == "blocker"]
    return {"item": {k: item[k] for k in ("key", "title", "subtitle", "facts", "conflicts")},
            "findings": findings, "blocked": bool(blockers),
            "summary": "\n".join(f"[{f['severity'].upper()}] {f['rule']} {f['title']}: {f['message']}"
                                 for f in findings)}


@app.post("/packs/{pid}/draft")
def draft(pid: str, ref: ItemRef):
    pack, item, resolved, findings = _assess(pid, ref)
    if any(f["severity"] == "blocker" for f in findings):
        raise HTTPException(409, "Blockers are open – resolve them first.")
    return {"document_md": pack.draft(item, resolved, findings)}


@app.post("/packs/{pid}/approve")
def approve(pid: str, body: Approval):
    pack, item, resolved, findings = _assess(pid, body)
    try:
        return {"work_order_id": pack.approve(item, body.document_md, resolved, findings, body.approver)}
    except ValueError as e:
        raise HTTPException(409, str(e))


@app.get("/packs/bridges_ni/top")
def top_bridges(min_grade: float = 3.0, limit: int = 20):
    """Ranked list for a weekly report (used by the n8n workflow)."""
    pack = _pack("bridges_ni")
    if pack.id not in _items_cache:
        _items_cache[pack.id] = {i["key"]: i for i in pack.items()}
    rows = []
    for it in _items_cache[pack.id].values():
        if (it["context"]["grade"] or 0) < min_grade:
            continue
        fs = pack.assess(it)
        rows.append({"key": it["key"], "bridge": it["title"], "grade": it["context"]["grade"],
                     "heavy_per_day": it["context"]["dtv_heavy"],
                     "high_flags": [f["rule"] for f in fs if f["severity"] == "high"],
                     "source": it["facts"][0]["source"] + " " + it["facts"][0]["ref"]})
    rows.sort(key=lambda r: (len(r["high_flags"]), r["grade"], r["heavy_per_day"] or 0), reverse=True)
    return rows[:limit]
