# wc_drinkingroom_distance_checker.py
# Usage:
#   from wc_drinkingroom_distance_checker import run_wc_drinkingroom_distance_check
#   run_wc_drinkingroom_distance_check(ifc=your_ifc_bytes_or_path_or_ifcopenshell_file)
#
# Or standalone:
#   streamlit run wc_drinkingroom_distance_checker.py
#
# Requires: pip install streamlit ifcopenshell pandas plotly numpy

import math
import tempfile
import pandas as pd
import numpy as np
import streamlit as st
import plotly.graph_objects as go

try:
    import ifcopenshell
    import ifcopenshell.geom
except Exception:
    st.error("⚠️ Please install dependencies: pip install ifcopenshell streamlit pandas plotly numpy")

MIN_DISTANCE_M = 15.0  # threshold
WC_NAME = "wc room"
DRINK_NAME = "drinking room"


def run_wc_drinkingroom_distance_check(ifc=None, min_distance_m: float = MIN_DISTANCE_M):
    st.caption("code: 4-1-7-18")
    st.header("📏 WC Room ↔ Drinking Room Distance Check")

    # --- IFC open (upload locally if none provided) ---
    model = None
    if ifc is None:
        with st.sidebar:
            st.subheader("📁 Upload IFC")
            up_ifc = st.file_uploader("Upload .ifc", type=["ifc"], key="ifc_upload_wcdr")
        if up_ifc:
            model = _open_model_from_bytes(up_ifc.read())
        else:
            st.info("⬆️ Upload an IFC file to continue.")
            return  # Soft exit, keeping other app modules alive
    else:
        model = _open_model_generic(ifc)

    if model is None:
        st.error("Could not open IFC model.")
        return

    # --- Collect rooms (exact LongName) ---
    spaces = model.by_type("IfcSpace")
    wc_rooms = [s for s in spaces if s.LongName and _ci(s.LongName) == WC_NAME]
    dr_rooms = [s for s in spaces if s.LongName and _ci(s.LongName) == DRINK_NAME]

    # FIX: Replaced st.stop() with soft returns to prevent tab freezing
    if not wc_rooms:
        st.warning(f"❌ No rooms found with LongName exactly '{WC_NAME}'.")
        return
    if not dr_rooms:
        st.warning(f"❌ No rooms found with LongName exactly '{DRINK_NAME}'.")
        return

    # --- Compute centers & cache raw geometry for 3D view ---
    settings = ifcopenshell.geom.settings()
    settings.set(settings.USE_WORLD_COORDS, True)

    def process_room_geom(sp):
        cx, cy, cz = _center_world_xyz(sp, model, settings)
        mesh_data = _get_mesh_geometry(sp, settings)
        return {"space": sp, "cx": cx, "cy": cy, "cz": cz, "mesh": mesh_data}

    wc_processed = [process_room_geom(s) for s in wc_rooms]
    dr_processed = [process_room_geom(s) for s in dr_rooms]

    # Validate centers
    wc_valid = [r for r in wc_processed if r["cx"] is not None]
    dr_valid = [r for r in dr_processed if r["cx"] is not None]

    if not wc_valid or not dr_valid:
        st.error("Could not derive 3D centers for rooms. Check footprints/representation.")
        return

    # --- Pairwise distances & summary ---
    rows = []
    min_dist_centroid = None
    min_pair = None
    target_wc_data = None
    target_dr_data = None
    closest_pts_pair = None
    min_dist_closest = float("inf")

    for wc_item in wc_valid:
        wc_label = _label_room(model, wc_item["space"])
        wc_verts = wc_item["mesh"]["verts"] if wc_item["mesh"] else np.array([[wc_item["cx"], wc_item["cy"], wc_item["cz"]]])
        
        for dr_item in dr_valid:
            dr_label = _label_room(model, dr_item["space"])
            dr_verts = dr_item["mesh"]["verts"] if dr_item["mesh"] else np.array([[dr_item["cx"], dr_item["cy"], dr_item["cz"]]])
            
            # Method 1: Centroid-to-Centroid (2D horizontal mapping)
            d_center = round(math.hypot(wc_item["cx"] - dr_item["cx"], wc_item["cy"] - dr_item["cy"]), 3)
            
            # Method 2: Boundary Closest Points Calculation
            d_close_local = float("inf")
            p_wc_best, p_dr_best = None, None
            
            for w_v in wc_verts:
                dists = np.linalg.norm(dr_verts - w_v, axis=1)
                idx_min = np.argmin(dists)
                if dists[idx_min] < d_close_local:
                    d_close_local = dists[idx_min]
                    p_wc_best = w_v
                    p_dr_best = dr_verts[idx_min]
            
            d_close_local = round(float(d_close_local), 3)

            rows.append({
                "WC Room": wc_label,
                "Drinking Room": dr_label,
                "Centroid Distance (m)": d_center,
                "Closest Edge Distance (m)": d_close_local
            })
            
            if (min_dist_centroid is None) or (d_center < min_dist_centroid):
                min_dist_centroid = d_center
                min_pair = (wc_label, dr_label)
                target_wc_data = wc_item
                target_dr_data = dr_item
                
            if d_close_local < min_dist_closest:
                min_dist_closest = d_close_local
                closest_pts_pair = (p_wc_best, p_dr_best)

    # --- Verdict & Compliance ---
    st.subheader(f"Threshold: Distance ≥ {min_distance_m:.2f} m")
    if min_dist_centroid is None:
        st.error("No distances could be computed.")
        return

    is_compliant = min_dist_centroid >= min_distance_m
    short_by = max(0.0, round(min_distance_m - min_dist_centroid, 3))
    status_color = "#2ecc71" if is_compliant else "#e74c3c"
    
    if is_compliant:
        st.success(f"✅ Pass: Sufficient clearance measured. "
                   f" {min_pair[0]} ↔ {min_pair[1]} = {min_dist_centroid:.3f} m (Threshold: {min_distance_m:.2f} m).")
    else:
        st.error(f"❌ Violation: Rooms closer than code limits. "
                 f" {min_pair[0]} ↔ {min_pair[1]} = {min_dist_centroid:.3f} m "
                 f"(Short by {short_by:.3f} m).")

    # --- Metrics Layout Box ---
    m_col1, m_col2 = st.columns(2)
    m_col1.metric("Centroid-to-Centroid Path", f"{min_dist_centroid:.3f} m")
    m_col2.metric("Boundary Point-to-Point Path", f"{min_dist_closest:.3f} m")

    # --- Interactive 3D Digital Twin Viewport ---
    st.markdown("#### 📦 Spatial Verification Layout (3D)")
    fig3d = go.Figure()

    # 1. Background context geometry (Gray)
    for space in spaces:
        if space in [target_wc_data["space"], target_dr_data["space"]]:
            continue
        mesh = _get_mesh_geometry(space, settings)
        if mesh:
            fig3d.add_trace(go.Mesh3d(
                x=mesh["verts"][:, 0], y=mesh["verts"][:, 1], z=mesh["verts"][:, 2],
                i=mesh["faces"][:, 0], j=mesh["faces"][:, 1], k=mesh["faces"][:, 2],
                color="#E5E7E9", opacity=0.12, showlegend=False, hoverinfo='skip'
            ))

    # 2. Focus compliance targets (Colorized)
    for item, label, role in [(target_wc_data, min_pair[0], "WC"), (target_dr_data, min_pair[1], "Drink")]:
        mesh = item["mesh"]
        if mesh:
            fig3d.add_trace(go.Mesh3d(
                x=mesh["verts"][:, 0], y=mesh["verts"][:, 1], z=mesh["verts"][:, 2],
                i=mesh["faces"][:, 0], j=mesh["faces"][:, 1], k=mesh["faces"][:, 2],
                color=status_color, opacity=0.75, name=role, legendgroup=role
            ))
        fig3d.add_trace(go.Scatter3d(
            x=[item["cx"]], y=[item["cy"]], z=[item["cz"] + 1.2],
            text=[label], mode="text",
            textfont=dict(size=10, color="black"), legendgroup=role, showlegend=False
        ))

    # 3. Vector Path #1: Centroids (Solid Line)
    fig3d.add_trace(go.Scatter3d(
        x=[target_wc_data["cx"], target_dr_data["cx"]],
        y=[target_wc_data["cy"], target_dr_data["cy"]],
        z=[target_wc_data["cz"], target_dr_data["cz"]],
        mode="lines+markers",
        line=dict(color="black", width=5),
        marker=dict(size=5, color="black"),
        name=f"Centroid Path ({min_dist_centroid:.2f}m)"
    ))

    # 4. Vector Path #2: Absolute Closest Points (Dotted Blue Line)
    if closest_pts_pair is not None:
        p_wc, p_dr = closest_pts_pair
        fig3d.add_trace(go.Scatter3d(
            x=[p_wc[0], p_dr[0]],
            y=[p_wc[1], p_dr[1]],
            z=[p_wc[2], p_dr[2]],
            mode="lines+markers",
            line=dict(color="#2980b9", width=4, dash="dot"),
            marker=dict(size=4, color="#2980b9"),
            name=f"Closest Edge Path ({min_dist_closest:.2f}m)"
        ))

    fig3d.update_layout(
        scene=dict(aspectmode='data', dragmode='orbit'),
        height=600,
        margin=dict(l=0, r=0, b=0, t=0),
        legend=dict(yanchor="top", y=0.99, xanchor="left", x=0.01)
    )
    st.plotly_chart(fig3d, use_container_width=True)

    # --- Details Table ---
    st.subheader("Preview Distances Table")
    df = pd.DataFrame(rows).sort_values("Centroid Distance (m)")
    st.dataframe(df, use_container_width=True)


