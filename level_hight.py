import streamlit as st
import pandas as pd
try:
    import ifcopenshell
    import ifcopenshell.util.element as uel
except ImportError:
    ifcopenshell = None

# ==========================================
# HELPER FUNCTIONS
# ==========================================

def get_storey_heights(model):
    """
    Calculates floor-to-floor height by sorting storeys by elevation.
    Returns {Storey_GlobalId: Height_Float}
    """
    storeys = model.by_type("IfcBuildingStorey")
    # Sort by Elevation
    storeys = sorted(storeys, key=lambda s: float(getattr(s, "Elevation", 0.0) or 0.0))
    
    heights = {}
    for i, s in enumerate(storeys):
        elev = float(getattr(s, "Elevation", 0.0) or 0.0)
        
        # Method 1: Elevation difference with next floor
        if i < len(storeys) - 1:
            next_elev = float(getattr(storeys[i+1], "Elevation", 0.0) or 0.0)
            h = next_elev - elev
            if h > 0.1: 
                heights[s.GlobalId] = h
                continue

        # Method 2: Fallback to properties (e.g. for top floor)
        h_prop = None
        if uel:
            psets = uel.get_psets(s)
            val = psets.get("Pset_BuildingStoreyCommon", {}).get("NominalHeight")
            if val is None:
                val = psets.get("Qto_BuildingStoreyBaseQuantities", {}).get("GrossHeight")
            if val:
                try:
                    h_prop = float(val)
                except:
                    pass
        
        heights[s.GlobalId] = h_prop

    return heights

def get_storey_of_space(space):
    """Finds the IfcBuildingStorey a space belongs to."""
    rel = getattr(space, "Decomposes", None)
    if rel:
        obj = rel[0].RelatingObject
        if obj.is_a("IfcBuildingStorey"):
            return obj
    if uel:
        return uel.get_container(space)
    return None

# ==========================================
# MAIN RENDERER
# ==========================================

def run_classroom_level_check(ifc_file=None):
    """
    Checks if levels containing classrooms meet the > 3.4m height requirement.
    Auto-detects IFC from st.session_state if ifc_file is not provided.
    """
    st.caption("Code: 5-1-2-4-1")
    st.subheader("Educational Levels Height Check")

    # 1. Resolve IFC File (Argument -> Session State)
    model = ifc_file or st.session_state.get("ifc")
    
    if not model:
        st.info("Please upload an IFC file in the main app to see results.")
        return

    # 2. Logic
    with st.spinner("Analyzing level heights..."):
        height_map = get_storey_heights(model)
        spaces = model.by_type("IfcSpace")
        classroom_storeys = {} 

        for sp in spaces:
            name = (getattr(sp, "LongName", "") or getattr(sp, "Name", "") or "").lower()
            if "classroom" in name:
                storey = get_storey_of_space(sp)
                if storey and storey.is_a("IfcBuildingStorey"):
                    classroom_storeys[storey.GlobalId] = getattr(storey, "Name", "Unnamed")

    if not classroom_storeys:
        st.info("ℹ️ No spaces named 'classroom' found in this model.")
        return

    # 3. Report
    data = []
    limit = 3.4
    any_violation = False

    for guid, name in classroom_storeys.items():
        h = height_map.get(guid)
        
        if h is None:
            status = "⚠️ Unknown (Top floor?)"
            h_str = "N/A"
        elif h > limit:
            status = "✅ Pass"
            h_str = f"{h:.2f} m"
        else:
            status = f"❌ VIOLATION (<= {limit}m)"
            h_str = f"{h:.2f} m"
            any_violation = True
            
        data.append({
            "Level Name": name,
            "Height": h_str,
            "Status": status
        })

    if any_violation:
        st.error(f"Some levels with classrooms are not taller than {limit}m.")
    else:
        st.success(f"All levels with classrooms meet the > {limit}m height requirement.")

    df = pd.DataFrame(data)
    st.dataframe(df, use_container_width=True, hide_index=True)

# ==========================================
# STANDALONE SUPPORT
# ==========================================
if __name__ == "__main__":
    # This block allows the file to run by itself for testing
    st.set_page_config(layout="wide")
    st.title("Standalone: Level Height")
    
    with st.sidebar:
        up = st.file_uploader("Upload IFC", type=["ifc"])
    
    if up:
        import tempfile
        with tempfile.NamedTemporaryFile(delete=False, suffix=".ifc") as t:
            t.write(up.getbuffer())
            path = t.name
        model = ifcopenshell.open(path)
        run_classroom_level_check(model)