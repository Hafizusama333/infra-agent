"""Telecom pack: the "documents" are the operator's site data; the knowledge is federal law."""
from pathlib import Path
import yaml

FORM = yaml.safe_load((Path(__file__).parent / "pack.yaml").read_text(encoding="utf-8"))["input_form"]


def defaults() -> dict:
    return {f["name"]: f["default"] for f in FORM}


def get_items(inputs: dict | None = None) -> list[dict]:
    x = {**defaults(), **(inputs or {})}
    for f in FORM:                                    # normalise types coming from forms / n8n / JSON
        v = x.get(f["name"])
        if f["type"] == "number" and v not in (None, ""):
            x[f["name"]] = float(v)
        elif f["type"] == "bool" and isinstance(v, str):
            x[f["name"]] = v.strip().lower() in ("true", "yes", "ja", "1", "on")
    total = (x["new_eirp_w"] or 0) + (x["existing_eirp_w"] or 0)
    ctx = {**x, "total_eirp_w": total,
           "needs_certificate": (x["new_eirp_w"] or 0) > 0.1 and total >= 10,
           "coords_given": x.get("lat") not in (None, "") and x.get("lon") not in (None, "")}
    facts = [{"label": f["label"], "value": x[f["name"]], "source": "operator input", "ref": "site form",
              "quote": None, "verified": None, "by": "operator"} for f in FORM]
    facts.append({"label": "Total EIRP at site (W)", "value": total, "source": "calculated", "ref": "new + existing",
                  "quote": None, "verified": None, "by": "engine"})
    return [{"key": x["site_name"], "title": x["site_name"], "subtitle": x["address"],
             "context": ctx, "facts": facts, "conflicts": []}]
