import math
import tempfile
import pandas as pd
import streamlit as st
import numpy as np
import plotly.graph_objects as go
import os

try:
    import ifcopenshell
    from ifcopenshell.util import placement as ifcplace
    import ifcopenshell.util.element as uel
except Exception:
    ifcopenshell = None

try:
    import ifcopenshell.geom as ifcgeom
    GEOM_OK = True
except Exception:
    GEOM_OK = False

# ---- Settings ----
XY_PAD = 0.5
Z_PAD_LOW = 0.1
Z_PAD_HIGH = 0.1

# ==========================================
# HELPERS
# ==========================================
def _open_ifc_from_any(obj):
    if obj is None: return None
    if hasattr(obj, "by_type") and hasattr(obj, "schema"): return obj
    data = obj.getvalue() if hasattr(obj, "getvalue") else obj
    with tempfile.NamedTemporaryFile(delete=False, suffix=".ifc") as tmp:
        tmp.write(data)
        return ifcopenshell.open(tmp.name)

def _geom_settings():
    s = ifcgeom.settings()
    s.set(s.USE_WORLD_COORDS, True)
    return s

def get_element_geometry_data(element):
    """Extracts mesh data vertices, faces, edges, and centroids for Plotly 3D renders."""
    if not GEOM_OK: return None
    try:
        shape = ifcopenshell.geom.create_shape(_geom_settings(), element)
        verts = np.array(shape.geometry.verts).reshape(-1, 3)
        faces = np.array(shape.geometry.faces).reshape(-1, 3)
        edges = shape.geometry.edges
        
        xmin, ymin, zmin = np.min(verts, axis=0)
        xmax, ymax, zmax = np.max(verts, axis=0)
        return {
            "verts": verts, 
            "faces": faces, 
            "edges": edges,
            "center": ((xmin + xmax) / 2, (ymin + ymax) / 2, (zmin + zmax) / 2),
            "max_z": float(zmax)
        }
    except:
        return None

def _element_bbox(elem):
    if not GEOM_OK: return None
    try:
        shp = ifcgeom.create_shape(_geom_settings(), elem)
        v = shp.geometry.verts
        xs, ys, zs = v[0::3], v[1::3], v[2::3]
        return (min(xs), max(xs), min(ys), max(ys), min(zs), max(zs))
    except:
        return None

def _element_centroid(elem):
    if not GEOM_OK: return None
    try:
        shp = ifcgeom.create_shape(_geom_settings(), elem)
        v = shp.geometry.verts
        xs, ys, zs = v[0::3], v[1::3], v[2::3]
        return (sum(xs)/len(xs), sum(ys)/len(ys), sum(zs)/len(zs))
    except:
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

def _storey_of_space(ifc, space):
    for rel in ifc.get_inverse(space):
        if rel.is_a("IfcRelContainedInSpatialStructure"):
            rs = getattr(rel, "RelatingStructure", None)
            if rs and rs.is_a("IfcBuildingStorey"):
                return rs
    return None

def _windows_in_storey(ifc, storey):
    if storey is None:
        return list(ifcopenshell.file.by_type(ifc, "IfcWindow"))
    out = []
    for inv in ifc.get_inverse(storey):
        if inv.is_a("IfcRelContainedInSpatialStructure"):
            for el in inv.RelatedElements or []:
                if el and el.is_a("IfcWindow"):
                    out.append(el)
    return out

def _find_classroom_spaces(ifc):
    out = []
    for sp in ifc.by_type("IfcSpace"):
        for v in (getattr(sp, "LongName", None), getattr(sp, "Name", None), getattr(sp, "ObjectType", None)):
            if isinstance(v, str) and v.strip().lower() == "classroom":
                out.append(sp); break
    return out

def _get_host_wall(ifc, window):
    """Traverses opening voids relationships to locate the structural wall hosting the window element."""
    try:
        for rel in ifc.get_inverse(window):
            if rel.is_a("IfcRelFillsElement"):
                opening = rel.RelatingOpeningElement
                for rel_void in ifc.get_inverse(opening):
                    if rel_void.is_a("IfcRelVoidsElement"):
                        return rel_void.RelatingBuildingElement
    except:
        pass
    return None

