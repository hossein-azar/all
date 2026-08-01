# classroom_size_checker.py
# Usage in your main app:
#   from classroom_size_checker import run_classroom_size_check
#   run_classroom_size_check(ifc=your_ifc_bytes_or_path_or_ifcopenshell_file)
#
# Or standalone:
#   streamlit run classroom_size_checker.py

import re
import math
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
# Config (internal defaults; no sidebar controls)
# -----------------------------
DEFAULT_MAX_L = 8.0
DEFAULT_MAX_W = 7.0
DEFAULT_MIN_H = 3.0


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
def run_classroom_size_check(ifc=None, max_l: float = DEFAULT_MAX_L, max_w: float = DEFAULT_MAX_W, min_h: float = DEFAULT_MIN_H):
    """Render the Classroom Size Check UI with 3D visualization support."""
    try:
        import ifcopenshell
    except ImportError:
        st.error("⚠️ **Dependency Error:** Please install requirements to use this tab: `pip install ifcopenshell`")
        return

    st.caption("code: 4-1-1-3, 4-1-1-9")
    st.header("🏫 Classroom Size Check")

    # -----------------------------
    # Sidebar: file upload
    # -----------------------------
    model = None
    if ifc is None:
        with st.sidebar:
            st.header("📁 Upload IFC")
            up_ifc = st.file_uploader("Upload .ifc", type=["ifc"], key="classroom_ifc_upload")
        if not up_ifc:
            st.info("⬆️ Upload an IFC file to continue.")
            return
        model = _open_model_from_bytes(up_ifc.read(), ifcopenshell)
    else:
        model = _open_model_generic(ifc, ifcopenshell)

    if model is None:
        st.error("Could not open IFC model.")
        return

    # Collect classrooms
    spaces = [
        s for s in model.by_type("IfcSpace")
        if getattr(s, "LongName", None) and _ci(s.LongName) == "classroom"
    ]

    if not spaces:
        st.warning("❌ No rooms found with LongName equal to 'classroom'.")
        return

    # Configure geometry engine for visualization settings
    geom_settings = ifcopenshell.geom.settings()
    geom_settings.set(geom_settings.USE_WORLD_COORDS, True)

    ok_lines, bad_lines = [], []
    ok_nums, bad_nums = [], []
    rows = []
    visualization_records = []

    for sp in spaces:
        rnum = _get_room_number(model, sp)
        pts = _extract_profile_xy_points_from_space(sp)
        
        L = W = None
        if pts:
            L, W = _bounds_LW(pts)

        H = _get_height(model, sp)

        # Evaluate layout orientations
        plan_ok = False
        caseA = caseB = False
        if L is not None and W is not None:
            caseA = (L <= max_l and W <= max_w)
            caseB = (L <= max_w and W <= max_l)
            plan_ok = (caseA or caseB)

        height_ok = (H is not None and H >= min_h)
        status_ok = bool(plan_ok and height_ok)

        if status_ok:
            if caseA and not caseB:
                orientation = "(x=L, y=W)"
            elif caseB and not caseA:
                orientation = "(x=W, y=L)"
            else:
                orientation = "(Both orientations possible)"

            line = f"Room {rnum} → L/W: {L} × {W} , H: {H} {orientation}"
            ok_lines.append(line)
            ok_nums.append(str(rnum))
            status = "OK"
            detail = orientation
        else:
            parts = []
            if not plan_ok:
                parts.append(f"Too big, L/W ({L or '-'} , {W or '-'}) / MAX ({max_l:.2f}, {max_w:.2f})")
            if not height_ok:
                parts.append(f"H={H or '-'} / (should be ≥ {min_h:.2f})")
            bad_lines.append(f"Room {rnum} → " + " | ".join(parts) if parts else f"Room {rnum} → Missing data")
            bad_nums.append(str(rnum))
            status = "NOT OK"
            detail = " | ".join(parts) if parts else "Missing data"

        rows.append({
            "Room Number": rnum,
            "Room LongName": getattr(sp, "LongName", None),
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
                "longname": getattr(sp, "LongName", None) or "Classroom",
                "status_ok": status_ok,
                "footprint": footprint,
                "zmin": zmin,
                "zmax": zmax,
                "verts": verts,
                "faces": faces,
                "label": f"Room {rnum} ({L or '-'}x{W or '-'}x{H or '-'})"
            })

    # Report overview
    st.caption("Code: 5-1-1-5-2")
    st.success(f"✅ {len(spaces)} classrooms evaluated.")

    report = []
    if ok_lines:
        report.append(f"### ✅ {len(ok_lines)} Classrooms are OK in size ({', '.join(ok_nums)})")
        report.extend(ok_lines)
    else:
        report.append("NO rooms are OK")

    report.append("")
    if bad_lines:
        report.append(f"### ❌ {len(bad_lines)} Classrooms are NOT OK in size ({', '.join(bad_nums)})")
        report.extend(bad_lines)
    else:
        report.append("All rooms are OK")

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

    # -----------------------------
    # Table + CSV Data Extraction
    # -----------------------------
    df = pd.DataFrame(rows)
    st.subheader("Results Table")
    st.dataframe(df, use_container_width=True)

    csv = df.to_csv(index=False).encode("utf-8")
    st.download_button("💾 Download CSV", csv, "classroom_size_check.csv", "text/csv")


