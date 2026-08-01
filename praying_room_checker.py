# praying_room_checker.py — 🕌 Praying Room Area Check (auto mode)
import re
from typing import Optional, Dict
from io import StringIO
import pandas as pd
import streamlit as st
import numpy as np
import plotly.graph_objects as go

# ---------- Constants (UI + rules) ----------
SCHOOL_TYPES = [
    ("ebtedaei dore 1", 0.5),
    ("ebtedaei dore 2", 0.667),
    ("motevassete", 0.667),
    ("mixed dore 1 &2", 0.5),
]
SECOND_COEFS = [0.8, 0.8, 0.9, 0.9]

# Standardized names (fixed)
STANDARD_ROOM_NAME = "praying room"
STANDARD_STUDENT_CHAIR_NAME = "student chair"

# ---------- Optional deps ----------
try:
    import ifcopenshell
    import ifcopenshell.geom as ifcgeom
except Exception:
    ifcopenshell = None
    ifcgeom = None

try:
    from shapely.geometry import Polygon
    from shapely.ops import unary_union
    _HAS_SHAPELY = True
except Exception:
    _HAS_SHAPELY = False

# ---------- Text helpers ----------
NUM_TOKEN_RE = re.compile(r"(?:^|\s)(?:#?\d+)(?=\s|$)")

def strip_numeric_tokens(s: str) -> str:
    s = " ".join(s.strip().split())
    s = NUM_TOKEN_RE.sub(" ", s)
    return " ".join(s.split())

def canonicalize(label: str) -> str:
    if not label:
        return ""
    s = strip_numeric_tokens(" ".join(label.strip().split()))
    return s.lower()

# ---------- IFC unit helpers ----------
def _si_prefix_scale(prefix):
    m = {
        "EXA":1e18,"PETA":1e15,"TERA":1e12,"GIGA":1e9,"MEGA":1e6,"KILO":1e3,
        "HECTO":1e2,"DECA":1e1,"DECI":1e-1,"CENTI":1e-2,"MILLI":1e-3,
        "MICRO":1e-6,"NANO":1e-9,"PICO":1e-12,"FEMTO":1e-15,"ATTO":1e-18
    }
    return m.get((prefix or "").upper(), 1.0)

def get_length_scale_m(ifc):
    try:
        proj = ifc.by_type("IfcProject")[0]
        ua = getattr(proj, "UnitsInContext", None)
        if not ua:
            return 1.0
        for u in ua.Units or []:
            if u.is_a("IfcSIUnit") and getattr(u, "UnitType", None) == "LENGTHUNIT":
                name = getattr(u, "Name", "METRE")
                prefix = getattr(u, "Prefix", None)
                if name == "METRE":
                    return _si_prefix_scale(prefix) if prefix else 1.0
            if u.is_a("IfcConversionBasedUnit") and getattr(u, "UnitType", None) == "LENGTHUNIT":
                mu = u.ConversionFactor
                val = float(getattr(mu, "ValueComponent", 1.0))
                unit = mu.UnitComponent
                if unit.is_a("IfcSIUnit") and getattr(unit, "Name", None) == "METRE":
                    return val
    except Exception:
        pass
    return 1.0

def get_area_scale_m2(ifc):
    s = get_length_scale_m(ifc)
    return s * s

# ---------- IFC label helpers ----------
def best_furnishing_label(elem, ifc):
    for attr in ("Name", "ObjectType"):
        v = getattr(elem, attr, None)
        if isinstance(v, str) and v.strip():
            return v.strip()
    try:
        for inv in ifc.get_inverse(elem):
            if inv.is_a("IfcRelDefinesByType"):
                t = inv.RelatingType
                if t:
                    for attr in ("Name", "ElementType", "Tag"):
                        tv = getattr(t, attr, None)
                        if isinstance(tv, str) and tv.strip():
                            return tv.strip()
    except Exception:
        pass
    tag = getattr(elem, "Tag", None)
    if isinstance(tag, str) and tag.strip():
        return tag.strip()
    return ""

def collect_furniture_instance_labels(ifc):
    labels = []
    for e in ifc.by_type("IfcFurnishingElement"):
        lab = best_furnishing_label(e, ifc)
        if lab:
            labels.append(lab)
    try:
        for e in ifc.by_type("IfcFurniture"):
            lab = best_furnishing_label(e, ifc)
            if lab:
                labels.append(lab)
    except RuntimeError:
        pass
    return labels

