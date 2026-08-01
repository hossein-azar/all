# window_direction_checker.py
# Usage:
#   from window_direction_checker import run_classroom_window_outward_check
#   run_classroom_window_outward_check(ifc=ifc)

import math
import tempfile
import pandas as pd
import numpy as np
import streamlit as st
import plotly.graph_objects as go

try:
    import ifcopenshell  # type: ignore
    from ifcopenshell.util import placement as ifcplace  # type: ignore
except Exception:
    st.error("⚠️ Please install ifcopenshell (pip install ifcopenshell)")
    st.stop()

try:
    import ifcopenshell.geom as ifcgeom  # type: ignore
    GEOM_OK = True
except Exception:
    GEOM_OK = False

# ---- Settings (same flow/thresholds as doors) ----
XY_PAD = 0.25
Z_PAD_LOW = 0.1
Z_PAD_HIGH = 0.1
ALIGN_TOL_DEG = 15.0       # ≤ this → outward
OPPOSITE_TOL_DEG = 15.0    # ≥ (180 - this) → inward (wrong)

# ---------- Helpers ----------
def _open_ifc_from_any(obj):
    if obj is None:
        return None
    if hasattr(obj, "by_type") and hasattr(obj, "schema"):
        return obj
    data = None
    if hasattr(obj, "getvalue"):
        try: data = obj.getvalue()
        except Exception: pass
    if data is None and hasattr(obj, "read"):
        try: data = obj.read()
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
    if not obj:
        return fallback
    for a in ("Name", "LongName", "ObjectType"):
        v = getattr(obj, a, None)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return fallback

def _geom_settings():
    s = ifcgeom.settings()
    s.set(s.USE_WORLD_COORDS, True)
    s.set(s.DISABLE_OPENING_SUBTRACTIONS, False)
    return s

def _get_element_mesh(elem, settings):
    """Extracts 3D vertices and faces for Plotly visualization."""
    if not GEOM_OK: return None
    try:
        shp = ifcgeom.create_shape(settings, elem)
        verts = np.array(shp.geometry.verts).reshape(-1, 3)
        faces = np.array(shp.geometry.faces).reshape(-1, 3)
        return verts, faces
    except Exception:
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

def _point_in_padded_bbox(pt, bbox):
    if not pt or not bbox: return False
    x,y,z = pt
    minx,maxx,miny,maxy,minz,maxz = bbox
    return (
        (minx - XY_PAD) <= x <= (maxx + XY_PAD) and
        (miny - XY_PAD) <= y <= (maxy + XY_PAD) and
        (minz - Z_PAD_LOW) <= z <= (maxz + Z_PAD_HIGH)
    )

def _find_classroom_spaces(ifc):
    out = []
    for sp in ifc.by_type("IfcSpace"):
        for v in (getattr(sp, "LongName", None), getattr(sp, "Name", None), getattr(sp, "ObjectType", None)):
            if isinstance(v, str) and v.strip().lower() == "classroom":
                out.append(sp); break
    return out

def _placement_matrix(elem):
    try:
        return ifcplace.get_local_placement(elem.ObjectPlacement)
    except Exception:
        return None

def _apply_mat_to_point(m, p):
    x,y,z = p
    return (
        m[0][0]*x + m[0][1]*y + m[0][2]*z + m[0][3],
        m[1][0]*x + m[1][1]*y + m[1][2]*z + m[1][3],
        m[2][0]*x + m[2][1]*y + m[2][2]*z + m[2][3],
    )

def _window_world_axes(window):
    m = _placement_matrix(window)
    if m is None:
        return None
    o  = _apply_mat_to_point(m, (0,0,0))
    ex = _apply_mat_to_point(m, (1,0,0))
    ey = _apply_mat_to_point(m, (0,1,0))
    ez = _apply_mat_to_point(m, (0,0,1))
    def _norm(u):
        ux,uy,uz = (u[0]-o[0], u[1]-o[1], u[2]-o[2])
        L = math.sqrt(ux*ux+uy*uy+uz*uz) or 1.0
        return (ux/L, uy/L, uz/L)
    xw, yw, zw = _norm(ex), _norm(ey), _norm(ez)
    return xw, yw, zw

def _angle_2d_deg(v1, v2):
    x1,y1 = v1[0], v1[1]
    x2,y2 = v2[0], v2[1]
    dot = x1*x2 + y1*y2
    cross_z = x1*y2 - y1*x2
    a = math.degrees(math.atan2(cross_z, dot))  # (-180..180]
    if a < 0: a += 360.0
    return a