# --- AREA CHECK HELPER ---
def _get_window_area_hybrid(window, bbox=None):
    if uel:
        psets = uel.get_psets(window)
        qto = psets.get("Qto_WindowBaseQuantities", {})
        if "Area" in qto and float(qto["Area"]) > 0:
            return float(qto["Area"]), "Property"
        common = psets.get("Pset_WindowCommon", {})
        h = common.get("OverallHeight")
        w = common.get("OverallWidth")
        if h and w:
             return float(h) * float(w), "Property"

    if not bbox: bbox = _element_bbox(window)
    if bbox:
        wx = bbox[1] - bbox[0]
        wy = bbox[3] - bbox[2]
        wz = bbox[5] - bbox[4]
        return max(wx, wy) * wz, "Geometry"
    return 0.0, "None"

# --- ROBUST HEIGHT CHECK HELPER ---
def _get_window_height_robust(window):
    if not uel: return None
    psets = uel.get_psets(window)
    
    head_candidates = []
    sill_candidates = []
    
    for pset_name, props in psets.items():
        for key, val in props.items():
            if not isinstance(val, (int, float)): continue
            k = key.lower().replace(" ", "")
            if "headheight" in k or "headlevel" in k:
                head_candidates.append(float(val))
            if "sillheight" in k or "silllevel" in k:
                sill_candidates.append(float(val))

    head = head_candidates[0] if head_candidates else None
    sill = sill_candidates[0] if sill_candidates else None
    
    if head is not None and sill is not None:
        return {
            "height": abs(head - sill),
            "method": "Param (Head-Sill)",
            "head": head,
            "sill": sill
        }
        
    qto = psets.get("Qto_WindowBaseQuantities", {})
    common = psets.get("Pset_WindowCommon", {})
    
    h_param = common.get("OverallHeight") or qto.get("Height")
    if h_param:
        return {
            "height": float(h_param),
            "method": "Param (Height Only)",
            "head": None, 
            "sill": None
        }

    bbox = _element_bbox(window)
    if bbox:
        z_min, z_max = bbox[4], bbox[5]
        geo_h = z_max - z_min
        return {
            "height": geo_h,
            "method": "Geometry",
            "head": None,
            "sill": None
        }

    return None

def append_wireframe_edges(geom, edge_x, edge_y, edge_z):
    """Stitches edge pairs into single trace segments separated by None."""
    v = geom["verts"]
    e = geom["edges"]
    for idx in range(0, len(e), 2):
        p1 = v[e[idx]]
        p2 = v[e[idx+1]]
        edge_x.extend([p1[0], p2[0], None])
        edge_y.extend([p1[1], p2[1], None])
        edge_z.extend([p1[2], p2[2], None])

