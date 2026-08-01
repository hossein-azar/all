import streamlit as st
import ifcopenshell
import ifcopenshell.geom
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import matplotlib.pyplot as plt
import tempfile
import os
from io import BytesIO

class IntegratedIFCViewer:
    def __init__(self):
        self.settings = ifcopenshell.geom.settings()
        self.settings.set(self.settings.USE_WORLD_COORDS, True)
        
        # Dedicated colors for our structural classification
        self.stair_color = "#2ecc71"  # Vibrant Green
        self.room_color = "#E5E7E9"   # Neutral Light Gray

    def get_element_geometry(self, element):
        """Extracts vertices, faces, and bounding box data for 3D/2D processing."""
        try:
            if element.Representation:
                shape = ifcopenshell.geom.create_shape(self.settings, element)
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

    def get_2d_lines(self, geom_data):
        """Extracts individual 2D line segments to prevent overlapping artifacts."""
        if not geom_data or "shape" not in geom_data: return []
        verts = geom_data["verts"]
        edges = geom_data["edges"]
        lines = []
        for i in range(0, len(edges), 2):
            p1 = verts[edges[i]]
            p2 = verts[edges[i+1]]
            lines.append([(p1[0], p1[1]), (p2[0], p2[1])])
        return lines

    def find_closest_floor(self, z_val, storey_map):
        """Matches a Z-coordinate to the nearest building storey to prevent multi-floor blending."""
        if z_val is None or not storey_map: return "Unknown"
        sorted_elevs = sorted(storey_map.items(), key=lambda x: x[1])
        for i, (name, elev) in enumerate(sorted_elevs):
            next_elev = sorted_elevs[i+1][1] if i+1 < len(sorted_elevs) else elev + 5.0
            if elev <= z_val < next_elev: 
                return name
        return min(storey_map.keys(), key=lambda n: abs(z_val - storey_map[n]))

    def run(self, uploaded_ifc=None):
        st.subheader("🏢 IFC Spatial Model Explorer (Stairs, Landings & Rooms)")

        if not uploaded_ifc:
            uploaded_file = st.file_uploader("Upload IFC File", type=["ifc"], key="standalone_viewer_ifc")
        else:
            uploaded_file = uploaded_ifc

        if not uploaded_file:
            st.info("Please upload an IFC model configuration file to begin analysis.")
            return

        with tempfile.NamedTemporaryFile(delete=False, suffix=".ifc") as tmp:
            tmp.write(uploaded_file.getbuffer())
            tmp_path = tmp.name

        try:
            model = ifcopenshell.open(tmp_path)
            
            # 1. Parse Levels & Organize Spatial Maps
            storeys = sorted(model.by_type("IfcBuildingStorey"), key=lambda s: s.Elevation if s.Elevation else 0)
            storey_map = {s.Name: float(s.Elevation if s.Elevation else 0) for s in storeys}
            
            # 2. Control UI Layout Controls
            st.write("### 🎯 Filter Options")
            storey_options = ["All Levels"] + list(storey_map.keys())
            selected_level = st.selectbox("Select Floor Level", options=storey_options, key="viewer_floor_sel")

            # 3. Process Geometries (Rooms, Stairs, and Landings)
            processed_elements = []
            stair_count = 0
            landing_count = 0
            room_count = 0
            
            # Extract Rooms
            for space in model.by_type("IfcSpace"):
                geom = self.get_element_geometry(space)
                if not geom: continue
                
                floor = self.find_closest_floor(geom["center"][2], storey_map)
                if selected_level != "All Levels" and floor != selected_level:
                    continue
                
                room_count += 1
                processed_elements.append({
                    "name": space.Name or "Room Space",
                    "category": "Room",
                    "floor": floor,
                    "geom": geom,
                    "color": self.room_color,
                    "opacity": 0.20
                })

            # Extract Stair Flights and Stair Assemblies
            stair_elements = model.by_type("IfcStair") + model.by_type("IfcStairFlight")
            for stair in stair_elements:
                geom = self.get_element_geometry(stair)
                if not geom: continue
                
                floor = self.find_closest_floor(geom["center"][2], storey_map)
                if selected_level != "All Levels" and floor != selected_level:
                    continue
                
                stair_count += 1
                processed_elements.append({
                    "name": stair.Name or "Staircase Run",
                    "category": "Stair",
                    "floor": floor,
                    "geom": geom,
                    "color": self.stair_color,
                    "opacity": 0.85
                })

            # Extract Landings (Usually categorized as IfcSlab with PredefinedType='LANDING')
            for slab in model.by_type("IfcSlab"):
                # Safely check for predefined type attribute
                predefined_type = getattr(slab, "PredefinedType", None)
                if predefined_type == "LANDING" or "landing" in (slab.Name or "").lower():
                    geom = self.get_element_geometry(slab)
                    if not geom: continue
                    
                    floor = self.find_closest_floor(geom["center"][2], storey_map)
                    if selected_level != "All Levels" and floor != selected_level:
                        continue
                    
                    landing_count += 1
                    processed_elements.append({
                        "name": slab.Name or "Stair Landing",
                        "category": "Landing",
                        "floor": floor,
                        "geom": geom,
                        "color": self.stair_color,  # Keep the same green color theme
                        "opacity": 0.85
                    })

            # 4. Render 3D Model View Tab
            st.write("#### 📦 3D Model Interactive Visualization")
            fig3d = go.Figure()
            tracked_legends = set()

            for el in processed_elements:
                v = el["geom"]["verts"]
                f = el["geom"]["faces"]
                
                show_in_legend = False
                legend_group = "Stair Components" if el["category"] in ["Stair", "Landing"] else "Rooms (Context)"
                
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

                # Explicit labels for vertical circulation paths
                if el["category"] in ["Stair", "Landing"]:
                    fig3d.add_trace(go.Scatter3d(
                        x=[el["geom"]["center"][0]], 
                        y=[el["geom"]["center"][1]], 
                        z=[np.max(v[:, 2]) + 0.15],
                        text=[el["name"]], mode="text",
                        textfont=dict(size=9, color="green"),
                        legendgroup=legend_group,
                        showlegend=False
                    ))

            fig3d.update_layout(
                scene=dict(aspectmode='data', dragmode='orbit'), 
                height=650, 
                margin=dict(l=0, r=0, b=0, t=0),
                legend=dict(yanchor="top", y=0.99, xanchor="left", x=0.01)
            )
            st.plotly_chart(fig3d, use_container_width=True)

            # 5. Quantification Inventory Summaries
            st.divider()
            col_metrics, col_table = st.columns([1, 2])
            
            with col_metrics:
                st.write("#### 📊 Target Aggregates")
                st.metric(label="Stair Runs Detected", value=stair_count)
                st.metric(label="Intermediate Landings", value=landing_count)
                st.metric(label="Room Environments Bound", value=room_count)

            with col_table:
                st.write("#### 📋 Structural Inventory Registry")
                matched_records = [
                    {"Floor Level": el["floor"], "Component Class": el["category"], "Designation": el["name"]}
                    for el in processed_elements
                ]
                if matched_records:
                    st.dataframe(pd.DataFrame(matched_records), use_container_width=True, hide_index=True)
                else:
                    st.caption("No visible building elements match current selection matrix.")

            # 6. Render Isolated 2D Section View Tab
            st.divider()
            st.write("#### 📐 Architectural 2D Plan Projections")
            
            plan_level = selected_level if selected_level != "All Levels" else list(storey_map.keys())[0]
            st.caption(f"Showing localized framework footprint extraction for **{plan_level}**.")

            with st.spinner(f"Slicing 2D structural layout for {plan_level}..."):
                fig2d, ax = plt.subplots(figsize=(7, 5))
                
                # Draw structural walls as background frame context
                walls = model.by_type("IfcWall")
                for wall in walls:
                    w_geom = self.get_element_geometry(wall)
                    if w_geom and self.find_closest_floor(w_geom["center"][2], storey_map) == plan_level:
                        for line in self.get_2d_lines(w_geom):
                            ax.plot([line[0][0], line[1][0]], [line[0][1], line[1][1]], color="#BDC3C7", lw=0.8, alpha=0.5)

                # Overlay structural circulation profiles on 2D map
                for el in processed_elements:
                    if el["floor"] == plan_level:
                        is_circulation = el["category"] in ["Stair", "Landing"]
                        text_color = "#27ae60" if is_circulation else "#7F8C8D"
                        font_size = 6 if is_circulation else 4
                        
                        ax.text(
                            el["geom"]["center"][0], el["geom"]["center"][1], 
                            f"{el['name']}", 
                            fontsize=font_size, color=text_color, ha="center", va="center",
                            bbox=dict(boxstyle="round,pad=0.1", fc="white", alpha=0.8, ec="none")
                        )

                ax.set_aspect("equal")
                ax.axis("off")

                with st.container(border=True):
                    col_center_plan, _ = st.columns([2, 1])
                    with col_center_plan:
                        st.pyplot(fig2d, use_container_width=False)

                # Export Layout
                buf = BytesIO()
                fig2d.savefig(buf, format="jpg", dpi=150, bbox_inches='tight')
                st.download_button(
                    label=f"📥 Download {plan_level} Structural Layout (JPEG)",
                    data=buf.getvalue(),
                    file_name=f"Circulation_Layout_{plan_level}.jpg",
                    mime="image/jpeg"
                )

        except Exception as e:
            st.error(f"Execution pipeline halted: {str(e)}")
        finally:
            if 'tmp_path' in locals() and os.path.exists(tmp_path):
                os.remove(tmp_path)

if __name__ == "__main__":
    st.set_page_config(page_title="IFC Stair & Spatial Explorer", layout="wide")
    app = IntegratedIFCViewer()
    app.run()