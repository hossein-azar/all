# parking_checker.py
# Streamlit app: Parking adequacy check from IFC
# - Importable function: render_parking_checker(ifc=None)
# - Standalone friendly: streamlit run parking_checker.py

import math
import os
import io
import tempfile
from functools import lru_cache
from typing import Dict, List, Optional, Tuple

import pandas as pd
import streamlit as st

# ---------------------------
# Imports (minimal checks)
# ---------------------------
try:
    import ifcopenshell
except Exception:
    ifcopenshell = None

try:
    import ifcopenshell.geom as ifcgeom  # optional (for geometric areas)
except Exception:
    ifcgeom = None

try:
    from shapely.geometry import Polygon
    from shapely.ops import unary_union
except Exception:
    Polygon = None
    unary_union = None


# ---------------------------
# Helpers
# ---------------------------
def _open_ifc_from_bytes(b: bytes):
    if ifcopenshell is None:
        return None
    try:
        with tempfile.NamedTemporaryFile(suffix=".ifc", delete=False) as tmp:
            tmp.write(b)
            tmp_path = tmp.name
        return ifcopenshell.open(tmp_path)
    except Exception:
        return None

def _open_ifc_from_path(path: str):
    if ifcopenshell is None:
        return None
    try:
        return ifcopenshell.open(path)
    except Exception:
        return None

def _detect_length_scale_meters(ifc) -> float:
    try:
        uas = ifc.by_type("IfcUnitAssignment")
        if not uas:
            return 1.0
        ua = uas[0]
        for u in ua.Units:
            t = getattr(u, "UnitType", None)
            if str(t).upper().endswith("LENGTHUNIT"):
                nm = str(getattr(u, "Name", "METRE")).upper()
                pref = str(getattr(u, "Prefix", "") or "").upper()
                if nm in ("METRE", "METRES", "METER", "METERS"):
                    if pref in ("", "UNIT"):
                        return 1.0
                    if pref == "MILLI":
                        return 0.001
                    if pref == "CENTI":
                        return 0.01
                    if pref == "DECI":
                        return 0.1
                if nm in ("FOOT", "FEET"):
                    return 0.3048
        return 1.0
    except Exception:
        return 1.0

def _triangles_from_shape(shape, scale: float) -> List[List[Tuple[float, float]]]:
    tris = []
    verts = shape.geometry.verts
    faces = shape.geometry.faces
    for i in range(0, len(faces), 3):
        i0, i1, i2 = faces[i], faces[i+1], faces[i+2]
        x0, y0, z0 = verts[3*i0:3*i0+3]
        x1, y1, z1 = verts[3*i1:3*i1+3]
        x2, y2, z2 = verts[3*i2:3*i2+3]
        tris.append(((x0*scale, y0*scale), (x1*scale, y1*scale), (x2*scale, z2*0 + y2*scale)))  # keep XY
    return tris

def _space_geom_area_m2(ifc, space, scale_m: float) -> Optional[float]:
    # Use meshed geometry to approximate footprint area
    if ifcgeom is None or Polygon is None or unary_union is None:
        return None
    try:
        settings = ifcgeom.settings()
        settings.set(settings.USE_WORLD_COORDS, True)
        shape = ifcgeom.create_shape(settings, space)
    except Exception:
        return None
    try:
        polys = [Polygon(t) for t in _triangles_from_shape(shape, scale_m)]
        if not polys:
            return None
        merged = unary_union(polys)
        return float(merged.area)
    except Exception:
        return None

def _fallback_space_area_m2(space) -> Optional[float]:
    # Try area from quantities / attributes when geometry isn’t available
    try:
        for rel in getattr(space, "IsDefinedBy", []) or []:
            q = getattr(rel, "RelatingPropertyDefinition", None)
            if not q:
                continue
            for qa in getattr(q, "Quantities", []) or []:
                val = getattr(qa, "AreaValue", None)
                if val and val > 0:
                    return float(val)
    except Exception:
        pass
    try:
        a = getattr(space, "Area", None)
        if a and a > 0:
            return float(a)
    except Exception:
        pass
    return None

def _space_name(s) -> str:
    ln = (getattr(s, "LongName", None) or "").strip()
    nm = (getattr(s, "Name", None) or "").strip()
    return ln if ln else nm

def _prefer_storey_name(sto) -> str:
    nm = (getattr(sto, "LongName", None) or getattr(sto, "Name", None) or "").strip()
    return nm or f"Unnamed Storey ({getattr(sto, 'GlobalId', '') or 'NO-ID'})"

def _storey_of_space(space) -> Optional[object]:
    # Traverse parents to find IfcBuildingStorey
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
    # util.get_container fallback
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


# ---------------------------
# Core checker (importable)
# ---------------------------
REQ_PER_STAFF = 1/3.0     # 1 slot per 3 staff
SLOT_AREA = 21.0          # m² per slot

def _is_exact_parking(name: str) -> bool:
    """True if the given name/longname is exactly 'parking' (case-insensitive, trimmed)."""
    return (name or "").strip().lower() == "parking"

