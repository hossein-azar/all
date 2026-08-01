# disabled_wc_checker.py
# Usage:
#   from disabled_wc_checker import render_disabled_wc_check
#   with tabs[5]:
#       render_disabled_wc_check(ifc)

import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import re
from typing import Optional

# ------------------- IMPORT IFC ------------------
try:
    import ifcopenshell
    import ifcopenshell.geom
    import ifcopenshell.util.element
except Exception:
    ifcopenshell = None


# ================= RULES =================
STANDARD_DISABLED_WC_NAME = "wc for disabled"
MIN_SIDE_SHORT = 1.5   # meters
MIN_SIDE_LONG = 1.7    # meters


# ================= HELPERS =================

def get_space_name(space) -> str:
    """Return LongName first, then Name."""
    return (space.LongName or space.Name or "").strip()


def get_level_name(space) -> str:
    """
    Determine the level of an IfcSpace.
    """
    # 1) Try container
    try:
        container = ifcopenshell.util.element.get_container(space)
        if container and container.is_a("IfcBuildingStorey"):
            return container.Name
    except:
        pass

    # 2) Revit fallback
    try:
        if space.Decomposes:
            for rel in space.Decomposes:
                if rel.is_a("IfcRelAggregates") and rel.RelatingObject.is_a("IfcBuildingStorey"):
                    return rel.RelatingObject.Name
    except:
        pass

    # 3) Check attributes
    for attr_name in ["Level", "Reference", "LayerName"]:
        val = getattr(space, attr_name, None)
        if val and hasattr(val, "Name"):
            return val.Name
        if isinstance(val, str) and val.strip():
            return val.strip()

    return "Unknown Level"


def get_bbox_dimensions(space) -> tuple:
    """
    Returns (dx, dy) based on the local or global bounding box if available,
    or falls back to an approximated bounding box from geometry.
    """
    ifcopenshell_geom = globals().get("ifcopenshell") or ifcopenshell
    if not ifcopenshell_geom or not ifcopenshell_geom.geom:
        return None, None

    try:
        settings = ifcopenshell_geom.geom.settings()
        settings.set(settings.USE_WORLD_COORDS, True)
        shape = ifcopenshell_geom.geom.create_shape(settings, space)
        verts = np.array(shape.geometry.verts).reshape(-1, 3)
        if len(verts) == 0:
            return None, None
        
        xmin, ymin = verts[:, 0].min(), verts[:, 1].min()
        xmax, ymax = verts[:, 0].max(), verts[:, 1].max()
        dx = round(abs(xmax - xmin), 2)
        dy = round(abs(ymax - ymin), 2)
        return dx, dy
    except:
        return None, None


def check_wc_size(dx, dy) -> bool:
    if dx is None or dy is None:
        return False
    s = min(dx, dy)
    l = max(dx, dy)
    return (s >= MIN_SIDE_SHORT) and (l >= MIN_SIDE_LONG)


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


# ================= MAIN RENDER FUNCTION =================

