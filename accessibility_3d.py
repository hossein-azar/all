import streamlit as st
import ifcopenshell
import ifcopenshell.geom
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import tempfile
import os

# =====================================================
# RULES & CONFIGURATION
# =====================================================
MIN_WIDTH = 1.20      # m
MAX_RUN = 8.00        # m
MIN_RATIO = 5.00      # 1:5
MAX_RATIO = 8.00      # 1:8
MARGIN = 0.50         # 50 cm margin to capture ramps reliably

# =====================================================
# COMPLIANCE ENGINE
# =====================================================
def calculate_ramp_compliance(element, settings):
    """Calculates geometry metrics, checks compliance against rules, and appends threshold limits."""
    try:
        shape = ifcopenshell.geom.create_shape(settings, element)
        verts = np.array(shape.geometry.verts).reshape(-1, 3)
        faces = np.array(shape.geometry.faces).reshape(-1, 3)
    except Exception as e:
        return {"Compliance": "ERROR", "Error": f"Geometry error: {str(e)}"}

    if len(verts) < 3:
        return {"Compliance": "ERROR", "Error": "Insufficient vertices"}

    xmin, ymin, zmin = np.min(verts, axis=0)
    xmax, ymax, zmax = np.max(verts, axis=0)

    xdim = xmax - xmin
    ydim = ymax - ymin

    width = min(xdim, ydim)
    run = max(xdim, ydim)
    rise = zmax - zmin

    slope = rise / run if run > 0 else None
    slope_percent = slope * 100 if slope is not None else None
    ratio_value = run / rise if rise > 0 else None
    slope_ratio = f"1:{round(ratio_value, 2)}" if ratio_value is not None else None

    # Checks
    width_pass = width >= MIN_WIDTH
    run_pass = run <= MAX_RUN
    ratio_pass = MIN_RATIO <= ratio_value <= MAX_RATIO if ratio_value is not None else False
    overall_pass = width_pass and run_pass and ratio_pass

    return {
        "Width (m)": round(width, 3),
        "Run (m)": round(run, 3),
        "Rise (m)": round(rise, 3),
        "Slope (%)": round(slope_percent, 2) if slope_percent is not None else None,
        "Slope Ratio": slope_ratio,
        "Width Check": "✅PASS" if width_pass else f"❌FAIL (Should be ≥ {MIN_WIDTH}m)",
        "Run Check": "✅PASS" if run_pass else f"❌FAIL (Should be ≤ {MAX_RUN}m)",
        "Slope Check": "✅PASS" if ratio_pass else f"❌FAIL (Should be between 1:{int(MIN_RATIO)} and 1:{int(MAX_RATIO)})",
        "Compliance": "✅PASS" if overall_pass else "❌FAIL",
        "BBox": (xmin, xmax, ymin, ymax, zmin, zmax),
        "verts": verts,
        "faces": faces
    }

def is_ramp_near_room(room_bbox, ramp_bbox, margin=0.50):
    """Checks if a ramp bounding box intersects with an expanded room bounding box."""
    rm_xmin, rm_xmax, rm_ymin, rm_ymax, rm_zmin, rm_zmax = room_bbox
    rp_xmin, rp_xmax, rp_ymin, rp_ymax, rp_zmin, rp_zmax = ramp_bbox

    return (
        (rp_xmin <= rm_xmax + margin) and (rp_xmax >= rm_xmin - margin) and
        (rp_ymin <= rm_ymax + margin) and (rp_ymax >= rm_ymin - margin) and
        (rp_zmin <= rm_zmax + margin) and (rp_zmax >= rm_zmin - margin)
    )