# ----------------- Geometry & IFC helpers -----------------
def _center_world_xyz(space, model, settings):
    try:
        shape = ifcopenshell.geom.create_shape(settings, space)
        verts = np.array(shape.geometry.verts).reshape(-1, 3)
        return float(verts[:, 0].mean()), float(verts[:, 1].mean()), float(verts[:, 2].mean())
    except Exception:
        pass

    pts_local = _extract_profile_xy_points_from_space(space)
    if not pts_local:
        return None, None, None

    ux = _unit_scale_m(model)
    tx, ty = _object_translation_xy(space)
    xs = [(p[0] * ux) + tx for p in pts_local]
    ys = [(p[1] * ux) + ty for p in pts_local]
    
    cz = 0.0
    try:
        cur = getattr(space, "ObjectPlacement", None)
        if cur:
            from ifcopenshell.util.placement import get_local_placement
            M = get_local_placement(cur)
            cz = float(M[2][3])
    except Exception:
        pass
        
    return sum(xs) / len(xs), sum(ys) / len(ys), cz


def _get_mesh_geometry(element, settings):
    try:
        if element.Representation:
            shape = ifcopenshell.geom.create_shape(settings, element)
            verts = np.array(shape.geometry.verts).reshape(-1, 3)
            faces = np.array(shape.geometry.faces).reshape(-1, 3)
            return {"verts": verts, "faces": faces}
    except Exception:
        pass
    return None


