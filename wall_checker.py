import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import tempfile
import os

try:
    import ifcopenshell
    import ifcopenshell.util.element as uel
except ImportError:
    ifcopenshell = None

# Try importing geometry engine for calculations and 3D views
try:
    import ifcopenshell.geom as ifcgeom
    GEOM_OK = True
except ImportError:
    GEOM_OK = False

# ==========================================
# HELPERS
# ==========================================

def _geom_settings():
    """Settings for geometry processing matching the view layout configuration."""
    s = ifcgeom.settings()
    s.set(s.USE_WORLD_COORDS, True)
    return s

def get_element_geometry_data(element):
    """
    Extracts vertices, faces, and bounding box height for any structural element.
    """
    if not GEOM_OK:
        return None
    try:
        shape = ifcgeom.create_shape(_geom_settings(), element)
        verts = np.array(shape.geometry.verts).reshape(-1, 3)
        faces = np.array(shape.geometry.faces).reshape(-1, 3)
        
        xmin, ymin, zmin = np.min(verts, axis=0)
        xmax, ymax, zmax = np.max(verts, axis=0)
        
        return {
            "verts": verts,
            "faces": faces,
            "height": float(zmax - zmin),
            "center": ((xmin + xmax) / 2, (ymin + ymax) / 2, (zmin + zmax) / 2)
        }
    except Exception:
        return None

def get_wall_height_value(wall, fallback_height=None):
    """
    Robust strategy to find wall height (ORIGINAL PROPERTY-FIRST LOGIC):
    1. 'Unconnected Height' (Revit specific)
    2. Qto_WallBaseQuantities -> Height
    3. Pset_WallCommon -> NominalHeight
    4. GEOMETRY FALLBACK
    """
    if not uel:
        return None

    # --- Strategy A: Properties ---
    psets = uel.get_psets(wall)

    # 1. Unconnected Height (Revit)
    for pset_name, props in psets.items():
        for key, val in props.items():
            if "unconnected height" in key.lower():
                try: return float(val)
                except: pass

    # 2. Standard Qto
    qto = psets.get("Qto_WallBaseQuantities", {})
    if "Height" in qto:
        try: return float(qto["Height"])
        except: pass

    # 3. Nominal Height
    common = psets.get("Pset_WallCommon", {})
    if "NominalHeight" in common:
        try: return float(common["NominalHeight"])
        except: pass

    # --- Strategy B: Geometry Fallback ---
    if fallback_height is not None and fallback_height > 0.1:
        return fallback_height

    return None

def get_name(el):
    return (getattr(el, "Name", "") or "").lower()

