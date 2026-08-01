# class_count_checker.py
# Uses already-opened IFC at st.session_state.ifc
# Fixed to "classroom"
# Auto-runs (no button)
# Two-line verdict with your custom text

import streamlit as st

# Optional: safe import for ifcopenshell + psets
try:
    import ifcopenshell
    try:
        from ifcopenshell.util.element import get_psets as _get_psets
    except Exception:
        _get_psets = None
except Exception:
    ifcopenshell = None
    _get_psets = None

# ---------------- Config ----------------
FIXED_ROOM_TERM = "classroom"

SCHOOL_TYPES = {
    "1": {"label": "first 3-year school", "valid_counts": [6, 9, 12, 15]},
    "2": {"label": "second 3-year school", "valid_counts": [6, 9, 12, 15]},
    "3": {"label": "mixed type", "valid_counts": [6, 12, 18]},
}

MESSAGES = {
    "result_ok": "School has {count} classrooms for {label}\n✅ meets the standard.",
    "result_not_ok": "School has {count} classrooms for {label}\n❌ NOT standard. classrooms number one of: {standards}",
}

# ---------------- Helpers ----------------
def _norm(s):
    return (s or "").strip().casefold()

def _get_pset_value(space, key):
    if _get_psets is None:
        return None
    try:
        p = _get_psets(space) or {}
        val = p.get("Pset_SpaceCommon", {}).get(key)
        return val if isinstance(val, str) and val.strip() else None
    except Exception:
        return None

def _space_is_classroom(space) -> bool:
    """Detect spaces that exactly match 'classroom'."""
    term = FIXED_ROOM_TERM.casefold()
    fields = [
        getattr(space, "LongName", None),
        getattr(space, "Name", None),
        getattr(space, "ObjectType", None),
        getattr(space, "PredefinedType", None),
        _get_pset_value(space, "LongName"),
        _get_pset_value(space, "Name"),
    ]
    return any(_norm(v) == term for v in fields if isinstance(v, str))

def _count_classrooms(ifc_file):
    try:
        spaces = ifc_file.by_type("IfcSpace")
    except Exception:
        spaces = []
    return sum(1 for s in spaces if _space_is_classroom(s))

# ---------------- Main Renderer ----------------
def render_class_count_checker():
    st.caption("Code: 5-1-1-5-1")
    st.subheader("🏫 Classroom Count Checker")

    if ifcopenshell is None:
        st.error("`ifcopenshell` is not installed. Try: pip install ifcopenshell")
        return
    if "ifc" not in st.session_state or st.session_state.ifc is None:
        st.info("Upload an IFC in the sidebar first.")
        return

    ifc_file = st.session_state.ifc

    # --- School type dropdown ---
    st.markdown("**School type**")
    keys = list(SCHOOL_TYPES.keys())
    labels = [f"{k} — {SCHOOL_TYPES[k]['label']}" for k in keys]
    sel = st.selectbox("Select school type", labels, index=0, key="class_count_school")
    sel_key = sel.split(" — ", 1)[0]
    label = SCHOOL_TYPES[sel_key]["label"]
    standards = SCHOOL_TYPES[sel_key]["valid_counts"]

    st.divider()

    # --- Compute immediately ---
    count = _count_classrooms(ifc_file)

    # --- Metrics ---
    c1, c2 = st.columns(2)
    c1.metric("Target room", FIXED_ROOM_TERM)
    c2.metric("Classroom numbers", count)

    # --- Verdict ---
    if count in standards:
        msg = MESSAGES["result_ok"].format(count=count, label=label)
        st.success(msg)
    else:
        msg = MESSAGES["result_not_ok"].format(count=count, label=label, standards=standards)
        st.error(msg)
