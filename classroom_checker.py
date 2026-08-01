# classroom_checker.py
# Streamlit web UI for Classroom Capacity Checker (IFC + Shapely)
# - Uses IFC passed from main app (no internal uploader)
# - Optional standalone mode with a uniquely-keyed uploader

import os
import tempfile
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import pandas as pd
import numpy as np
import streamlit as st
import plotly.graph_objects as go

# IFC + geometry
import ifcopenshell
import ifcopenshell.geom as ifcgeom
from shapely.geometry import Polygon, Point
from shapely.ops import unary_union

# -------------------------
# Config
# -------------------------
DEFAULT_MAX_CAPACITY = 24
DEFAULT_Z_TOL_M = 1.0  # fixed (hidden)

STANDARD_CHAIR_NAMES = {
    "student chair": "student chair",
    "drinking tap": "drinking tap",
}

# -------------------------
# Data models
# -------------------------
@dataclass
class ChairRec:
    ifc_id: int
    name: Optional[str]
    family: Optional[str]
    type_name: Optional[str]
    x: float
    y: float
    z: float

@dataclass
class RoomRec:
    ifc_id: int
    longname: str
    name: str
    zmin: float
    zmax: float
    elev: float
    footprint: Polygon
    chairs: List[int]
    verts: Optional[np.ndarray] = None
    faces: Optional[np.ndarray] = None

# -------------------------
# Helpers
# -------------------------
def _norm(s: Optional[str]) -> str:
    return (s or "").strip()

def _lower(s: Optional[str]) -> str:
    return _norm(s).lower()

def get_element_world_point(el) -> Optional[Tuple[float, float, float]]:
    try:
        pl = el.ObjectPlacement
        if not pl:
            return None

        def placement_to_matrix(placement):
            loc = placement.RelativePlacement.Location
            ox, oy, oz = (loc.Coordinates[0], loc.Coordinates[1], loc.Coordinates[2])

            def vec3(v, default):
                return (
                    float(v.DirectionRatios[0]),
                    float(v.DirectionRatios[1]),
                    float(v.DirectionRatios[2]),
                ) if v else default

            ref = getattr(placement.RelativePlacement, "RefDirection", None)
            axis = getattr(placement.RelativePlacement, "Axis", None)
            zaxis = vec3(axis, (0.0, 0.0, 1.0))
            xaxis = vec3(ref,  (1.0, 0.0, 0.0))
            yaxis = (
                zaxis[1]*xaxis[2] - zaxis[2]*xaxis[1],
                zaxis[2]*xaxis[0] - zaxis[0]*xaxis[2],
                zaxis[0]*xaxis[1] - zaxis[1]*xaxis[0],
            )
            return (
                (xaxis[0], yaxis[0], zaxis[0], ox),
                (xaxis[1], yaxis[1], zaxis[1], oy),
                (xaxis[2], yaxis[2], zaxis[2], oz),
                (0.0, 0.0, 0.0, 1.0),
            )

        mats = []
        cur = pl
        while cur:
            mats.append(placement_to_matrix(cur))
            cur = getattr(cur, "PlacementRelTo", None)

        def mm(a, b):
            out = [[0.0]*4 for _ in range(4)]
            for i in range(4):
                for j in range(4):
                    out[i][j] = sum(a[i][k]*b[k][j] for k in range(4))
            return tuple(tuple(row) for row in out)

        M = mats[-1]
        for i in range(len(mats)-2, -1, -1):
            M = mm(M, mats[i])

        return float(M[0][3]), float(M[1][3]), float(M[2][3])
    except Exception:
        return None

def build_space_footprint(space, settings):
    try:
        shape = ifcgeom.create_shape(settings, space)
    except Exception:
        return None
    verts_raw = list(shape.geometry.verts)
    if not verts_raw:
        return None
    xs, ys, zs = verts_raw[0::3], verts_raw[1::3], verts_raw[2::3]
    zmin, zmax, elev = min(zs), max(zs), min(zs)
    faces_raw = list(shape.geometry.faces)
    polys = []
    for i in range(0, len(faces_raw), 3):
        try:
            i1, i2, i3 = faces_raw[i], faces_raw[i+1], faces_raw[i+2]
            tri = Polygon([(xs[i1], ys[i1]), (xs[i2], ys[i2]), (xs[i3], ys[i3])])
            if tri.is_valid and tri.area > 0:
                polys.append(tri)
        except Exception:
            continue
    if not polys:
        return None
    footprint = unary_union(polys)
    if footprint.is_empty:
        return None
        
    verts = np.array(verts_raw).reshape(-1, 3)
    faces = np.array(faces_raw).reshape(-1, 3)
    return footprint, float(zmin), float(zmax), float(elev), verts, faces

