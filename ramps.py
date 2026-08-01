import streamlit as st
import ifcopenshell
import ifcopenshell.geom
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import tempfile
import os

# =====================================================
# HARDCODED REGULATORY LIMITS (UPDATED FOR RISE/RUN)
# =====================================================
MIN_WIDTH = 1.20      # m (Should be ≥ 1.20m)
MAX_RUN = 8.00        # m (Should be ≤ 8.00m)

# For Rise/Run: 1:5 is 0.20 (steeper limit), 1:8 is 0.125 (gentler limit)
MAX_SLOPE_RISE_RUN = 1 / 5.0  # 0.20
MIN_SLOPE_RISE_RUN = 1 / 8.0  # 0.125


def _open_ifc_from_any(obj):
    """Open IFC safely from various input types following the working template."""
    if obj is None:
        return None
    # already-opened IFC
    if hasattr(obj, "by_type") and hasattr(obj, "schema"):
        return obj

    # UploadedFile or bytes
    data = None
    if hasattr(obj, "getvalue"):
        try:
            data = obj.getvalue()
        except Exception:
            pass
    if data is None and hasattr(obj, "read"):
        try:
            data = obj.read()
        except Exception:
            pass
    if isinstance(obj, (bytes, bytearray)):
        data = bytes(obj)

    if data:
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".ifc") as tmp:
                tmp.write(data)
                tmp.flush()
                return ifcopenshell.open(tmp.name)
        except Exception:
            return None

    # Path-like
    try:
        return ifcopenshell.open(str(obj))
    except Exception:
        return None


# =====================================================
# COMPLIANCE ENGINE FOR RAMP FLIGHTS
# =====================================================
def analyze_ramp_flight(element, settings):
    """Calculates geometry metrics and flags compliance failures based on Rise/Run slope."""
    try:
        shape = ifcopenshell.geom.create_shape(settings, element)
        verts = np.array(shape.geometry.verts).reshape(-1, 3)
        faces = np.array(shape.geometry.faces).reshape(-1, 3)
    except Exception as e:
        return {"Status": "GEOMETRY ERROR", "Notes": f"Could not parse shape: {str(e)}"}

    if len(verts) < 3:
        return {"Status": "GEOMETRY ERROR", "Notes": "Insufficient vertices."}

    xmin, ymin, zmin = np.min(verts, axis=0)
    xmax, ymax, zmax = np.max(verts, axis=0)

    xdim = xmax - xmin
    ydim = ymax - ymin
    
    width = min(xdim, ydim)
    run = max(xdim, ydim)
    rise = zmax - zmin

    # Calculate Slope as Rise / Run
    slope_value = rise / run if run > 0 else 0.0
    
    # Format as standard 1:X ratio for structural legibility
    if slope_value > 0:
        inverse_run = round(1 / slope_value, 2)
        slope_ratio_str = f"1:{inverse_run} ({round(slope_value * 100, 1)}%)"
    else:
        slope_ratio_str = "Flat (0%)"

    # Boolean Checks
    width_pass = width >= MIN_WIDTH
    run_pass = run <= MAX_RUN
    # Slope passes if it sits within the allowable bounds (e.g., between 1:8 and 1:5)
    ratio_pass = MIN_SLOPE_RISE_RUN <= slope_value <= MAX_SLOPE_RISE_RUN if rise > 0 else False
    overall_pass = width_pass and run_pass and ratio_pass

    failures = []
    if not width_pass:
        failures.append(f"Width {width:.2f}m < Req {MIN_WIDTH}m")
    if not run_pass:
        failures.append(f"Run {run:.2f}m > Max {MAX_RUN}m")
    if not ratio_pass:
        failures.append(f"Slope {slope_ratio_str} outside acceptable range (1:8 to 1:5)")

    return {
        "Status": "✅ PASS" if overall_pass else "❌ FAIL",
        "Width Value": f"{round(width, 2)}m {'✅' if width_pass else '❌'}",
        "Run Value": f"{round(run, 2)}m {'✅' if run_pass else '❌'}",
        "Rise Value": f"{round(rise, 2)}m",
        "Slope Ratio Value": f"{slope_ratio_str} {'✅' if ratio_pass else '❌'}",
        "Reasons/Notes": "Compliant" if overall_pass else " | ".join(failures),
        "verts": verts,
        "faces": faces,
        "center_z_top": (xmin + xdim/2, ymin + ydim/2, zmax + 0.3)
    }