# =====================================================
# TAB RENDER ENTRY POINT (For app_guide.py)
# =====================================================
def render_3d_accessibility_tab(uploaded_ifc):
    """
    Renders the updated 3D Accessibility & Ramp Compliance Analysis.
    Handles both raw Streamlit UploadedFile and opened ifcopenshell models.
    """
    if uploaded_ifc is None:
        st.info("Upload an IFC data configuration file via the sidebar to initiate tracking.")
        return

    temp_path = None
    try:
        # --- Handle raw uploaded files vs already opened models ---
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

        # Pre-calculate all ramp data
        ramp_flights = model.by_type("IfcRampFlight")
        processed_ramps = []
        for ramp in ramp_flights:
            ramp_data = {
                "GlobalId": ramp.GlobalId,
                "Name": getattr(ramp, "Name", "Unnamed Ramp")
            }
            metrics = calculate_ramp_compliance(ramp, settings)
            if metrics:
                ramp_data.update(metrics)
            processed_ramps.append(ramp_data)

        # --- LEVEL SELECTION ---
        storeys = model.by_type("IfcBuildingStorey")
        storey_options = ["All Levels"] + [s.Name for s in storeys]
        sel_level_name = st.selectbox("1. Select Floor Level for View", options=storey_options, key="main_level_sel")
        active_storeys = storeys if sel_level_name == "All Levels" else [s for s in storeys if s.Name == sel_level_name]

        # --- EXTRACT ROOM TYPES ---
        room_types_list = set()
        all_rooms_to_process = []
        for s in active_storeys:
            for rel in s.IsDecomposedBy:
                if rel.is_a("IfcRelAggregates"):
                    for obj in rel.RelatedObjects:
                        if obj.is_a("IfcSpace"):
                            all_rooms_to_process.append((obj, s))
                            if obj.LongName: 
                                room_types_list.add(obj.LongName)
        
        selected_types = st.multiselect("2. Target List (Check these for ramps)", options=sorted(list(room_types_list)), key="main_target_sel")

        fig = go.Figure()
        report_data = []
        level_stats = []
        visible_ramp_ids = set()
        
        # --- PROCESS SPATIAL AUDIT ---
        for storey in active_storeys:
            level_elev = round(storey.Elevation or 0.0, 2)
            storey_slabs = []
            found_heights = []
            
            # Slab Evaluation
            slabs = model.by_type("IfcSlab")
            storey_slab_count = 0
            for slab in slabs:
                is_in_storey = any(rel.RelatingStructure == storey for rel in getattr(slab, "ContainedInStructure", []))
                if is_in_storey:
                    try:
                        s_shape = ifcopenshell.geom.create_shape(settings, slab)
                        s_verts = np.array(s_shape.geometry.verts).reshape(-1, 3)
                        s_faces = np.array(s_shape.geometry.faces).reshape(-1, 3)
                        top_z = round(np.max(s_verts[:, 2]), 2)
                        
                        storey_slab_count += 1
                        found_heights.append(top_z)
                        is_offset = abs(top_z - level_elev) > 0.02
                        
                        # Show elevated in Red, standard in a pale green (#C8E6C9)
                        slab_color = "#FF0000" if is_offset else "#C8E6C9"
                        
                        # Dynamic flattening logic removed here to preserve true geometric thickness

                        storey_slabs.append({
                            'min_x': np.min(s_verts[:, 0]), 'max_x': np.max(s_verts[:, 0]),
                            'min_y': np.min(s_verts[:, 1]), 'max_y': np.max(s_verts[:, 1]),
                            'top_z': top_z, 'is_red': is_offset
                        })

                        fig.add_trace(go.Mesh3d(
                            x=s_verts[:, 0], y=s_verts[:, 1], z=s_verts[:, 2],
                            i=s_faces[:, 0], j=s_faces[:, 1], k=s_faces[:, 2],
                            color=slab_color, opacity=0.7 if is_offset else 0.2, 
                            name=f"Slab @ {top_z}m", showlegend=False
                        ))
                    except: 
                        continue

            storey_rooms = [r[0] for r in all_rooms_to_process if r[1] == storey]
            elevator = next((r for r in storey_rooms if "elevator" in (r.LongName or r.Name or "").lower()), None)
            
            level_stats.append({
                "Level": storey.Name, "Level Height (m)": level_elev,
                "Slabs Found": storey_slab_count, "Slab Top Heights (m)": sorted(list(set(found_heights))),
                "Has Elevator": "✅ Yes" if elevator else "❌ No"
            })

            for room in storey_rooms:
                try:
                    r_type = room.LongName or "Room"
                    r_num = room.Name or "N/A"
                    full_name = f"{r_type} ({r_num})"
                    shape = ifcopenshell.geom.create_shape(settings, room)
                    verts = np.array(shape.geometry.verts).reshape(-1, 3)
                    faces = np.array(shape.geometry.faces).reshape(-1, 3)
                    
                    rx, ry = np.mean(verts[:, 0]), np.mean(verts[:, 1])
                    room_bbox = (np.min(verts[:, 0]), np.max(verts[:, 0]),
                                 np.min(verts[:, 1]), np.max(verts[:, 1]),
                                 np.min(verts[:, 2]), np.max(verts[:, 2]))
                    
                    is_targeted = r_type in selected_types or room == elevator
                    on_offset = any(s['min_x'] <= rx <= s['max_x'] and s['min_y'] <= ry <= s['max_y'] and s['is_red'] for s in storey_slabs)
                    
                    ramp_status = "Accessible"
                    associated_ramp_id = "N/A"
                    compliance_details = {}

                    if is_targeted and on_offset:
                        matched_ramp = None
                        for r_data in processed_ramps:
                            if "BBox" in r_data and r_data["BBox"]:
                                if is_ramp_near_room(room_bbox, r_data["BBox"], margin=MARGIN):
                                    matched_ramp = r_data
                                    break
                        
                        if matched_ramp:
                            ramp_status = f"Ramp Found ({matched_ramp['Compliance']})"
                            associated_ramp_id = matched_ramp['GlobalId']
                            compliance_details = matched_ramp
                            visible_ramp_ids.add(matched_ramp['GlobalId'])
                        else:
                            ramp_status = "❌ No Ramp Provided"

                    color = "#27ae60" if room == elevator else ("#880808" if (is_targeted and on_offset and "Found" not in ramp_status) else "#2ecc71")
                    
                    # 1. Render Space Mesh
                    fig.add_trace(go.Mesh3d(
                        x=verts[:, 0], y=verts[:, 1], z=verts[:, 2],
                        i=faces[:, 0], j=faces[:, 1], k=faces[:, 2],
                        color=color if is_targeted else "#E5E7E9", 
                        opacity=0.4 if is_targeted else 0.05, name=full_name
                    ))

                    # 2. Render Room Text Labels (Only kept intact if a specific floor is selected)
                    if sel_level_name != "All Levels":
                        fig.add_trace(go.Scatter3d(
                            x=[rx], y=[ry], z=[np.max(verts[:, 2]) + 0.2],
                            text=[full_name], mode="text", 
                            textfont=dict(size=10, color="red" if (is_targeted and on_offset and "Found" not in ramp_status) else "black"),
                            showlegend=False
                        ))

                    if is_targeted:
                        record = {
                            "Level": storey.Name, 
                            "Room Type": r_type, 
                            "Number": r_num,
                            "Elevation Change": "Yes" if on_offset else "No",
                            "Access Audit": ramp_status,
                            "Ramp ID": associated_ramp_id
                        }
                        if compliance_details and "Compliance" in compliance_details:
                            record.update({
                                "Width (m)": compliance_details.get("Width (m)"),
                                "Width Check": compliance_details.get("Width Check"),
                                "Run (m)": compliance_details.get("Run (m)"),
                                "Run Check": compliance_details.get("Run Check"),
                                "Slope Ratio": compliance_details.get("Slope Ratio"),
                                "Slope Check": compliance_details.get("Slope Check")
                            })
                        report_data.append(record)
                except: 
                    continue

        # --- SHOW ASSOCIATED RAMP GEOMETRY ---
        for r_data in processed_ramps:
            if r_data['GlobalId'] in visible_ramp_ids and "verts" in r_data:
                rv = r_data["verts"]
                rf = r_data["faces"]
                ramp_color = "#2980b9" if "PASS" in r_data["Compliance"] else "#e67e22"
                
                fig.add_trace(go.Mesh3d(
                    x=rv[:, 0], y=rv[:, 1], z=rv[:, 2],
                    i=rf[:, 0], j=rf[:, 1], k=rf[:, 2],
                    color=ramp_color, opacity=0.9,
                    name=f"Ramp: {r_data['Name']} ({r_data['Compliance']})"
                ))

        # Render Layout Components
        st.subheader("📢 Level & Slab Structural Audit")
        st.table(pd.DataFrame(level_stats)) 

        st.subheader("📦 Model Visualization")
        fig.update_layout(scene=dict(aspectmode='data', dragmode='orbit'), height=750, margin=dict(l=0, r=0, b=0, t=0))
        st.plotly_chart(fig, use_container_width=True)

        if report_data:
            st.subheader("📋 Integrated Accessibility & Engineering Compliance Report")
            final_df = pd.DataFrame(report_data)
            
            col_order = ["Level", "Room Type", "Number", "Elevation Change", "Access Audit", "Ramp ID", "Width (m)", "Width Check", "Run (m)", "Run Check", "Slope Ratio", "Slope Check"]
            col_order = [c for c in col_order if c in final_df.columns]
            
            st.dataframe(final_df[col_order], use_container_width=True, hide_index=True)
            
            st.download_button(
                label="📥 Download Comprehensive Audit CSV", 
                data=final_df[col_order].to_csv(index=False).encode('utf-8'), 
                file_name="Comprehensive_Accessibility_Ramp_Audit.csv", 
                mime='text/csv'
            )

    except Exception as e:
        st.error(f"Critical error runtime loop: {str(e)}")
    finally:
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)

# Allows running standalone if executed directly, but won't conflict with app_guide.py
if __name__ == "__main__":
    st.set_page_config(page_title="Integrated Accessibility & Ramp Compliance Checker", layout="wide")
    st.title("IFC Accessibility Audit & Ramp Compliance Checker")
    uploaded_file = st.sidebar.file_uploader("Upload IFC File", type=["ifc"])
    render_3d_accessibility_tab(uploaded_file)