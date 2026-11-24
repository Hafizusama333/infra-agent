"""Sources layer: every document becomes a list of citable units ("pages").
A PDF page is cited as 'p.N'; a legal text is cited by its paragraph ('§ 4 Abs. 1 BEMFV').
Everything downstream points back to a (source, ref, quote) triple that can be verified."""
import json
import re
from functools import lru_cache
from pathlib import Path
from pypdf import PdfReader

SOURCES_DIR = Path(__file__).resolve().parent.parent / "sources"


@lru_cache(maxsize=None)
def load(name: str) -> dict:
    path = SOURCES_DIR / name
    if path.suffix == ".pdf":
        pages = [{"ref": f"p.{i}", "text": p.extract_text() or ""}
                 for i, p in enumerate(PdfReader(path).pages, start=1)]
        return {"id": name, "title": path.stem.replace("_", " "), "pages": pages}
    data = json.loads(path.read_text(encoding="utf-8"))
    data["id"] = name
    return data


def _norm(s: str) -> str:
    # PDF text layers lose/insert spaces and break words with hyphens across lines,
    # so compare with whitespace and hyphens removed. Everything else must match exactly.
    return re.sub(r"[\s\-­]+", "", s).lower()


@lru_cache(maxsize=None)
def _norm_page(source: str, ref: str) -> str | None:
    page = next((p for p in load(source)["pages"] if p["ref"] == ref), None)
    return _norm(page["text"]) if page else None


def verify(source: str, ref: str, quote: str) -> bool:
    """True if `quote` really appears in `ref` of `source`. This is what makes a citation trustworthy."""
    if not quote:
        return False
    try:
        page = _norm_page(source, ref)
    except FileNotFoundError:
        return False
    return page is not None and _norm(quote) in page


def url_of(source: str, ref: str) -> str | None:
    try:
        page = next((p for p in load(source)["pages"] if p["ref"] == ref), None)
    except FileNotFoundError:
        return None
    return page.get("url") if page else None