def collect_rooms(ifc):
    spaces = list(ifc.by_type("IfcSpace") or [])
    if not spaces:
        return {}
    settings = ifcopenshell.geom.settings()
    settings.set(settings.USE_WORLD_COORDS, True)
    rooms = {}
    for sp in spaces:
        built = build_space_footprint(sp, settings)
        if not built:
            continue
        fp, zmin, zmax, elev, verts, faces = built
        longname = _norm(getattr(sp, "LongName", None))
        name = _norm(getattr(sp, "Name", None)) or longname or f"Space_{sp.id()}"
        rooms[str(sp.id())] = RoomRec(sp.id(), longname, name, zmin, zmax, elev, fp, [], verts, faces)
    return rooms

def collect_chairs(ifc):
    elems = list(ifc.by_type("IfcFurnishingElement") or [])
    out = []
    for el in elems:
        pt = get_element_world_point(el)
        if not pt:
            continue
        tname = None
        try:
            if el.IsTypedBy:
                t = el.IsTypedBy[0].RelatingType
                tname = _norm(getattr(t, "Name", None) or getattr(t, "ElementType", None))
        except Exception:
            pass
        out.append(
            ChairRec(
                ifc_id=el.id(),
                name=_norm(getattr(el, "Name", None)),
                family=_norm(getattr(el, "ObjectType", None)),
                type_name=tname,
                x=pt[0], y=pt[1], z=pt[2],
            )
        )
    return out

def chair_matches(ch: ChairRec, query: str):
    q = _lower(query)
    return any(q in _lower(c) for c in [ch.name, ch.family, ch.type_name] if c)

def assign_chairs_to_rooms(rooms: Dict[str, RoomRec], chairs: List[ChairRec], z_tol_m: float):
    for r in rooms.values():
        r.chairs = []
    for ch in chairs:
        p = Point(ch.x, ch.y)
        for r in rooms.values():
            if abs(ch.z - r.elev) > z_tol_m:
                continue
            if r.footprint.contains(p) or r.footprint.touches(p):
                r.chairs.append(ch.ifc_id)
                break

def is_classroom(room: RoomRec) -> bool:
    ln, nm = _lower(room.longname), _lower(room.name)
    return (ln == "classroom") or (nm == "classroom")

# -------------------------
# Results rendering
# -------------------------
def render_results(title, rooms_filtered, max_cap, out_csv_name, code_text):
    st.caption(f"Code: {code_text}")
    st.title("👨🏻‍🏫Classroom Capacity Checker")

    st.subheader(title)

    if not rooms_filtered:
        st.info("No matching rooms found.")
        return

    results = []
    good, bad = [], []
    ordered = sorted(rooms_filtered.values(), key=lambda r: (r.longname or r.name, r.ifc_id))
    for r in ordered:
        cnt = len(r.chairs)

        room_number = None
        if r.name.isdigit():
            try:
                room_number = int(r.name)
            except Exception:
                room_number = None

        status_ok = (cnt <= max_cap)
        rec = {
            "room_name": r.longname or r.name,
            "room_number": room_number,
            "chair_count": cnt,
            "status": "OK" if status_ok else "NOT OK",
        }
        results.append(rec)
        (good if status_ok else bad).append(rec)

    gcount, bcount = len(good), len(bad)
    st.markdown(f"**✅ GOOD rooms:** {gcount} &nbsp;&nbsp; | &nbsp;&nbsp; **❌ BAD rooms:** {bcount}")

    def line_fmt(i, rec, ok: bool) -> str:
        rn = rec["room_name"]
        num = f" {rec['room_number']}" if rec["room_number"] is not None else ""
        if ok:
            return f"{i}. {rn}{num} — {rec['chair_count']} chair(s) — OK (≤ {max_cap})"
        else:
            return f"{i}. {rn}{num} — {rec['chair_count']} chair(s) — NOT OK  Over (≥ {max_cap})"

    colA, colB = st.columns(2)
    with colA:
        st.markdown(f"### ✅ GOOD ({gcount})")
        st.write("\n".join(line_fmt(i, r, True) for i, r in enumerate(good, 1)) if gcount else "_None_")
    with colB:
        st.markdown(f"### ❌ BAD ({bcount})")
        st.write("\n".join(line_fmt(i, r, False) for i, r in enumerate(bad, 1)) if bcount else "_None_")

    # -------------------------
    # Interactive 3D Visualization Pipeline
    # -------------------------
    st.divider()
    st.subheader("📦 3D Model Capacity Visualization")
    
    fig3d = go.Figure()
    tracked_3d_legends = set()

    for r in ordered:
        if r.verts is None or r.faces is None:
            continue
            
        status_ok = (len(r.chairs) <= max_cap)
        
        # Soft architectural pale colors
        color = "#a9dfbf" if status_ok else "#f5b7b1"
        legend_group_name = "✅ GOOD Classrooms" if status_ok else "❌ BAD Classrooms"
        
        # Room label text detailing the longname, exact room number, and capacity
        full_label = f"{r.longname or 'Classroom'} #{r.name} ({len(r.chairs)} chairs)"
        
        show_in_legend = False
        if legend_group_name not in tracked_3d_legends:
            show_in_legend = True
            tracked_3d_legends.add(legend_group_name)

        # 3D Mesh boundaries
        fig3d.add_trace(go.Mesh3d(
            x=r.verts[:, 0], y=r.verts[:, 1], z=r.verts[:, 2],
            i=r.faces[:, 0], j=r.faces[:, 1], k=r.faces[:, 2],
            color=color, opacity=0.55, 
            name=legend_group_name,
            legendgroup=legend_group_name,
            showlegend=show_in_legend
        ))

        # Add physical borders/outlines around rooms using the footprint polygon
        geoms = [r.footprint] if r.footprint.geom_type == 'Polygon' else list(r.footprint.geoms)
        for g in geoms:
            x_coords, y_coords = g.exterior.xy
            x_list, y_list = list(x_coords), list(y_coords)
            
            # Draw bottom profile wireframe line
            fig3d.add_trace(go.Scatter3d(
                x=x_list, y=y_list, z=[r.zmin] * len(x_list),
                mode='lines', line=dict(color='#2c3e50', width=2),
                legendgroup=legend_group_name, showlegend=False
            ))
            # Draw top profile wireframe line
            fig3d.add_trace(go.Scatter3d(
                x=x_list, y=y_list, z=[r.zmax] * len(x_list),
                mode='lines', line=dict(color='#2c3e50', width=2),
                legendgroup=legend_group_name, showlegend=False
            ))

        # Room Matrix Text Tracker
        x_center, y_center = r.footprint.centroid.x, r.footprint.centroid.y
        fig3d.add_trace(go.Scatter3d(
            x=[x_center], 
            y=[y_center], 
            z=[r.zmax + 0.1],
            text=[full_label], mode="text",
            textfont=dict(size=9, color="black"),
            legendgroup=legend_group_name,
            showlegend=False
        ))

    fig3d.update_layout(
        scene=dict(aspectmode='data', dragmode='orbit'), 
        height=650, 
        margin=dict(l=0, r=0, b=0, t=0),
        legend=dict(yanchor="top", y=0.99, xanchor="left", x=0.01)
    )
    st.plotly_chart(fig3d, use_container_width=True)

    # -------------------------
    # Data Table View
    # -------------------------
    df = pd.DataFrame(results, columns=["room_name", "room_number", "chair_count", "status"])
    st.markdown("### Table")
    styled = (
        df.style
          .set_properties(**{"text-align": "center"})
          .set_table_styles([{"selector": "th", "props": [("text-align", "center")]}])
    )
    st.table(styled)

    csv_bytes = df.to_csv(index=False).encode("utf-8")
    st.download_button("⬇️ Download CSV", data=csv_bytes, file_name=out_csv_name, mime="text/csv")

