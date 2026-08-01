# wc_size_check_xy.py
# Streamlit app — WC (only exact 'wc') minimum dimension check with 3D View
# Run: streamlit run wc_size_check_xy.py

import re
import tempfile
import pandas as pd
import numpy as np
import streamlit as st
import plotly.graph_objects as go

import ifcopenshell
import ifcopenshell.geom as ifcgeom
from shapely.geometry import Polygon
from shapely.ops import unary_union

# -----------------------------
# Config
# -----------------------------
DEFAULT_MIN_L = 1.3
DEFAULT_MIN_W = 1.1
DEFAULT_MIN_H = 2.6

# ✅ Exact name only
WC_LONGNAMES = {"wc"}


# =============================
# Geometry Helpers for 3D View
# =============================
def build_space_mesh_and_footprint(space, settings):
    """Generates the actual 3D mesh arrays and 2D footprint boundary for visualization."""
    try:
        shape = ifcgeom.create_shape(settings, space)
    except Exception:
        return None
    verts_raw = list(shape.geometry.verts)
    if not verts_raw:
        return None
        
    xs, ys, zs = verts_raw[0::3], verts_raw[1::3], verts_raw[2::3]
    zmin, zmax = min(zs), max(zs)
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
    return footprint, float(zmin), float(zmax), verts, faces