def _unit_scale_m(model):
    try:
        from ifcopenshell.util.unit import calculate_unit_scale
        return float(calculate_unit_scale(model)) or 1.0
    except Exception:
        return 1.0


def _object_translation_xy(product):
    try:
        from ifcopenshell.util.placement import get_local_placement
        opl = getattr(product, "ObjectPlacement", None)
        if not opl: return (0.0, 0.0)
        M = get_local_placement(opl)
        return float(M[0][3]), float(M[1][3])
    except Exception:
        tx = ty = 0.0
        cur = getattr(product, "ObjectPlacement", None)
        visited = 0
        while cur and visited < 32:
            loc = getattr(getattr(cur, "RelativePlacement", None), "Location", None)
            if loc and getattr(loc, "Coordinates", None) and len(loc.Coordinates) >= 2:
                tx += float(loc.Coordinates[0])
                ty += float(loc.Coordinates[1])
            cur = getattr(cur, "PlacementRelTo", None)
            visited += 1
        return tx, ty

def _ci(s):
    return (s or "").strip().lower()

def _coords2d(pt):
    cs = list(pt.Coordinates)
    if len(cs) < 2: return None
    return float(cs[0]), float(cs[1])

def _polyline_points2d(curve):
    if not curve or not curve.is_a("IfcPolyline"):
        return []
    pts = []
    for p in curve.Points or []:
        c2 = _coords2d(p)
        if c2: pts.append(c2)
    if pts and pts[0] != pts[-1]:
        pts.append(pts[0])
    return pts

