import streamlit as st
import ifcopenshell
import ifcopenshell.geom
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import tempfile
import os

# =====================================================
# GEOMETRY EXTRACTION HELPER
# =====================================================
def get_element_mesh(element, settings):
    """Safely extracts vertices, faces, and calculates the center bounding box point."""
    try:
        shape = ifcopenshell.geom.create_shape(settings, element)
        verts = np.array(shape.geometry.verts).reshape(-1, 3)
        faces = np.array(shape.geometry.faces).reshape(-1, 3)
        center = np.mean(verts, axis=0)
        bbox = (np.min(verts[:, 0]), np.max(verts[:, 0]),
                np.min(verts[:, 1]), np.max(verts[:, 1]),
                np.min(verts[:, 2]), np.max(verts[:, 2]))
        return verts, faces, center, bbox
    except:
        return None, None, None, None

def get_elements_in_storey(model, storey, type_name):
    """Returns elements of a specific type contained within or aggregated by a storey."""
    elements = model.by_type(type_name)
    storey_elements = []
    for el in elements:
        is_contained = any(rel.RelatingStructure == storey for rel in getattr(el, "ContainedInStructure", []))
        if not is_contained:
            for rel in getattr(el, "Decomposes", []):
                if rel.is_a("IfcRelAggregates") and rel.RelatingObject == storey:
                    is_contained = True
        if is_contained:
            storey_elements.append(el)
    return storey_elements

# =====================================================
# HUMAN-LIKE NATURAL PATH SMOOTHER
# =====================================================
def smooth_path_coordinates(x, y, z, subdivisions=12):
    """
    Smooths out sharp 90-degree corner turns into a natural human-like curve
    using basic linear subdivision/interpolation (Chaikin's algorithm approach).
    """
    if len(x) < 3:
        return x, y, z
        
    sx, sy, sz = [], [], []
    for i in range(len(x) - 1):
        x_vals = np.linspace(x[i], x[i+1], subdivisions)
        y_vals = np.linspace(y[i], y[i+1], subdivisions)
        z_vals = np.linspace(z[i], z[i+1], subdivisions)
        
        sx.extend(x_vals[:-1])
        sy.extend(y_vals[:-1])
        sz.extend(z_vals[:-1])
        
    sx.append(x[-1])
    sy.append(y[-1])
    sz.append(z[-1])
    
    window = 5
    smoothed_x = np.convolve(sx, np.ones(window)/window, mode='same')
    smoothed_y = np.convolve(sy, np.ones(window)/window, mode='same')
    
    smoothed_x[0], smoothed_x[-1] = x[0], x[-1]
    smoothed_y[0], smoothed_y[-1] = y[0], y[-1]
    
    return list(smoothed_x), list(smoothed_y), sz

def generate_hallway_mesh_path(start_pt, end_pt, hall_bbox, step=1.2):
    """
    Generates a natural-looking human path by creating an inner mesh grid 
    constrained inside the hallway's bounding box boundaries.
    """
    if not hall_bbox or all(v == 0 for v in hall_bbox):
        return [start_pt[0], end_pt[0]], [start_pt[1], end_pt[1]], [start_pt[2], end_pt[2]]
        
    h_xmin, h_xmax, h_ymin, h_ymax, h_zmin, h_zmax = hall_bbox
    
    xs = np.arange(h_xmin + 0.3, h_xmax - 0.3, step)
    ys = np.arange(h_ymin + 0.3, h_ymax - 0.3, step)
    
    if len(xs) < 2 or len(ys) < 2:
        return [start_pt[0], end_pt[0]], [start_pt[1], end_pt[1]], [start_pt[2], end_pt[2]]
    
    path_x = [start_pt[0]]
    path_y = [start_pt[1]]
    path_z = [start_pt[2]]
    
    closest_x_idx = np.abs(xs - start_pt[0]).argmin()
    closest_y_idx = np.abs(ys - start_pt[1]).argmin()
    
    target_x_idx = np.abs(xs - end_pt[0]).argmin()
    target_y_idx = np.abs(ys - end_pt[1]).argmin()
    
    path_x.append(xs[closest_x_idx])
    path_y.append(ys[closest_y_idx])
    path_z.append(start_pt[2])
    
    path_x.append(xs[target_x_idx])
    path_y.append(ys[closest_y_idx])
    path_z.append(start_pt[2])
    
    path_x.append(xs[target_x_idx])
    path_y.append(ys[target_y_idx])
    path_z.append(end_pt[2])
    
    path_x.append(end_pt[0])
    path_y.append(end_pt[1])
    path_z.append(end_pt[2])
    
    return path_x, path_y, path_z

