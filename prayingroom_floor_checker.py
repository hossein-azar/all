# praying_room_check.py
# Use in your app:
#   from praying_room_check import run_praying_room_check
#   run_praying_room_check(ifc)

import tempfile
import streamlit as st
import numpy as np
import plotly.graph_objects as go

try:
    import ifcopenshell  # type: ignore
    import ifcopenshell.geom
except Exception:
    st.error("⚠️ Please install ifcopenshell (pip install ifcopenshell)")
    st.stop()


def _open_ifc_from_any(obj):
    """Open IFC safely from various input types."""
    if obj is None:
        return None
    # already-opened IFC
    if hasattr(obj, "by_type") and hasattr(obj, "schema"):
        return obj

    # UploadedFile or bytes
    data = None
    if hasattr(obj, "getvalue"):
        try:
            data = obj.getvalue()
        except Exception:
            pass
    if data is None and hasattr(obj, "read"):
        try:
            data = obj.read()
        except Exception:
            pass
    if isinstance(obj, (bytes, bytearray)):
        data = bytes(obj)

    if data:
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".ifc") as tmp:
                tmp.write(data)
                tmp.flush()
                return ifcopenshell.open(tmp.name)
        except Exception:
            return None

    # Path-like
    try:
        return ifcopenshell.open(str(obj))
    except Exception:
        return None


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