# ==========================================
# MAIN LOGIC
# ==========================================
def run_wall_height_check(ifc_file=None):
    st.caption("Code: 5-1-2-4-2")
    st.subheader("🧱 Wall Height Checker with 3D view")

    # 1. Resolve IFC
    model = ifc_file or st.session_state.get("ifc")
    if not model:
        st.info("Please upload an IFC file in the main app.")
        return
    
    if not GEOM_OK:
        st.warning("⚠️ `ifcopenshell.geom` is not installed. 3D Context views cannot be rendered.")

    # 2. School Type Selector
    school_type = st.selectbox(
        "Select School Type (for Yard Walls rules):",
        options=["Select school type", "ebtedaei", "motevassete"],
        index=0
    )

    st.markdown("---")

    # 3. Processing Structural Walls
    walls = model.by_type("IfcWall")
    
    roof_data = []
    yard_data = []
    wall_viz_elements = []

    # Filter target walls matching original structural rules
    target_walls = [w for w in walls if "roof wall" in get_name(w) or "yard wall" in get_name(w)]
    
    if not target_walls:
        st.info("No walls named 'Roof Wall' or 'Yard Wall' found.")
        return

    progress_bar = st.progress(0)
    
    for i, w in enumerate(target_walls):
        progress_bar.progress((i + 1) / len(target_walls))
        
        w_name = get_name(w)
        geom_data = get_element_geometry_data(w)
        
        # Calculate height safely using original cascading parameters
        fallback_h = geom_data["height"] if geom_data else None
        h = get_wall_height_value(w, fallback_h)
        h_display = f"{h:.2f} m" if h is not None else "N/A"
        
        color = "#E5E7E9"
        status = "Unknown"
        
        # --- Roof Wall Logic ---
        if "roof wall" in w_name:
            if h is not None:
                if h > 1.1:
                    status = "✅ Pass"
                    color = "#2ecc71"  # Green
                else:
                    status = "❌ Violation (Shorter than 1.1m)"
                    color = "#e74c3c"  # Red
            
            roof_data.append({
                "Wall Name": getattr(w, "Name", ""),
                "Height": h_display,
                "Status": status
            })

        # --- Yard Wall Logic ---
        elif "yard wall" in w_name:
            status = "Waiting for selection..."
            if school_type == "Select school type":
                status = "⚠️ Select Type above"
                color = "#F4D03F"
            elif h is None:
                status = "⚠️ Unknown Height"
            else:
                if school_type == "ebtedaei":
                    if 2.4 <= h <= 2.6:
                        status = "✅ Pass"
                        color = "#2ecc71"  # Green
                    elif h < 2.4:
                        status = "❌ Violation (Shorter than 2.4m)"
                        color = "#e74c3c"  # Red
                    else:
                        status = "❌ Violation (Taller than 2.6m)"
                        color = "#8e44ad"  # Purple
                elif school_type == "motevassete":
                    if 2.5 <= h <= 2.7:
                        status = "✅ Pass"
                        color = "#2ecc71"  # Green
                    elif h < 2.5:
                        status = "❌ Violation (Shorter than 2.5m)"
                        color = "#e74c3c"  # Red
                    else:
                        status = "❌ Violation (Taller than 2.7m)"
                        color = "#8e44ad"  # Purple

            yard_data.append({
                "Wall Name": getattr(w, "Name", ""),
                "Height": h_display,
                "Status": status
            })

        if geom_data:
            wall_viz_elements.append({
                "name": getattr(w, "Name", "Wall"),
                "geom": geom_data,
                "color": color,
                "status": status,
                "height_val": h
            })

    progress_bar.empty()

    # 4. Render 3D Scene View with Background Room Shadows
    if GEOM_OK:
        st.write("#### 📦 3D Visualizer (Rooms + Walls)")
        fig3d = go.Figure()
        
        # --- A. Render Room Spaces as Translucent Context Layer (From View File) ---
        spaces = model.by_type("IfcSpace")
        tracked_space_legend = False
        
        for space in spaces:
            sp_geom = get_element_geometry_data(space)
            if sp_geom:
                sv = sp_geom["verts"]
                sf = sp_geom["faces"]
                sp_label = f"{(space.LongName or space.Name or 'Room').upper()} (# {space.Name or 'N/A'})"
                
                fig3d.add_trace(go.Mesh3d(
                    x=sv[:, 0], y=sv[:, 1], z=sv[:, 2],
                    i=sf[:, 0], j=sf[:, 1], k=sf[:, 2],
                    color="#E5E7E9", 
                    opacity=0.12,  # Subdued background spaces context
                    name="Room Spaces Context",
                    legendgroup="Context Spaces",
                    showlegend=not tracked_space_legend,
                    hoverinfo="text",
                    text=sp_label
                ))
                tracked_space_legend = True

        # --- B. Overlap Evaluated Audit Walls ---
        tracked_legends = set()
        for elem in wall_viz_elements:
            v = elem["geom"]["verts"]
            f = elem["geom"]["faces"]
            
            if "Pass" in elem["status"]:
                legend_group = "Passed Walls"
            elif "Shorter" in elem["status"]:
                legend_group = "Violated Walls (Too Short)"
            elif "Taller" in elem["status"]:
                legend_group = "Violated Walls (Too Tall)"
            else:
                legend_group = "Pending/Unknown Walls"

            show_in_legend = legend_group not in tracked_legends
            if show_in_legend:
                tracked_legends.add(legend_group)
                
            fig3d.add_trace(go.Mesh3d(
                x=v[:, 0], y=v[:, 1], z=v[:, 2],
                i=f[:, 0], j=f[:, 1], k=f[:, 2],
                color=elem["color"],
                opacity=0.9,
                name=legend_group,
                legendgroup=legend_group,
                showlegend=show_in_legend,
                hoverinfo="text",
                text=f"{elem['name']}<br>Measured Height: {elem['height_val']:.2f}m<br>Status: {elem['status']}"
            ))

        fig3d.update_layout(
            scene=dict(aspectmode='data', dragmode='orbit'),
            height=650,
            margin=dict(l=0, r=0, b=0, t=0),
            legend=dict(yanchor="top", y=0.99, xanchor="left", x=0.01)
        )
        st.plotly_chart(fig3d, use_container_width=True)
        st.markdown("---")

    # 5. Display Reports & Tables
    st.markdown("### 🏠 Roof Walls (> 1.1m)")
    if roof_data:
        df_roof = pd.DataFrame(roof_data)
        st.dataframe(df_roof, use_container_width=True, hide_index=True)
        
        if any("Violation" in x["Status"] for x in roof_data):
            st.error("Some Roof Walls are too short!")
        elif any("Pass" in x["Status"] for x in roof_data):
            st.success("All Roof Walls meet the height requirement.")
            
        csv_roof = df_roof.to_csv(index=False).encode('utf-8')
        st.download_button("Download Roof Walls CSV", csv_roof, "roof_walls_check.csv", "text/csv")
    else:
        st.info("No walls named 'Roof Wall' found.")

    st.markdown("---")

    st.markdown("### 🌳 Yard Walls")
    if yard_data:
        df_yard = pd.DataFrame(yard_data)
        st.dataframe(df_yard, use_container_width=True, hide_index=True)
        
        if school_type != "Select school type":
            if any("Violation" in x["Status"] for x in yard_data):
                st.error(f"Some Yard Walls do not meet the {school_type} limits!")
            elif any("Pass" in x["Status"] for x in yard_data):
                st.success(f"All Yard Walls meet the {school_type} requirements.")
        
        csv_yard = df_yard.to_csv(index=False).encode('utf-8')
        st.download_button("Download Yard Walls CSV", csv_yard, "yard_walls_check.csv", "text/csv")
    else:
        st.info("No walls named 'Yard Wall' found.")

# ==========================================
# STANDALONE SUPPORT
# ==========================================
if __name__ == "__main__":
    st.set_page_config(layout="wide", page_title="Wall Height Check")
    st.title("Standalone: Wall Height Checker")
    
    with st.sidebar:
        up = st.file_uploader("Upload IFC", type=["ifc"])
    
    if up:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".ifc") as t:
            t.write(up.getbuffer())
            path = t.name
        
        try:
            model = ifcopenshell.open(path)
            run_wall_height_check(model)
        finally:
            if os.path.exists(path):
                os.remove(path)
    else:
        st.info("Upload IFC in sidebar.")