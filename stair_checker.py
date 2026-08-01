import streamlit as st
import ifcopenshell
import ifcopenshell.geom
import ifcopenshell.util.element
import pandas as pd
import numpy as np
import plotly.graph_objects as go

# --- DESIGN RULES ---
RISER_H_MIN = 17.0
RISER_H_MAX = 18.0
BLONDEL_MIN = 61.0
BLONDEL_MAX = 63.0
STEPS_MIN = 3
STEPS_MAX = 12

# --- HELPERS FOR GEOMETRY ---

def get_element_geometry(element, geom_settings):
    """Extracts vertices, faces, and bounding box data for 3D processing."""
    try:
        if element.Representation:
            shape = ifcopenshell.geom.create_shape(geom_settings, element)
            verts = np.array(shape.geometry.verts).reshape(-1, 3)
            faces = np.array(shape.geometry.faces).reshape(-1, 3)
            edges = shape.geometry.edges
            
            xmin, ymin, zmin = np.min(verts, axis=0)
            xmax, ymax, zmax = np.max(verts, axis=0)
            x_center = (xmin + xmax) / 2
            y_center = (ymin + ymax) / 2
            z_mean = verts[:, 2].mean()
            
            return {
                "verts": verts, "faces": faces, "edges": edges, "shape": shape,
                "center": (x_center, y_center, z_mean),
                "bbox": (xmin, ymin, xmax, ymax, zmin, zmax)
            }
    except:
        pass
    return None

def find_closest_floor(z_val, storey_map):
    """Matches a Z-coordinate to the nearest building storey."""
    if z_val is None or not storey_map: return "Unknown Level"
    sorted_elevs = sorted(storey_map.items(), key=lambda x: x[1])
    for i, (name, elev) in enumerate(sorted_elevs):
        next_elev = sorted_elevs[i+1][1] if i+1 < len(sorted_elevs) else elev + 5.0
        if elev <= z_val < next_elev: 
            return name
    return min(storey_map.keys(), key=lambda n: abs(z_val - storey_map[n]))

# --- HELPERS FOR DATA ---

def get_property_value(psets, pset_name, prop_name):
    """Safely retrieves a property from a specific property set."""
    if pset_name in psets:
        return psets[pset_name].get(prop_name)
    return None

def get_level_name(element):
    """Finds the IfcBuildingStorey containing this element."""
    try:
        container = ifcopenshell.util.element.get_container(element)
        if container and container.is_a("IfcBuildingStorey"):
            return container.Name
    except:
        pass
    return "Unknown Level"

def get_revit_mark(element):
    """Specifically looks for the 'Mark' parameter found in Revit 'Identity Data'."""
    psets = ifcopenshell.util.element.get_psets(element)
    
    for pset_name, props in psets.items():
        if "Identity Data" in pset_name or "Revit" in pset_name:
            if "Mark" in props:
                return props["Mark"]
    
    for props in psets.values():
        if "Mark" in props:
            return props["Mark"]

    if element.Tag:
        return element.Tag
        
    return "No Mark"

def extract_data(element, parent_stair):
    """Extracts geometry, counts, and checks compliance."""
    psets = ifcopenshell.util.element.get_psets(element)
    level = get_level_name(element)
    real_mark = get_revit_mark(parent_stair)

    riser_h = None
    tread = None
    width = None

    # --- 1. EXTRACT GEOMETRY ---
    if element.is_a("IfcStairFlight"):
        riser_h = getattr(element, "RiserHeight", None)
        tread = getattr(element, "TreadLength", None)
        width = get_property_value(psets, "Pset_StairFlightCommon", "WalkingLineOffset")
    else:
        common = psets.get("Pset_StairCommon", {})
        riser_h = common.get("RiserHeight")
        tread = common.get("TreadLength")
        width = common.get("Width")

    if width is None:
        for p in psets.values():
            if "Width" in p: width = p["Width"]; break

    # --- 2. EXTRACT STEP COUNT ---
    step_count = getattr(element, "NumberOfRisers", None)
    if step_count is None: step_count = get_property_value(psets, "Pset_StairFlightCommon", "NumberOfRisers")
    if step_count is None: step_count = get_property_value(psets, "Pset_StairFlightCommon", "NumberOfRiser")
    if step_count is None: step_count = get_property_value(psets, "Pset_StairCommon", "NumberOfRiser")

    if step_count is not None:
        try:
            step_count = int(step_count)
        except:
            step_count = 0
    else:
        step_count = 0

    # --- 3. CONVERT UNITS (Feet -> CM) ---
    def to_cm(val):
        if val is not None and isinstance(val, (int, float)):
            return round(val * 30.48, 2)
        return None

    h_cm = to_cm(riser_h)
    t_cm = to_cm(tread)
    
    # --- 4. CHECK COMPLIANCE ---
    status = "✅ OK"
    issues = []
    
    if h_cm and not (RISER_H_MIN <= h_cm <= RISER_H_MAX):
        issues.append(f"Height {h_cm}cm")
        status = "❌ FAIL"
    
    if h_cm and t_cm:
        blondel = round((2 * h_cm) + t_cm, 2)
        if not (BLONDEL_MIN <= blondel <= BLONDEL_MAX):
            issues.append(f"Blondel {blondel}")
            status = "❌ FAIL"
            
    if step_count > 0:
        if step_count < STEPS_MIN:
            issues.append(f"Short Run ({step_count})")
            status = "❌ FAIL"
        elif step_count > STEPS_MAX:
            issues.append(f"Long Run ({step_count})")
            status = "❌ FAIL"
    else:
        step_count = 0 
        issues.append("Count missing")
        status = "⚠️ DATA"

    return {
        "Level": level,
        "Stair Mark": real_mark,
        "Stair GlobalID": parent_stair.GlobalId,
        "Element GlobalID": element.GlobalId,
        "Run Name": element.Name if element.Name else "Run",
        "Step Count": step_count,
        "Riser H (cm)": h_cm,
        "Tread D (cm)": t_cm,
        "Blondel": round((2*h_cm)+t_cm, 2) if (h_cm and t_cm) else None,
        "Status": status,
        "Issues": "; ".join(issues)
    }

