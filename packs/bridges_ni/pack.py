"""Bridges pack: real data from Bundestag Drucksache 19/12802 (Lower Saxony)."""
import json
from datetime import date
from pathlib import Path

from engine import llm, sources
from .parser import parse

SRC = "Drucksache_19-12802_Bruecken_Niedersachsen.pdf"
CACHE = Path(__file__).resolve().parent.parent.parent / "cache" / "bridges_ni.json"

FALLBACK_SCHEMA = {
    "type": "object",
    "properties": {
        "row": {"type": "integer"},
        "table": {"type": "string", "enum": ["Anlage 1", "Anlage 2"]},
        "asb": {"type": "string", "description": "7-digit ASB number"},
        "tbw": {"type": ["string", "null"], "description": "Teilbauwerk number/code"},
        "name": {"type": "string"},
        "area_m2": {"type": ["integer", "null"]},
        "grade": {"type": ["number", "null"], "description": "Zustandsnote (Anlage 2)"},
        "dtv": {"type": ["integer", "null"]}, "dtv_heavy": {"type": ["integer", "null"]},
        "stage": {"type": ["string", "null"]},
        "replacement": {"type": ["boolean", "null"]}, "replacement_year": {"type": ["integer", "null"]},
        "strengthening": {"type": ["boolean", "null"]}, "strengthening_year": {"type": ["integer", "null"]},
        "demolition_only": {"type": ["boolean", "null"], "description": "true if text says 'nur Abriss'"},
        "quote": {"type": "string", "description": "exact substring of the row text identifying this row"},
    },
    "required": ["row", "table", "asb", "quote"],
}

FALLBACK_INSTRUCTIONS = (
    "These are rows from German bridge tables that a regex parser could not read. "
    "Anlage 1 columns: Straße, ASB-Nr., Teilbauwerk, Bauwerksname, Fläche m², Stadium der Planung, "
    "Ersatzneubau ja/nein, Umsetzung Jahr, Verstärkung ja/nein, Umsetzung Jahr, Kosten Verstärkung, "
    "Kosten Ersatzneubau. Anlage 2 columns: Bauwerksname, Ort, Fläche, Zustandsnote, Notenklasse, "
    "Bauwerks-Nr. (ASB), Teilbw-Nr., oben, unten, DTV, DTV-SV.")


def build(force: bool = False) -> dict:
    """Parse + LLM fallback + reconcile. Cached, because the PDF has 188 pages."""
    if CACHE.exists() and not force:
        return json.loads(CACHE.read_text())
    r = parse(str(sources.SOURCES_DIR / SRC))
    a1, a2, unparsed = r["anlage1"], r["anlage2"], r["unparsed"]
    for row in a1 + a2:
        row["source"], row["ref"], row["by"] = SRC, f"p.{row['page']}", "parser"

    recovered, still_unparsed = [], unparsed
    if unparsed and llm.available():
        rows = [{"source": SRC, "ref": f"p.{u['page']}", "text": u["text"]} for u in unparsed]
        recovered = []
        for i in range(0, len(rows), 10):
            try:
                batch = llm.extract_rows(rows[i:i + 10], FALLBACK_SCHEMA, FALLBACK_INSTRUCTIONS)
            except Exception as e:  # Claude unavailable/failed: rows stay in human review, app keeps working
                print(f"[bridges_ni] Claude fallback skipped: {e}")
                batch = []
            for x in batch:
                x["by"] = "claude"
                x["verified"] = sources.verify(x["source"], x["ref"], x["quote"])
                if x["verified"]:
                    recovered.append(x)
        found = {(x["ref"]) for x in recovered}
        still_unparsed = [u for u in unparsed if f"p.{u['page']}" not in found]
        for x in recovered:
            (a1 if x["table"] == "Anlage 1" else a2).append(
                {**x, "label": x.get("name"), "tbw": x.get("tbw"), "page": int(x["ref"][2:])})

    data = {"anlage1": a1, "anlage2": a2, "unparsed": still_unparsed,
            "stats": {"pages": len(r["pages"]), "anlage1_rows": len(a1), "anlage2_rows": len(a2),
                      "parsed_by_regex": len(r["anlage1"]) + len(r["anlage2"]) - len(recovered),
                      "recovered_by_claude": len(recovered), "needs_human_review": len(still_unparsed)}}
    CACHE.parent.mkdir(exist_ok=True)
    CACHE.write_text(json.dumps(data, ensure_ascii=False))
    return data


