import streamlit as st
import pandas as pd
try:
    import ifcopenshell
    import ifcopenshell.util.element as uel
except ImportError:
    ifcopenshell = None

# ==========================================
# HELPER: Calculate Storey Heights
# ==========================================
def get_storey_heights(model, sorted_storeys):
    """
    Calculates floor-to-floor height given a sorted list of storeys.
    Returns {Storey_GlobalId: Height_Float}
    """
    heights = {}
    
    for i, s in enumerate(sorted_storeys):
        elev = float(getattr(s, "Elevation", 0.0) or 0.0)
        
        # Method 1: Difference with next floor
        if i < len(sorted_storeys) - 1:
            next_s = sorted_storeys[i+1]
            next_elev = float(getattr(next_s, "Elevation", 0.0) or 0.0)
            diff = next_elev - elev
            if diff > 0.1: # Ignore tiny geometry errors
                heights[s.GlobalId] = diff
                continue

        # Method 2: Fallback to properties (e.g. for bottom-most or top-most if calculation fails)
        h_prop = None
        if uel:
            psets = uel.get_psets(s)
            # Try Common Pset
            val = psets.get("Pset_BuildingStoreyCommon", {}).get("NominalHeight")
            # Try Qto
            if val is None:
                val = psets.get("Qto_BuildingStoreyBaseQuantities", {}).get("GrossHeight")
            
            if val:
                try:
                    h_prop = float(val)
                except:
                    pass
        
        heights[s.GlobalId] = h_prop

    return heights

# ==========================================
# MAIN LOGIC
# ==========================================
def run_basement_check(ifc_file=None):
    st.caption("Code: 5-1-2-4-8")
    st.header("Basement Level Checker")

    # 1. Resolve IFC
    model = ifc_file or st.session_state.get("ifc")
    if not model:
        st.info("Please upload an IFC file in the main app.")
        return

    # 2. Get and Sort Levels
    storeys = model.by_type("IfcBuildingStorey")
    if not storeys:
        st.warning("No levels found in the IFC model.")
        return

    # Sort by Elevation (Low -> High)
    sorted_storeys = sorted(storeys, key=lambda s: float(getattr(s, "Elevation", 0.0) or 0.0))
    
    # Calculate heights beforehand
    height_map = get_storey_heights(model, sorted_storeys)

    # 3. User Selection: Choose First Floor
    # We map names to objects for easier selection
    storey_options = {getattr(s, "Name", f"Unnamed {s.GlobalId}"): s for s in sorted_storeys}
    
    selected_name = st.selectbox(
        "Select the First Floor (Ground Level):",
        options=list(storey_options.keys()),
        index=None,
        placeholder="Choose first floor..."
    )

    st.markdown("---")

    if not selected_name:
        st.info("Please select the First Floor above to identify basements.")
        return

    # 4. Identify Basements
    # Logic: Any level with an elevation strictly lower than the selected level is a basement.
    selected_storey = storey_options[selected_name]
    cutoff_elevation = float(getattr(selected_storey, "Elevation", 0.0) or 0.0)

    basements = []
    for s in sorted_storeys:
        elev = float(getattr(s, "Elevation", 0.0) or 0.0)
        # Using a small epsilon for float comparison safety
        if elev < (cutoff_elevation - 0.001):
            basements.append(s)

    count = len(basements)

    # 5. Display Verdict
    c1, c2 = st.columns(2)
    with c1:
        st.metric("Basement Levels Found", count)
    with c2:
        if count <= 3:
            st.success("✅ Standard basement (Max 3 allowed)")
        else:
            st.error(f"❌ Violation: Too many basements! ({count} > 3)")

    # 6. Display Table
    if basements:
        st.subheader("Basement Details")
        data = []
        for b in basements:
            h_val = height_map.get(b.GlobalId)
            h_str = f"{h_val:.2f} m" if h_val is not None else "N/A"
            
            data.append({
                "Level Name": getattr(b, "Name", "Unnamed"),
                "Elevation": f"{float(getattr(b, 'Elevation', 0)):.2f}",
                "Height": h_str
            })
        
        df = pd.DataFrame(data)
        st.dataframe(df, use_container_width=True, hide_index=True)
    else:
        st.info("No basement levels found below the selected floor.")


# ==========================================
# STANDALONE SUPPORT
# ==========================================
if __name__ == "__main__":
    st.set_page_config(layout="wide", page_title="Basement Checker")
    st.title("Standalone: Basement Checker")
    
    with st.sidebar:
        up = st.file_uploader("Upload IFC", type=["ifc"])
    
    if up:
        import tempfile
        import os
        with tempfile.NamedTemporaryFile(delete=False, suffix=".ifc") as t:
            t.write(up.getbuffer())
            path = t.name
        
        try:
            model = ifcopenshell.open(path)
            run_basement_check(model)
        finally:
            if os.path.exists(path):
                os.remove(path)
    else:
        st.info("Upload IFC in sidebar.")