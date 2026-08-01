import streamlit as st
import ifcopenshell
import ifcopenshell.geom
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import tempfile
import os

# =====================================================
# ACCESSIBILITY COMPLIANCE SETTINGS
# =====================================================
MIN_WIDTH = 1.20      # m
MAX_RUN = 8.00        # m
MIN_RATIO = 5.00      # 1:5
MAX_RATIO = 8.00      # 1:8
MARGIN = 0.50         # 50 cm margin to capture ramps reliably

# =====================================================
# GEOMETRY EXTRACTION HELPERS
# =====================================================
def get_element_mesh(element, settings):
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

def calculate_ramp_metrics(element, settings):
    try:
        shape = ifcopenshell.geom.create_shape(settings, element)
        verts = np.array(shape.geometry.verts).reshape(-1, 3)
    except:
        return None

    if len(verts) < 3:
        return None

    xmin, ymin, zmin = np.min(verts, axis=0)
    xmax, ymax, zmax = np.max(verts, axis=0)

    xdim = xmax - xmin
    ydim = ymax - ymin

    width = min(xdim, ydim)
    run = max(xdim, ydim)
    rise = zmax - zmin

    ratio_value = run / rise if rise > 0 else None
    slope_ratio = f"1:{round(ratio_value, 2)}" if ratio_value is not None else "N/A"

    width_pass = width >= MIN_WIDTH
    run_pass = run <= MAX_RUN
    ratio_pass = MIN_RATIO <= ratio_value <= MAX_RATIO if ratio_value is not None else False
    is_compliant = width_pass and run_pass and ratio_pass

    failure_reasons = []
    if not width_pass:
        failure_reasons.append(f"Width ({round(width, 2)}m) < required {MIN_WIDTH}m")
    if not run_pass:
        failure_reasons.append(f"Run length ({round(run, 2)}m) > maximum allowed {MAX_RUN}m")
    if not ratio_pass:
        failure_reasons.append(f"Slope ratio ({slope_ratio}) out of standard 1:{int(MIN_RATIO)} to 1:{int(MAX_RATIO)} bounds")

    return {
        "GlobalId": element.GlobalId,
        "Name": getattr(element, "Name", "Unnamed Ramp"),
        "Width": round(width, 2),
        "Run": round(run, 2),
        "Slope Ratio": slope_ratio,
        "Is Compliant": is_compliant,
        "BBox": (xmin, xmax, ymin, ymax, zmin, zmax),
        "Failure Reasons": "; ".join(failure_reasons) if failure_reasons else "None"
    }

def is_ramp_near_room(room_bbox, ramp_bbox, margin=0.50):
    rm_xmin, rm_xmax, rm_ymin, rm_ymax, rm_zmin, rm_zmax = room_bbox
    rp_xmin, rp_xmax, rp_ymin, rp_ymax, rp_zmin, rp_zmax = ramp_bbox
    return (
        (rp_xmin <= rm_xmax + margin) and (rp_xmax >= rm_xmin - margin) and
        (rp_ymin <= rm_ymax + margin) and (rp_ymax >= rm_ymin - margin) and
        (rp_zmin <= rm_zmax + margin) and (rp_zmax >= rm_zmin - margin)
    )