def _fact(label, value, row, quote=None):
    q = quote or row["quote"]
    return {"label": label, "value": value, "source": row["source"], "ref": row["ref"],
            "quote": q, "verified": sources.verify(row["source"], row["ref"], q), "by": row.get("by")}


def get_items(inputs=None, tolerance_pct: float = 2) -> list[dict]:
    data = build()
    prog_by_key, prog_by_asb = {}, {}
    for p in data["anlage1"]:
        prog_by_key[(p["asb"], str(p["tbw"]))] = p
        prog_by_asb.setdefault(p["asb"], []).append(p)

    items = []
    for b in data["anlage2"]:
        p = prog_by_key.get((b["asb"], str(b["tbw"])))
        if p is None and len(prog_by_asb.get(b["asb"], [])) == 1 and prog_by_asb[b["asb"]][0]["tbw"] is None:
            p = prog_by_asb[b["asb"]][0]       # programme list omits the section number
        ctx = {"grade": b.get("grade"), "dtv": b.get("dtv"), "dtv_heavy": b.get("dtv_heavy"),
               "area_m2": b.get("area_m2"), "in_programme": p is not None,
               "replacement": bool(p and p.get("replacement")), "replacement_year": p and p.get("replacement_year"),
               "strengthening": bool(p and p.get("strengthening")),
               "strengthening_year": p and p.get("strengthening_year"),
               "stage": p and p.get("stage"), "current_year": date.today().year}
        facts = [_fact("Condition grade (Zustandsnote)", b.get("grade"), b),
                 _fact("Area m²", b.get("area_m2"), b),
                 _fact("Traffic per day (DTV / heavy)", f"{b.get('dtv')} / {b.get('dtv_heavy')}", b)]
        conflicts = []
        if p:
            yn = lambda v: "yes" if v else ("no" if v is False else "n/a")
            facts.append(_fact(
                "Strengthening programme (Anlage 1)",
                f"planning stage (Stadium der Planung): {p.get('stage')}; "
                f"replacement (Ersatzneubau): {yn(p.get('replacement'))}, planned implementation year "
                f"{p.get('replacement_year') or 'n/a'}; strengthening (Verstärkung): {yn(p.get('strengthening'))}, "
                f"planned implementation year {p.get('strengthening_year') or 'n/a'}", p))
            if p.get("cost_replacement_eur") or p.get("cost_strengthening_eur"):
                facts.append(_fact("Estimated cost EUR (replacement / strengthening)",
                                   f"{p.get('cost_replacement_eur')} / {p.get('cost_strengthening_eur')}", p))
            a, c = b.get("area_m2"), p.get("area_m2")
            if a and c and abs(a - c) / max(a, c) * 100 > tolerance_pct:
                conflicts.append({"field": "area_m2", "label": "Bridge area (m²)", "suggested": 0, "options": [
                    {"value": a, "source": b["source"], "ref": b["ref"], "quote": b["quote"],
                     "note": "condition table (Anlage 2)", "verified": sources.verify(b["source"], b["ref"], b["quote"])},
                    {"value": c, "source": p["source"], "ref": p["ref"], "quote": p["quote"],
                     "note": "programme table (Anlage 1)", "verified": sources.verify(p["source"], p["ref"], p["quote"])}]})
        items.append({"key": f"{b['asb']}-{b['tbw']}",
                      "title": (b.get("label") or b.get("name") or "")[:110],
                      "subtitle": f"ASB {b['asb']} / TBW {b['tbw']} · grade {b.get('grade')} · {b['ref']}",
                      "context": ctx, "facts": facts, "conflicts": conflicts})
    return items


def summary() -> dict:
    data = build()
    a2_keys = {(b["asb"], str(b["tbw"])) for b in data["anlage2"]}
    a2_asb = {b["asb"] for b in data["anlage2"]}
    orphans = [p for p in data["anlage1"]
               if (p["asb"], str(p["tbw"])) not in a2_keys and not (p["tbw"] is None and p["asb"] in a2_asb)]
    return {**data["stats"],
            "programme_rows_without_condition_record": len(orphans),
            "orphans_demolished": sum(1 for p in orphans if p.get("stage") == "Bauwerk beseitigt"),
            "unparsed_examples": [u["text"][:160] for u in data["unparsed"][:5]]}