# =============================
# Helpers (internal)
# =============================
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
                        if xdim and ydim:
                            return _rectangle_points2d(xdim, ydim)
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

def _bounds_LW(points):
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    L = round(max(xs) - min(xs), 3)
    W = round(max(ys) - min(ys), 3)
    return L, W

def _get_height(model, space):
    rep = getattr(space, "Representation", None)
    if rep:
        for r in (rep.Representations or []):
            for item in (r.Items or []):
                try:
                    if item.is_a("IfcExtrudedAreaSolid"):
                        depth = getattr(item, "Depth", None)
                        if depth and float(depth) > 0:
                            return round(float(depth), 3)
                except Exception:
                    continue
        for r in (rep.Representations or []):
            rid = _ci(getattr(r, "RepresentationIdentifier", "") or "")
            if rid == "bounding box":
                items = r.Items or []
                if items:
                    zdim = getattr(items[0], "ZDim", None)
                    if zdim and float(zdim) > 0:
                        return round(float(zdim), 3)

    try:
        for rel in model.by_type("IfcRelDefinesByProperties"):
            if not getattr(rel, "RelatedObjects", None) or space not in rel.RelatedObjects:
                continue
            eq = getattr(rel, "RelatingPropertyDefinition", None)
            if not eq or not eq.is_a("IfcElementQuantity"):
                continue
            for q in eq.Quantities or []:
                if q.is_a("IfcQuantityLength"):
                    qn = _ci(getattr(q, "Name", "") or "")
                    if qn in ("height", "grossheight", "netheight"):
                        v = getattr(q, "LengthValue", None)
                        if v is not None and float(v) > 0:
                            return round(float(v), 3)
    except Exception:
        pass
    return None

def _value_to_str(v):
    try:
        return str(getattr(v, "wrappedValue", v))
    except Exception:
        return str(v)

def _get_room_number(model, space):
    candidates = []
    if getattr(space, "Name", None):
        candidates.append(_value_to_str(space.Name))
    if getattr(space, "LongName", None):
        candidates.append(_value_to_str(space.LongName))
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
                            candidates.append(_value_to_str(val))
    except Exception:
        pass
    for text in candidates:
        if text is None: continue
        m = re.search(r"\d+(\.\d+)?", str(text))
        if m: return m.group(0)
    return "-"

def _open_model_from_bytes(b: bytes, ifcopenshell):
    with tempfile.NamedTemporaryFile(delete=False, suffix=".ifc") as tmp:
        tmp.write(b)
        tmp.flush()
        return ifcopenshell.open(tmp.name)

def _open_model_generic(ifc, ifcopenshell):
    try:
        from ifcopenshell.file import file as _IfcFileClass
    except Exception:
        _IfcFileClass = None

    if _IfcFileClass and isinstance(ifc, _IfcFileClass):
        return ifc
    if isinstance(ifc, (bytes, bytearray)):
        return _open_model_from_bytes(ifc, ifcopenshell)
    if isinstance(ifc, str):
        try:
            return ifcopenshell.open(ifc)
        except Exception:
            return None
    read = getattr(ifc, "read", None)
    if callable(read):
        try:
            return _open_model_from_bytes(read(), ifcopenshell)
        except Exception:
            return None
    return None


if __name__ == "__main__":
    run_classroom_size_check()