def run_praying_room_check(ifc=None):
    """Checks if 'praying room' is in the selected first floor with 3D verification visualization."""
    st.caption("code: 4-1-2-3")
    st.header("📍 Praying Room — Floor Level Check & 3D Verification")

    # Load IFC
    if ifc is None:
        ifc = _open_ifc_from_any(st.session_state.get("GLOBAL_ifc"))
    else:
        ifc = _open_ifc_from_any(ifc)

    if ifc is None:
        st.warning("❌ Please upload an IFC globally (key='GLOBAL_ifc') or pass it directly.")
        return

    # ----------------- helpers -----------------
    def storey_name(s):
        return (getattr(s, "Name", None) or getattr(s, "LongName", None) or "(unnamed)").strip()

    def get_space_storey(space):
        for rel in ifc.get_inverse(space):
            if rel.is_a("IfcRelContainedInSpatialStructure"):
                rs = getattr(rel, "RelatingStructure", None)
                if rs and rs.is_a("IfcBuildingStorey"):
                    return rs
            if rel.is_a("IfcRelAggregates"):
                ro = getattr(rel, "RelatingObject", None)
                if ro and ro.is_a("IfcBuildingStorey"):
                    return ro
        return None

    def get_storeys_with_elevation():
        res = []
        for s in ifc.by_type("IfcBuildingStorey"):
            elev = getattr(s, "Elevation", None)
            if elev is None:
                continue
            try:
                res.append((s, float(elev)))
            except Exception:
                pass
        return res

    def find_praying_room():
        target = "praying room"
        for sp in ifc.by_type("IfcSpace"):
            for c in (getattr(sp, "LongName", None), getattr(sp, "Name", None), getattr(sp, "ObjectType", None)):
                if isinstance(c, str) and c.strip().lower() == target:
                    return sp
        return None

    # ----------------- logic -----------------
    storeys = get_storeys_with_elevation()
    if not storeys:
        st.error("No IfcBuildingStorey elements found with Elevation.")
        return

    storeys_sorted = sorted(storeys, key=lambda t: t[1])
    option_labels = [f"{storey_name(s)} — {e:.2f} m" for (s, e) in storeys_sorted]

    # ¯ True placeholder behavior
    select_placeholder = st.empty()
    chosen_label = select_placeholder.selectbox(
        "select first floor:",
        options=["please select first floor..."] + option_labels,
        index=0,  # start with placeholder visible
    )

    # If still placeholder -> stop
    if chosen_label == "please select first floor...":
        st.info("❌ Please select the first floor from the box above.")
        return

    # Resolve selection
    chosen_idx = option_labels.index(chosen_label)
    first_floor, first_elev = storeys_sorted[chosen_idx]
    first_floor_name = storey_name(first_floor)

    # Find praying room
    praying_space = find_praying_room()
    if not praying_space:
        st.warning("No room named exactly 'praying room' (case-insensitive) found.")
        st.info(f"Selected first floor: **{first_floor_name}** ({first_elev:.2f} m)")
        return

    praying_storey = get_space_storey(praying_space)
    is_correct_floor = False
    
    if praying_storey and praying_storey.GlobalId == first_floor.GlobalId:
        st.success(f"✅ Praying room is in the first floor (**{first_floor_name}**) and is OK.")
        is_correct_floor = True
    elif praying_storey:
        st.error(
            f"❌ Praying room is not in the first floor — it is in **{storey_name(praying_storey)}**.\n"
            f"❌ Recommended to be in: **{first_floor_name}**"
        )
    else:
        st.warning(
            f"❌ Praying room found, but its storey could not be determined.\n"
            f"Selected first floor: **{first_floor_name}** ({first_elev:.2f} m)"
        )

    # =================================================
    # 3D GEOMETRY VISUALIZATION
    # =================================================
    st.divider()
    st.subheader("📦 3D Model Spatial Verification")

    geom_settings = ifcopenshell.geom.settings()
    geom_settings.set(geom_settings.USE_WORLD_COORDS, True)
    
    fig3d = go.Figure()
    tracked_3d_legends = set()
    all_spaces = ifc.by_type("IfcSpace")

    for sp in all_spaces:
        # Determine if this space belongs to the user-selected reference floor
        current_space_storey = get_space_storey(sp)
        is_on_target_floor = (current_space_storey and current_space_storey.GlobalId == first_floor.GlobalId)
        
        # Check if this space is the actual praying room
        is_praying_room = (sp.GlobalId == praying_space.GlobalId)

        # Isolate views: render praying room anywhere, but background rooms ONLY for the selected target floor
        if not is_praying_room and not is_on_target_floor:
            continue

        geom_data = get_space_geometry(sp, geom_settings)
        if not geom_data:
            continue
            
        if is_praying_room:
            if is_correct_floor:
                color = "#2ecc71"  # Vibrant Green
                legend_group_name = "✅ Compliant Praying Room"
            else:
                color = "#e74c3c"  # Vibrant Red
                legend_group_name = "❌ Wrong Floor Praying Room"
            opacity = 0.85
        else:
            # Context background elements on the target first floor layout level
            color = "#abebc6"  
            opacity = 0.20
            legend_group_name = f"✅ Target Floor Layout ({first_floor_name})"

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

        # Add profile wireframe outlines to the target unit
        if is_praying_room and len(geom_data["edges"]) > 0:
            v = geom_data["verts"]
            edges = geom_data["edges"]
            for i in range(0, len(edges), 2):
                p1 = v[edges[i]]
                p2 = v[edges[i+1]]
                fig3d.add_trace(go.Scatter3d(
                    x=[p1[0], p2[0]], y=[p1[1], p2[1]], z=[p1[2], p2[2]],
                    mode='lines', line=dict(color='#1a252f', width=2.0),
                    legendgroup=legend_group_name, showlegend=False
                ))

        # Render explicit descriptive label on top of the praying room
        if is_praying_room:
            detected_storey_name = storey_name(praying_storey) if praying_storey else "Unknown"
            fig3d.add_trace(go.Scatter3d(
                x=[geom_data["center"][0]], 
                y=[geom_data["center"][1]], 
                z=[geom_data["z_max"] + 0.20],
                text=[f"PRAYING ROOM ({detected_storey_name})"], mode="text",
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

    # Render multi-column view block 
    view_col, info_col = st.columns([3, 1])
    with view_col:
        st.plotly_chart(fig3d, use_container_width=True)
    with info_col:
        st.markdown("#### 🏢 Level Reference Data")
        if is_correct_floor:
            st.success(f"ℹ️ **Note:** The praying room is correctly placed on `{first_floor_name}`.")
        else:
            actual_floor = storey_name(praying_storey) if praying_storey else "Unknown"
            st.error(f"⚠️ **Note:** Currently on floor `{actual_floor}`. It should be moved down to `{first_floor_name}` floor.")


# Standalone run (optional)
if __name__ == "__main__":
    st.set_page_config(page_title="Praying Room Check", layout="centered")
    st.sidebar.file_uploader("Upload IFC (.ifc)", type=["ifc"], key="GLOBAL_ifc")
    run_praying_room_check()