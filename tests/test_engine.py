"""End-to-end tests without an API key (Claude is replaced by a fake client).
Run:  python -m tests.test_engine"""
import os
import tempfile
from types import SimpleNamespace as NS

os.environ["INFRA_AGENT_DB"] = os.path.join(tempfile.mkdtemp(), "test.db")

from engine import packs, rules, sources, store  # noqa: E402


class FakeClaude:
    def __init__(self):
        self.messages = self
        self.last = None

    def create(self, **kw):
        self.last = kw
        return NS(content=[NS(type="text", text="## Summary\nTest draft [x p.1]\n\nDraft prepared by AI – requires engineer approval.")])


def test_expression_engine():
    assert rules.evaluate("a >= 3 and not b", {"a": 3.1, "b": False})
    assert not rules.evaluate("a >= 3", {"a": None})                     # missing data never triggers
    assert rules.evaluate("s != 'x'", {"s": None})
    assert rules.evaluate("a < 10 and a > 0.1 and t >= 10", {"a": 5, "t": 13})
    try:
        rules.evaluate("__import__('os')", {})
        raise AssertionError("unsafe expression was evaluated")
    except ValueError:
        pass


def test_citation_verification():
    assert sources.verify("laws_telecom.json", "§ 7a 26. BImSchV", "durch die Betreiber gehört")
    assert not sources.verify("laws_telecom.json", "§ 7a 26. BImSchV", "durch die Betreiber genehmigt")
    assert not sources.verify("laws_telecom.json", "§ 4 Abs. 1 BEMFV", "durch die Betreiber gehört")  # wrong paragraph


def test_bridges_pack():
    p = packs.get("bridges_ni")
    assert p.unverified_rules == [], p.unverified_rules
    items = {i["key"]: i for i in p.items()}
    assert len(items) > 5000
    facts = [f for i in items.values() for f in i["facts"]]
    assert sum(f["verified"] for f in facts) / len(facts) > 0.99

    # a real section with grade 3.0 under heavy traffic (Anlage 2, page 41)
    it = items["2726506-1"]
    assert it["context"]["grade"] == 3.0 and it["context"]["dtv_heavy"] == 9757
    fired = {f["rule"] for f in p.assess(it)}
    assert {"B2", "B3", "B4"} <= fired, fired

    # a real conflict between the two annexes must block until an engineer decides
    conflicted = next(i for i in items.values() if i["conflicts"])
    assert p.assess(conflicted)[0]["severity"] == "blocker"
    resolved = {c["field"]: c["options"][0] for c in conflicted["conflicts"]}
    assert all(f["severity"] != "blocker" for f in p.assess(conflicted, resolved))

    fake = FakeClaude()
    findings = p.assess(conflicted, resolved)
    doc = p.draft(conflicted, resolved, findings, client=fake)
    assert "Use ONLY the data" in fake.last["system"]
    wo = p.approve(conflicted, doc, resolved, findings, "Test Engineer")
    log = store.table("decision_log")[0]
    assert log["work_order_id"] == wo and len(log["document_sha256"]) == 64
    assert store.table("master_data")[0]["field"] == "area_m2"


def test_telecom_pack():
    p = packs.get("telecom_permit")
    assert p.unverified_rules == ["T12"]          # building law source deliberately not loaded

    def rules_for(**kw):
        return {f["rule"] for f in p.assess(p.items(kw)[0])}

    default = rules_for()
    assert {"T1", "T6", "T9", "T10"} <= default
    assert "T2" in rules_for(new_eirp_w=5, existing_eirp_w=8)        # small cell pushes site over 10 W
    assert "T1" not in rules_for(new_eirp_w=5, existing_eirp_w=0)
    assert "T3" in rules_for(new_eirp_w=0.05)
    ready = rules_for(municipality_consulted=True, building_drawing=True)
    assert not any(r in ready for r in ("T5", "T6", "T7", "T8", "T9"))

    it = p.items({"municipality_consulted": True, "building_drawing": True})[0]
    findings = p.assess(it)
    try:
        p.approve(p.items()[0], "x", {}, p.assess(p.items()[0]), "Eng")
        raise AssertionError("approved with blockers")
    except ValueError:
        pass
    assert p.approve(it, p.draft(it, {}, findings, client=FakeClaude()), {}, findings, "Eng") > 0


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
    print("ALL TESTS PASSED")
