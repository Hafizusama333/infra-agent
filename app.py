"""Infra Agent – one engine, many use cases.   Run:  streamlit run app.py"""
import pandas as pd
import streamlit as st

from engine import llm, packs, store

st.set_page_config(page_title="Infra Agent", layout="wide")
SEV = {"blocker": "⛔ blocker", "high": "🔴 high", "medium": "🟠 medium", "info": "🔵 info"}


@st.cache_resource(show_spinner="Reading sources and verifying citations…")
def load_pack(pid):
    p = packs.get(pid)
    return p, (p.items() if not p.meta.get("input_form") else None)


def cite_md(c):
    if not c:
        return ""
    tick = "✅ verified in source" if c.get("verified") else "⚠️ not verified – check manually"
    link = f" · [open]({c['url']})" if c.get("url") else ""
    quote = f" — “{c['quote']}”" if c.get("quote") else ""
    return f"<small>`{c['source']}` **{c['ref']}**{quote} · {tick}{link}</small>"


with st.sidebar:
    st.title("Infra Agent")
    st.caption("Agents do the work. Engineers make the decisions.")
    pid = st.radio("Use case (pack)", packs.available(),
                   format_func=lambda x: packs.get(x).meta["name"])
    pack, cached_items = load_pack(pid)
    st.caption(pack.meta["description"])
    st.write("**Rules:**", len(pack.rules), "· citations verified:",
             sum(1 for r in pack.rules if (r.get("cite") or {}).get("verified")), "/", len(pack.rules))
    if not llm.available():
        st.warning("ANTHROPIC_API_KEY not set – everything works except Claude drafting / fallback extraction.")

ss = st.session_state

# ---------- choose item ----------
if pack.meta.get("input_form"):
    st.header("Site data")
    defaults = pack.module.defaults()
    inputs, cols = {}, st.columns(3)
    for i, f in enumerate(pack.meta["input_form"]):
        with cols[i % 3]:
            k = f"{pid}:{f['name']}"
            if f["type"] == "number":
                inputs[f["name"]] = st.number_input(f["label"], value=float(defaults[f["name"]]), key=k)
            elif f["type"] == "bool":
                inputs[f["name"]] = st.checkbox(f["label"], value=defaults[f["name"]], key=k)
            elif f["type"] == "select":
                inputs[f["name"]] = st.selectbox(f["label"], f["options"],
                                                 index=f["options"].index(defaults[f["name"]]), key=k)
            else:
                inputs[f["name"]] = st.text_input(f["label"], value=defaults[f["name"]], key=k)
    item = pack.items(inputs)[0]
else:
    s = pack.summary()
    st.header("Triage – every bridge section in the source")
    m = st.columns(5)
    m[0].metric("PDF pages read", s["pages"])
    m[1].metric("Bridge sections", s["anlage2_rows"])
    m[2].metric("Programme entries", s["anlage1_rows"])
    m[3].metric("Rows read by Claude", s["recovered_by_claude"])
    m[4].metric("Rows for human review", s["needs_human_review"])

    @st.cache_data(show_spinner="Running rules on every section…")
    def triage(pid):
        rows = []
        for it in cached_items:
            fs = pack.assess(it)
            rows.append({"key": it["key"], "bridge": it["title"], "grade": it["context"]["grade"],
                         "heavy vehicles/day": it["context"]["dtv_heavy"],
                         "in programme": it["context"]["in_programme"],
                         "conflicts": len(it["conflicts"]),
                         "high": sum(f["severity"] == "high" for f in fs),
                         "medium": sum(f["severity"] == "medium" for f in fs),
                         "flags": ", ".join(sorted({f["rule"] for f in fs if f["severity"] != "info"}))})
        df = pd.DataFrame(rows)
        return df.sort_values(["high", "grade", "heavy vehicles/day"], ascending=False)

    df = triage(pid)
    c1, c2, c3 = st.columns([2, 1, 1])
    q = c1.text_input("Search name / ASB number")
    min_grade = c2.slider("Minimum grade", 1.0, 4.0, 3.0, 0.1)
    only_conf = c3.checkbox("Only sections where the two tables disagree")
    view = df[df["grade"].fillna(0) >= min_grade]
    if only_conf:
        view = df[df["conflicts"] > 0]
    if q:
        view = view[view["bridge"].str.contains(q, case=False) | view["key"].str.contains(q)]
    st.caption(f"{len(view)} of {len(df)} sections · sorted by high-severity flags, grade, truck traffic")
    st.dataframe(view, hide_index=True, width="stretch", height=300)
    if view.empty:
        st.stop()
    key = st.selectbox("Open a section", view["key"].tolist(),
                       format_func=lambda k: f"{k} – {df.set_index('key').loc[k, 'bridge'][:80]}")
    item = next(i for i in cached_items if i["key"] == key)

# ---------- item detail ----------
st.header(item["title"])
st.caption(item["subtitle"])

with st.expander("Facts and where they come from", expanded=not pack.meta.get("input_form")):
    for f in item["facts"]:
        if f["verified"] is None:
            st.markdown(f"**{f['label']}**: {f['value']}  <small>({f['source']})</small>", unsafe_allow_html=True)
        else:
            st.markdown(f"**{f['label']}**: {f['value']}  \n" + cite_md(f) +
                        (" · read by Claude" if f.get("by") == "claude" else ""), unsafe_allow_html=True)

resolved = {}
if item["conflicts"]:
    st.subheader("Sources disagree – your decision")
    for c in item["conflicts"]:
        labels = [f"{o['value']} — {o['note']} ({o['source']} {o['ref']}) {'✅' if o['verified'] else '⚠️'}"
                  for o in c["options"]]
        pick = st.radio(f"**{c['label']}**", labels + ["Not decided yet"], index=len(labels),
                        key=f"{pid}:{item['key']}:{c['field']}")
        if pick != "Not decided yet":
            resolved[c["field"]] = c["options"][labels.index(pick)]

st.subheader("Rule checks")
findings = pack.assess(item, resolved)
for f in findings:
    st.markdown(f"{SEV[f['severity']]} **{f['rule']} – {f['title']}**: {f['message']}  \n{cite_md(f['cite'])}",
                unsafe_allow_html=True)
blocked = any(f["severity"] == "blocker" for f in findings)
if blocked:
    st.error("Blockers are open. Nothing can be drafted or filed until they are resolved.")

st.subheader("Draft → review → approve")
dkey = f"draft:{pid}:{item['key']}"
if st.button("Draft with Claude", disabled=blocked or not llm.available(), type="primary"):
    with st.spinner("Claude is drafting from verified facts only…"):
        ss[dkey] = pack.draft(item, resolved, findings)
if dkey in ss:
    doc = st.text_area("Edit before approval", ss[dkey], height=380)
    approver = st.text_input("Approved by", key=f"appr:{dkey}")
    if st.button("✅ Approve and write back", disabled=blocked or not approver):
        wo = pack.approve(item, doc, resolved, findings, approver)
        st.success(f"Work order #{wo} filed. Decision logged with document hash.")

with st.expander("System of record (mock SAP PM / permit tracker)"):
    for t in ("work_orders", "decision_log", "master_data"):
        st.write(t)
        st.dataframe(store.table(t), hide_index=True, width="stretch")