def _rectangle_points2d(xdim, ydim):
    x = float(xdim); y = float(ydim)
    return [(0,0),(x,0),(x,y),(0,y),(0,0)]

def _extract_profile_xy_points_from_space(space):
    rep = getattr(space, "Representation", None)
    if not rep: return []
    for r in (rep.Representations or []):
        for item in (r.Items or []):
            try:
                if item.is_a("IfcExtrudedAreaSolid"):
                    prof = item.SweptArea
                    if not prof: continue
                    if prof.is_a("IfcRectangleProfileDef"):
                        xdim = getattr(prof, "XDim", None)
                        ydim = getattr(prof, "YDim", None)
                        if xdim and ydim: return _rectangle_points2d(xdim, ydim)
                    if prof.is_a("IfcArbitraryClosedProfileDef"):
                        curve = getattr(prof, "OuterCurve", None)
                        pts = _polyline_points2d(curve)
                        if pts: return pts
                if item.is_a("IfcCurveBoundedPlane"):
                    outer = getattr(item, "OuterBoundary", None)
                    pts = _polyline_points2d(outer)
                    if pts: return pts
            except Exception:
                continue
    return []

def _label_room(model, space):
    num = _get_room_number(model, space)
    ln = getattr(space, "LongName", None) or "-"
    return f"Room-{num} ({ln})"

def _get_room_number(model, space):
    import re
    candidates = []
    if getattr(space, "Name", None): candidates.append(str(space.Name))
    if getattr(space, "LongName", None): candidates.append(str(space.LongName))
    try:
        for rel in model.by_type("IfcRelDefinesByProperties"):
            if not getattr(rel, "RelatedObjects", None) or space not in rel.RelatedObjects:
                continue
            pset = getattr(rel, "RelatingPropertyDefinition", None)
            if not pset or not pset.is_a("IfcPropertySet"):
                continue
            for prop in pset.HasProperties or []:
                pname = _ci(getattr(prop, "Name", "") or "")
                if "number" in pname:
                    if prop.is_a("IfcPropertySingleValue"):
                        val = getattr(prop, "NominalValue", None)
                        if val is not None:
                            candidates.append(str(getattr(val, "wrappedValue", val)))
    except Exception:
        pass
    for text in candidates:
        if not text: continue
        m = re.search(r"\d+(\.\d+)?", text)
        if m: return m.group(0)
    return "-"

def _open_model_from_bytes(b: bytes):
    with tempfile.NamedTemporaryFile(delete=False, suffix=".ifc") as tmp:
        tmp.write(b); tmp.flush()
        return ifcopenshell.open(tmp.name)

def _open_model_generic(ifc):
    try:
        from ifcopenshell.file import file as IfcFile
    except Exception:
        IfcFile = None
    if IfcFile and isinstance(ifc, IfcFile): return ifc
    if isinstance(ifc, (bytes, bytearray)): return _open_model_from_bytes(ifc)
    if isinstance(ifc, str):
        try: return ifcopenshell.open(ifc)
        except Exception: return None
    read = getattr(ifc, "read", None)
    if callable(read):
        try: return _open_model_from_bytes(read())
        except Exception: return None
    return None


if __name__ == "__main__":
    st.set_page_config(layout="wide")
    run_wc_drinkingroom_distance_check()