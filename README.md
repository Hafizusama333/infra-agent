# Infra Agent

**A small, working model of an agentic workflow engine for infrastructure, built on real public data.**
Built by Hafiz Usama as a work sample for the Forward Deployed Engineer role at Arken Labs.

Move's idea, as I understand it: *agents do the work, experts make the decisions, and every value can be traced to the passage behind it.* I wanted to show I understand that design by building a small version of it, and by testing it on real German documents instead of tidy fake ones.

---

## What it does

Two use cases run on **one engine**:

| Pack | Input | Output |
|---|---|---|
| **Bridges** (roads & bridges) | A real 188-page Bundestag answer (Drucksache 19/12802): the federal strengthening programme for Lower Saxony + condition grades and truck traffic for every federal bridge section | Ranked triage of **5,521 bridge sections**, cross-checks between the two tables, and a cited memo per section, filed only after an engineer approves |
| **Telecom site permit** (telecom infrastructure) | Data for a planned mobile site (power, mounting, documents ready, municipality consulted) | Which federal requirements apply, what's missing from the BNetzA application, and a cited permit-readiness memo |

Adding the second pack required **no engine changes**: a pack is one `pack.yaml` (rules, citations, draft prompt, input form) plus a small `pack.py` describing how its data becomes items.

## What the engine found in the real data

Running the bridges pack on the source PDF (published Aug 2019). The counts below come from the deterministic parse; the rows Claude adds can shift them slightly:

- **5,524 bridge sections** and **407 programme entries** read. 99.7% of rows are read by a deterministic parser. Of the 17 rows it can't read (for example "nur Abriss", or "PPP" in a cost column), **16 are read by Claude and verified against the page**, and **1 is left for human review**.
- **89 bridge sections where the two annex tables of the same government document give different bridge areas** (more than 2% apart). These are shown as conflicts: the engineer picks the correct value, and nothing is filed until they do.
- **175 sections with condition grade ≥ 3.0**. **31** of them carry more than 5,000 heavy vehicles per day (2015 count), and **111** of them are not in the strengthening programme list.
- **119 programme entries whose planned replacement year has already passed** since the data was published. They're flagged as "confirm status before relying on this record."
- **17,119 of 17,122 cited facts verified** against the PDF text. The 3 that fail come from one row where the PDF's own text layer is scrambled, and they're shown with ⚠️ rather than trusted.

## How it maps to Move's layers

| Move (from arkenlabs.de) | Here | File |
|---|---|---|
| **Sources & connectors** | PDF and legal-text loaders. Every document becomes citable units (`p.41`, `§ 4 Abs. 5 BEMFV`) | `engine/sources.py` |
| **Ontology** (assets, standards, regulations) | Pack YAML: fields, rules, and the legal or source passage each rule rests on | `packs/*/pack.yaml` |
| **Capabilities**: extraction, rule checks, citations | Table parser + Claude fallback, a safe rule evaluator, quote verification | `packs/bridges_ni/parser.py`, `engine/rules.py` |
| **Agents** (LLMs + ontology) | Claude handles messy rows and drafting only, and its output must pass verification before it's used | `engine/llm.py` |
| **Human decision, then write-back with a decision record** | Conflicts block the workflow until an engineer decides. Approval writes master data + a work order + an audit entry (approver, time, SHA-256 of the document) | `engine/store.py` |

## Design choices

1. **Deterministic first, LLM where it adds value.** Parsing 5,500 table rows with a regex is free, fast and auditable. Claude only gets the rows the parser can't read, plus the drafting. This keeps cost and error rate low enough for regulated customers.
2. **Citations are verified, not just displayed.** Every fact and every rule carries `(source, ref, quote)`, and the engine checks that the quote really appears in that page or paragraph. When a pack loads, each rule's citation is checked against the law text, so the rulebook can't drift from the regulation it claims to implement.
3. **Unknown data never triggers or clears a rule.** A missing truck count doesn't make a bridge look safe.
4. **The system says what it doesn't know.** The Berlin building-code rule (T12) is in the rulebook, but its source text isn't loaded yet, so the UI shows "⚠️ not verified – check manually" instead of pretending.
5. **Blockers are enforced in the engine**, not just in the UI: the API returns `409` if anyone tries to draft or approve with open conflicts or missing documents.

## Run it

```bash
pip install -r requirements.txt
python -m tests.test_engine          # full test run, no API key needed
```

Set your Claude API key, then start the app:

```bash
# Mac/Linux
export ANTHROPIC_API_KEY=sk-ant-...
# Windows PowerShell
$env:ANTHROPIC_API_KEY="sk-ant-..."

streamlit run app.py                 # UI
uvicorn api:app --port 8000          # API, docs at http://127.0.0.1:8000/docs
```

The first start reads all 188 pages (about 30 s), then caches the result in `cache/`.

**Windows:** if security policy blocks `pip.exe` / `streamlit.exe`, use `python -m pip ...`, `python -m streamlit run app.py` and `python -m uvicorn api:app --port 8000`.

## n8n workflow

`n8n/telecom_site_check.json` connects the same engine to a no-code flow:

**Web form (new site request)** → `POST /packs/telecom_permit/assess` → if blocked: show the missing items with legal references → otherwise: Claude drafts the memo → **approval form for the engineer** → `POST /approve` → work order ID shown.

To set it up: in n8n go to **Workflows → Import from file**, start the API, and open the form URL. The workflow targets the Form Trigger 2.3 / Form 2.3 / HTTP Request 4.2 node versions, and its parameters are checked against the `n8n-nodes-base` definitions.

## Sources

- Deutscher Bundestag, Drucksache 19/12802 (29.08.2019): *Sanierungsbedürftige Brücken in Niedersachsen*. Annexes 1 and 2.
- Deutscher Bundestag, Drucksache 20/13579 (31.10.2024): *Zustand der Verkehrsinfrastruktur in Bayern*. Used for the inspection cycle, relief measures and prioritisation principles that the bridge rules cite.
- BEMFV § 4 and 26. BImSchV §§ 2, 7a: official German text from gesetze-im-internet.de, retrieved 24.11.2026 (`sources/laws_telecom.json`).

## Limits (honest list)

- The bridge data is from 2019 and the traffic counts from 2015. The pack flags this on every record (rule B6) instead of hiding it.
- Rule thresholds such as "≥ 5,000 heavy vehicles/day" are my illustrative choices, not official criteria. The citation shows which principle each rule rests on, not that a law sets that number.
- The telecom pack covers federal law only. State building law (BauO) is flagged as unverified.
- `system_of_record.db` is SQLite standing in for SAP PM or a permit tracker.

## What I'd build next at Arken

1. **A Grid pack**: grid connection pre-checks for data centers and batteries, using the same engine, with network operator requirements as the rulebook.
2. **Connectors that write back to SAP PM / DMS** through their APIs, with the decision log attached to the work order.
3. **A coverage report per customer:** how many rows were read deterministically, by the LLM, or left for humans, so the client sees where the tool can and can't be trusted.
4. **Pack-authoring tools for forward deployed engineers**, so adding a customer's handbook becomes a YAML + test change, not an engineering project.
