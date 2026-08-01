# floors_checker.py
import io, re, tempfile
from typing import Optional, Dict, Any, List, Set
import pandas as pd
import streamlit as st

try:
    import ifcopenshell
except Exception:
    ifcopenshell = None


# ---------------- helpers ----------------
def _norm(s: str) -> str:
    return (s or "").strip().lower()

def _prefer_longname(space) -> str:
    return (getattr(space, "LongName", None) or getattr(space, "Name", None) or "").strip()

def _prefer_storey_name(sto) -> str:
    nm = (getattr(sto, "LongName", None) or getattr(sto, "Name", None) or "").strip()
    return nm or f"Unnamed Storey ({getattr(sto, 'GlobalId', '') or 'NO-ID'})"

def _storey_of_space(space) -> Optional[object]:
    # 1) climb spatial decomposition
    decomp = getattr(space, "Decomposes", None)
    if decomp:
        st_ = decomp[0].RelatingObject
        seen = set()
        while st_ and id(st_) not in seen:
            seen.add(id(st_))
            try:
                if st_.is_a("IfcBuildingStorey"):
                    return st_
            except Exception:
                pass
            parents = getattr(st_, "Decomposes", None)
            st_ = parents[0].RelatingObject if parents else None

    # 2) util.get_container fallback
    try:
        from ifcopenshell.util import element as uel
        st_ = uel.get_container(space)
        seen = set()
        while st_ and id(st_) not in seen:
            seen.add(id(st_))
            try:
                if st_.is_a("IfcBuildingStorey"):
                    return st_
            except Exception:
                pass
            parents = getattr(st_, "Decomposes", None)
            st_ = parents[0].RelatingObject if parents else None
    except Exception:
        pass
    return None


# ---------------- main renderer ----------------
def render_floors_with_classrooms(ifc: Optional[object] = None, *, max_allowed_default: int = 3):
    """
    Uses the globally opened IFC (st.session_state.ifc) unless `ifc` is provided.

    - Auto-matches spaces whose LongName/Name contains 'classroom' (case-insensitive)
    - Pulls room number from IfcSpace.Name -> 'Room No.'
    - Adds 'Labeled Name' right after 'Space Name' as '<Room No.> <Space Name-without-"classroom">'
    - Renders matching table + per-storey summary and CSV download
    """
    st.caption("Code: 5-1-1-5-2")
    st.subheader("Floors with Classrooms")

    if ifcopenshell is None:
        st.error("`ifcopenshell` is not installed. Install: `pip install ifcopenshell`")
        return

    # Resolve IFC
    model = ifc or st.session_state.get("ifc")
    if model is None:
        st.info("No IFC in session. Upload an IFC in the sidebar.")
        return

    # Controls: only the standard limit
    c1, = st.columns(1)
    with c1:
        max_allowed = st.number_input(
            "Max allowed floors containing classrooms",
            min_value=0, value=max_allowed_default, step=1
        )
        # Tip image right under the input
        try:
            st.image("5-5.png", use_container_width=True)
        except Exception:
            st.caption("Tip image (5-5.png) not found in the app folder.")

    st.markdown("---")

    # Collect spaces
    try:
        spaces_all = model.by_type("IfcSpace") or []
    except Exception as e:
        st.error(f"Could not read IfcSpace entities: {e}")
        return

    records: List[Dict[str, Any]] = []
    storey_names: Set[str] = set()

    for sp in spaces_all:
        longname = _prefer_longname(sp)
        if "classroom" in _norm(longname):
            # Room number (often numeric) from IfcSpace.Name
            raw_room_no = getattr(sp, "Name", "") or ""
            room_no_str = str(raw_room_no).strip()

            # Remove the word 'classroom' from the label (case-insensitive), then trim spaces
            longname_no_cls = re.sub(r"classroom", "", longname, flags=re.IGNORECASE).strip()
            longname_no_cls = longname_no_cls or longname  # fallback

            strobj = _storey_of_space(sp)
            stname = _prefer_storey_name(strobj) if strobj else "(No storey)"
            storey_names.add(stname)

            labeled = f"{room_no_str} {longname_no_cls}".strip() if room_no_str else longname_no_cls

            records.append({
                "Space GlobalId": getattr(sp, "GlobalId", "") or "",
                "Space Name": longname,
                "Labeled Name": labeled,   # number + name without "classroom"
                "Room No.": room_no_str,
                "Storey": stname,
            })

    # Verdict + levels
    levels = sorted(storey_names, key=lambda s: s.lower())
    count_levels = len(levels)

    v1, v2 = st.columns(2)
    with v1:
        st.subheader("Verdict")
        if count_levels <= max_allowed:
            st.success(f"✅ Standard: {count_levels} level(s) (max {max_allowed}).")
        else:
            st.error(f"❌ Exceeds by {count_levels - max_allowed}: {count_levels} level(s) (max {max_allowed}).")

    with v2:
        st.subheader("Levels with classrooms")
        if levels:
            st.write("\n".join(f"- {x}" for x in levels))
        else:
            st.info("No levels found with classrooms under the current rules.")

    st.markdown("---")
    st.subheader("Matching spaces")

    if not records:
        st.info("No matching spaces (no 'classroom' found in IfcSpace names).")
        return

    # Table with “Labeled Name” after “Space Name”
    df = pd.DataFrame(records)
    ordered = ["Space GlobalId", "Space Name", "Labeled Name", "Room No.", "Storey"]
    df = df[[c for c in ordered if c in df.columns]]
    st.dataframe(df, use_container_width=True, hide_index=True)

    st.subheader("Summary by storey")
    summary = df.groupby("Storey", dropna=False).size().reset_index(name="Classroom spaces")
    st.dataframe(summary, use_container_width=True, hide_index=True)

    # CSV
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    st.download_button("Download CSV", buf.getvalue(), "classrooms_by_storey.csv", "text/csv")


# ---------------- standalone support ----------------
def ensure_ifc_from_sidebar():
    """
    If no IFC is present in st.session_state.ifc, shows a sidebar uploader,
    opens the file once, and caches the opened model + file name.
    """
    if "ifc" in st.session_state and st.session_state.ifc is not None:
        return

    with st.sidebar:
        st.subheader("IFC")
        up = st.file_uploader(
            "Upload IFC (.ifc / .ifczip)",
            type=["ifc", "ifczip"],
            key="__standalone_ifc_upload",
            help="Upload once here."
        )

    if up is None:
        st.info("Upload an IFC file in the sidebar to begin.")
        st.stop()

    suffix = ".ifczip" if up.name.lower().endswith(".ifczip") else ".ifc"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as _tmp:
        _tmp.write(up.getbuffer())
        _path = _tmp.name
    try:
        st.session_state.ifc = ifcopenshell.open(_path)
        st.session_state.ifc_name = up.name
    except Exception as e:
        st.error(f"IFC open error: {e}")
        st.stop()


def run_standalone():
    st.set_page_config(page_title="Floors with Classrooms", page_icon="🏫", layout="wide")
    st.title("🏫 Floors with Classrooms – Checker")
    if ifcopenshell is None:
        st.error("`ifcopenshell` is not installed. Install: `pip install ifcopenshell`")
        st.stop()

    ensure_ifc_from_sidebar()
    render_floors_with_classrooms()  # uses st.session_state.ifc


# When run as: streamlit run floors_checker.py
if __name__ == "__main__":
    run_standalone()