# =============================
# Public entry point
# =============================
def run_wc_size_check(ifc=None,
                      min_l: float = DEFAULT_MIN_L,
                      min_w: float = DEFAULT_MIN_W,
                      min_h: float = DEFAULT_MIN_H):
    """Render the WC Size Check UI safely within a multi-tab or standalone app."""
    # Safe Import Check inside execution context
    try:
        import ifcopenshell
    except ImportError:
        st.error("⚠️ **Dependency Error:** Please install requirements to use this tab: `pip install ifcopenshell`")
        return # Safe return to protect sibling tabs

    st.caption("code: 4-1-7-7, 4-1-7-10")
    st.header("🚻 WC Size Check")

    # -------------- IFC upload --------------
    model = None
    if ifc is None:
        with st.sidebar:
            st.header("📁 Upload IFC")
            up_ifc = st.file_uploader("Upload .ifc", type=["ifc"], key="ifc_upload_wc")
        if not up_ifc:
            st.info("⬆️ Upload an IFC file to continue.")
            return # Safe return instead of st.stop()
        model = _open_model_from_bytes(up_ifc.read(), ifcopenshell)
    else:
        model = _open_model_generic(ifc, ifcopenshell)

    if model is None:
        st.error("Could not open IFC model.")
        return

    # -------------- Find WC spaces (exact name == 'wc') --------------
    spaces = [
        s for s in model.by_type("IfcSpace")
        if _ci(getattr(s, "LongName", None)) == "wc"
    ]

    if not spaces:
        st.warning("❌ No rooms found with LongName exactly equal to 'wc'.")
        return

    # Configure geometry engine for visualization settings
    geom_settings = ifcopenshell.geom.settings()
    geom_settings.set(geom_settings.USE_WORLD_COORDS, True)

    # -------------- Check logic --------------
    ok_lines, bad_lines, ok_nums, bad_nums, rows = [], [], [], [], []
    visualization_records = []

    for sp in spaces:
        rnum = _get_room_number(model, sp)
        pts = _extract_profile_xy_points_from_space(sp)
        L, W = (None, None)
        if pts:
            L, W = _bounds_LW(pts)
        H = _get_height(model, sp)

        plan_ok = False
        caseA = caseB = False
        if L is not None and W is not None:
            caseA = (L >= min_l and W >= min_w)
            caseB = (L >= min_w and W >= min_l)
            plan_ok = caseA or caseB
        height_ok = (H is not None and H >= min_h)
        status_ok = bool(plan_ok and height_ok)

        if status_ok:
            orientation = "(x=L, y=W)" if caseA else "(x=W, y=L)" if caseB else "(Both)"
            ok_lines.append(f"Room {rnum} → L/W: {L} × {W}, H: {H} {orientation}")
            ok_nums.append(str(rnum))
            status, detail = "OK", orientation
        else:
            parts = []
            if not plan_ok:
                parts.append(f"L/W ({L}, {W}) / MIN ({min_l}, {min_w})")
            if not height_ok:
                parts.append(f"H={H} / (≥ {min_h})")
            bad_lines.append(f"Room {rnum} → " + " | ".join(parts))
            bad_nums.append(str(rnum))
            status, detail = "NOT OK", " | ".join(parts)

        rows.append({
            "Room Name": getattr(sp, "LongName", None),
            "Room Number": rnum,
            "Length (m)": L if L is not None else "-",
            "Width (m)": W if W is not None else "-",
            "Height (m)": H if H is not None else "-",
            "Status": status,
            "Detail": detail,
        })

        # Process geometry structural meshes for plotting engine mapping
        mesh_data = build_space_mesh_and_footprint(sp, geom_settings)
        if mesh_data:
            footprint, zmin, zmax, verts, faces = mesh_data
            visualization_records.append({
                "number": rnum,
                "longname": getattr(sp, "LongName", None) or "WC",
                "status_ok": status_ok,
                "footprint": footprint,
                "zmin": zmin,
                "zmax": zmax,
                "verts": verts,
                "faces": faces,
                "label": f"Room {rnum} ({L or '-'}x{W or '-'}x{H or '-'})"
            })

    # -------------- Report --------------
    st.success(f"✅ {len(spaces)} WC rooms found.")

    report = []
    if ok_lines:
        report.append(f"### ✅ {len(ok_lines)} WC rooms meet minimum size ({', '.join(ok_nums)})")
        report.extend(ok_lines)
    else:
        report.append("NO WC rooms meet the minimums.")

    report.append("")
    if bad_lines:
        report.append(f"### ❌ {len(bad_lines)} WC rooms do NOT meet minimum size ({', '.join(bad_nums)})")
        report.extend(bad_lines)
    else:
        report.append("All WC rooms meet the minimums.")

    st.markdown("\n\n".join(report))

    # -----------------------------
    # 3D Spatial Visualization
    # -----------------------------
    if visualization_records:
        st.divider()
        st.subheader("📦 3D Space Compliance Visualization")
        
        fig3d = go.Figure()
        tracked_legends = set()
        
        for vr in visualization_records:
            color = "#a9dfbf" if vr["status_ok"] else "#f5b7b1"
            legend_group = "✅ Compliant Size" if vr["status_ok"] else "❌ Non-Compliant Size"
            
            show_legend = legend_group not in tracked_legends
            if show_legend:
                tracked_legends.add(legend_group)
                
            # Render volumetric space shell
            fig3d.add_trace(go.Mesh3d(
                x=vr["verts"][:, 0], y=vr["verts"][:, 1], z=vr["verts"][:, 2],
                i=vr["faces"][:, 0], j=vr["faces"][:, 1], k=vr["faces"][:, 2],
                color=color, opacity=0.6,
                name=legend_group,
                legendgroup=legend_group,
                showlegend=show_legend
            ))
            
            # Boundary profile wires mapping
            geoms = [vr["footprint"]] if vr["footprint"].geom_type == 'Polygon' else list(vr["footprint"].geoms)
            for g in geoms:
                x_coords, y_coords = g.exterior.xy
                x_list, y_list = list(x_coords), list(y_coords)
                
                # Base ring wireframe
                fig3d.add_trace(go.Scatter3d(
                    x=x_list, y=y_list, z=[vr["zmin"]] * len(x_list),
                    mode='lines', line=dict(color='#2c3e50', width=2),
                    legendgroup=legend_group, showlegend=False
                ))
                # Top ring wireframe
                fig3d.add_trace(go.Scatter3d(
                    x=x_list, y=y_list, z=[vr["zmax"]] * len(x_list),
                    mode='lines', line=dict(color='#2c3e50', width=2),
                    legendgroup=legend_group, showlegend=False
                ))
                
            # Text annotation anchors
            xc, yc = vr["footprint"].centroid.x, vr["footprint"].centroid.y
            fig3d.add_trace(go.Scatter3d(
                x=[xc], y=[yc], z=[vr["zmax"] + 0.1],
                text=[vr["label"]], mode="text",
                textfont=dict(size=9, color="black"),
                legendgroup=legend_group, showlegend=False
            ))
            
        fig3d.update_layout(
            scene=dict(aspectmode='data', dragmode='orbit'),
            height=650,
            margin=dict(l=0, r=0, b=0, t=0),
            legend=dict(yanchor="top", y=0.99, xanchor="left", x=0.01)
        )
        st.plotly_chart(fig3d, use_container_width=True)

    # -------------- Table + CSV --------------
    df = pd.DataFrame(rows)
    st.subheader("Results Table")
    st.dataframe(df, use_container_width=True)

    csv = df.to_csv(index=False).encode("utf-8")
    st.download_button("💾 Download CSV", csv, "wc_size_check.csv", "text/csv")