def best_room_label(space) -> str:
    for attr in ("LongName", "Name"):
        v = getattr(space, attr, None)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""

# ---------- Mesh extraction helper for 3D Viewer ----------
def get_element_geometry(element, settings):
    """Extracts vertices, faces, and bounding box data for processing."""
    try:
        if element.Representation:
            shape = ifcgeom.create_shape(settings, element)
            verts = np.array(shape.geometry.verts).reshape(-1, 3)
            faces = np.array(shape.geometry.faces).reshape(-1, 3)
            
            xmin, ymin, zmin = np.min(verts, axis=0)
            xmax, ymax, zmax = np.max(verts, axis=0)
            x_center = (xmin + xmax) / 2
            y_center = (ymin + ymax) / 2
            z_mean = verts[:, 2].mean()
            
            return {
                "verts": verts, "faces": faces, "shape": shape,
                "center": (x_center, y_center, z_mean)
            }
    except:
        pass
    return None

# ---------- Mesh → XY area ----------
def area_from_shape_mesh(shape) -> float:
    if not _HAS_SHAPELY or shape is None:
        return 0.0
    try:
        geom = shape.geometry
        verts = geom.verts
        faces = geom.faces
    except Exception:
        return 0.0

    coords3d = [(verts[i], verts[i+1], verts[i+2]) for i in range(0, len(verts), 3)]
    if not coords3d:
        return 0.0

    triangles = []
    for i in range(0, len(faces), 3):
        try:
            a = coords3d[faces[i]]; b = coords3d[faces[i+1]]; c = coords3d[faces[i+2]]
        except IndexError:
            continue
        tri2d = [(a[0], a[1]), (b[0], b[1]), (c[0], c[1])]
        try:
            poly = Polygon(tri2d)
            if poly.is_valid and not poly.is_empty and poly.area > 0:
                triangles.append(poly)
        except Exception:
            pass

    if not triangles:
        return 0.0

    try:
        merged = unary_union(triangles)
        return float(merged.area) if merged and not merged.is_empty else 0.0
    except Exception:
        pts_2d = []
        for poly in triangles:
            pts_2d.extend(list(poly.exterior.coords))
        try:
            return Polygon(pts_2d).convex_hull.area
        except Exception:
            return 0.0

def rooms_area_by_name_geom(ifc, target_name: str) -> float:
    if ifcgeom is None or not _HAS_SHAPELY:
        return 0.0

    target_can = canonicalize(target_name)
    settings = ifcgeom.settings()
    settings.set(settings.USE_WORLD_COORDS, True)
    area_scale = get_area_scale_m2(ifc)

    total = 0.0
    for sp in ifc.by_type("IfcSpace"):
        n = best_room_label(sp)
        if not n or canonicalize(n) != target_can:
            continue
        try:
            shape = ifcgeom.create_shape(settings, sp)
        except Exception:
            continue

        a_model_units2 = area_from_shape_mesh(shape)
        if a_model_units2 > 0:
            total += a_model_units2 * area_scale

    return total

# ---------- Internal counts built from IFC ----------
def build_furniture_type_map(ifc) -> Dict[str, Dict[str, int]]:
    labels = collect_furniture_instance_labels(ifc)
    type_map: Dict[str, Dict[str, int]] = {}
    for lbl in labels:
        key = canonicalize(lbl)
        if not key:
            continue
        disp = strip_numeric_tokens(lbl)
        if key not in type_map:
            type_map[key] = {"display": disp, "count": 0}
        type_map[key]["count"] += 1
    return type_map

