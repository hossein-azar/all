# wc_number_checker.py — 🚻 WC Number Check (auto, exact-match)
# Usage inside your main Streamlit app (as a tab):
#
#   from wc_number_checker import render_wc_number_check
#   with tabs[3]:
#       render_wc_number_check(ifc)

import os
import re
import json
from typing import Optional, List, Dict
import pandas as pd
import numpy as np
import streamlit as st
import plotly.graph_objects as go

try:
    import ifcopenshell
    import ifcopenshell.geom
except ImportError:
    ifcopenshell = None

# ----------- Matching / config -----------
DEFAULT_CONFIG = {
    "matching": {
        "mode": "exact",             # <— force EXACT matching
        "case_sensitive": False,     # <— case-insensitive ("WC" == "wc")
        "ignore_numeric_names": True,
        "ignore_patterns": ["^tmp", "^test"],
    }
}

STANDARD_CLASSROOM_NAME = "classroom"
STANDARD_WC_NAME = "wc"

def load_config(path: str = "config.school.json"):
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))  # deep copy
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if "matching" in data and isinstance(data["matching"], dict):
                cfg["matching"].update(data["matching"])
        except Exception:
            pass

    # Enforce EXACT + case-insensitive regardless of external file
    cfg["matching"]["mode"] = "exact"
    cfg["matching"]["case_sensitive"] = False
    return cfg

# ----------- Helpers -----------
def get_space_name(space) -> str:
    nm = getattr(space, "Name", None) or ""
    ln = getattr(space, "LongName", None) or ""
    return (ln or nm).strip()

def collect_unique_room_names(ifc, cfg) -> List[str]:
    spaces = ifc.by_type("IfcSpace") if ifc else []
    names = []

    ignore_re = None
    pats = cfg["matching"].get("ignore_patterns") or []
    if pats:
        try:
            ignore_re = re.compile("|".join(pats), flags=re.IGNORECASE)
        except Exception:
            ignore_re = None

    for sp in spaces:
        nm = get_space_name(sp)
        if not nm:
            continue
        if cfg["matching"].get("ignore_numeric_names") and nm.isdigit():
            continue
        if ignore_re and ignore_re.match(nm):
            continue
        names.append(nm)

    seen = set()
    out = []
    cs = cfg["matching"].get("case_sensitive", False)
    for n in names:
        key = n if cs else n.lower()
        if key not in seen:
            seen.add(key)
            out.append(n)
    out.sort(key=lambda s: s.lower())
    return out

def count_rooms_by_label(ifc, label: str, cfg) -> int:
    if not label:
        return 0
    spaces = ifc.by_type("IfcSpace") if ifc else []
    cs = cfg["matching"].get("case_sensitive", False)

    label_cmp = label if cs else label.lower()
    count = 0
    for sp in spaces:
        nm = get_space_name(sp)
        if not nm:
            continue
        cmp_nm = nm if cs else nm.lower()
        if cmp_nm == label_cmp:   # <-- EXACT only
            count += 1
    return count

def get_space_geometry(space, settings):
    """Safely extracts 3D vertices, faces, and center coordinates for room visualization."""
    try:
        if space.Representation:
            shape = ifcopenshell.geom.create_shape(settings, space)
            verts = np.array(shape.geometry.verts).reshape(-1, 3)
            faces = np.array(shape.geometry.faces).reshape(-1, 3)
            edges = shape.geometry.edges if hasattr(shape.geometry, "edges") else []
            if len(verts) > 0:
                center = verts.mean(axis=0)
                z_max = verts[:, 2].max()
                return {"verts": verts, "faces": faces, "edges": edges, "center": center, "z_max": z_max}
    except Exception:
        pass
    return None

