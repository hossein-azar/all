# staff_wc_checker.py — 🚻 Staff WC Number Check (auto, exact-match)
# Usage inside ch2.py:
#   from staff_wc_checker import render_staff_wc_check
#   ...
#   with tabs[4]:
#       render_staff_wc_check(ifc)

import json
import os
import re
import math
from typing import Optional, List, Dict
import pandas as pd
import numpy as np
import streamlit as st
import plotly.graph_objects as go

try:
    import ifcopenshell
    import ifcopenshell.geom
except Exception:
    ifcopenshell = None

# ----------- Fixed labels (EXACT match, case-insensitive) -----------
STANDARD_CLASSROOM_NAME = "classroom"
STANDARD_STAFF_WC_NAME = "staff wc"

DEFAULT_CONFIG = {
    "matching": {
        "mode": "exact",          # enforced to exact
        "case_sensitive": False,  # enforced to False
        "ignore_numeric_names": True,
        "ignore_patterns": ["^tmp", "^test"],
    }
}

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
        if cmp_nm == label_cmp:
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

# ----------- Public UI Entry -----------
def render_staff_wc_check(ifc: Optional[object]):
    """
    Render the Staff WC check tab. Uses fixed labels:
    - Classroom: "classroom"
    - Staff WC: "staff wc"
    Required ratio: 1 Staff WC per 6 Classrooms (rounded up).
    """
    st.caption("Code: 2-1-4")
    st.title("🚻 Staff WC Number Check")

    if ifc is None:
        st.info("Upload an IFC file in the main app to continue.")
        st.stop()

    if ifcopenshell is None:
        st.error("Python package 'ifcopenshell' is not installed.\nInstall via: pip install ifcopenshell")
        st.stop()

    cfg = load_config()

    unique_names = collect_unique_room_names(ifc, cfg)

    class_label = STANDARD_CLASSROOM_NAME
    staffwc_label = STANDARD_STAFF_WC_NAME

    cls_n = count_rooms_by_label(ifc, class_label, cfg)
    staffwc_n = count_rooms_by_label(ifc, staffwc_label, cfg)

    # Calculate rule metrics (1 per 6, minimum 1 if classrooms exist)
    if cls_n > 0:
        required = math.ceil(cls_n / 6.0)
    else:
        required = 0

    cols = st.columns(3)
    cols[0].metric("Classrooms", cls_n)
    cols[1].metric("Staff WCs", staffwc_n)
    cols[2].metric("Required (1 per 6)", required)

    if staffwc_n >= required:
        st.success(
            f"✅ Staff WC count is sufficient.\n\n"
            f"**Classrooms:** {cls_n}  |  **Required:** {required}  |  **Provided:** {staffwc_n}"
        )
        status = "OK"
        deficit = 0
    else:
        deficit = max(0, required - staffwc_n)
        st.warning(
            f"⚠️ Not enough Staff WCs.\n\n"
            f"**Classrooms:** {cls_n}  |  **Required:** {required}  |  **Provided:** {staffwc_n}\n\n"
            f"**Needs {deficit} more.**"
        )
        status = "NOT_OK"

    # === 3D Model Spatial Verification Panel ===
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
        is_staff_wc = (cmp_nm == (staffwc_label if cs else staffwc_label.lower()))
        
        # Omit classrooms from display viewport
        if is_classroom:
            continue
            
        if is_staff_wc:
            color = "#a3e4d7"  # Distinct pale teal/green color
            opacity = 0.65
            legend_group_name = "🚻 Target Staff WCs"
        else:
            color = "#E5E7E9"  # Pale gray for contextual background spaces
            opacity = 0.12
            legend_group_name = "Other Background Spaces"

        show_in_legend = False
        if legend_group_name not in tracked_3d_legends:
            show_in_legend = True
            tracked_3d_legends.add(legend_group_name)

        # Append 3D mesh volume
        fig3d.add_trace(go.Mesh3d(
            x=geom_data["verts"][:, 0], y=geom_data["verts"][:, 1], z=geom_data["verts"][:, 2],
            i=geom_data["faces"][:, 0], j=geom_data["faces"][:, 1], k=geom_data["faces"][:, 2],
            color=color, opacity=opacity, 
            name=legend_group_name,
            legendgroup=legend_group_name,
            showlegend=show_in_legend
        ))

        # Render explicit dark border outlines around selected spaces
        if is_staff_wc and len(geom_data["edges"]) > 0:
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
        if is_staff_wc:
            raw_name_prop = (getattr(space, "Name", None) or "").strip()
            
            if raw_name_prop.isdigit():
                room_number = raw_name_prop
            else:
                found_nums = re.findall(r'\d+', raw_name_prop)
                room_number = found_nums[0] if found_nums else str(space.id())
                
            fig3d.add_trace(go.Scatter3d(
                x=[geom_data["center"][0]], 
                y=[geom_data["center"][1]], 
                z=[geom_data["z_max"] + 0.15],
                text=[f"Staff WC #{room_number}"], mode="text",
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

    # Summary table
    st.markdown("### Results summary")
    summary_df = pd.DataFrame(
        [
            {"Type": "Classroom", "Label": class_label,  "Count": cls_n},
            {"Type": "Staff WC",  "Label": staffwc_label, "Count": staffwc_n},
        ]
    )
    st.dataframe(summary_df, use_container_width=True, hide_index=True)

    # CSV export
    st.markdown("---")
    export_df = pd.DataFrame([{
        "class_label": class_label,
        "class_count": cls_n,
        "staff_wc_label": staffwc_label,
        "staff_wc_count": staffwc_n,
        "required_staff_wc": required,
        "status": status,
        "deficit": deficit,
    }])

    csv_bytes = export_df.to_csv(index=False).encode("utf-8")
    st.download_button(
        "⬇️ Download CSV",
        data=csv_bytes,
        file_name="staff_wc_number_check.csv",
        mime="text/csv",
    )