def get_stair_dataframe(ifc_model):
    """Iterates through the provided IFC model object."""
    data = []
    stairs = ifc_model.by_type("IfcStair")
    
    for stair in stairs:
        decompositions = stair.IsDecomposedBy
        flights_found = False
        if decompositions:
            for rel in decompositions:
                for related_object in rel.RelatedObjects:
                    if related_object.is_a("IfcStairFlight"):
                        flights_found = True
                        data.append(extract_data(related_object, stair))
        
        if not flights_found:
            data.append(extract_data(stair, stair))
            
    df = pd.DataFrame(data)
    if not df.empty:
        df = df.sort_values(by=["Level", "Stair Mark"])
    return df

# --- MAIN RUN FUNCTION ---

def run_stair_check(ifc):
    """
    Analyzes stairs for Riser Height, Blondel Formula, and Step Count,
    and displays compliance metrics along with an integrated 3D spatial view.
    """
    st.caption("code: 4-1-6-6, 4-1-6-11, 4-1-6-12")
    st.header("Staircase Check & Spatial Explorer")
    
    # 1. Rules Panel
    c1, c2, c3 = st.columns(3)
    c1.info(f"**Riser Height:** {RISER_H_MIN}-{RISER_H_MAX} cm")
    c2.info(f"**Blondel(2*h+t):** {BLONDEL_MIN}-{BLONDEL_MAX} cm")
    c3.info(f"**Steps in Run:** {STEPS_MIN}-{STEPS_MAX}")

    # Generate Dataframe from the passed IFC object
    df = get_stair_dataframe(ifc)

    if df.empty:
        st.warning("No stairs found in the model.")
        return

    # --- 2. INTEGRATED 3D MODEL VIEW ---
    st.subheader("🌐 Spatial Model & Compliance View")

    # Set up geometry settings
    settings = ifcopenshell.geom.settings()
    settings.set(settings.USE_WORLD_COORDS, True)

    # Parse Levels & Organize Spatial Maps
    storeys = sorted(ifc.by_type("IfcBuildingStorey"), key=lambda s: s.Elevation if s.Elevation else 0)
    storey_map = {s.Name: float(s.Elevation if s.Elevation else 0) for s in storeys}
    
    storey_options = ["All Levels"] + list(storey_map.keys())
    selected_level = st.selectbox("Filter Geometry View by Floor Level", options=storey_options, key="viewer_floor_sel")

    processed_elements = []

    # Extract Room Context
    for space in ifc.by_type("IfcSpace"):
        geom = get_element_geometry(space, settings)
        if not geom: continue
        
        floor = find_closest_floor(geom["center"][2], storey_map)
        if selected_level != "All Levels" and floor != selected_level:
            continue
        
        processed_elements.append({
            "name": space.Name or "Room Space",
            "category": "Room",
            "floor": floor,
            "geom": geom,
            "color": "#E5E7E9", # Neutral Light Gray
            "opacity": 0.15,
            "legend_group": "Rooms (Context)"
        })

    # Process Stair/Flight Entities with Dynamic Color Assignment
    stair_elements = ifc.by_type("IfcStair") + ifc.by_type("IfcStairFlight")
    for stair in stair_elements:
        geom = get_element_geometry(stair, settings)
        if not geom: continue
        
        floor = find_closest_floor(geom["center"][2], storey_map)
        if selected_level != "All Levels" and floor != selected_level:
            continue

        # Match back against evaluated compliance status dataframe using GlobalID
        matched_status = df[df["Element GlobalID"] == stair.GlobalId]
        if matched_status.empty:
            matched_status = df[df["Stair GlobalID"] == stair.GlobalId]

        # Conditional color formatting: Red for Failures, Green for OK
        if not matched_status.empty and "❌ FAIL" in matched_status["Status"].values:
            color_theme = "#e74c3c"  # Stark Crimson Red
            legend_name = "Stair Components (FAIL)"
        else:
            color_theme = "#2ecc71"  # Vibrant Emerald Green
            legend_name = "Stair Components (OK)"

        processed_elements.append({
            "name": stair.Name or "Staircase Run",
            "category": "Stair",
            "floor": floor,
            "geom": geom,
            "color": color_theme,
            "opacity": 0.85,
            "legend_group": legend_name
        })

    # Process Landings 
    for slab in ifc.by_type("IfcSlab"):
        predefined_type = getattr(slab, "PredefinedType", None)
        if predefined_type == "LANDING" or "landing" in (slab.Name or "").lower():
            geom = get_element_geometry(slab, settings)
            if not geom: continue
            
            floor = find_closest_floor(geom["center"][2], storey_map)
            if selected_level != "All Levels" and floor != selected_level:
                continue
            
            processed_elements.append({
                "name": slab.Name or "Stair Landing",
                "category": "Landing",
                "floor": floor,
                "geom": geom,
                "color": "#2ecc71",  # Standard landings
                "opacity": 0.85,
                "legend_group": "Stair Components (OK)"
            })

    # Render interactive 3D WebGL layout
    fig3d = go.Figure()
    tracked_legends = set()

    for el in processed_elements:
        v = el["geom"]["verts"]
        f = el["geom"]["faces"]
        
        show_in_legend = False
        legend_group = el["legend_group"]
        
        if legend_group not in tracked_legends:
            show_in_legend = True
            tracked_legends.add(legend_group)

        fig3d.add_trace(go.Mesh3d(
            x=v[:, 0], y=v[:, 1], z=v[:, 2],
            i=f[:, 0], j=f[:, 1], k=f[:, 2],
            color=el["color"], opacity=el["opacity"], 
            name=legend_group,
            legendgroup=legend_group,
            showlegend=show_in_legend
        ))

        if el["category"] in ["Stair", "Landing"]:
            lbl_color = "red" if el["color"] == "#e74c3c" else "green"
            fig3d.add_trace(go.Scatter3d(
                x=[el["geom"]["center"][0]], 
                y=[el["geom"]["center"][1]], 
                z=[np.max(v[:, 2]) + 0.15],
                text=[el["name"]], mode="text",
                textfont=dict(size=9, color=lbl_color),
                legendgroup=legend_group,
                showlegend=False
            ))

    fig3d.update_layout(
        scene=dict(aspectmode='data', dragmode='orbit'), 
        height=600, 
        margin=dict(l=0, r=0, b=0, t=0),
        legend=dict(yanchor="top", y=0.99, xanchor="left", x=0.01)
    )
    st.plotly_chart(fig3d, use_container_width=True)

    # --- 3. DATA TABLES (BELOW 3D VIEW) ---
    st.divider()
    st.subheader("📋 Data Table")
    
    hidden_cols = ["Stair Mark", "Stair GlobalID", "Element GlobalID"]
    display_cols = [c for c in df.columns if c not in hidden_cols]
    st.dataframe(df[display_cols], use_container_width=True, hide_index=True)

    # Violations Report Breakdown
    failed_df = df[df["Status"] == "❌ FAIL"]
    if not failed_df.empty:
        st.error(f"⚠️ Found {len(failed_df)} violations.")
        st.dataframe(failed_df[["Level", "Run Name", "Issues"]], hide_index=True)

# --- INDEPENDENT RUN EXECUTION GUARD ---
if __name__ == "__main__":
    st.set_page_config(layout="wide")
    st.title("Stair Checker Standalone Runner")
    
    uploaded_file = st.file_uploader("Upload an IFC file to test this module:", type=["ifc"])
    if uploaded_file is not None:
        with st.spinner("Parsing IFC data structure..."):
            file_bytes = uploaded_file.read()
            ifc_model = ifcopenshell.file.from_string(file_bytes.decode("utf-8", errors="ignore"))
        
        run_stair_check(ifc_model)
    else:
        st.info("Please drop an `.ifc` file above to test the layout and 3D functionality.")