def find_transition_point(room_bbox, hall_bbox, doors, margin=0.8):
    if not room_bbox or not hall_bbox:
        return None

    rm_xmin, rm_xmax, rm_ymin, rm_ymax, rm_zmin, rm_zmax = room_bbox
    hl_xmin, hl_xmax, hl_ymin, hl_ymax, hl_zmin, hl_zmax = hall_bbox

    for door in doors:
        try:
            _, _, d_center, d_bbox = door
            if d_bbox:
                d_xmin, d_xmax, d_ymin, d_ymax, _, _ = d_bbox
                if (d_xmin <= rm_xmax + margin and d_xmax >= rm_xmin - margin and
                    d_ymin <= rm_ymax + margin and d_ymax >= rm_ymin - margin and
                    d_xmin <= hl_xmax + margin and d_xmax >= hl_xmin - margin and
                    d_ymin <= hl_ymax + margin and d_ymax >= hl_ymin - margin):
                    return [d_center[0], d_center[1], d_center[2]]
        except:
            continue

    x_overlap = max(0, min(rm_xmax, hl_xmax) - max(rm_xmin, hl_xmin))
    y_overlap = max(0, min(rm_ymax, hl_ymax) - max(rm_ymin, hl_ymin))

    if x_overlap > 0 and abs(rm_ymin - hl_ymax) <= margin:
        return [max(rm_xmin, hl_xmin) + x_overlap/2.0, (rm_ymin + hl_ymax)/2.0, (rm_zmin + rm_zmax)/2.0]
    elif x_overlap > 0 and abs(rm_ymax - hl_ymin) <= margin:
        return [max(rm_xmin, hl_xmin) + x_overlap/2.0, (rm_ymax + hl_ymin)/2.0, (rm_zmin + rm_zmax)/2.0]
    elif y_overlap > 0 and abs(rm_xmin - hl_xmax) <= margin:
        return [(rm_xmin + hl_xmax)/2.0, max(rm_ymin, hl_ymin) + y_overlap/2.0, (rm_zmin + rm_zmax)/2.0]
    elif y_overlap > 0 and abs(rm_xmax - hl_xmin) <= margin:
        return [(rm_xmax + hl_xmin)/2.0, max(rm_ymin, hl_ymin) + y_overlap/2.0, (rm_zmin + rm_zmax)/2.0]

    return [(rm_xmin + rm_xmax)/2.0, (rm_ymin + rm_ymax)/2.0, (rm_zmin + rm_zmax)/2.0]