def render_parking_checker(ifc: Optional[object] = None, *, default_staff: int = 9):
    """
    Parking adequacy checker (UI + logic).

    - Uses global IFC from st.session_state.ifc if `ifc` is None.
    - Matches IfcSpace whose LongName **or** Name is **exactly** 'parking' (case-insensitive).
    - Keeps Staff count input; computes verdict automatically (no button).
    """
    st.caption("code: 5-1-2-3-1")
    st.header("🚗 Parking Area Checker")

    # Resolve IFC
    model = ifc or st.session_state.get("ifc")
    if model is None:
        st.info("No IFC loaded. Upload an IFC in the sidebar.")
        return

    # Units & spaces
    scale_m = _detect_length_scale_meters(model)
    spaces = list(model.by_type("IfcSpace") or [])
    if not spaces:
        st.error("No `IfcSpace` elements found in the model.")
        return

    @lru_cache(maxsize=None)
    def area_for_id(space_id) -> Optional[float]:
        s = model.by_id(space_id)
        a = _space_geom_area_m2(model, s, scale_m)
        if a and a > 0:
            return a
        return _fallback_space_area_m2(s)

    # Collect only spaces named exactly "parking"
    rows: List[Dict] = []
    for s in spaces:
        ln = (getattr(s, "LongName", None) or "").strip()
        nm = (getattr(s, "Name", None) or "").strip()

        if _is_exact_parking(ln) or _is_exact_parking(nm):
            a = area_for_id(s.id())
            sto = _storey_of_space(s)
            rows.append({
                "Space Name": _space_name(s) or "parking",
                "Storey": _prefer_storey_name(sto) if sto else "(No storey)",
                "Area (m²)": round(a, 2) if a else None,
                "GlobalId": getattr(s, "GlobalId", ""),
                "_id": s.id(),
            })

    # Aggregate totals
    total_area = sum(r["Area (m²)"] or 0.0 for r in rows)
    slots_raw = total_area / SLOT_AREA if total_area > 0 else 0.0
    slots_int = int(math.floor(slots_raw))

    # Inputs (Staff count only)
    colL, colR = st.columns([1, 1])
    with colL:
        staff_count = st.number_input("Staff count", min_value=0, step=1, value=default_staff)
    
    # Auto compute verdict
    req_slots = int(math.ceil(staff_count * REQ_PER_STAFF))
    ok = slots_raw >= req_slots
    short_slots = max(0, req_slots - slots_int)
    short_area = max(0.0, req_slots * SLOT_AREA - total_area)

    if ok:
        st.success(
            f"✅ **OK** —\n"
            f"- Required slots: **{req_slots}**\n"
            f"- Available (approx.): **{slots_raw:.2f}**\n"
            f"- Usable whole slots: **{slots_int}**\n"
            f"- Total area: **{total_area:.2f} m²**"
        )
    else:
        st.error(
            f"❌ **Not OK** —\n"
            f"- Required slots: **{req_slots}**\n"
            f"- Available (approx.): **{slots_raw:.2f}**\n"
            f"- Usable whole slots: **{slots_int}**\n"
            f"- Shortfall: **{short_slots}** slot(s)\n"
            f"- (~**{short_area:.2f} m²** missing)"
        )

    st.markdown("---")
    st.subheader("Matched parking spaces (exact name)")
    if rows:
        df = pd.DataFrame(rows)[["Space Name", "Storey", "Area (m²)", "GlobalId"]]
        st.dataframe(df, use_container_width=True, hide_index=True)

        buf = io.StringIO()
        df.to_csv(buf, index=False)
        st.download_button("Download CSV", buf.getvalue(), "parking_spaces_exact.csv", "text/csv")
    else:
        st.info("No spaces named exactly ‘parking’ were found in IfcSpace LongName/Name.")


# ---------------------------
# Standalone support
# ---------------------------
def _ensure_ifc_from_sidebar():
    """
    If no IFC present in st.session_state.ifc, show a sidebar uploader and open the model.
    """
    if "ifc" in st.session_state and st.session_state.ifc is not None:
        return

    with st.sidebar:
        st.subheader("📁 IFC")
        up = st.file_uploader(
            "Upload IFC (.ifc / .ifczip)",
            type=["ifc", "ifczip"],
            key="__parking_ifc_upload",
            help="Upload once here."
        )

    if up is None:
        st.info("Upload an IFC file in the sidebar to begin.")
        st.stop()

    # open once
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


def _run_standalone():
    st.set_page_config(page_title="Parking Checker (IFC)", page_icon="🚗", layout="wide")

    if ifcopenshell is None:
        st.error("`ifcopenshell` is not installed. Install with: `pip install ifcopenshell`")
        st.stop()
    if Polygon is None or unary_union is None:
        st.error("`shapely` is required. Install with: `pip install shapely`")
        st.stop()

    _ensure_ifc_from_sidebar()
    render_parking_checker()  # uses st.session_state.ifc


# When run directly:  streamlit run parking_checker.py
if __name__ == "__main__":
    _run_standalone()