# -------------------------
# Public entry point (for tabs) — NO uploader here
# -------------------------
def run_classroom_checker(ifc=None, max_cap: Optional[int] = None):
    # Use passed IFC or session
    if ifc is None:
        ifc = st.session_state.get("ifc")
    if ifc is None:
        st.warning("No IFC loaded. Please upload it in the main app.")
        return

    with st.sidebar:
        max_cap = st.number_input(
            "Student number (max capacity per room)",
            min_value=1,
            max_value=10000,
            value=DEFAULT_MAX_CAPACITY,
            step=1
    )
        
    st.session_state["max_cap"] = max_cap

    rooms = collect_rooms(ifc)
    chairs = collect_chairs(ifc)

    classroom_rooms = {k: r for k, r in rooms.items() if is_classroom(r)}
    classroom_chairs = [c for c in chairs if chair_matches(c, STANDARD_CHAIR_NAMES["student chair"])]

    assign_chairs_to_rooms(classroom_rooms, classroom_chairs, z_tol_m=DEFAULT_Z_TOL_M)

    render_results(
        "",
        classroom_rooms,
        max_cap,
        "classroom_capacity_report.csv",
        "2-1-1",
    )

# -------------------------
# Standalone run (optional, uniquely keyed uploader)
# -------------------------
if __name__ == "__main__":
    st.set_page_config(page_title="Classroom Capacity Checker", layout="wide")
    st.title("Classroom Capacity Checker — Standalone")

    with st.sidebar:
        up = st.file_uploader(
            "Upload IFC (.ifc / .ifczip)",
            type=["ifc", "ifczip"],
            key="classroom_ifc_upload_STANDALONE"  # unique key to avoid clashes
        )
        max_cap = st.number_input(
            "Student number (max capacity per room)",
            min_value=1, max_value=10000, value=DEFAULT_MAX_CAPACITY, step=1,
            help="Urban classrooms: 24 (max 30). Rural classrooms: 18."
        )

    if up is None:
        st.info("Upload an IFC file in the sidebar to begin.")
        st.stop()

    suffix = ".ifczip" if up.name.lower().endswith(".ifczip") else ".ifc"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(up.read())
        path = tmp.name

    try:
        ifc_standalone = ifcopenshell.open(path)
    except Exception as e:
        st.error(f"IFC open error for `{up.name}`: {e}")
        st.stop()

    run_classroom_checker(ifc_standalone, max_cap)