# =====================================================
# HUMAN-LIKE NATURAL PATH SMOOTHER
# =====================================================
def smooth_path_coordinates(x, y, z, subdivisions=12):
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
                "number": r_num, "element": space, "storey": parent_storey
            })

        ramps = [calculate_ramp_metrics(r, settings) for r in model.by_type("IfcRampFlight")]
        ramps = [r for r in ramps if r is not None]

        st.subheader("🚶‍♂️ Disabled Path controller")
        room_types = sorted(list(room_database.keys()))
        default_start_idx = room_types.index("yard") if "yard" in room_types else 0
        
        st.write("**From (Starting Point):**")
        col_s1, col_s2 = st.columns(2)
        with col_s1:
            start_room_type = st.selectbox("Select Start Room Type", options=room_types, index=default_start_idx, key="nav_start_type")
        available_start_rooms = room_database.get(start_room_type, [])
        start_room_numbers = sorted(list(set([r["number"] for r in available_start_rooms])))
        with col_s2:
            selected_start_num = st.selectbox(f"Select specific starting {start_room_type} number:", options=start_room_numbers, key="nav_start_num")
        start_space_data = next((r for r in available_start_rooms if r["number"] == selected_start_num), None)
        
        st.write("**To (Destination Point):**")
        col_t1, col_t2 = st.columns(2)
        with col_t1:
            target_room_type = st.selectbox("Select Target Room Type", options=room_types, key="nav_target_type")
        available_target_rooms = room_database.get(target_room_type, [])
        target_room_numbers = sorted(list(set([r["number"] for r in available_target_rooms])))
        with col_t2:
            selected_target_num = st.selectbox(f"Select specific target {target_room_type} number:", options=["All Rooms"] + target_room_numbers, key="nav_target_num")
        
        if not start_space_data:
            st.error("Selected location details could not be parsed.")
            return

        # =====================================================
        # CONDITIONAL EXECUTION BRANCHES
        # =====================================================
        if selected_target_num != "All Rooms":
            target_space_data = next((r for r in available_target_rooms if r["number"] == selected_target_num), None)
            if not target_space_data:
                st.error("Selected location details could not be parsed.")
                return

            start_floor_name = start_space_data["storey"]
            dest_floor_name = target_space_data["storey"]
            
            audit_levels = [start_floor_name]
            if dest_floor_name != start_floor_name:
                audit_levels.append(dest_floor_name)
                
            level_stats = []
            for f_name in audit_levels:
                storey = storey_map.get(f_name)
                if storey:
                    level_elev = round(storey.Elevation or 0.0, 2)
                    storey_slab_count = 0
                    found_heights = []
                    slabs = model.by_type("IfcSlab")
                    for slab in slabs:
                        is_in_storey = any(rel.RelatingStructure == storey for rel in getattr(slab, "ContainedInStructure", []))
                        if is_in_storey:
                            _, _, _, s_bbox = get_element_mesh(slab, settings)
                            if s_bbox:
                                top_z = round(s_bbox[5], 2)
                                storey_slab_count += 1
                                found_heights.append(top_z)
                    storey_spaces = get_elements_in_storey(model, storey, "IfcSpace")
                    elevator = next((r for r in storey_spaces if "elevator" in (r.LongName or r.Name or "").lower()), None)
                    level_stats.append({
                        "Level": f_name, "Level Height (m)": level_elev,
                        "Slabs Found": storey_slab_count, "Slab Top Heights (m)": sorted(list(set(found_heights))),
                        "Has Elevator": "✅ Yes" if elevator else "❌ No"
                    })
            st.subheader("📢 Level & Slab Structural Audit")
            st.table(pd.DataFrame(level_stats))

            path_is_possible = True
            start_accessible = True
            dest_accessible = True
            vertical_accessible = True
            start_fail_reason = "None (Accessible standard floor level)"
            dest_fail_reason = "None (Accessible standard floor level)"

            elevated_slabs = []
            for storey in storeys:
                level_elev = round(storey.Elevation or 0.0, 2)
                for slab in model.by_type("IfcSlab"):
                    is_in_storey = any(rel.RelatingStructure == storey for rel in getattr(slab, "ContainedInStructure", []))
                    if is_in_storey:
                        _, _, _, s_bbox = get_element_mesh(slab, settings)
                        if s_bbox and abs(round(s_bbox[5], 2) - level_elev) > 0.02:
                            elevated_slabs.append({'bbox': s_bbox, 'storey': storey.Name})

            def audit_room_bounds(space_data, label):
                nonlocal path_is_possible, start_fail_reason, dest_fail_reason, start_accessible, dest_accessible
                center, bbox = None, None
                try:
                    shape = ifcopenshell.geom.create_shape(settings, space_data["element"])
                    verts = np.array(shape.geometry.verts).reshape(-1, 3)
                    center = np.mean(verts, axis=0)
                    bbox = (np.min(verts[:, 0]), np.max(verts[:, 0]), np.min(verts[:, 1]), np.max(verts[:, 1]), np.min(verts[:, 2]), np.max(verts[:, 2]))
                except:
                    pass
                if not bbox:
                    return "Standard Level Floor"
                cx, cy = center[0], center[1]
                is_elevated = any(s['bbox'][0] <= cx <= s['bbox'][1] and s['bbox'][2] <= cy <= s['bbox'][3] and s['storey'] == space_data['storey'] for s in elevated_slabs)
                if is_elevated:
                    matched_ramp = next((r for r in ramps if is_ramp_near_room(bbox, r["BBox"], margin=MARGIN)), None)
                    if not matched_ramp:
                        path_is_possible = False
                        reason = "Missing Ramp Element (Elevated Room with no ramp provided)"
                        if label == "Origin Point": 
                            start_fail_reason = reason
                            start_accessible = False
                        else: 
                            dest_fail_reason = reason
                            dest_accessible = False
                        st.error(f"❌ Path is blocked: Elevated room '{space_data['number']}' ({label}) does not have a ramp element!")
                        return "Elevated (No Ramp)"
                    elif not matched_ramp["Is Compliant"]:
                        path_is_possible = False  
                        reason = f"Non-Standard Ramp ({matched_ramp['Failure Reasons']})"
                        if label == "Origin Point": 
                            start_fail_reason = reason
                            start_accessible = False
                        else: 
                            dest_fail_reason = reason
                            dest_accessible = False
                        st.warning(f"⚠️ Warning: Near-room ramp ({matched_ramp['Name']}) is non-standard! Slope ratio is unsafe: {matched_ramp['Slope Ratio']}")
                        return f"Elevated (Non-Standard Ramp: {matched_ramp['Slope Ratio']})"
                    else:
                        st.success(f"✅ Safe Access: Room handles platform shift via compliant ramp ({matched_ramp['Name']}).")
                        return "Elevated (Compliant Ramp)"
                return "Standard Level Floor"

            start_status = audit_room_bounds(start_space_data, "Origin Point")
            dest_status = audit_room_bounds(target_space_data, "Destination Point")

            vertical_mechanism = "Single Floor Horizontal Transfer"
            vertical_fail_reason = "None (Rooms Are in Same level)"
            if start_floor_name != dest_floor_name:
                elevators_by_floor_check = {}
                for elev in [s for s in all_spaces if "elevator" in (s.LongName or s.Name or "").lower()]:
                    for rel in getattr(elev, "Decomposes", []):
                        if rel.is_a("IfcRelAggregates") and rel.RelatingObject.is_a("IfcBuildingStorey"):
                            elevators_by_floor_check[rel.RelatingObject.Name] = elev
                if start_floor_name not in elevators_by_floor_check or dest_floor_name not in elevators_by_floor_check:
                    path_is_possible = False
                    vertical_accessible = False
                    vertical_fail_reason = "Missing Elevator connection between Selected floors"
                    st.error(f"❌ Path is not connected. start floor ({start_floor_name}) or destination floor ({dest_floor_name}) does not have an elevator connection.")
                    vertical_mechanism = "No Elevator Link Found (Blocked)"
                else:
                    st.success(f"✅ Elevator connection exists between {start_floor_name} and {dest_floor_name}.")
                    vertical_mechanism = "Inter-floor Elevator Connection"

            # =====================================================
            # 2D ROUTE COORDINATE PRE-CALCULATIONS
            # =====================================================
            start_room_center, start_room_bbox = None, None
            dest_room_center, dest_room_bbox = None, None
            start_hall_center, start_hall_bbox = None, None
            dest_hall_center, dest_hall_bbox = None, None
            
            levels_to_render = set([start_floor_name, dest_floor_name])
            target_storey_elements = [storey_map[name] for name in levels_to_render if name in storey_map]
            
            elevators_by_floor = {name: [] for name in levels_to_render}
            stairs_by_floor = {name: [] for name in levels_to_render}
            doors_by_floor = {name: [] for name in levels_to_render}

            for storey in target_storey_elements:
                fl_name = storey.Name
                
                for door_el in get_elements_in_storey(model, storey, "IfcDoor"):
                    d_mesh = get_element_mesh(door_el, settings)
                    if d_mesh[2] is not None:
                        doors_by_floor[fl_name].append(d_mesh)

                storey_spaces = get_elements_in_storey(model, storey, "IfcSpace")
                for space in storey_spaces:
                    r_type = space.LongName or "Room"
                    r_num = space.Name or "N/A"
                    full_label = f"{r_type} ({r_num})"
                    is_hall = "hall" in full_label.lower() or "corridor" in full_label.lower() or "circulation" in full_label.lower()
                    is_stair = "stair" in full_label.lower()
                    is_elev = "elevator" in full_label.lower()

                    if space.GlobalId == start_space_data["element"].GlobalId or space.GlobalId == target_space_data["element"].GlobalId or is_hall or is_stair or is_elev:
                        _, _, center, bbox = get_element_mesh(space, settings)
                        if center is not None:
                            if space.GlobalId == start_space_data["element"].GlobalId:
                                start_room_center = center
                                start_room_bbox = bbox
                            if space.GlobalId == target_space_data["element"].GlobalId:
                                dest_room_center = center
                                dest_room_bbox = bbox
                            if fl_name == start_floor_name and is_hall and start_hall_center is None:
                                start_hall_center = center
                                start_hall_bbox = bbox
                            if fl_name == dest_floor_name and is_hall and dest_hall_center is None:
                                dest_hall_center = center
                                dest_hall_bbox = bbox
                            if is_elev:
                                elevators_by_floor[fl_name].append(center)
                            if is_stair:
                                stairs_by_floor[fl_name].append(center)

            sx, sy, sz = [], [], []
            esx, esy, esz = [], [], []

            if start_room_center is not None and dest_room_center is not None:
                start_exit_pt = find_transition_point(start_room_bbox, start_hall_bbox, doors_by_floor.get(start_floor_name, []))
                dest_entry_pt = find_transition_point(dest_room_bbox, dest_hall_bbox, doors_by_floor.get(dest_floor_name, []))

                if start_floor_name == dest_floor_name:
                    x_total, y_total, z_total = [], [], []
                    if start_hall_center is not None:
                        x_total.append(start_room_center[0]); y_total.append(start_room_center[1]); z_total.append(start_room_center[2])
                        x1, y1, z1 = generate_hallway_mesh_path(start_exit_pt, start_hall_center, start_hall_bbox)
                        x2, y2, z2 = generate_hallway_mesh_path(start_hall_center, dest_entry_pt, start_hall_bbox)
                        x_total.extend(x1 + x2); y_total.extend(y1 + y2); z_total.extend(z1 + z2)
                        x_total.append(dest_room_center[0]); y_total.append(dest_room_center[1]); z_total.append(dest_room_center[2])
                    else:
                        x_total, y_total, z_total = generate_hallway_mesh_path(start_room_center, dest_room_center, None)
                    sx, sy, sz = smooth_path_coordinates(x_total, y_total, z_total)
                else:
                    has_elev_link = len(elevators_by_floor.get(start_floor_name, [])) > 0 and len(elevators_by_floor.get(dest_floor_name, [])) > 0
                    if has_elev_link:
                        start_elev_idx = np.argmin([np.linalg.norm(np.array(c) - (np.array(start_hall_center) if start_hall_center is not None else np.array(start_room_center))) for c in elevators_by_floor[start_floor_name]])
                        start_elev = elevators_by_floor[start_floor_name][start_elev_idx]
                        dest_elev = elevators_by_floor[dest_floor_name][start_elev_idx] if start_elev_idx < len(elevators_by_floor[dest_floor_name]) else elevators_by_floor[dest_floor_name][0]
                        
                        elev_x, elev_y, elev_z = [], [], []
                        elev_x.append(start_room_center[0]); elev_y.append(start_room_center[1]); elev_z.append(start_room_center[2])
                        
                        if start_hall_center is not None:
                            x1, y1, z1 = generate_hallway_mesh_path(start_exit_pt, start_hall_center, start_hall_bbox)
                            x2, y2, z2 = generate_hallway_mesh_path(start_hall_center, start_elev, start_hall_bbox)
                            elev_x.extend(x1 + x2); elev_y.extend(y1 + y2); elev_z.extend(z1 + z2)
                        else:
                            x1, y1, z1 = generate_hallway_mesh_path(start_room_center, start_elev, None)
                            elev_x.extend(x1); elev_y.extend(y1); elev_z.extend(z1)
                            
                        elev_x.append(dest_elev[0]); elev_y.append(dest_elev[1]); elev_z.append(dest_elev[2])
                        
                        if dest_hall_center is not None:
                            x3, y3, z3 = generate_hallway_mesh_path(dest_elev, dest_hall_center, dest_hall_bbox)
                            x4, y4, z4 = generate_hallway_mesh_path(dest_hall_center, dest_entry_pt, dest_hall_bbox)
                            elev_x.extend(x3 + x4); elev_y.extend(y3 + y4); elev_z.extend(z3 + z4)
                        else:
                            x3, y3, z3 = generate_hallway_mesh_path(dest_elev, dest_room_center, None)
                            elev_x.extend(x3); elev_y.extend(y3); elev_z.extend(z3)
                            
                        elev_x.append(dest_room_center[0]); elev_y.append(dest_room_center[1]); elev_z.append(dest_room_center[2])
                        esx, esy, esz = smooth_path_coordinates(elev_x, elev_y, elev_z)

            # =====================================================
            # 2D FLOOR PLAN VISUALIZATION LAYER (UPDATED BLUE & DOORS DISPLAY)
            # =====================================================
            st.markdown("---")
            st.subheader("🗺️ 2D Plan Layout & Corridor Passage Routing")
            
            floors_to_plot = [start_floor_name]
            if dest_floor_name != start_floor_name:
                floors_to_plot.append(dest_floor_name)
            
            cols_2d = st.columns(len(floors_to_plot))
            
            for f_idx, f_name in enumerate(floors_to_plot):
                with cols_2d[f_idx]:
                    st.write(f"**Floor Layout Plan: {f_name}**")
                    fig_2d = go.Figure()
                    storey = storey_map.get(f_name)
                    
                    if storey:
                        # Render Walls as structural barriers
                        walls = get_elements_in_storey(model, storey, "IfcWall") + get_elements_in_storey(model, storey, "IfcWallStandardCase")
                        for wall in walls:
                            w_verts, _, _, _ = get_element_mesh(wall, settings)
                            if w_verts is not None:
                                wx_min, wx_max = np.min(w_verts[:, 0]), np.max(w_verts[:, 0])
                                wy_min, wy_max = np.min(w_verts[:, 1]), np.max(w_verts[:, 1])
                                fig_2d.add_shape(
                                    type="rect", x0=wx_min, y0=wy_min, x1=wx_max, y1=wy_max,
                                    fillcolor="rgba(80, 80, 80, 0.7)", line=dict(color="rgba(40, 40, 40, 0.9)", width=1)
                                )

                        # Render EVERY Door on this floor alongside the walls
                        floor_doors = get_elements_in_storey(model, storey, "IfcDoor")
                        for door_el in floor_doors:
                            d_verts, _, _, d_bbox = get_element_mesh(door_el, settings)
                            if d_bbox:
                                dx_min, dx_max, dy_min, dy_max, _, _ = d_bbox
                                fig_2d.add_shape(
                                    type="rect", x0=dx_min, y0=dy_min, x1=dx_max, y1=dy_max,
                                    fillcolor="rgba(243, 156, 18, 0.8)", line=dict(color="rgba(211, 84, 0, 1.0)", width=1)
                                )

                        storey_spaces = get_elements_in_storey(model, storey, "IfcSpace")
                        for space in storey_spaces:
                            r_type = space.LongName or "Room"
                            r_num = space.Name or "N/A"
                            full_label = f"{r_type} ({r_num})"
                            
                            is_start = (space.GlobalId == start_space_data["element"].GlobalId)
                            is_dest = (space.GlobalId == target_space_data["element"].GlobalId)
                            is_hall = "hall" in full_label.lower() or "corridor" in full_label.lower() or "circulation" in full_label.lower()
                            is_elev = "elevator" in full_label.lower() or "elevator" in (space.Name or "").lower() or "elevator" in (space.LongName or "").lower()
                            
                            if is_start:
                                fill_color = "rgba(0, 0, 255, 0.4)"       # More Bluish Deep Blue
                            elif is_dest:
                                fill_color = "rgba(144, 238, 144, 0.6)"   # Green / Light Green
                            elif is_hall:
                                fill_color = "rgba(210, 225, 235, 0.6)"   # Translucent Hallway Light Blue
                            elif is_elev:
                                fill_color = "rgba(46, 139, 87, 0.6)"     # Green (Elevators)
                            else:
                                fill_color = "rgba(255, 182, 193, 0.4)"   # Pale Red (Other places)
                            
                            s_verts, s_faces, s_center, _ = get_element_mesh(space, settings)
                            if s_verts is not None and s_faces is not None and len(s_verts) > 0:
                                
                                for face in s_faces:
                                    fx = [s_verts[face[0], 0], s_verts[face[1], 0], s_verts[face[2], 0], s_verts[face[0], 0]]
                                    fy = [s_verts[face[0], 1], s_verts[face[1], 1], s_verts[face[2], 1], s_verts[face[0], 1]]
                                    
                                    fig_2d.add_trace(go.Scatter(
                                        x=fx, y=fy, fill="toself",
                                        fillcolor=fill_color,
                                        line=dict(color="rgba(0,0,0,0)", width=0),
                                        mode="lines", hoverinfo="skip", showlegend=False
                                    ))
                                
                                if (is_start and f_name == start_floor_name) or (is_dest and f_name == dest_floor_name):
                                    fig_2d.add_trace(go.Scatter(
                                        x=[s_center[0]], y=[s_center[1]], mode="markers+text",
                                        marker=dict(color="green" if is_dest else "blue", size=12, line=dict(color="black", width=1.5)),
                                        text=[full_label], textposition="top center",
                                        textfont=dict(size=11, color="black", family="Arial Black"), showlegend=False
                                    ))
                                    
                        current_floor_z = start_room_center[2] if f_name == start_floor_name else dest_room_center[2]
                        
                        if start_floor_name == dest_floor_name:
                            if len(sx) > 0:
                                fig_2d.add_trace(go.Scatter(
                                    x=sx, y=sy, mode="lines+markers",
                                    line=dict(color="#27AE60" if path_is_possible else "#E74C3C", width=4),
                                    marker=dict(size=4), name="Route Path", showlegend=False
                                ))
                        else:
                            if len(esx) > 0:
                                fx_elev = [x for x, z in zip(esx, esz) if abs(z - current_floor_z) < 1.5]
                                fy_elev = [y for y, z in zip(esy, esz) if abs(z - current_floor_z) < 1.5]
                                if fx_elev:
                                    fig_2d.add_trace(go.Scatter(
                                        x=fx_elev, y=fy_elev, mode="lines+markers",
                                        line=dict(color="#27AE60" if path_is_possible else "#E74C3C", width=4),
                                        name="Elevator Route Segment", showlegend=False
                                    ))
                                    
                    fig_2d.update_layout(
                        xaxis=dict(showgrid=True, zeroline=False, title="X Coords (m)"),
                        yaxis=dict(showgrid=True, zeroline=False, scaleanchor="x", scaleratio=1, title="Y Coords (m)"),
                        margin=dict(l=20, r=20, b=20, t=20), height=500, plot_bgcolor="white"
                    )
                    st.plotly_chart(fig_2d, use_container_width=True)

            st.markdown("---")
            st.subheader("📋 Path Summary Report")
            
            summary_data = {
                "Path Checkpoint": ["1. Start Room Access", "2. Floor Connection", "3. Destination Room Access"],
                "Location Context": [f"{start_room_type} ({selected_start_num})", f"{start_floor_name} to {dest_floor_name}", f"{target_room_type} ({selected_target_num})"],
                "Status": [
                    "✅ Accessible" if start_accessible else "❌ Not Accessible",
                    "✅ Accessible" if vertical_accessible else "❌ Not Accessible",
                    "✅ Accessible" if dest_accessible else "❌ Not Accessible"
                ],
                "Failure Reasons by Standards": [start_fail_reason, vertical_fail_reason, dest_fail_reason]
            }
            st.dataframe(pd.DataFrame(summary_data), use_container_width=True, hide_index=True)

            if path_is_possible:
                st.success("🏁 **Overall Pathfinding Conclusion:** The path is fully **ACCESSIBLE**.")
            else:
                st.error("🚫 **Overall Pathfinding Conclusion:** The path is **NOT ACCESSIBLE**.")

        else:
            pass

    except Exception as e:
        st.error(f"Navigation Processing failure: {str(e)}")
    finally:
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)

if __name__ == "__main__":
    st.set_page_config(page_title="IFC Human Path Router", layout="wide")
    st.title("🗺️ disabled Path Controller")
    uploaded_file = st.sidebar.file_uploader("Upload IFC Architectural Model", type=["ifc"])
    render_navigation_with_routing_tab(uploaded_file)