# =====================================================
# MAIN TAB FUNCTION
# =====================================================
def run_ramp_compliance_check(ifc=None):
    st.caption("code: 4-1-11-2-11, 4-1-11-2-12, 4-1-11-2-13")
    st.header("🏛️ Ramp Flight Compliance Auditor")

    # Independent Check: If no external IFC object is passed, look for a local or session file
    if ifc is None:
        if "GLOBAL_ifc" in st.session_state and st.session_state["GLOBAL_ifc"] is not None:
            ifc = st.session_state["GLOBAL_ifc"]
        else:
            # Local fallback uploader so the script can run completely standalone
            ifc = st.file_uploader("Upload an IFC file (.ifc) to begin analysis", type=["ifc"], key="local_ramp_ifc")

    # Safely load the incoming/uploaded IFC object 
    ifc = _open_ifc_from_any(ifc)

    if ifc is None:
        st.info("💡 Please upload an IFC file above to start the compliance analysis.")
        return

    try:
        settings = ifcopenshell.geom.settings()
        if hasattr(settings, "USE_WORLD_COORDS"):
            settings.set(settings.USE_WORLD_COORDS, True)
        elif hasattr(settings, "USE_WORLD_COORDINATES"):
            settings.set(settings.USE_WORLD_COORDINATES, True)

        fig = go.Figure()
        report_data = []

        # 1. RENDER ALL ROOMS / SPACES AS BACKGROUND CONTEXT
        rooms = ifc.by_type("IfcSpace")
        for room in rooms:
            try:
                r_type = room.LongName or "Room"
                r_num = room.Name or "N/A"
                full_name = f"{r_type} ({r_num})"
                
                shape = ifcopenshell.geom.create_shape(settings, room)
                verts = np.array(shape.geometry.verts).reshape(-1, 3)
                faces = np.array(shape.geometry.faces).reshape(-1, 3)
                
                fig.add_trace(go.Mesh3d(
                    x=verts[:, 0], y=verts[:, 1], z=verts[:, 2],
                    i=faces[:, 0], j=faces[:, 1], k=faces[:, 2],
                    color="#E5E7E9", 
                    opacity=0.12, 
                    name=full_name,
                    showlegend=False,
                    hoverinfo="skip"
                ))
            except:
                continue

        # 2. ISOLATE, AUDIT, AND NUMBER ONLY IFCRAMPFLIGHTS
        ramp_flights = ifc.by_type("IfcRampFlight")
        
        for idx, element in enumerate(ramp_flights):
            ramp_idx_num = idx + 1
            name = getattr(element, "Name", f"Flight")
            label_id = f"#{ramp_idx_num}"
            guid = element.GlobalId
            
            metrics = analyze_ramp_flight(element, settings)
            status_indicator = metrics["Status"]

            if "verts" in metrics:
                mesh_color = "#2ecc71" if status_indicator == "✅ PASS" else "#e74c3c"
                
                # Render 3D Mesh
                fig.add_trace(go.Mesh3d(
                    x=metrics["verts"][:, 0], y=metrics["verts"][:, 1], z=metrics["verts"][:, 2],
                    i=metrics["faces"][:, 0], j=metrics["faces"][:, 1], k=metrics["faces"][:, 2],
                    color=mesh_color,
                    opacity=0.95,
                    name=f"Ramp {label_id}: {name} ({status_indicator})"
                ))

                # Render Text Label over the Ramp Flight in the viewport
                cx, cy, cz = metrics["center_z_top"]
                fig.add_trace(go.Scatter3d(
                    x=[cx], y=[cy], z=[cz],
                    text=[f"<b>{label_id}</b>"],
                    mode="text",
                    textfont=dict(size=14, color="black"),
                    showlegend=False
                ))

            report_data.append({
                "Ramp ID": label_id,
                "Element Name": name,
                "Compliance Status": status_indicator,
                "Width (m)": metrics.get("Width Value"),
                "Run Length (m)": metrics.get("Run Value"),
                "Rise Height (m)": metrics.get("Rise Value"),
                "Slope Ratio (Rise/Run)": metrics.get("Slope Ratio Value"),
                "Audit Observations / Failures": metrics.get("Reasons/Notes"),
                "Global ID": guid
            })

        # Render 3D Chart Canvas
        st.subheader("📦 Model Visualization Workspace")
        st.markdown("**Color Codes:** 🟢 Green Ramp = Passed | 🔴 Red Ramp = Failed (Numbered IDs matched below)")
        
        fig.update_layout(
            scene=dict(aspectmode='data', dragmode='orbit'),
            height=750,
            margin=dict(l=0, r=0, b=0, t=0)
        )
        st.plotly_chart(fig, use_container_width=True)

        # Render Regulatory Audit Table Summary
        st.subheader("📋 Ramps Compliance Audit Report")
        if report_data:
            df_summary = pd.DataFrame(report_data)
            col_order = [
                "Ramp ID", 
                "Element Name", 
                "Compliance Status", 
                "Width (m)", 
                "Run Length (m)", 
                "Rise Height (m)", 
                "Slope Ratio (Rise/Run)", 
                "Audit Observations / Failures", 
                "Global ID"
            ]
            st.dataframe(df_summary[col_order], use_container_width=True, hide_index=True)
            
            st.download_button(
                label="📥 Download Audit CSV",
                data=df_summary[col_order].to_csv(index=False).encode('utf-8'),
                file_name="IfcRampFlight_Compliance_Report.csv",
                mime="text/csv"
            )
        else:
            st.warning("No `IfcRampFlight` instances detected inside this IFC file structure.")

    except Exception as e:
        st.error(f"Critical execution error: {str(e)}")


if __name__ == "__main__":
    st.set_page_config(page_title="Ramp Flight Compliance Auditor", layout="wide")
    # This sidebar entry is left here for integration compatibility, but fallback works inside the app framework seamlessly
    st.sidebar.file_uploader("Upload IFC (.ifc)", type=["ifc"], key="GLOBAL_ifc")
    run_ramp_compliance_check()