def render_disabled_wc_check(ifc: Optional[object]):
    st.caption("Code: 2-1-4-disabled")
    st.title("♿ Disabled WC Compliance Check")

    if ifc is None:
        st.info("Upload an IFC file in the main app to continue.")
        return  # Changed from st.stop() to allow remaining tabs to function safely

    if ifcopenshell is None:
        st.error("Python package 'ifcopenshell' is not installed. Install via: pip install ifcopenshell")
        return

    # Find spaces matching "wc for disabled"
    all_spaces = ifc.by_type("IfcSpace")
    wc_spaces = []
    
    for sp in all_spaces:
        name_lower = (sp.Name or "").lower()
        longname_lower = (sp.LongName or "").lower()
        if (STANDARD_DISABLED_WC_NAME in name_lower) or (STANDARD_DISABLED_WC_NAME in longname_lower):
            wc_spaces.append(sp)

    if not wc_spaces:
        st.warning(f"No spaces found matching standard label: '{STANDARD_DISABLED_WC_NAME}'")
        return

    st.write(f"Found **{len(wc_spaces)}** Disabled WC space(s). Checking conditions...")

    # Establish all unique storey levels to fill the selection dropdown
    all_storeys = ifc.by_type("IfcBuildingStorey")
    sorted_storeys = []
    for s in all_storeys:
        elev = getattr(s, "Elevation", 0.0)
        sorted_storeys.append((s.Name, elev))
    sorted_storeys.sort(key=lambda x: x[1])
    
    # Prepend a clean unselected placeholder string
    placeholder = "Select first floor..."
    storey_options = [placeholder] + [s[0] for s in sorted_storeys] if sorted_storeys else [placeholder, "Level 1"]

    # =================================================
    # 🎛️ USER CONTROL PANEL (MANUAL CHOSEN FIRST FLOOR)
    # =================================================
    st.markdown("---")
    selected_first_floor = st.selectbox(
        "🔍 Select Target First Floor Level", 
        options=storey_options, 
        index=0,
        help="Select which level represents the correct target first floor layout level for building verification."
    )

    if selected_first_floor == placeholder:
        st.info("💡 Please choose a target level from the select box dropdown to execute compliance matrix tracking.")
        return  # Changed from st.stop() to prevent breaking structural tab execution flows

    # =================================================
    # 1️⃣ LOCATION LEVEL CHECK 
    # =================================================
    st.subheader("1️⃣ Location Floor Level Check")

    level_rows = []
    has_level_error = False

    for sp in wc_spaces:
        lvl = get_level_name(sp)
        is_ok = (lvl.lower() == selected_first_floor.lower())
        
        if not is_ok:
            has_level_error = True

        level_rows.append({
            "Room Name": get_space_name(sp),
            "Detected Level": lvl,
            "Target First Floor": selected_first_floor,
            "Level OK": is_ok
        })

    df_lvl = pd.DataFrame(level_rows)
    st.dataframe(df_lvl, use_container_width=True, hide_index=True)

    if not has_level_error:
        st.success(f"✅ All Disabled WC are located on the correct floor level ({selected_first_floor}).")
    else:
        st.error(f"❌ Location Alert: Disabled WC should be located on the selected target floor level ({selected_first_floor}).")

    # =================================================
    # 2️⃣ DIMENSION CHECK (ROTATION-SAFE)
    # =================================================
    st.subheader("2️⃣ Dimension Compliance Check (≥ 1.5 × 1.7 m)")

    dim_rows = []
    dim_ok_count = 0
    space_compliance_status = {}

    for sp in wc_spaces:
        dx, dy = get_bbox_dimensions(sp)
        size_ok = check_wc_size(dx, dy)
        space_compliance_status[sp.id()] = size_ok

        if size_ok:
            dim_ok_count += 1

        dim_rows.append({
            "Room Name": get_space_name(sp),
            " X (m)": dx,
            " Y (m)": dy,
            "Short Side ≥ 1.5": dx is not None and dy is not None and min(dx, dy) >= 1.5,
            "Long Side ≥ 1.7": dx is not None and dy is not None and max(dx, dy) >= 1.7,
            "Size OK": size_ok
        })

    df_dim = pd.DataFrame(dim_rows)
    st.dataframe(df_dim, use_container_width=True, hide_index=True)

    if dim_ok_count > 0:
        st.success(f"✅ {dim_ok_count} Disabled WC space(s) meet minimum size constraints.")
    else:
        st.error("❌ No Disabled WC meets the required size standard dimensions.")

    # =================================================
    # 3️⃣ 3D GEOMETRY VISUALIZATION WITH SELECTED LEVEL HIGHLIGHT
    # =================================================
    st.divider()
    st.subheader("📦 3D Model Spatial Verification")

    geom_settings = ifcopenshell.geom.settings()
    geom_settings.set(geom_settings.USE_WORLD_COORDS, True)
    
    fig3d = go.Figure()
    tracked_3d_legends = set()

    for sp in all_spaces:
        lvl_name = get_level_name(sp)
        name_lower = (sp.Name or "").lower()
        longname_lower = (sp.LongName or "").lower()
        is_target_disabled_wc = (STANDARD_DISABLED_WC_NAME in name_lower) or (STANDARD_DISABLED_WC_NAME in longname_lower)
        
        # Isolate views: render targets anywhere, but render regular background rooms ONLY for the selected first floor
        if not is_target_disabled_wc and (lvl_name.lower() != selected_first_floor.lower()):
            continue

        geom_data = get_space_geometry(sp, geom_settings)
        if not geom_data:
            continue
            
        if is_target_disabled_wc:
            is_valid_size = space_compliance_status.get(sp.id(), False)
            if is_valid_size:
                color = "#2ecc71"  # Vibrant Green
                legend_group_name = "♿ Compliant Disabled WC"
            else:
                color = "#e74c3c"  # Vibrant Red
                legend_group_name = "⚠️ Non-Compliant Disabled WC"
            opacity = 0.80
        else:
            # Highlight only the user-selected proper target first floor level as clean green
            color = "#abebc6"  
            opacity = 0.22
            legend_group_name = f"🟢 Proper Target Floor ({selected_first_floor})"

        show_in_legend = False
        if legend_group_name not in tracked_3d_legends:
            show_in_legend = True
            tracked_3d_legends.add(legend_group_name)

        # Draw structural volume mesh
        fig3d.add_trace(go.Mesh3d(
            x=geom_data["verts"][:, 0], y=geom_data["verts"][:, 1], z=geom_data["verts"][:, 2],
            i=geom_data["faces"][:, 0], j=geom_data["faces"][:, 1], k=geom_data["faces"][:, 2],
            color=color, opacity=opacity, 
            name=legend_group_name,
            legendgroup=legend_group_name,
            showlegend=show_in_legend
        ))

        # Add sharp dark border profiles to target units
        if is_target_disabled_wc and len(geom_data["edges"]) > 0:
            v = geom_data["verts"]
            edges = geom_data["edges"]
            for i in range(0, len(edges), 2):
                p1 = v[edges[i]]
                p2 = v[edges[i+1]]
                fig3d.add_trace(go.Scatter3d(
                    x=[p1[0], p2[0]], y=[p1[1], p2[1]], z=[p1[2], p2[2]],
                    mode='lines', line=dict(color='#1a252f', width=1.8),
                    legendgroup=legend_group_name, showlegend=False
                ))

        # Render numbered labels text
        if is_target_disabled_wc:
            raw_name_prop = (getattr(sp, "Name", None) or "").strip()
            if raw_name_prop.isdigit():
                room_number = raw_name_prop
            else:
                found_nums = re.findall(r'\d+', raw_name_prop)
                room_number = found_nums[0] if found_nums else str(sp.id())

            fig3d.add_trace(go.Scatter3d(
                x=[geom_data["center"][0]], 
                y=[geom_data["center"][1]], 
                z=[geom_data["z_max"] + 0.15],
                text=[f"DISABLED WC #{room_number} ({lvl_name})"], mode="text",
                textfont=dict(size=10, color="black", weight="bold"),
                legendgroup=legend_group_name,
                showlegend=False
            ))

    fig3d.update_layout(
        scene=dict(aspectmode='data', dragmode='orbit'), 
        height=600, 
        margin=dict(l=0, r=0, b=0, t=0),
        legend=dict(yanchor="top", y=0.99, xanchor="left", x=0.01)
    )

    # Render side-by-side columns: 3D view on Left, Floor name details panel on Right
    view_col, info_col = st.columns([3, 1])
    with view_col:
        st.plotly_chart(fig3d, use_container_width=True)
    with info_col:
        st.markdown("#### 🏢 Level Reference Data")
        
        # Combined into 1 clear, unambiguous note
        if not has_level_error:
            st.success(f"ℹ️ **Note:** It is on the right floor (`{selected_first_floor}`).")
        else:
            st.warning(f"⚠️ **Note:** It should be in `{selected_first_floor}` floor.")

    # =================================================
    # 4️⃣ EXPORT SUMMARY
    # =================================================
    st.markdown("---")
    export_rows = []
    for sp in wc_spaces:
        dx, dy = get_bbox_dimensions(sp)
        lvl = get_level_name(sp)
        export_rows.append({
            "Room_Name": get_space_name(sp),
            "Level": lvl,
            "Target_First_Floor_Limit": selected_first_floor,
            "Level_Valid": (lvl.lower() == selected_first_floor.lower()),
            "Width_X_m": dx,
            "Length_Y_m": dy,
            "Min_Required_Short_Side_m": MIN_SIDE_SHORT,
            "Min_Required_Long_Side_m": MIN_SIDE_LONG,
            "Dimension_Valid": check_wc_size(dx, dy)
        })
        
    if export_rows:
        df_export = pd.DataFrame(export_rows)
        csv_bytes = df_export.to_csv(index=False).encode("utf-8")
        st.download_button(
            "⬇️ Download CSV Report",
            data=csv_bytes,
            file_name="disabled_wc_compliance_report.csv",
            mime="text/csv",
        )