"""Pack loader + the one workflow every pack shares:

    items -> facts with verified citations -> conflicts (engineer decides) -> rules
          -> Claude drafts from verified data only -> engineer approves -> write-back + audit log

A pack = pack.yaml (rules with legal/source citations, draft prompt, optional input form)
       + pack.py (how to turn its documents or inputs into items).
The engine never changes when a pack is added."""
import importlib
from functools import lru_cache
from pathlib import Path

import yaml

from . import llm, rules, store

PACKS_DIR = Path(__file__).resolve().parent.parent / "packs"

CITATION_RULES = """
Hard rules:
- Use ONLY the data in the JSON. Never invent numbers, dates, costs, laws or measures.
- End every factual sentence with its citation in square brackets: [<source> <ref>],
  e.g. [Drucksache_19-12802_Bruecken_Niedersachsen.pdf p.41] or [laws_telecom.json § 4 Abs. 1 BEMFV].
- If a citation in the JSON has verified=false, say the requirement must be checked manually.
- Where sources conflicted, use the engineer's resolved value and say so.
- End with a line: "Draft prepared by AI – requires engineer approval."
"""


class Pack:
    def __init__(self, pack_id: str):
        folder = PACKS_DIR / pack_id
        self.meta = yaml.safe_load((folder / "pack.yaml").read_text(encoding="utf-8"))
        self.id = self.meta["id"]
        self.rules = rules.check_citations(self.meta.get("rules", []))
        self.module = importlib.import_module(f"packs.{pack_id}.pack")

    @property
    def unverified_rules(self):
        return [r["id"] for r in self.rules if r.get("cite") and not r["cite"]["verified"]]

    def items(self, inputs: dict | None = None) -> list[dict]:
        kwargs = {}
        if "conflict_tolerance_pct" in self.meta:
            kwargs["tolerance_pct"] = self.meta["conflict_tolerance_pct"]
        return self.module.get_items(inputs, **kwargs)

    def assess(self, item: dict, resolved: dict | None = None) -> list[dict]:
        return rules.run(self.rules, item["context"], item.get("conflicts", []), resolved or {})

    def draft(self, item: dict, resolved: dict, findings: list[dict], client=None) -> str:
        if any(f["severity"] == "blocker" for f in findings):
            raise ValueError("Resolve blockers before drafting.")
        conflicts = [{"field": c["field"], "label": c["label"],
                      "options": [{k: o.get(k) for k in ("value", "note", "source", "ref")} for o in c["options"]],
                      "engineer_chose": (resolved.get(c["field"]) or {}).get("value")}
                     for c in item.get("conflicts", [])]
        payload = {"item": {k: item[k] for k in ("key", "title", "subtitle", "facts")},
                   "conflicts_between_sources": conflicts, "rule_findings": findings}
        return llm.draft(self.meta["draft_prompt"] + CITATION_RULES, payload, client)

    def approve(self, item, document_md, resolved, findings, approver) -> int:
        return store.approve(self.id, item, document_md, resolved, findings, approver)

    def summary(self) -> dict:
        return self.module.summary() if hasattr(self.module, "summary") else {}


@lru_cache(maxsize=None)
def get(pack_id: str) -> Pack:
    return Pack(pack_id)


def available() -> list[str]:
    return sorted(p.name for p in PACKS_DIR.iterdir() if (p / "pack.yaml").exists())