# ---------- Public Function ----------
def run_classroom_window_outward_check(ifc=None):
    st.caption("code: 4-1-6-2")
    st.header("🪟 Classrooms Window Opening Direction")

    if ifc is None:
        if "ifc" in st.session_state:
            ifc = st.session_state["ifc"]
        elif "GLOBAL_ifc" in st.session_state:
            ifc = st.session_state["GLOBAL_ifc"]

    ifc = _open_ifc_from_any(ifc)

    if ifc is None:
        st.warning("Please pass an opened IFC or upload once in the main app.")
        return
    if not GEOM_OK:
        st.error("⚠️ ifcopenshell.geom (OCC build) is required for geometry.")
        return

    classrooms = _find_classroom_spaces(ifc)
    if not classrooms:
        st.warning("No spaces named exactly 'classroom' found.")
        return

    geom_settings = _geom_settings()
    visualization_records = {"classrooms": [], "windows": []}
    rows = []

    for sp in classrooms:
        room_name   = getattr(sp, "LongName", None) or "(no room name)"
        room_number = getattr(sp, "Name", None) or "(no room number)"
        bbox = _element_bbox(sp)

        # Save classroom layout geometry and layout labels context
        cls_mesh = _get_element_mesh(sp, geom_settings)
        if cls_mesh:
            v_arr = cls_mesh[0]
            cx, cy, cz = v_arr[:, 0].mean(), v_arr[:, 1].mean(), v_arr[:, 2].max()
            visualization_records["classrooms"].append({
                "verts": v_arr, "faces": cls_mesh[1],
                "label": f"{room_number} - {room_name}",
                "label_center": (cx, cy, cz)
            })

        if bbox is None:
            rows.append({
                "room_name": room_name, "room_number": room_number,
                "opens": "", "window_name": "(no space geometry)", "window_id": "", "angle_deg": "",
            })
            continue

        cx = (bbox[0] + bbox[1]) * 0.5
        cy = (bbox[2] + bbox[3]) * 0.5
        room_center = (cx, cy, (bbox[4] + bbox[5]) * 0.5)

        storey = _storey_of_space(ifc, sp)
        windows = _windows_in_storey(ifc, storey)

        found_any = False
        for w in windows:
            w_cent = _element_centroid(w)
            if w_cent is None or not _point_in_padded_bbox(w_cent, bbox):
                continue

            axes = _window_world_axes(w)
            w_mesh = _get_element_mesh(w, geom_settings)

            if axes is None:
                rows.append({
                    "room_name": room_name, "room_number": room_number,
                    "opens": "⚠️ (orientation unknown)", "window_name": _name_any(w, "(window)"),
                    "window_id": getattr(w, "GlobalId", ""), "angle_deg": "",
                })
                if w_mesh:
                    visualization_records["windows"].append({
                        "verts": w_mesh[0], "faces": w_mesh[1], "status": "unknown"
                    })
                found_any = True
                continue

            _, win_y_world, _ = axes
            to_win = (w_cent[0] - room_center[0], w_cent[1] - room_center[1], 0.0)
            if abs(to_win[0]) + abs(to_win[1]) < 1e-6:
                rows.append({
                    "room_name": room_name, "room_number": room_number,
                    "opens": "⚠️ (ambiguous location)", "window_name": _name_any(w, "(window)"),
                    "window_id": getattr(w, "GlobalId", ""), "angle_deg": "",
                })
                if w_mesh:
                    visualization_records["windows"].append({
                        "verts": w_mesh[0], "faces": w_mesh[1], "status": "unknown"
                    })
                found_any = True
                continue

            ang_raw = _angle_2d_deg(win_y_world, to_win)
            is_outward = not (90.0 <= ang_raw <= 270.0)

            # For windows, inward orientation is correct and compliant
            opens_text = "❌ outward (wrong)" if is_outward else "✅ inward"
            status_ok = not is_outward

            rows.append({
                "room_name": room_name, "room_number": room_number,
                "opens": opens_text, "window_name": _name_any(w, "(window)"),
                "window_id": getattr(w, "GlobalId", ""), "angle_deg": round(ang_raw, 1),
            })

            if w_mesh:
                visualization_records["windows"].append({
                    "verts": w_mesh[0], "faces": w_mesh[1],
                    "status": "ok" if status_ok else "bad"
                })
            found_any = True

        if not found_any:
            rows.append({
                "room_name": room_name, "room_number": room_number,
                "opens": "", "window_name": "(no windows inside bbox)", "window_id": "", "angle_deg": "",
            })

    # ---- Display Summary Header ----
    st.header("Summary")
    
    # Columns ordered exactly as requested: 'opens' right after 'room_number'
    df = pd.DataFrame(rows, columns=["room_name", "room_number", "opens", "window_name", "window_id", "angle_deg"])

    rooms_unique = df[["room_name", "room_number"]].drop_duplicates()
    total_rooms = len(rooms_unique)

    rooms_with_windows = (
        df.loc[~df["window_name"].str.contains("no window", case=False, na=False), ["room_name", "room_number"]]
        .drop_duplicates()
    )
    count_with_windows = len(rooms_with_windows)

    bad_rooms = (
        df.loc[df["opens"].str.contains("outward", na=False), ["room_name", "room_number"]]
        .drop_duplicates()
        .sort_values(by=["room_number", "room_name"])
    )
    bad_count = len(bad_rooms)

    rooms_no_windows = rooms_unique.merge(rooms_with_windows, how="left", indicator=True)
    rooms_no_windows = rooms_no_windows[rooms_no_windows["_merge"] == "left_only"][["room_name", "room_number"]]
    count_no_windows = len(rooms_no_windows)

    st.success(f"**{total_rooms}** classrooms found.")
    st.info(f"🪟 Windows found in **{count_with_windows} / {total_rooms}** classrooms.")

    if count_no_windows > 0:
        st.warning(f"⚠️ {count_no_windows} classroom(s) have **no windows detected**:")
        for _, r in rooms_no_windows.iterrows():
            st.write(f"- classroom number **{r['room_number']}**")

    if bad_count > 0:
        st.error(f"❌ {bad_count} classroom(s) have bad opening window direction (outward):")
        for _, r in bad_rooms.iterrows():
            st.write(f"- classroom number **{r['room_number']}**")
    else:
        st.success("✅ All detected windows open inward (Compliant).")

    # ================= 3D VISUALIZATION ENGINE =================
    if visualization_records["classrooms"] or visualization_records["windows"]:
        st.divider()
        st.subheader("📦 3D Space & Window Swing Direction Analysis")

        fig3d = go.Figure()
        tracked_legends = set()

        # Render rooms blueprint shells
        for cr in visualization_records["classrooms"]:
            fig3d.add_trace(go.Mesh3d(
                x=cr["verts"][:, 0], y=cr["verts"][:, 1], z=cr["verts"][:, 2],
                i=cr["faces"][:, 0], j=cr["faces"][:, 1], k=cr["faces"][:, 2],
                color="#ced4da", opacity=0.20,
                name="Classroom Space Layout", legendgroup="rooms",
                showlegend=("Classroom Space Layout" not in tracked_legends)
            ))
            tracked_legends.add("Classroom Space Layout")

            # Dynamic structural text labeling mapped above spaces
            lbl_x, lbl_y, lbl_z = cr["label_center"]
            fig3d.add_trace(go.Scatter3d(
                x=[lbl_x], y=[lbl_y], z=[lbl_z + 0.20],
                text=[cr["label"]], mode="text",
                textfont=dict(size=10, color="#2c3e50", weight="bold"),
                showlegend=False, legendgroup="rooms"
            ))

        # Render explicit conditional window meshes
        for wr in visualization_records["windows"]:
            if wr["status"] == "ok":
                color = "#2ecc71"  # Vibrant Green
                lg = "✅ Inward (Compliant)"
            elif wr["status"] == "bad":
                color = "#e74c3c"  # Vibrant Red
                lg = "❌ Outward (Non-Compliant)"
            else:
                color = "#f1c40f"  # Yellow
                lg = "⚠️ Unknown / Ambiguous Orientation"

            fig3d.add_trace(go.Mesh3d(
                x=wr["verts"][:, 0], y=wr["verts"][:, 1], z=wr["verts"][:, 2],
                i=wr["faces"][:, 0], j=wr["faces"][:, 1], k=wr["faces"][:, 2],
                color=color, opacity=0.95,
                name=lg, legendgroup=lg,
                showlegend=(lg not in tracked_legends)
            ))
            tracked_legends.add(lg)

        fig3d.update_layout(
            scene=dict(aspectmode='data', dragmode='orbit'),
            height=650, margin=dict(l=0, r=0, b=0, t=0),
            legend=dict(yanchor="top", y=0.99, xanchor="left", x=0.01)
        )
        st.plotly_chart(fig3d, use_container_width=True)

    # Preview Dataframe Table & CSV Export block
    st.header("")
    st.header("Preview")
    st.dataframe(df, use_container_width=True)
    if not df.empty:
        st.download_button(
            "Download CSV",
            data=df.to_csv(index=False).encode("utf-8"),
            file_name="classroom_window_opening_direction.csv",
            mime="text/csv",
        )


if __name__ == "__main__":
    st.set_page_config(page_title="Classrooms — Window Opening Direction", layout="wide")
    st.sidebar.file_uploader("Upload IFC (.ifc)", type=["ifc"], key="GLOBAL_ifc")
    run_classroom_window_outward_check()