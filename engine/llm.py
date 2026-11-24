"""Agent layer: Claude is used where judgment over messy text helps, and nowhere else.
  1. fallback extraction: table rows the deterministic parser could not read
  2. drafting: the brief / dossier, from the verified record only, with citations
Every value Claude returns still has to pass sources.verify() before it is trusted."""
import json
import os

MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-5-5")


def _client():
    import anthropic
    return anthropic.Anthropic()


def available() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY"))


def extract_rows(rows: list[dict], schema: dict, instructions: str, client=None) -> list[dict]:
    """rows: [{'source','ref','text'}]  ->  structured rows, each carrying source/ref/quote."""
    client = client or _client()
    tool = {"name": "save_rows", "description": "Save the extracted table rows.",
            "input_schema": {"type": "object", "properties": {"rows": {"type": "array", "items": schema}},
                             "required": ["rows"]}}
    listing = "\n\n".join(f"[ROW {i} | {r['source']} {r['ref']}]\n{r['text']}" for i, r in enumerate(rows))
    # tool_choice "auto" works on every model; the prompt tells Claude to always call the tool.
    resp = client.messages.create(
        model=MODEL, max_tokens=8000, tools=[tool], tool_choice={"type": "auto"},
        system=instructions + "\nAlways answer by calling the save_rows tool. "
               "Only use values that are literally in the text. 'quote' must be an exact "
               "substring of the row text. 'row' is the ROW index. A text block may contain several rows.",
        messages=[{"role": "user", "content": listing}])
    call = next((b for b in resp.content if b.type == "tool_use"), None)
    out = call.input.get("rows", []) if call else []
    for r in out:
        src = rows[int(r.get("row", 0))]
        r["source"], r["ref"] = src["source"], src["ref"]
    return out


def draft(system: str, payload: dict, client=None) -> str:
    client = client or _client()
    resp = client.messages.create(
        model=MODEL, max_tokens=2500, system=system,
        messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False, default=str)}])
    return "".join(b.text for b in resp.content if b.type == "text")
