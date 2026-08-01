# classroom_stair_distance_check.py
import math
import tempfile
import pandas as pd
import streamlit as st
import numpy as np
import plotly.graph_objects as go

try:
    import ifcopenshell  # type: ignore
    from ifcopenshell.util import placement as ifcplace
except Exception:
    st.error("⚠️ Please install ifcopenshell (pip install ifcopenshell)")
    st.stop()

try:
    import ifcopenshell.geom as ifcgeom  # type: ignore
    GEOM_OK = True
except Exception:
    GEOM_OK = False


# ---- Settings ----
XY_PAD = 0.1
Z_PAD_LOW = 0.1
Z_PAD_HIGH = 0.1
MIN_DIST_CHECK = 1.2  # Meters


# ---------- Geometry Helpers ----------
def _open_ifc_from_any(obj):
    if obj is None: return None
    if hasattr(obj, "by_type") and hasattr(obj, "schema"): return obj
    data = None
    if hasattr(obj, "getvalue"):
        try: data = obj.getvalue()
        except Exception: pass
    if isinstance(obj, (bytes, bytearray)):
        data = bytes(obj)
    if data:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".ifc") as tmp:
            tmp.write(data); tmp.flush()
            return ifcopenshell.open(tmp.name)
    try:
        return ifcopenshell.open(str(obj))
    except Exception:
        return None

def _name_any(obj, fallback="(unnamed)"):
    if not obj: return fallback
    for a in ("Name", "LongName", "ObjectType"):
        v = getattr(obj, a, None)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return fallback

def _geom_settings():
    s = ifcgeom.settings()
    s.set(s.USE_WORLD_COORDS, True)
    return s

def _get_element_geometry(element, settings):
    """Extracts vertices, faces, edges, and center coordinates for 3D mapping."""
    if not GEOM_OK: return None
    try:
        if element.Representation:
            shape = ifcgeom.create_shape(settings, element)
            verts = np.array(shape.geometry.verts).reshape(-1, 3)
            faces = np.array(shape.geometry.faces).reshape(-1, 3)
            edges = shape.geometry.edges
            return {
                "verts": verts,
                "faces": faces,
                "edges": edges,
                "center": np.mean(verts, axis=0)
            }
    except Exception:
        pass
    return None

def _element_bbox(elem):
    if not GEOM_OK: return None
    try:
        shp = ifcgeom.create_shape(_geom_settings(), elem)
        v = shp.geometry.verts
        if not v: return None
        xs, ys, zs = v[0::3], v[1::3], v[2::3]
        return (min(xs), max(xs), min(ys), max(ys), min(zs), max(zs))
    except Exception:
        return None

def _element_centroid(elem):
    if not GEOM_OK: return None
    try:
        shp = ifcgeom.create_shape(_geom_settings(), elem)
        v = shp.geometry.verts
        if not v: return None
        xs, ys, zs = v[0::3], v[1::3], v[2::3]
        n = len(xs)
        if n == 0: return None
        return (sum(xs)/n, sum(ys)/n, sum(zs)/n)
    except Exception:
        return None

def _point_in_padded_bbox(pt, bbox):
    if not pt or not bbox: return False
    x, y, z = pt
    minx, maxx, miny, maxy, minz, maxz = bbox
    return (
        (minx - XY_PAD) <= x <= (maxx + XY_PAD) and
        (miny - XY_PAD) <= y <= (maxy + XY_PAD) and
        (minz - Z_PAD_LOW) <= z <= (maxz + Z_PAD_HIGH)
    )

def _dist_point_to_bbox(pt, bbox):
    """Calculates the minimum Euclidean distance from a point to an axis-aligned bounding box."""
    x, y, z = pt
    minx, maxx, miny, maxy, minz, maxz = bbox
    
    dx = max(minx - x, 0, x - maxx)
    dy = max(miny - y, 0, y - maxy)
    dz = max(minz - z, 0, z - maxz)
    
    return math.sqrt(dx*dx + dy*dy + dz*dz)