# =====================================================
# STREAMLIT INTERFACE COMPONENT
# =====================================================
def render_navigation_with_routing_tab(uploaded_ifc):
    if uploaded_ifc is None:
        st.info("Please upload an IFC architectural model to generate routing paths.")
        return

    temp_path = None
    try:
        if hasattr(uploaded_ifc, "getbuffer"):
            with tempfile.NamedTemporaryFile(delete=False, suffix=".ifc") as tmp:
                tmp.write(uploaded_ifc.getbuffer())
                temp_path = tmp.name
            model = ifcopenshell.open(temp_path)
        elif hasattr(uploaded_ifc, "getvalue"):
            ifc_bytes = uploaded_ifc.getvalue().decode("utf-8")
            model = ifcopenshell.file.from_string(ifc_bytes)
        else:
            model = uploaded_ifc

        settings = ifcopenshell.geom.settings()
        if hasattr(settings, "USE_WORLD_COORDS"):
            settings.set(settings.USE_WORLD_COORDS, True)
        elif hasattr(settings, "USE_WORLD_COORDINATES"):
            settings.set(settings.USE_WORLD_COORDINATES, True)

        storeys = model.by_type("IfcBuildingStorey")
        storey_map = {s.Name: s for s in storeys}
        
        room_database = {}
        all_spaces = model.by_type("IfcSpace")

        for space in all_spaces:
            r_type = space.LongName or "Room"
            r_num = space.Name or "N/A"
            
            parent_storey = "Unknown"
            for rel in getattr(space, "Decomposes", []):
                if rel.is_a("IfcRelAggregates") and rel.RelatingObject.is_a("IfcBuildingStorey"):
                    parent_storey = rel.RelatingObject.Name
            if parent_storey == "Unknown":
                for rel in model.by_type("IfcRelContainedInSpatialStructure"):
                    if space in rel.RelatedElements:
                        parent_storey = rel.RelatingStructure.Name

            if r_type not in room_database:
                room_database[r_type] = []
            
            room_database[r_type].append({
                "number": r_num,
                "element": space,
                "storey": parent_storey
            })

        st.subheader("🚶‍♂️ Human-Scale Path Configuration")
        
        room_types = sorted(list(room_database.keys()))
        default_start_idx = room_types.index("yard") if "yard" in room_types else 0
        
        # --- SELECT STARTING POINT ---
        st.write("**From (Starting Point):**")
        col_s1, col_s2 = st.columns(2)
        with col_s1:
            start_room_type = st.selectbox("Select Start Room Type", options=room_types, index=default_start_idx, key="nav_start_type")
        
        available_start_rooms = room_database.get(start_room_type, [])
        start_room_numbers = sorted(list(set([r["number"] for r in available_start_rooms])))
        
        with col_s2:
            selected_start_room_number = st.selectbox(f"Select specific starting {start_room_type} number:", options=start_room_numbers, key="nav_start_num")
            
        start_space_data = next((r for r in available_start_rooms if r["number"] == selected_start_room_number), None)
        
        # --- SELECT TARGET POINT ---
        st.write("**To (Destination Point):**")
        col_t1, col_t2 = st.columns(2)
        with col_t1:
            target_room_type = st.selectbox("Select Target Room Type", options=room_types, key="nav_target_type")
        
        available_target_rooms = room_database.get(target_room_type, [])
        target_room_numbers = sorted(list(set([r["number"] for r in available_target_rooms])))

        with col_t2:
            selected_target_room_number = st.selectbox(f"Select specific target {target_room_type} number:", options=target_room_numbers, key="nav_target_num")

        target_space_data = next((r for r in available_target_rooms if r["number"] == selected_target_room_number), None)
        
        if not start_space_data or not target_space_data:
            st.error("Selected location details could not be parsed.")
            return

        start_floor_name = start_space_data["storey"]
        dest_floor_name = target_space_data["storey"]
        
        levels_to_render = set([start_floor_name, dest_floor_name])
        target_storey_elements = [storey_map[name] for name in levels_to_render if name in storey_map]

        fig = go.Figure()
        
        start_room_center = None
        start_hall_center = None
        start_hall_name = "N/A"
        start_hall_bbox = None
        
        dest_room_center = None
        dest_hall_center = None
        dest_hall_name = "N/A"
        dest_hall_bbox = None

        elevators_by_floor = {name: [] for name in levels_to_render}
        elevator_names_by_floor = {name: [] for name in levels_to_render}
        stairs_by_floor = {name: [] for name in levels_to_render}
        stair_names_by_floor = {name: [] for name in levels_to_render}

        for storey in target_storey_elements:
            fl_name = storey.Name
            
            # --- RENDER DOORS IN BROWN ---
            storey_doors = get_elements_in_storey(model, storey, "IfcDoor")
            for door in storey_doors:
                d_verts, d_faces, _, _ = get_element_mesh(door, settings)
                if d_verts is not None:
                    fig.add_trace(go.Mesh3d(
                        x=d_verts[:, 0], y=d_verts[:, 1], z=d_verts[:, 2],
                        i=d_faces[:, 0], j=d_faces[:, 1], k=d_faces[:, 2],
                        color="#8B4513", opacity=0.8, name=f"Door: {door.Name or 'N/A'}",
                        showlegend=False
                    ))
            
            storey_spaces = get_elements_in_storey(model, storey, "IfcSpace")
            floor_elevators = [s for s in storey_spaces if "elevator" in (s.LongName or s.Name or "").lower()]
            floor_standard_spaces = [s for s in storey_spaces if "elevator" not in (s.LongName or s.Name or "").lower()]

            for elev in floor_elevators:
                verts, faces, center, _ = get_element_mesh(elev, settings)
                if verts is not None:
                    elev_label = f"Elevator {elev.Name or 'N/A'}"
                    elevators_by_floor[fl_name].append(center)
                    elevator_names_by_floor[fl_name].append(elev_label)
                    fig.add_trace(go.Mesh3d(
                        x=verts[:, 0], y=verts[:, 1], z=verts[:, 2],
                        i=faces[:, 0], j=faces[:, 1], k=faces[:, 2],
                        color="#A3E4D7", opacity=0.3, name=f"{elev_label} ({fl_name})"
                    ))

            for space in floor_standard_spaces:
                r_type = space.LongName or "Room"
                r_num = space.Name or "N/A"
                full_label = f"{r_type} ({r_num})"
                
                is_hall = "hall" in full_label.lower() or "corridor" in full_label.lower() or "circulation" in full_label.lower()
                is_stair = "stair" in full_label.lower()
                
                is_start_room = (space.GlobalId == start_space_data["element"].GlobalId)
                is_destination_room = (space.GlobalId == target_space_data["element"].GlobalId)

                verts, faces, center, bbox = get_element_mesh(space, settings)
                if verts is None:
                    continue

                if fl_name == start_floor_name and is_hall and start_hall_center is None:
                    start_hall_center = center
                    start_hall_bbox = bbox
                    start_hall_name = r_num
                
                if fl_name == dest_floor_name and is_hall and dest_hall_center is None:
                    dest_hall_center = center
                    dest_hall_bbox = bbox
                    dest_hall_name = r_num

                if is_stair:
                    stairs_by_floor[fl_name].append(center)
                    stair_names_by_floor[fl_name].append(full_label)

                # Made room colors noticeably pale
                if is_start_room:
                    color = "#FADBD8" # Pale red
                    opacity = 0.35
                    start_room_center = center
                elif is_destination_room:
                    color = "#AED6F1" # Pale blue
                    opacity = 0.35
                    dest_room_center = center
                elif is_hall:
                    color = "#EAEDED" # Pale gray hallway
                    opacity = 0.25
                elif is_stair:
                    color = "#FCF3CF" # Pale yellow stairs
                    opacity = 0.25
                else:
                    color = "#F8F9F9" # Extremely pale background elements
                    opacity = 0.08

                fig.add_trace(go.Mesh3d(
                    x=verts[:, 0], y=verts[:, 1], z=verts[:, 2],
                    i=faces[:, 0], j=faces[:, 1], k=faces[:, 2],
                    color=color, opacity=opacity, name=full_label
                ))
                
                if is_start_room or is_destination_room or is_hall or is_stair:
                    fig.add_trace(go.Scatter3d(
                        x=[center[0]], y=[center[1]], z=[np.max(verts[:, 2]) + 0.05],
                        text=[full_label], mode="text",
                        textfont=dict(size=9, color="#7F8C8D"), showlegend=False
                    ))

        # =====================================================
        # ENHANCED HALLWAY CORRIDOR ROUTING LOGIC
        # =====================================================
        summary_rows = []
        
        if start_room_center is not None and dest_room_center is not None:
            # Anchor start/end indicators explicitly
            fig.add_trace(go.Scatter3d(
                x=[start_room_center[0]], y=[start_room_center[1]], z=[start_room_center[2] + 0.5],
                mode="markers+text",
                marker=dict(size=8, color="#C0392B", symbol="diamond"),
                text=[f"📍 Start: {start_room_type}"],
                textposition="top center",
                name="Start Node"
            ))

            # CASE 1: Single Floor Navigation (Guarantees path moves through Hallway Center)
            if start_floor_name == dest_floor_name:
                x_total, y_total, z_total = [], [], []
                
                # Path Segment 1: Start Room -> Hallway Center
                if start_hall_center is not None:
                    x1, y1, z1 = generate_hallway_mesh_path(start_room_center, start_hall_center, start_hall_bbox)
                    # Path Segment 2: Hallway Center -> Target Destination Room
                    x2, y2, z2 = generate_hallway_mesh_path(start_hall_center, dest_room_center, start_hall_bbox)
                    x_total.extend(x1 + x2)
                    y_total.extend(y1 + y2)
                    z_total.extend(z1 + z2)
                else:
                    x_total, y_total, z_total = generate_hallway_mesh_path(start_room_center, dest_room_center, None)
                
                sx, sy, sz = smooth_path_coordinates(x_total, y_total, z_total)
                sz_offset = [z + 0.35 for z in sz] # Raised slightly to easily pass through door geometry thresholds
                
                fig.add_trace(go.Scatter3d(
                    x=sx, y=sy, z=sz_offset, 
                    mode="lines+markers", line=dict(color="#27AE60", width=6),
                    marker=dict(size=3, color="#1E8449"), name="Complete Route Path"
                ))
                
                summary_rows.append({
                    "Route Type": "Direct Floor Route",
                    "From Location": f"{start_room_type} {selected_start_room_number} ({start_floor_name})",
                    "Transit Mechanism": f"Room Door ➔ Hallway Corridor ({start_hall_name}) ➔ Destination Door",
                    "To Location": f"{target_room_type} {selected_target_room_number} ({dest_floor_name})"
                })
            
            # CASE 2: Multi-Floor Navigation via Verticals (Elevator/Stairs)
            else:
                has_elev_link = len(elevators_by_floor.get(start_floor_name, [])) > 0 and len(elevators_by_floor.get(dest_floor_name, [])) > 0
                has_stair_link = len(stairs_by_floor.get(start_floor_name, [])) > 0 and len(stairs_by_floor.get(dest_floor_name, [])) > 0

                if has_elev_link:
                    start_elev_idx = np.argmin([np.linalg.norm(np.array(c) - (np.array(start_hall_center) if start_hall_center is not None else np.array(start_room_center))) for c in elevators_by_floor[start_floor_name]])
                    start_elev = elevators_by_floor[start_floor_name][start_elev_idx]
                    dest_elev = elevators_by_floor[dest_floor_name][start_elev_idx] if start_elev_idx < len(elevators_by_floor[dest_floor_name]) else elevators_by_floor[dest_floor_name][0]
                    elev_name = elevator_names_by_floor[start_floor_name][start_elev_idx]

                    elev_x, elev_y, elev_z = [], [], []
                    
                    # 1. Start room out to current floor hallway center
                    if start_hall_center is not None:
                        x1, y1, z1 = generate_hallway_mesh_path(start_room_center, start_hall_center, start_hall_bbox)
                        x2, y2, z2 = generate_hallway_mesh_path(start_hall_center, start_elev, start_hall_bbox)
                        elev_x.extend(x1 + x2); elev_y.extend(y1 + y2); elev_z.extend(z1 + z2)
                    else:
                        x1, y1, z1 = generate_hallway_mesh_path(start_room_center, start_elev, None)
                        elev_x.extend(x1); elev_y.extend(y1); elev_z.extend(z1)

                    # 2. Vertical Elevator Shaft Connection
                    elev_x.append(dest_elev[0]); elev_y.append(dest_elev[1]); elev_z.append(dest_elev[2])

                    # 3. Destination floor elevator through hallway center to final destination room
                    if dest_hall_center is not None:
                        x3, y3, z3 = generate_hallway_mesh_path(dest_elev, dest_hall_center, dest_hall_bbox)
                        x4, y4, z4 = generate_hallway_mesh_path(dest_hall_center, dest_room_center, dest_hall_bbox)
                        elev_x.extend(x3 + x4); elev_y.extend(y3 + y4); elev_z.extend(z3 + z4)
                    else:
                        x3, y3, z3 = generate_hallway_mesh_path(dest_elev, dest_room_center, None)
                        elev_x.extend(x3); elev_y.extend(y3); elev_z.extend(z3)

                    esx, esy, esz = smooth_path_coordinates(elev_x, elev_y, elev_z)
                    fig.add_trace(go.Scatter3d(
                        x=esx, y=esy, z=[z + 0.35 for z in esz], 
                        mode="lines+markers", line=dict(color="#27AE60", width=6),
                        marker=dict(size=3, color="#27AE60"), name="Elevator Route (Primary)"
                    ))
                    
                    summary_rows.append({
                        "Route Type": "Primary Green Path",
                        "From Location": f"{start_room_type} {selected_start_room_number} ({start_floor_name})",
                        "Transit Mechanism": f"Hallway ({start_hall_name}) ➔ Elevator ({elev_name}) ➔ Hallway ({dest_hall_name})",
                        "To Location": f"{target_room_type} {selected_target_room_number} ({dest_floor_name})"
                    })

                if has_stair_link:
                    dest_stair_idx = np.argmin([np.linalg.norm(np.array(c) - (np.array(dest_hall_center) if dest_hall_center is not None else np.array(dest_room_center))) for c in stairs_by_floor[dest_floor_name]])
                    start_stair_idx = dest_stair_idx if dest_stair_idx < len(stairs_by_floor[start_floor_name]) else 0
                    
                    start_stair = stairs_by_floor[start_floor_name][start_stair_idx]
                    dest_stair = stairs_by_floor[dest_floor_name][dest_stair_idx]
                    stair_name = stair_names_by_floor[dest_floor_name][dest_stair_idx]

                    stair_x, stair_y, stair_z = [], [], []
                    
                    if start_hall_center is not None:
                        sx1, sy1, sz1 = generate_hallway_mesh_path(start_room_center, start_hall_center, start_hall_bbox)
                        sx2, sy2, sz2 = generate_hallway_mesh_path(start_hall_center, start_stair, start_hall_bbox)
                        stair_x.extend(sx1 + sx2); stair_y.extend(sy1 + sy2); stair_z.extend(sz1 + sz2)
                    else:
                        sx1, sy1, sz1 = generate_hallway_mesh_path(start_room_center, start_stair, None)
                        stair_x.extend(sx1); stair_y.extend(sy1); stair_z.extend(sz1)

                    stair_x.append(dest_stair[0]); stair_y.append(dest_stair[1]); stair_z.append(dest_stair[2])

                    if dest_hall_center is not None:
                        sx3, sy3, sz3 = generate_hallway_mesh_path(dest_stair, dest_hall_center, dest_hall_bbox)
                        sx4, sy4, sz4 = generate_hallway_mesh_path(dest_hall_center, dest_room_center, dest_hall_bbox)
                        stair_x.extend(sx3 + sx4); stair_y.extend(sy3 + sy4); stair_z.extend(sz3 + sz4)
                    else:
                        sx3, sy3, sz3 = generate_hallway_mesh_path(dest_stair, dest_room_center, None)
                        stair_x.extend(sx3); stair_y.extend(sy3); stair_z.extend(sz3)

                    ssx, ssy, ssz = smooth_path_coordinates(stair_x, stair_y, stair_z)
                    fig.add_trace(go.Scatter3d(
                        x=ssx, y=ssy, z=[z + 0.35 for z in ssz], 
                        mode="lines+markers", line=dict(color="#F1C40F", width=5),
                        marker=dict(size=3, color="#D4AC0D"), name="Stairs Route (Alternative)"
                    ))
                    
                    summary_rows.append({
                        "Route Type": "Alternative Yellow Path",
                        "From Location": f"{start_room_type} {selected_start_room_number} ({start_floor_name})",
                        "Transit Mechanism": f"Hallway ({start_hall_name}) ➔ Stairs ({stair_name}) ➔ Hallway ({dest_hall_name})",
                        "To Location": f"{target_room_type} {selected_target_room_number} ({dest_floor_name})"
                    })

        fig.update_layout(
            scene=dict(aspectmode='data', dragmode='orbit'),
            height=800,
            margin=dict(l=0, r=0, b=0, t=0),
            legend=dict(x=0.01, y=0.99, xanchor='left', yanchor='top', bgcolor="rgba(255, 255, 255, 0.7)")
        )
        st.plotly_chart(fig, use_container_width=True)
        
        if summary_rows:
            st.markdown("---")
            st.subheader("📋 Summary Route Network Report")
            df = pd.DataFrame(summary_rows)
            st.dataframe(df, use_container_width=True)
            
            csv_data = df.to_csv(index=False).encode('utf-8')
            st.download_button(
                label="📥 Download Path Summary Report",
                data=csv_data,
                file_name="ifc_route_navigation_report.csv",
                mime="text/csv"
            )

    except Exception as e:
        st.error(f"Navigation Processing failure: {str(e)}")
    finally:
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)

if __name__ == "__main__":
    st.set_page_config(page_title="IFC Human Path Router", layout="wide")
    st.title("🗺️ Natural Human Mesh Path Network Finder")
    uploaded_file = st.sidebar.file_uploader("Upload IFC Architectural Model", type=["ifc"])
    render_navigation_with_routing_tab(uploaded_file)