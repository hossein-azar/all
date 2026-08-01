# window_sill_checker.py
# Usage in your app:
#   from window_sill_checker import run_classroom_window_sill_simple
#   run_classroom_window_sill_simple(ifc=ifc)

import tempfile
import pandas as pd
import numpy as np
import streamlit as st
import plotly.graph_objects as go

try:
    import ifcopenshell  # type: ignore
except Exception:
    st.error("⚠️ Please install ifcopenshell (pip install ifcopenshell)")
    st.stop()

try:
    import ifcopenshell.geom as ifcgeom  # type: ignore
    GEOM_OK = True
except Exception:
    GEOM_OK = False

# ---- Settings ----
XY_PAD = 0.8        # Slightly increased padding for robust spatial detection
Z_PAD_LOW = 0.5
Z_PAD_HIGH = 0.5
MIN_SILL_M = 1.4  # ✅ minimum acceptable sill height

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


# ---------- Public Main Frame ----------
def run_classroom_window_sill_simple(ifc=None):
    st.caption("code: 4-1-6-1")
    st.header("🪟 Classrooms Windows Sill Height Check")

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
        st.error("⚠️ ifcopenshell.geom (OCC build) is required for geometry analysis.")
        return

    classrooms = _find_classroom_spaces(ifc)
    if not classrooms:
        st.warning("No spaces named exactly 'classroom' found.")
        return

    geom_settings = _geom_settings()
    visualization_records = {"classrooms": [], "windows": []}
    rows = []

    # 🚀 FIX: Query all windows globally to bypass strict spatial relationship containment gaps
    all_windows = list(ifcopenshell.file.by_type(ifc, "IfcWindow"))

    for sp in classrooms:
        room_name   = getattr(sp, "LongName", None) or "(no room name)"
        room_number = getattr(sp, "Name", None) or "(no room number)"
        bbox = _element_bbox(sp)

        # Save room layout for 3D engine context maps
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
                "sill_height_m": "⚠️ Space geometry missing", "window_name": "", "window_id": "",
            })
            continue

        z_floor = bbox[4] # Minimum bound Z coordinate of room floor level
        found_any = False
        
        for w in all_windows:
            w_bbox = _element_bbox(w)
            if w_bbox is None:
                continue

            w_cent = ((w_bbox[0]+w_bbox[1])*0.5, (w_bbox[2]+w_bbox[3])*0.5, (w_bbox[4]+w_bbox[5])*0.5)
            if not _point_in_padded_bbox(w_cent, bbox):
                continue

            z_sill_window = w_bbox[4]
            calculated_sill = z_sill_window - z_floor
            is_compliant = (calculated_sill >= MIN_SILL_M)

            # Prefix emoji conditions dynamically based on compliance
            emoji_prefix = "✅ " if is_compliant else "❌ "
            formatted_sill_string = f"{emoji_prefix}{calculated_sill:.2f} m"

            rows.append({
                "room_name": room_name,
                "room_number": room_number,
                "sill_height_m": formatted_sill_string,
                "window_name": getattr(w, "Name", None) or "(unnamed window)",
                "window_id": getattr(w, "GlobalId", ""),
            })

            w_mesh = _get_element_mesh(w, geom_settings)
            if w_mesh:
                visualization_records["windows"].append({
                    "verts": w_mesh[0], "faces": w_mesh[1],
                    "status": "ok" if is_compliant else "bad"
                })
            found_any = True

        if not found_any:
            rows.append({
                "room_name": room_name,
                "room_number": room_number,
                "sill_height_m": "⚠️ No windows detected",
                "window_name": "",
                "window_id": "",
            })

    # Generate Structured Overview Table Dataframes
    df = pd.DataFrame(rows, columns=["room_name", "room_number", "sill_height_m", "window_name", "window_id"])

    # ---- Summary Metrics Section ----
    st.header("Summary")
    
    rooms_unique = df[["room_name", "room_number"]].drop_duplicates()
    total_rooms = len(rooms_unique)

    rooms_with_windows = (
        df.loc[~df["window_name"].str.contains("no windows|⚠️", case=False, na=False) & (df["window_name"] != ""), ["room_name", "room_number"]]
        .drop_duplicates()
    )
    count_with_windows = len(rooms_with_windows)

    # Restored metrics messages exactly as original
    st.success(f"Classrooms found: **{total_rooms}**")
    st.info(f"Windows detected in **{count_with_windows} / {total_rooms}** classrooms.")

    # Additional diagnostic notes
    bad_count = len(df[df["sill_height_m"].str.contains("❌", na=False)].drop_duplicates(subset=["room_name", "room_number"]))
    if bad_count > 0:
        st.error(f"❌ {bad_count} classroom(s) have windows below the minimum required sill height (< {MIN_SILL_M:.2f} m).")
    else:
        st.success(f"✅ All detected windows meet the minimum sill requirement (≥ {MIN_SILL_M:.2f} m).")

    # ================= 3D VISUALIZATION ENGINE =================
    if visualization_records["classrooms"] or visualization_records["windows"]:
        st.divider()
        st.subheader("📦 3D Space & Window Sill Level Breakdown")

        fig3d = go.Figure()
        tracked_legends = set()

        # Render room mesh references
        for cr in visualization_records["classrooms"]:
            fig3d.add_trace(go.Mesh3d(
                x=cr["verts"][:, 0], y=cr["verts"][:, 1], z=cr["verts"][:, 2],
                i=cr["faces"][:, 0], j=cr["faces"][:, 1], k=cr["faces"][:, 2],
                color="#ced4da", opacity=0.15,
                name="Classroom Space", legendgroup="rooms",
                showlegend=("Classroom Space" not in tracked_legends)
            ))
            tracked_legends.add("Classroom Space")

            # Dynamic Text labeling centered on room tops
            lx, ly, lz = cr["label_center"]
            fig3d.add_trace(go.Scatter3d(
                x=[lx], y=[ly], z=[lz + 0.25],
                text=[cr["label"]], mode="text",
                textfont=dict(size=10, color="#2c3e50", weight="bold"),
                showlegend=False, legendgroup="rooms"
            ))

        # Render window elements conditionally
        for wr in visualization_records["windows"]:
            if wr["status"] == "ok":
                color = "#2ecc71" # Vibrant Green
                lg = f"✅ Compliant Sill (≥ {MIN_SILL_M:.2f}m)"
            else:
                color = "#e74c3c" # Vibrant Red
                lg = f"❌ Low Sill (< {MIN_SILL_M:.2f}m)"

            fig3d.add_trace(go.Mesh3d(
                x=wr["verts"][:, 0], y=wr["verts"][:, 1], z=wr["verts"][:, 2],
                i=wr["faces"][:, 0], j=wr["faces"][:, 1], k=wr["faces"][:, 2],
                color=color, opacity=0.9,
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

    # ---- Table Preview + CSV Download ----
    st.divider()
    st.subheader("📋 Verification Data Preview")
    st.dataframe(df, use_container_width=True)

    if not df.empty:
        st.download_button(
            "Download CSV Report",
            data=df.to_csv(index=False).encode("utf-8"),
            file_name="classroom_window_sills.csv",
            mime="text/csv",
        )


if __name__ == "__main__":
    st.set_page_config(page_title="Classrooms — Windows Sill Height", layout="wide")
    st.sidebar.file_uploader("Upload IFC (.ifc)", type=["ifc"], key="GLOBAL_ifc")
    run_classroom_window_sill_simple()