# ---------- Level Logic ----------
def _get_storey_elevations(ifc):
    storeys = []
    for s in ifc.by_type("IfcBuildingStorey"):
        elev = getattr(s, "Elevation", 0.0)
        name = _name_any(s, "Unknown Storey")
        storeys.append({"name": name, "elev": float(elev), "obj": s})
    storeys.sort(key=lambda k: k['elev'])
    return storeys

def _find_level_by_z(z_val, storeys):
    if not storeys:
        return "Unknown Level"
    candidate = None
    for s in storeys:
        if s['elev'] <= (z_val + 0.1):
            candidate = s
        else:
            break
    if candidate:
        return candidate['name']
    return storeys[0]['name']

def _get_element_z(elem):
    try:
        matrix = ifcplace.get_local_placement(elem.ObjectPlacement)
        return matrix[2][3]
    except:
        return 0.0


# ---------- Main Logic ----------
def run_classroom_stair_check(ifc=None):
    st.caption("code: 4-1-6-2")
    st.header("📏 Classroom Door to Stair Distance & Spatial View")

    if ifc is None:
        if "ifc" in st.session_state: ifc = st.session_state["ifc"]
        elif "GLOBAL_ifc" in st.session_state: ifc = st.session_state["GLOBAL_ifc"]

    if not ifc or not GEOM_OK:
        st.info("Upload an IFC file to begin.")
        return

    # Hardening step: explicitly drop elements if file type does not match extensions
    if hasattr(ifc, 'name') and not ifc.name.lower().endswith('.ifc'):
        st.error(f"❌ Error: '{ifc.name}' is not a valid IFC data model file type.")
        return

    ifc = _open_ifc_from_any(ifc)

    # 1. Map Levels
    storeys = _get_storey_elevations(ifc)
    settings3d = _geom_settings()
    
    # 2. Find Rooms (Classrooms & Stairs)
    classrooms = []
    stairs = []
    all_spaces_geom = []
    
    for sp in ifc.by_type("IfcSpace"):
        name_vals = [getattr(sp, "LongName", ""), getattr(sp, "Name", ""), getattr(sp, "ObjectType", "")]
        full_name = " ".join([str(v) for v in name_vals]).lower()
        
        z_loc = _get_element_z(sp)
        level_name = _find_level_by_z(z_loc, storeys)
        
        bbox = _element_bbox(sp)
        if not bbox: continue

        geom3d = _get_element_geometry(sp, settings3d)

        entry = {
            "obj": sp,
            "name": _name_any(sp),
            "level": level_name,
            "bbox": bbox,
            "z": z_loc,
            "geom3d": geom3d,
            "is_stair": "stair" in full_name,
            "is_classroom": "classroom" in full_name
        }

        if entry["is_classroom"]:
            classrooms.append(entry)
        elif entry["is_stair"]:
            stairs.append(entry)
            
        all_spaces_geom.append(entry)

    if not classrooms:
        st.warning("No Classrooms found.")
        return
    if not stairs:
        st.warning("No Stair spaces found to calculate distance.")
        return

    # Extract internal structural components (Stair flights & Landings)
    stair_elements_geom = []
    stair_components = ifc.by_type("IfcStairFlight") + ifc.by_type("IfcStair")
    for flight in stair_components:
        geom = _get_element_geometry(flight, settings3d)
        if geom:
            z_loc = _get_element_z(flight)
            lvl = _find_level_by_z(z_loc, storeys)
            stair_elements_geom.append({"name": _name_any(flight), "geom3d": geom, "level": lvl, "type": "Flight"})

    for slab in ifc.by_type("IfcSlab"):
        predefined_type = getattr(slab, "PredefinedType", None)
        if predefined_type == "LANDING" or "landing" in (slab.Name or "").lower():
            geom = _get_element_geometry(slab, settings3d)
            if geom:
                z_loc = _get_element_z(slab)
                lvl = _find_level_by_z(z_loc, storeys)
                stair_elements_geom.append({"name": _name_any(slab), "geom3d": geom, "level": lvl, "type": "Landing"})

    # 3. Find Doors inside Classrooms
    all_doors = ifc.by_type("IfcDoor")
    door_data = []
    
    for d in all_doors:
        c = _element_centroid(d)
        geom3d = _get_element_geometry(d, settings3d)
        if c:
            door_data.append({"obj": d, "centroid": c, "geom3d": geom3d})

    rows = []
    door_visual_status = {}     # Door ID -> Color Hex
    classroom_compliance = {}   # Space ID -> Boolean (True if all doors OK)

    for room in classrooms:
        room_bbox = room["bbox"]
        room_level = room['level']
        room_id = room["obj"].GlobalId
        
        room_doors = []
        for d_entry in door_data:
            if _point_in_padded_bbox(d_entry["centroid"], room_bbox):
                room_doors.append(d_entry)
        
        if not room_doors:
            classroom_compliance[room_id] = True # No doors means no violations found
            rows.append({
                "Level": room_level,
                "Classroom": room["name"],
                "Door ID": "(No Door)",
                "Nearest Stair": "-",
                "Distance (m)": None,
                "Status": "No Door"
            })
            continue

        stairs_on_level = [s for s in stairs if s['level'] == room_level]
        target_stairs = stairs_on_level if stairs_on_level else stairs
        room_is_compliant = True

        for d_entry in room_doors:
            min_dist = float('inf')
            nearest_stair_name = "None"
            
            for stair in target_stairs:
                dist = _dist_point_to_bbox(d_entry["centroid"], stair["bbox"])
                if dist < min_dist:
                    min_dist = dist
                    nearest_stair_name = stair["name"]

            is_valid = min_dist >= MIN_DIST_CHECK
            if not is_valid:
                room_is_compliant = False

            status_icon = "✅ OK" if is_valid else "⚠️ Too Close"
            door_color = "#2ecc71" if is_valid else "#e74c3c"
            
            door_visual_status[d_entry["obj"].GlobalId] = door_color
            
            rows.append({
                "Level": room_level,
                "Classroom": room["name"],
                "Door ID": _name_any(d_entry["obj"]),
                "Nearest Stair": nearest_stair_name,
                "Distance (m)": round(min_dist, 3),
                "Status": f"{status_icon} ({MIN_DIST_CHECK}m)"
            })
        
        classroom_compliance[room_id] = room_is_compliant

    # ---------- 3D Render View Section ----------
    st.subheader("🌐 Spatial View & Escape Compliance Matrix")
    
    storey_options = ["All Levels"] + [s["name"] for s in storeys]
    selected_level = st.selectbox("Filter 3D Canvas View by Level", options=storey_options)

    fig3d = go.Figure()
    tracked_legends = set()

    # Dynamic trace handler ensuring visibility tracking on click events
    def add_mesh_trace(x, y, z, i, j, k, color, opacity, group_name):
        show_leg = False
        if group_name not in tracked_legends:
            show_leg = True
            tracked_legends.add(group_name)
            
        fig3d.add_trace(go.Mesh3d(
            x=x, y=y, z=z, i=i, j=j, k=k,
            color=color, opacity=opacity,
            name=group_name, legendgroup=group_name, showlegend=show_leg
        ))

    # 1. Render Room Spatial Blocks
    for sp in all_spaces_geom:
        if selected_level != "All Levels" and sp["level"] != selected_level:
            continue
        if not sp["geom3d"]: continue
        
        v = sp["geom3d"]["verts"]
        f = sp["geom3d"]["faces"]
        
        if sp["is_stair"]:
            color = "#FCF3CF"  # Pale Yellow
            opacity = 0.3
            border_color = "#F4D03F"
            legend_group = "Stair Rooms"
        elif sp["is_classroom"]:
            is_ok = classroom_compliance.get(sp["obj"].GlobalId, True)
            color = "#AEB6BF"  # Darker Gray
            opacity = 0.4
            border_color = "#7F8C8D"
            legend_group = "Classrooms (Compliant)" if is_ok else "Classrooms (Non-Compliant)"
        else:
            color = "#EAEDED"  # Light gray context
            opacity = 0.15
            border_color = None
            legend_group = "Other Rooms"

        add_mesh_trace(v[:, 0], v[:, 1], v[:, 2], f[:, 0], f[:, 1], f[:, 2], color, opacity, legend_group)

        # Wireframe tracing linked directly to the parent legend filter group
        if (sp["is_classroom"] or sp["is_stair"]) and border_color and "edges" in sp["geom3d"]:
            edges = sp["geom3d"]["edges"]
            for index in range(0, len(edges), 2):
                p1, p2 = v[edges[index]], v[edges[index+1]]
                fig3d.add_trace(go.Scatter3d(
                    x=[p1[0], p2[0]], y=[p1[1], p2[1]], z=[p1[2], p2[2]],
                    mode="lines", line=dict(color=border_color, width=2),
                    legendgroup=legend_group, showlegend=False
                ))
            
        # Text label indicators pinned inside respective elements
        if sp["is_classroom"]:
            fig3d.add_trace(go.Scatter3d(
                x=[sp["geom3d"]["center"][0]], 
                y=[sp["geom3d"]["center"][1]], 
                z=[np.max(v[:, 2]) + 0.1],
                text=[sp["name"]], mode="text",
                textfont=dict(size=10, color="#2C3E50", weight="bold"),
                legendgroup=legend_group, showlegend=False
            ))

    # 2. Render Stair Structural Components
    for item in stair_elements_geom:
        if selected_level != "All Levels" and item["level"] != selected_level:
            continue
        v = item["geom3d"]["verts"]
        f = item["geom3d"]["faces"]
        
        add_mesh_trace(v[:, 0], v[:, 1], v[:, 2], f[:, 0], f[:, 1], f[:, 2], "#2C3E50", 0.85, "Stair Structural Components")

    # 3. Render Classroom Doors (Split up into individual traces so turning them off works perfectly!)
    for d_entry in door_data:
        d_obj = d_entry["obj"]
        if d_obj.GlobalId not in door_visual_status:
            continue
            
        z_loc = _get_element_z(d_obj)
        door_lvl = _find_level_by_z(z_loc, storeys)
        if selected_level != "All Levels" and door_lvl != selected_level:
            continue
            
        if not d_entry["geom3d"]: continue
        v = d_entry["geom3d"]["verts"]
        f = d_entry["geom3d"]["faces"]
        color = door_visual_status[d_obj.GlobalId]
        
        legend_group = f"Compliant Doors (≥{MIN_DIST_CHECK}m)" if color == "#2ecc71" else f"Critical Doors (<{MIN_DIST_CHECK}m)"
        add_mesh_trace(v[:, 0], v[:, 1], v[:, 2], f[:, 0], f[:, 1], f[:, 2], color, 0.95, legend_group)

    fig3d.update_layout(
        scene=dict(aspectmode='data', dragmode='orbit'),
        height=600,
        margin=dict(l=0, r=0, b=0, t=0),
        legend=dict(yanchor="top", y=0.99, xanchor="left", x=0.01)
    )
    st.plotly_chart(fig3d, use_container_width=True)

    # ---------- Outputs and Tables ----------
    st.divider()
    st.subheader("📋 Distance Compliance Registry")
    df = pd.DataFrame(rows)
    st.info(f"Checking if distance from Classroom Door to Stair Area is ≥ {MIN_DIST_CHECK}m")
    st.dataframe(df, use_container_width=True, hide_index=True)

    if not df.empty:
        csv = df.to_csv(index=False).encode('utf-8')
        st.download_button("Download Report CSV", csv, "classroom_stair_dist.csv", "text/csv")


if __name__ == "__main__":
    st.set_page_config(page_title="Distance Check", layout="wide")
    uploaded_file = st.sidebar.file_uploader("Upload IFC", type=["ifc"], key="GLOBAL_ifc")
    if uploaded_file:
        run_classroom_stair_check(uploaded_file)
    else:
        run_classroom_stair_check()