# ==========================================
# MAIN FUNCTION
# ==========================================
def run_classroom_window_check(ifc=None):
    st.caption("Code: 5-1-2-4-12, 5-1-2-4-13")
    st.header("Classrooms Window Analysis")
    
    # --- Load IFC ---
    if ifc is None:
        if "ifc" in st.session_state: ifc = st.session_state["ifc"]
        elif "GLOBAL_ifc" in st.session_state: ifc = st.session_state["GLOBAL_ifc"]
    ifc = _open_ifc_from_any(ifc)
    if not ifc:
        st.warning("Please upload an IFC file."); return
    if not GEOM_OK:
        st.error("⚠️ ifcopenshell.geom is required."); return

    classrooms = _find_classroom_spaces(ifc)
    if not classrooms:
        st.warning("No 'classroom' spaces found."); return

    # --- Data Containers ---
    area_rows = []
    height_rows = []
    
    any_area_violation = False
    any_height_violation = False

    # Visual Elements Registries
    viz_spaces = []
    viz_host_walls = {}
    viz_windows_area = []
    viz_windows_height = []

    with st.spinner("Analyzing windows and architectural geometries..."):
        for sp in classrooms:
            room_name = (getattr(sp, "LongName", None) or "").upper()
            room_num = getattr(sp, "Name", None) or "N/A"
            room_label = f"CLASSROOM {room_num}" if not room_name else f"{room_name} (#{room_num})"

            # Collect Space Context Geometry
            sp_geom = get_element_geometry_data(sp)
            if sp_geom:
                viz_spaces.append({"label": room_label, "geom": sp_geom})

            bbox = _element_bbox(sp)
            if not bbox:
                area_rows.append({"Room": room_label, "Status": "⚠️ Geom Error", "Calc Method": "N/A"})
                continue
            
            dx = bbox[1]-bbox[0]
            dy = bbox[3]-bbox[2]
            dz = bbox[5]-bbox[4]
            wall_area = max(dx, dy) * dz
            dims_str = f"{max(dx, dy):.2f}m x {dz:.2f}m"

            storey = _storey_of_space(ifc, sp)
            windows = _windows_in_storey(ifc, storey)
            
            room_windows = []
            total_win_area = 0.0
            area_methods = set()
            
            temp_window_runs = []

            for w in windows:
                w_cent = _element_centroid(w)
                if w_cent and _point_in_padded_bbox(w_cent, bbox):
                    room_windows.append(w)
                    area, method = _get_window_area_hybrid(w)
                    total_win_area += area
                    area_methods.add(method)

                    w_name = getattr(w, "Name", "Unnamed")
                    h_data = _get_window_height_robust(w)
                    
                    h_status = "⚠️ Unknown"
                    h_val_str = "N/A"
                    details_str = "Params missing"
                    h_val = None
                    
                    if h_data:
                        h_val = h_data['height']
                        h_val_str = f"{h_val:.2f} m"
                        src = h_data['method']
                        
                        if 1.2 <= h_val <= 1.6:
                            h_status = "✅ Pass"
                        else:
                            h_status = "❌ Violation"
                            any_height_violation = True
                        
                        if h_data['head'] is not None:
                            details_str = f"Head: {h_data['head']:.2f}, Sill: {h_data['sill']:.2f}"
                        else:
                            details_str = f"Source: {src}"
                    
                    height_rows.append({
                        "Room": room_label,
                        "Window Name": w_name,
                        "Window Height": h_val_str,
                        "Status": h_status,
                        "Details": details_str
                    })

                    # Track unique host walls
                    host_wall = _get_host_wall(ifc, w)
                    if host_wall and host_wall.GlobalId not in viz_host_walls:
                        hw_geom = get_element_geometry_data(host_wall)
                        if hw_geom:
                            viz_host_walls[host_wall.GlobalId] = {
                                "name": getattr(host_wall, "Name", "Host Wall"),
                                "geom": hw_geom
                            }

                    w_geom = get_element_geometry_data(w)
                    if w_geom:
                        temp_window_runs.append({
                            "name": w_name,
                            "geom": w_geom,
                            "h_status": h_status,
                            "h_val": h_val
                        })

            # Area Evaluation Math
            if wall_area > 0.1:
                ratio = total_win_area / wall_area
                passed = ratio > (1/3)
                a_status = "✅ Pass" if passed else "❌ Violation"
                if not passed: any_area_violation = True
                ratio_str = f"{ratio:.1%}"
            else:
                ratio_str, a_status = "N/A", "⚠️ Invalid Dims"
                passed = False

            method_lbl = "Mixed" if len(area_methods) > 1 else (list(area_methods)[0] if area_methods else "N/A")

            area_rows.append({
                "Room": room_label,
                "L x H": dims_str,
                "Wall Area": f"{wall_area:.2f}",
                "Windows": len(room_windows),
                "Window Area": f"{total_win_area:.2f}",
                "Ratio": ratio_str,
                "Status": a_status,
                "Calc Method": method_lbl
            })

            for tw in temp_window_runs:
                viz_windows_area.append({
                    "name": tw["name"],
                    "geom": tw["geom"],
                    "color": "#2ecc71" if passed else "#e74c3c",
                    "status": f"Room Threshold: {a_status}"
                })
                viz_windows_height.append({
                    "name": tw["name"],
                    "geom": tw["geom"],
                    "color": "#2ecc71" if "Pass" in tw["h_status"] else "#e74c3c",
                    "status": f"Height Status: {tw['h_status']}",
                    "val": tw["h_val"]
                })

    # ==========================================
    # REPORT 1: AREA CHECK
    # ==========================================
    st.subheader("1. Window Area Check")
    st.caption("Target: > 33.3% of Wall Area")
    
    if GEOM_OK:
        fig1 = go.Figure()
        
        # Spaces Context Matrix + Floating Floating Labels
        for vs in viz_spaces:
            v, f = vs["geom"]["verts"], vs["geom"]["faces"]
            # Ghost Mesh
            fig1.add_trace(go.Mesh3d(
                x=v[:, 0], y=v[:, 1], z=v[:, 2], i=f[:, 0], j=f[:, 1], k=f[:, 2],
                color="#E5E7E9", opacity=0.08, showlegend=False, hoverinfo="text", text=vs["label"]
            ))
            # Text Marker Label Node
            fig1.add_trace(go.Scatter3d(
                x=[vs["geom"]["center"][0]], y=[vs["geom"]["center"][1]], z=[vs["geom"]["max_z"] + 0.2],
                text=[vs["label"]], mode="text", textfont=dict(size=10, color="#2C3E50"), showlegend=False
            ))
            
        # Host Wall Mesh + Wireframe Outlines
        tracked_wall_leg = False
        wall_edge_x, wall_edge_y, wall_edge_z = [], [], []
        
        for hw_id, hw in viz_host_walls.items():
            v, f = hw["geom"]["verts"], hw["geom"]["faces"]
            # Mesh Fill
            fig1.add_trace(go.Mesh3d(
                x=v[:, 0], y=v[:, 1], z=v[:, 2], i=f[:, 0], j=f[:, 1], k=f[:, 2],
                color="#BDC3C7", opacity=0.25, name="Window Host Walls",
                legendgroup="Walls", showlegend=not tracked_wall_leg, hoverinfo="none"
            ))
            tracked_wall_leg = True
            append_wireframe_edges(hw["geom"], wall_edge_x, wall_edge_y, wall_edge_z)
            
        # Add crisply rendered borders trace around host walls
        if wall_edge_x:
            fig1.add_trace(go.Scatter3d(
                x=wall_edge_x, y=wall_edge_y, z=wall_edge_z, mode="lines",
                line=dict(color="#2C3E50", width=3), name="Host Wall Outlines",
                legendgroup="Walls", showlegend=False, hoverinfo="none"
            ))

        # Windows Layer
        tracked_w_legs = set()
        for vw in viz_windows_area:
            v, f = vw["geom"]["verts"], vw["geom"]["faces"]
            lbl = "Passed Rooms (Windows)" if "#2ecc71" in vw["color"] else "Violated Rooms (Windows)"
            show_leg = lbl not in tracked_w_legs
            if show_leg: tracked_w_legs.add(lbl)
            
            fig1.add_trace(go.Mesh3d(
                x=v[:, 0], y=v[:, 1], z=v[:, 2], i=f[:, 0], j=f[:, 1], k=f[:, 2],
                color=vw["color"], opacity=0.9, name=lbl, legendgroup=lbl, showlegend=show_leg,
                hoverinfo="text", text=f"{vw['name']}<br>{vw['status']}"
            ))

        fig1.update_layout(
            scene=dict(aspectmode='data', dragmode='orbit'), height=550,
            margin=dict(l=0, r=0, b=0, t=0), legend=dict(yanchor="top", y=0.99, xanchor="left", x=0.01)
        )
        st.plotly_chart(fig1, use_container_width=True)

    df_area = pd.DataFrame(area_rows)
    st.dataframe(df_area, use_container_width=True, hide_index=True)
    
    if any_area_violation:
        st.error("❌ Some classrooms failed the Area Check.")
    else:
        st.success("✅ All Area checks passed.")

    if not df_area.empty:
        csv_area = df_area.to_csv(index=False).encode('utf-8')
        st.download_button("Download Area Report CSV", csv_area, "window_area_check.csv", "text/csv")

    # ==========================================
    # REPORT 2: HEIGHT CHECK
    # ==========================================
    st.markdown("---")
    st.subheader("2. Window Height Check")
    st.caption("Target: Window Height between **1.2m and 1.6m**")
    
    if GEOM_OK:
        fig2 = go.Figure()
        
        # Background Space Shadows & Floating Info Labels
        for vs in viz_spaces:
            v, f = vs["geom"]["verts"], vs["geom"]["faces"]
            fig2.add_trace(go.Mesh3d(
                x=v[:, 0], y=v[:, 1], z=v[:, 2], i=f[:, 0], j=f[:, 1], k=f[:, 2],
                color="#E5E7E9", opacity=0.08, showlegend=False, hoverinfo="none"
            ))
            fig2.add_trace(go.Scatter3d(
                x=[vs["geom"]["center"][0]], y=[vs["geom"]["center"][1]], z=[vs["geom"]["max_z"] + 0.2],
                text=[vs["label"]], mode="text", textfont=dict(size=10, color="#2C3E50"), showlegend=False
            ))

        # Host Walls + Wireframe Borders
        tracked_wall_leg2 = False
        wall_edge_x2, wall_edge_y2, wall_edge_z2 = [], [], []
        
        for hw_id, hw in viz_host_walls.items():
            v, f = hw["geom"]["verts"], hw["geom"]["faces"]
            fig2.add_trace(go.Mesh3d(
                x=v[:, 0], y=v[:, 1], z=v[:, 2], i=f[:, 0], j=f[:, 1], k=f[:, 2],
                color="#BDC3C7", opacity=0.25, name="Window Host Walls",
                legendgroup="Walls", showlegend=not tracked_wall_leg2, hoverinfo="none"
            ))
            tracked_wall_leg2 = True
            append_wireframe_edges(hw["geom"], wall_edge_x2, wall_edge_y2, wall_edge_z2)
            
        if wall_edge_x2:
            fig2.add_trace(go.Scatter3d(
                x=wall_edge_x2, y=wall_edge_y2, z=wall_edge_z2, mode="lines",
                line=dict(color="#2C3E50", width=3), name="Host Wall Outlines",
                legendgroup="Walls", showlegend=False, hoverinfo="none"
            ))

        # Windows Height Parameters Layer
        tracked_w_legs2 = set()
        for vw in viz_windows_height:
            v, f = vw["geom"]["verts"], vw["geom"]["faces"]
            lbl = "Compliant Height (1.2m-1.6m)" if "#2ecc71" in vw["color"] else "Non-Compliant Height"
            show_leg = lbl not in tracked_w_legs2
            if show_leg: tracked_w_legs2.add(lbl)
            
            h_val_lbl = f"{vw['val']:.2f}m" if vw['val'] is not None else "N/A"
            fig2.add_trace(go.Mesh3d(
                x=v[:, 0], y=v[:, 1], z=v[:, 2], i=f[:, 0], j=f[:, 1], k=f[:, 2],
                color=vw["color"], opacity=0.9, name=lbl, legendgroup=lbl, showlegend=show_leg,
                hoverinfo="text", text=f"{vw['name']}<br>Height: {h_val_lbl}<br>{vw['status']}"
            ))

        fig2.update_layout(
            scene=dict(aspectmode='data', dragmode='orbit'), height=550,
            margin=dict(l=0, r=0, b=0, t=0), legend=dict(yanchor="top", y=0.99, xanchor="left", x=0.01)
        )
        st.plotly_chart(fig2, use_container_width=True)

    if height_rows:
        df_height = pd.DataFrame(height_rows)
        cols = ["Room", "Window Name", "Window Height", "Status", "Details"]
        df_height = df_height[cols]
        
        st.dataframe(df_height, use_container_width=True, hide_index=True)
        
        if any_height_violation:
            st.error("❌ Some windows violate the 1.2m - 1.6m height limit.")
        else:
            st.success("✅ All Window Heights are within limits.")
            
        csv_height = df_height.to_csv(index=False).encode('utf-8')
        st.download_button("Download Height Report CSV", csv_height, "window_height_check.csv", "text/csv")
    else:
        st.info("No windows found to analyze.")

if __name__ == "__main__":
    st.set_page_config(layout="wide")
    st.sidebar.file_uploader("Upload IFC", type=["ifc"], key="GLOBAL_ifc")
    run_classroom_window_check()