"""Rule layer: rules live in each pack's YAML, never in engine code.

    - id: T1
      severity: high                    # info | medium | high | blocker
      when: "total_eirp_w >= 10"        # safe expression over the item's context
      message: "Site certificate required (total EIRP {total_eirp_w} W)."
      cite: {source: laws_telecom.json, ref: "§ 4 Abs. 1 BEMFV", quote: "..."}

Every rule's citation is verified against the source text when the pack loads, so the
rulebook cannot silently drift away from the regulation it claims to implement."""
import ast
import operator as op

from . import sources

SEVERITY_ORDER = {"blocker": 0, "high": 1, "medium": 2, "info": 3}

_OPS = {ast.Gt: op.gt, ast.GtE: op.ge, ast.Lt: op.lt, ast.LtE: op.le, ast.Eq: op.eq,
        ast.NotEq: op.ne, ast.In: lambda a, b: a in b, ast.NotIn: lambda a, b: a not in b,
        ast.Add: op.add, ast.Sub: op.sub, ast.Mult: op.mul, ast.Div: op.truediv}


def evaluate(expr: str, ctx: dict):
    """Tiny, safe expression evaluator: comparisons, and/or/not, arithmetic, literals, names."""
    def ev(n):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant):
            return n.value
        if isinstance(n, ast.Name):
            return ctx.get(n.id)
        if isinstance(n, (ast.List, ast.Tuple)):
            return [ev(e) for e in n.elts]
        if isinstance(n, ast.BoolOp):
            vals = (ev(v) for v in n.values)
            return all(vals) if isinstance(n.op, ast.And) else any(vals)
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, ast.Not):
            return not ev(n.operand)
        if isinstance(n, ast.BinOp) and type(n.op) in _OPS:
            left, right = ev(n.left), ev(n.right)
            return None if left is None or right is None else _OPS[type(n.op)](left, right)
        if isinstance(n, ast.Compare):
            left = ev(n.left)
            for o, comp in zip(n.ops, n.comparators):
                right = ev(comp)
                if left is None or right is None:
                    if isinstance(o, (ast.Eq, ast.NotEq)):
                        if not _OPS[type(o)](left, right):
                            return False
                        left = right
                        continue
                    return False          # unknown data never triggers or clears a numeric rule
                if not _OPS[type(o)](left, right):
                    return False
                left = right
            return True
        raise ValueError(f"Unsupported expression: {ast.dump(n)}")
    return ev(ast.parse(expr, mode="eval"))


def check_citations(rules: list[dict]) -> list[dict]:
    for r in rules:
        c = r.get("cite")
        if c:
            c["verified"] = sources.verify(c["source"], c["ref"], c.get("quote", ""))
            c["url"] = c.get("url") or sources.url_of(c["source"], c["ref"])
    return rules


class _Safe(dict):
    def __missing__(self, k):
        return "n/a"


def run(rules: list[dict], ctx: dict, conflicts: list[dict], resolved: dict) -> list[dict]:
    findings = []
    open_conflicts = [c["field"] for c in conflicts if c["field"] not in resolved]
    if open_conflicts:
        findings.append({"rule": "R0", "severity": "blocker", "title": "Unresolved data conflict",
                         "message": f"Sources disagree on: {', '.join(open_conflicts)}. "
                                    "An engineer must decide before anything is filed.", "cite": None})
    ctx = {**ctx, **{k: v["value"] for k, v in resolved.items()}}
    for r in rules:
        try:
            hit = evaluate(r["when"], ctx)
        except Exception as e:  # a broken rule must be visible, not silently skipped
            findings.append({"rule": r["id"], "severity": "blocker", "title": "Rule error",
                             "message": f"{r['id']}: {e}", "cite": None})
            continue
        if hit:
            findings.append({"rule": r["id"], "severity": r["severity"], "title": r.get("title", ""),
                             "message": r["message"].format_map(_Safe(ctx)), "cite": r.get("cite")})
    return sorted(findings, key=lambda f: SEVERITY_ORDER[f["severity"]])