# =============================
# Helpers
# =============================
def _ci(s): return (s or "").strip().lower()

def _coords2d(pt):
    c = list(pt.Coordinates)
    return (float(c[0]), float(c[1])) if len(c) >= 2 else None

def _polyline_points2d(curve):
    if not curve or not curve.is_a("IfcPolyline"):
        return []
    pts = [p for p in [_coords2d(pp) for pp in (curve.Points or [])] if p]
    if pts and pts[0] != pts[-1]: pts.append(pts[0])
    return pts

def _rectangle_points2d(x, y):
    return [(0,0),(x,0),(x,y),(0,y),(0,0)]

def _extract_profile_xy_points_from_space(space):
    rep = getattr(space, "Representation", None)
    if not rep: return []
    for r in (rep.Representations or []):
        for item in (r.Items or []):
            try:
                if item.is_a("IfcExtrudedAreaSolid"):
                    prof = item.SweptArea
                    if prof and prof.is_a("IfcRectangleProfileDef"):
                        return _rectangle_points2d(float(prof.XDim), float(prof.YDim))
                    if prof and prof.is_a("IfcArbitraryClosedProfileDef"):
                        return _polyline_points2d(prof.OuterCurve)
            except Exception:
                continue
    return []

def _bounds_LW(pts):
    xs, ys = zip(*pts)
    return round(max(xs)-min(xs),3), round(max(ys)-min(ys),3)

def _get_height(model, space):
    rep = getattr(space, "Representation", None)
    if rep:
        for r in rep.Representations or []:
            for item in (r.Items or []):
                if item.is_a("IfcExtrudedAreaSolid"):
                    d = getattr(item, "Depth", None)
                    if d: return round(float(d),3)
    return None

def _get_room_number(model, space):
    n = getattr(space, "Name", None)
    ln = getattr(space, "LongName", None)
    for txt in [n, ln]:
        if txt:
            m = re.search(r"\d+(\.\d+)?", str(txt))
            if m: return m.group(0)
    return "-"

def _open_model_from_bytes(b, ifcopenshell):
    with tempfile.NamedTemporaryFile(delete=False, suffix=".ifc") as tmp:
        tmp.write(b); tmp.flush()
        return ifcopenshell.open(tmp.name)

def _open_model_generic(ifc, ifcopenshell):
    try:
        from ifcopenshell.file import file as IfcFile
        if isinstance(ifc, IfcFile):
            return ifc
    except Exception:
        pass
    if isinstance(ifc, (bytes, bytearray)):
        return _open_model_from_bytes(ifc, ifcopenshell)
    if isinstance(ifc, str):
        try: return ifcopenshell.open(ifc)
        except: return None
    read = getattr(ifc, "read", None)
    if callable(read):
        return _open_model_from_bytes(read(), ifcopenshell)
    return None


# =============================
# Standalone run
# =============================
if __name__ == "__main__":
    run_wc_size_check()