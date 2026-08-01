# area_per_capita_app.py
import tempfile
import streamlit as st

def run_area_per_capita_app(ifc=None):
    try:
        import ifcopenshell
    except Exception:
        st.error("Package 'ifcopenshell' is not installed. Run: pip install ifcopenshell")
        return

    st.title("📏Areas per Capita check")

    # ---------- Resolve IFC: prefer argument, then global session, then fallback uploader ----------
    if ifc is None:
        ifc = st.session_state.get("ifc", None)

    if ifc is None:
        # Fallback uploader ONLY if no global IFC found
        with st.sidebar:
            st.info("No global IFC found. Upload one here (fallback).")
            up = st.file_uploader(
                "Upload IFC (.ifc / .ifczip)",
                type=["ifc", "ifczip"],
                key="ifc_upload_area_per_capita_fallback"
            )
        if up is None:
            st.warning("Please upload an IFC in the sidebar (global) or here (fallback).")
            return

        suffix = ".ifczip" if up.name.lower().endswith(".ifczip") else ".ifc"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(up.read())
            tmp_path = tmp.name
        try:
            ifc = ifcopenshell.open(tmp_path)
            # also stash it globally so other tabs can reuse
            st.session_state.ifc = ifc
            st.session_state.ifc_name = up.name
        except Exception as e:
            st.error(f"IFC open error: {e}")
            return

    # -------------------------
    # Tabs (internal to this app section)
    # -------------------------
    from area_per_capita_checker import run_area_per_capita_check
    from area_per_capita_checker2 import run_yard_checks

    tabs = st.tabs(["Closed Area", "Yard Area"])
    with tabs[0]:
        run_area_per_capita_check(ifc)
    with tabs[1]:
        run_yard_checks(ifc)
