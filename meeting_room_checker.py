# meeting_room_checker.py — 🪑 Meeting Room Seats Check (auto)
# Usage inside your main app (as a tab):
#   from meeting_room_checker import render_meeting_room_seats_check
#   with tabs[?]:
#       render_meeting_room_seats_check(ifc)

import re
from io import StringIO
from typing import Optional, Dict
import pandas as pd
import streamlit as st
import numpy as np
import plotly.graph_objects as go

# ---------- Fixed standard names ----------
STANDARD_ROOM_NAME = "meeting room"
STANDARD_CHAIR_NAME = "meeting room chair"

# ---------- Optional deps ----------
try:
    import ifcopenshell
    import ifcopenshell.geom as ifcgeom
except Exception:
    ifcopenshell = None
    ifcgeom = None

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

def build_furniture_type_map(ifc) -> Dict[str, Dict[str, int]]:
    """
    Returns: { canonical_label: { 'display': original_stripped, 'count': N } }
    """
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

# ---------- UI entry ----------
def render_meeting_room_seats_check(ifc: Optional[object]):
    st.caption("Code: 2-1-3-3")
    st.title("🪑 Meeting Room Seats Check")

    if ifc is None:
        st.info("Upload an IFC file in the main app to continue.")
        st.stop()
    if ifcopenshell is None:
        st.error("Python package 'ifcopenshell' is not installed. Install via: pip install ifcopenshell")
        st.stop()

    # Cache furniture map
    if "mr_furn_type_map" not in st.session_state:
        st.session_state.mr_furn_type_map = build_furniture_type_map(ifc)

    furn_map = st.session_state.mr_furn_type_map

    # === Auto-count seats for the standardized chair name (contains match on display text) ===
    chair_q = STANDARD_CHAIR_NAME
    seats_total = sum(
        v["count"]
        for v in furn_map.values()
        if chair_q.lower() in v["display"].lower()
    )

    # === School type selection (kept) — check runs automatically on change ===
    school_types = [
        "ebtedaei tak dore",
        "ebtedaei mix",
        "motevasete tak dore",
        "motevasete mix",
        "ebtedaei and motevasete",
    ]
    required_min = {
        "ebtedaei tak dore": 72,
        "ebtedaei mix": 144,
        "motevasete tak dore": 108,
        "motevasete mix": 180,
        "ebtedaei and motevasete": 216,
    }
    sel_school = st.selectbox("School type", school_types, index=0)

    # === Auto “check” (no button) ===
    req = int(required_min[sel_school])
    # room label is fixed to standard name (no user pick)
    room_label = STANDARD_ROOM_NAME

    # Headline status
    cols = st.columns(3)
    cols[0].metric("Seats found", seats_total)
    cols[1].metric("Minimum required", req)
    cols[2].metric("Shortfall", max(0, req - seats_total))

    if seats_total == 0:
        st.error(f"No chairs found for '{STANDARD_CHAIR_NAME}'.")
        status = "NOT_OK"
        shortfall = req
    elif seats_total >= req:
        st.success(
            f"✅ Standard seats met for **{room_label}**.\n\n"
            f"Seats found: {seats_total}\n\nMinimum required: {req}"
        )
        status = "OK"
        shortfall = 0
    else:
        shortfall = req - seats_total
        st.error(
            f"❌ Meeting room doesn't have enough seats for **{room_label}**.\n\n"
            f"Seats found: {seats_total}\n\nMinimum required: {req}\n\n"
            f"Short by: {shortfall}"
        )
        status = "NOT_OK"

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
                is_meeting_room = n and canonicalize(n) == target_can
                
                v = geom["verts"]
                f = geom["faces"]
                
                # Highlight logic (Pale Green vs Light Muted Gray)
                color = "#2ecc71" if is_meeting_room else "#E5E7E9"
                opacity = 0.85 if is_meeting_room else 0.15
                
                legend_group = "Meeting Room" if is_meeting_room else "Other Spaces"
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
                if is_meeting_room:
                    lbl_text = f"🪑 {n.upper()} (#{space.Name or 'N/A'})"
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
                st.plotly_chart(fig3d, use_container_width=True, key="meeting_room_3d_spatial_chart")
    else:
        st.caption("3D geometry rendering engine currently unavailable.")

    # === Summary table ===
    st.markdown("### Results summary")
    df = pd.DataFrame([{
        "room_name": room_label,
        "chair_name": STANDARD_CHAIR_NAME,
        "school_type": sel_school,
        "seats_found": int(seats_total),
        "min_required": int(req),
        "shortfall": int(shortfall),
        "status": status,
    }])
    st.dataframe(df, use_container_width=True, hide_index=True)

    # === CSV export ===
    st.markdown("---")
    csv_buf = StringIO()
    df.to_csv(csv_buf, index=False)
    st.download_button(
        "⬇️ Download CSV",
        data=csv_buf.getvalue(),
        file_name="meeting_room_seats_check.csv",
        mime="text/csv",
    )