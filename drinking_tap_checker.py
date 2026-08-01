# drinking_tap_vs_classrooms.py
# Usage:
#   from drinking_tap_vs_classrooms import run_drinking_tap_vs_classrooms_check
#   run_drinking_tap_vs_classrooms_check(ifc=your_ifc_bytes_or_path_or_ifcopenshell_file)
#
# Or standalone:
#   streamlit run drinking_tap_vs_classrooms.py
#
# Requires: pip install streamlit ifcopenshell pandas

import tempfile
import pandas as pd
import streamlit as st

try:
    import ifcopenshell
except Exception:
    st.error("⚠️ Please install dependencies: pip install ifcopenshell streamlit pandas")
    st.stop()

TARGET_TAP_NAME = "drinking tap"   # exact, case-insensitive
TARGET_ROOM_NAME = "classroom"     # exact, case-insensitive

def run_drinking_tap_vs_classrooms_check(ifc=None):
    st.caption("code: 4-1-7-20")
    st.header("🚰 Drinking Taps Number Check")
    st.subheader("Drinking Taps Should Be ≥ Classrooms Number")

    # --- IFC open (upload locally if none provided) ---
    model = None
    if ifc is None:
        with st.sidebar:
            st.subheader("📁 Upload IFC")
            up_ifc = st.file_uploader("Upload .ifc", type=["ifc"], key="ifc_upload_taps")
        if not up_ifc:
            st.info("⬆️ Upload an IFC file to continue.")
            st.stop()
        model = _open_model_from_bytes(up_ifc.read())
    else:
        model = _open_model_generic(ifc)

    if model is None:
        st.error("Could not open IFC model.")
        st.stop()

    # --- Count classrooms (IfcSpace.LongName == 'classroom') ---
    classrooms = [
        s for s in model.by_type("IfcSpace")
        if _ci(getattr(s, "LongName", None)) == TARGET_ROOM_NAME
    ]
    n_classrooms = len(classrooms)

    # --- Count drinking taps (elements with any name field exactly 'drinking tap') ---
    # We scan common element classes to be safe.
    candidate_types = [
        "IfcFlowTerminal", "IfcSanitaryTerminal", "IfcFlowFitting", "IfcFurnishingElement",
        "IfcDistributionElement", "IfcElement"
    ]
    elements = []
    for t in candidate_types:
        try:
            elements.extend(model.by_type(t))
        except Exception:
            pass

    taps = [e for e in elements if _is_exact_named(e, TARGET_TAP_NAME)]
    n_taps = len(_unique_entities(taps))

    # --- UI: two columns with counts ---
    c1, c2 = st.columns(2)
    with c1:
        st.metric(label="🏫 Number of Classrooms", value=n_classrooms)
    with c2:
        st.metric(label="🚰 Number of Drinking Taps", value=n_taps)

    # --- Verdict ---
    short = max(0, n_classrooms - n_taps)
    if n_taps >= n_classrooms:
        st.success(f"✅ Enough drinking taps.")
    else:
        st.error(f"❌ Not enough drinking taps. Short by {short} taps.")

    # Optional: small table summary
    df = pd.DataFrame(
        [{"Classrooms": n_classrooms, "Drinking Taps": n_taps, "Shortfall": short}]
    )
    st.dataframe(df, use_container_width=True)


# ----------------- Helpers -----------------
def _ci(s):
    return (s or "").strip().lower()

def _is_exact_named(entity, target_ci: str):
    """Return True if ANY common name-ish field equals target (case-insensitive)."""
    target = _ci(target_ci)
    fields = ("LongName", "Name", "ObjectType", "PredefinedType")
    for f in fields:
        val = getattr(entity, f, None)
        # PredefinedType may be enum-like; cast to str safely
        if val is not None and _ci(str(getattr(val, "value", val))) == target:
            return True
    # Also scan attached property sets with a 'Name' or single value labeled like a name
    try:
        for rel in entity.IsDefinedBy or []:
            pdef = getattr(rel, "RelatingPropertyDefinition", None)
            if not pdef: continue
            if pdef.is_a("IfcPropertySet"):
                for p in pdef.HasProperties or []:
                    pname = _ci(getattr(p, "Name", "") or "")
                    if pname in ("name", "type", "label"):
                        if getattr(p, "NominalValue", None) is not None:
                            if _ci(str(p.NominalValue.wrappedValue)) == target:
                                return True
    except Exception:
        pass
    return False

def _unique_entities(seq):
    """Deduplicate IFC entities by their id() or GlobalId if present."""
    seen = set()
    out = []
    for e in seq:
        gid = getattr(e, "GlobalId", None) or id(e)
        if gid not in seen:
            seen.add(gid)
            out.append(e)
    return out

def _open_model_from_bytes(b: bytes):
    with tempfile.NamedTemporaryFile(delete=False, suffix=".ifc") as tmp:
        tmp.write(b); tmp.flush()
        return ifcopenshell.open(tmp.name)

def _open_model_generic(ifc):
    # ifcopenshell file object
    try:
        from ifcopenshell.file import file as IfcFile
    except Exception:
        IfcFile = None
    if IfcFile and isinstance(ifc, IfcFile):
        return ifc
    # bytes-like
    if isinstance(ifc, (bytes, bytearray)):
        return _open_model_from_bytes(ifc)
    # path
    if isinstance(ifc, str):
        try:
            return ifcopenshell.open(ifc)
        except Exception:
            return None
    # UploadedFile-like with .read()
    read = getattr(ifc, "read", None)
    if callable(read):
        try:
            return _open_model_from_bytes(read())
        except Exception:
            return None
    return None


# Standalone
if __name__ == "__main__":
    run_drinking_tap_vs_classrooms_check()