# ---------- Public UI entry ----------
def render_praying_room_area_check(ifc: Optional[object]):
    try:
        st.set_page_config(layout="centered", initial_sidebar_state="expanded")
    except Exception:
        pass

    st.caption("Code: 2-1-3-2")
    st.title("🕌 Praying Room Area Check")

    if ifc is None:
        st.info("Upload an IFC file to continue.")
        st.stop()

    # Build furniture map and auto-count students by standardized chair label
    furn_type_map = build_furniture_type_map(ifc)
    auto_student_count = sum(
        v["count"]
        for v in furn_type_map.values()
        if STANDARD_STUDENT_CHAIR_NAME.lower() in v["display"].lower()
    )

    st.subheader("Students number")
    
    # Toggle for manual modification override
    manual_mode = st.toggle("Enter student count manually", value=False, key="pr_manual_toggle")
    
    if manual_mode:
        student_count = st.number_input("Specify Total Students", min_value=0, value=max(0, auto_student_count), step=1)
    else:
        student_count = auto_student_count
        st.success(f"Automatically detected students: **{student_count}**")

    # School type select → auto recompute everything
    st.subheader("Select School type")
    school_labels = [lbl for (lbl, _c) in SCHOOL_TYPES]
    sel_idx = st.selectbox(
        "School type",
        list(range(len(school_labels))),
        format_func=lambda i: school_labels[i],
        index=0,
    )

    # Auto compute requirement and availability
    first_coef = SCHOOL_TYPES[sel_idx][1]
    second_coef = SECOND_COEFS[sel_idx]
    area_required = student_count * first_coef * second_coef
    area_available = rooms_area_by_name_geom(ifc, STANDARD_ROOM_NAME) or 0.0
    shortage = max(0.0, area_required - area_available)
    status = "OK" if area_available >= area_required else "NOT_OK"

    # Show result immediately
    if status == "OK":
        st.success(
            f"✅ Enough.\n\n**Available:** {area_available:.2f} m²\n\n**Needed:** {area_required:.2f} m²"
        )
    else:
        st.error(
            f"❌ Not enough.\n\n**Available:** {area_available:.2f} m²\n\n"
            f"**Needed:** {area_required:.2f} m²\n\n**Shortage:** {shortage:.2f} m²"
        )

    # 3D Visualization Section
    st.divider()
    st.subheader("📦 Spatial Verification Map (3D)")
    
    if ifcgeom is not None:
        with st.spinner("Generating spatial mapping visualization..."):
            settings = ifcgeom.settings()
            settings.set(settings.USE_WORLD_COORDS, True)
            fig3d = go.Figure()
            
            target_can = canonicalize(STANDARD_ROOM_NAME)
            all_spaces = ifc.by_type("IfcSpace")
            
            tracked_legends = set()
            
            for space in all_spaces:
                geom = get_element_geometry(space, settings)
                if not geom:
                    continue
                
                n = best_room_label(space)
                is_praying_room = n and canonicalize(n) == target_can
                
                v = geom["verts"]
                f = geom["faces"]
                
                # Highlight logic (Pale Green vs Light Muted Gray)
                color = "#2ecc71" if is_praying_room else "#E5E7E9"
                opacity = 0.85 if is_praying_room else 0.15
                
                legend_group = "Praying Room" if is_praying_room else "Other Spaces"
                show_legend = legend_group not in tracked_legends
                if show_legend:
                    tracked_legends.add(legend_group)
                    
                fig3d.add_trace(go.Mesh3d(
                    x=v[:, 0], y=v[:, 1], z=v[:, 2],
                    i=f[:, 0], j=f[:, 1], k=f[:, 2],
                    color=color, opacity=opacity,
                    name=legend_group,
                    legendgroup=legend_group,
                    showlegend=show_legend
                ))
                
                # Annotation flags on top of target components
                if is_praying_room:
                    lbl_text = f"🕌 {n.upper()} (#{space.Name or 'N/A'})"
                    fig3d.add_trace(go.Scatter3d(
                        x=[geom["center"][0]],
                        y=[geom["center"][1]],
                        z=[np.max(v[:, 2]) + 0.1],
                        text=[lbl_text], mode="text",
                        textfont=dict(size=10, color="black"),
                        showlegend=False
                    ))
            
            fig3d.update_layout(
                scene=dict(aspectmode='data', dragmode='orbit'),
                height=500,
                margin=dict(l=0, r=0, b=0, t=0),
                legend=dict(yanchor="top", y=0.99, xanchor="left", x=0.01)
            )
            with st.container(border=True):
                st.plotly_chart(fig3d, use_container_width=True)
    else:
        st.caption("3D geometry rendering engine currently unavailable.")

    # Export result
    st.markdown("---")
    result_row = {
        "room_name": STANDARD_ROOM_NAME,
        "school_type": school_labels[sel_idx],
        "students": student_count,
        "area_required_m2": round(area_required, 2),
        "area_available_m2": round(area_available, 2),
        "shortage_m2": round(shortage, 2),
        "status": status,
    }
    df_res = pd.DataFrame([result_row])
    csv_buf = StringIO()
    df_res.to_csv(csv_buf, index=False)
    st.download_button(
        label="⬇️ Download CSV",
        data=csv_buf.getvalue(),
        file_name="praying_room_check_result.csv",
        mime="text/csv",
    )