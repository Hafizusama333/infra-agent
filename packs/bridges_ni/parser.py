"""Deterministic table parser for Bundestag Drucksache 19/12802 (bridges in Lower Saxony).

Anlage 1: 403 bridge sections in the federal strengthening programme (decision, year, cost)
Anlage 2: bridge sections with condition grade (Zustandsnote) and traffic (DTV, heavy vehicles)

Rows the regex cannot read with confidence are returned as `unparsed` so the engine can
hand them to Claude (LLM fallback) instead of silently dropping them.
"""
import re
from pypdf import PdfReader

ROAD = r"(?:BAB|B)\s?\d+[a-z]?"

A1_START = re.compile(rf"^({ROAD})\s+(\d{{7}})\b")
A1_ROW = re.compile(
    rf"^(?P<road>{ROAD})\s+(?P<asb>\d{{7}})\s+(?:(?P<tbw>\d)\s+)?(?P<name>.*?)\s+(?P<area>\d+)\s+"
    r"(?P<stage>erledigt|aktiv|Bauwerk beseitigt|ruhend|zurückgestellt|in Bau|[A-Za-zÄÖÜäöüß ]+?)"
    r"(?:\s+(?P<ers>ja|nein)\s*(?P<ers_year>\d{4})?\s+(?P<ver>ja|nein)\s*(?P<ver_year>\d{4})?"
    r"\s*(?P<c1>\d{1,3}(?:\.\d{3})+|\d+)?\s*(?P<c2>\d{1,3}(?:\.\d{3})+|\d+)?)?\s*$")

A2_TAIL = re.compile(
    r"^(?P<label>.*?)\s+(?P<area>\d+)\s+(?P<grade>\d(?:,\d)?)\s+(?P<lo>\d\.\d)\s*-?\s*(?P<hi>\d\.\d)\s+"
    r"(?P<asb>\d{7})\s+(?P<tbw>\d|[A-Z]\d?)\s+(?P<rest>.*?)\s*$")
TRAFFIC = re.compile(r"(\d+)\s+(\d+)\s*$")

SKIP = ("Drucksache", "Deutscher Bundestag", "Bauwerke (Teilbauwerke)", "Hier: Verkehrsmengen",
        "Anlage 1", "Anlage 2", "Bauwerksname", "Straße ASB", "Fläche", "Teil-", "bauwerk",
        "Stadium", "Ersatzneubau", "Verstärkung", "Kosten", "1)", "[m²]", "note", "klasse",
        "Nr.", "Teilbw", "Zustandsnoten", "Bauwerks-")


def _num(s):
    return int(s.replace(".", "")) if s else None


def _lines(page_text):
    for raw in page_text.splitlines():
        line = re.sub(r"\s+", " ", raw).strip()
        if line and not line.startswith(SKIP):
            yield line


def parse(pdf_path: str) -> dict:
    pages = [p.extract_text() or "" for p in PdfReader(pdf_path).pages]
    a1, a2, unparsed = [], [], []

    for pno, text in enumerate(pages, start=1):
        if "Anlage 1" in text and "Anlage 2" not in text:
            buf = None
            for line in _lines(text):
                if A1_START.match(line):
                    if buf:
                        _emit_a1(buf, pno, a1, unparsed)
                    buf = line
                elif buf:
                    buf += " " + line
            if buf:
                _emit_a1(buf, pno, a1, unparsed)

        elif "Anlage 2" in text and "Verkehrsmengen" in text:
            buf = ""
            for line in _lines(text):
                buf = f"{buf} {line}".strip()
                m = A2_TAIL.match(buf)
                if m:
                    t = TRAFFIC.search(m["rest"])
                    a2.append({
                        "asb": m["asb"], "tbw": m["tbw"], "label": m["label"],
                        "area_m2": int(m["area"]), "grade": float(m["grade"].replace(",", ".")),
                        "grade_band": f"{m['lo']}-{m['hi']}",
                        "roads": m["rest"][: t.start()].strip() if t else m["rest"],
                        "dtv": int(t[1]) if t else None, "dtv_heavy": int(t[2]) if t else None,
                        "page": pno, "quote": buf})
                    buf = ""
                elif len(buf) > 400:  # runaway buffer = layout we don't understand
                    unparsed.append({"table": "Anlage 2", "page": pno, "text": buf})
                    buf = ""
    return {"pages": pages, "anlage1": a1, "anlage2": a2, "unparsed": unparsed}


def _emit_a1(buf, pno, out, unparsed):
    m = A1_ROW.match(buf)
    if not m:
        unparsed.append({"table": "Anlage 1", "page": pno, "text": buf})
        return
    c1, c2 = _num(m["c1"]), _num(m["c2"])
    ers, ver = m["ers"] == "ja", m["ver"] == "ja"
    # Column order is: cost strengthening, cost replacement. With one value, it belongs to the "ja" measure.
    if c1 is not None and c2 is None:
        cost_ver, cost_ers = (c1, None) if (ver and not ers) else (None, c1)
    else:
        cost_ver, cost_ers = c1, c2
    out.append({
        "road": m["road"], "asb": m["asb"], "tbw": m["tbw"],
        "name": m["name"], "area_m2": int(m["area"]), "stage": m["stage"],
        "replacement": ers if m["ers"] else None, "replacement_year": _num(m["ers_year"]),
        "strengthening": ver if m["ver"] else None, "strengthening_year": _num(m["ver_year"]),
        "cost_strengthening_eur": cost_ver, "cost_replacement_eur": cost_ers,
        "page": pno, "quote": buf})


if __name__ == "__main__":
    import sys
    r = parse(sys.argv[1])
    print("Anlage 1 rows:", len(r["anlage1"]), "| Anlage 2 rows:", len(r["anlage2"]),
          "| unparsed:", len(r["unparsed"]))