# ----------- Public UI entry -----------
def render_wc_number_check(ifc: Optional[object]):
    """
    Render the WC number check in a tab. Uses fixed labels:
    - Classroom: "classroom"
    - WC: "wc"
    Performs EXACT (case-insensitive) match and shows 3D Visualization + CSV export.
    """
    st.caption("Code: 2-1-4")
    st.title("🚻 WC Number Check")

    if ifc is None:
        st.info("Upload an IFC file in the main app to continue.")
        st.stop()

    if ifcopenshell is None:
        st.error("Python package 'ifcopenshell' is not installed.\nInstall via: pip install ifcopenshell")
        st.stop()

    # Config (enforces exact + case-insensitive)
    cfg = load_config()

    # Informational only
    unique_names = collect_unique_room_names(ifc, cfg)

    # Fixed labels, EXACT matching
    class_label = STANDARD_CLASSROOM_NAME
    wc_label = STANDARD_WC_NAME
    cls_n = count_rooms_by_label(ifc, class_label, cfg)
    wc_n = count_rooms_by_label(ifc, wc_label, cfg)

    # Counter row under title (3 metrics)
    cols = st.columns(3)
    cols[0].metric("Classrooms", cls_n)
    cols[1].metric("WCs", wc_n)
    cols[2].metric("Required", cls_n)

    # Result message (auto, no button)
    if wc_n >= cls_n:
        st.success(f"✅ Number of WCs is good.\nWCs: {wc_n}  |  Classrooms: {cls_n}")
        status = "OK"
        deficit = 0
    else:
        deficit = cls_n - wc_n
        st.warning(f"⚠️ Not enough WCs.\nWCs: {wc_n}  |  Classrooms: {cls_n}\nNeeds {deficit} more.")
        status = "NOT_OK"

    # === 3D Model Multi-Dimensional Visualization ===
    st.divider()
    st.subheader("📦 3D Model Spatial Verification")

    geom_settings = ifcopenshell.geom.settings()
    geom_settings.set(geom_settings.USE_WORLD_COORDS, True)
    
    fig3d = go.Figure()
    tracked_3d_legends = set()

    all_spaces = ifc.by_type("IfcSpace")
    cs = cfg["matching"].get("case_sensitive", False)
    
    for space in all_spaces:
        geom_data = get_space_geometry(space, geom_settings)
        if not geom_data:
            continue
            
        nm = get_space_name(space)
        cmp_nm = nm if cs else nm.lower()
        
        is_classroom = (cmp_nm == (class_label if cs else class_label.lower()))
        is_wc = (cmp_nm == (wc_label if cs else wc_label.lower()))
        
        # Explicit exclusion rule: Do not render classrooms in the 3D viewer frame
        if is_classroom:
            continue
            
        if is_wc:
            color = "#a3e4d7"  # Distinct pale teal/green for WCs
            opacity = 0.65
            legend_group_name = "🚻 Target WCs"
        else:
            color = "#E5E7E9"  # Pale gray for background context layout
            opacity = 0.12
            legend_group_name = "Other Background Spaces"

        show_in_legend = False
        if legend_group_name not in tracked_3d_legends:
            show_in_legend = True
            tracked_3d_legends.add(legend_group_name)

        # Append 3D volume trace
        fig3d.add_trace(go.Mesh3d(
            x=geom_data["verts"][:, 0], y=geom_data["verts"][:, 1], z=geom_data["verts"][:, 2],
            i=geom_data["faces"][:, 0], j=geom_data["faces"][:, 1], k=geom_data["faces"][:, 2],
            color=color, opacity=opacity, 
            name=legend_group_name,
            legendgroup=legend_group_name,
            showlegend=show_in_legend
        ))

        # Render dark spatial border outlines around target WCs
        if is_wc and len(geom_data["edges"]) > 0:
            v = geom_data["verts"]
            edges = geom_data["edges"]
            for i in range(0, len(edges), 2):
                p1 = v[edges[i]]
                p2 = v[edges[i+1]]
                fig3d.add_trace(go.Scatter3d(
                    x=[p1[0], p2[0]], y=[p1[1], p2[1]], z=[p1[2], p2[2]],
                    mode='lines', line=dict(color='#1a252f', width=1.5),
                    legendgroup=legend_group_name, showlegend=False
                ))

        # Floating architectural labels replicating original classroom checker numbering logic
        if is_wc:
            # Replicates the classroom checker extraction parameters
            raw_name_prop = (getattr(space, "Name", None) or "").strip()
            
            # If the raw Name attribute contains an actual number or digit string, show it
            if raw_name_prop.isdigit():
                room_number = raw_name_prop
            else:
                # Fallback matching format used by structural properties
                found_nums = re.findall(r'\d+', raw_name_prop)
                room_number = found_nums[0] if found_nums else str(space.id())
                
            fig3d.add_trace(go.Scatter3d(
                x=[geom_data["center"][0]], 
                y=[geom_data["center"][1]], 
                z=[geom_data["z_max"] + 0.15],
                text=[f"WC #{room_number}"], mode="text",
                textfont=dict(size=10, color="black"),
                legendgroup=legend_group_name,
                showlegend=False
            ))

    fig3d.update_layout(
        scene=dict(aspectmode='data', dragmode='orbit'), 
        height=600, 
        margin=dict(l=0, r=0, b=0, t=0),
        legend=dict(yanchor="top", y=0.99, xanchor="left", x=0.01)
    )
    st.plotly_chart(fig3d, use_container_width=True)

    # Results summary table
    st.markdown("### Results summary")
    summary_df = pd.DataFrame(
        [
            {"Type": "Classroom", "Label": class_label, "Count": cls_n},
            {"Type": "WC",        "Label": wc_label,    "Count": wc_n},
        ]
    )
    st.dataframe(summary_df, use_container_width=True, hide_index=True)

    # CSV export (single file with both summary & headline)
    st.markdown("---")
    export_df = pd.DataFrame([{
        "class_label": class_label,
        "class_count": cls_n,
        "wc_label": wc_label,
        "wc_count": wc_n,
        "status": status,
        "deficit": deficit,
    }])

    csv_bytes = export_df.to_csv(index=False).encode("utf-8")
    st.download_button(
        "⬇️ Download CSV",
        data=csv_bytes,
        file_name="wc_number_check.csv",
        mime="text/csv",
    )