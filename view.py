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
        
        # A curated palette of distinct shades of green/teal for multiple room types
        self.green_palette = [
            "#2ecc71", "#27ae60", "#1abc9c", "#16a085", 
            "#a3e4d7", "#48c9b0", "#52be80", "#1e8449",
            "#117a65", "#a9dfbf", "#229954", "#138d75"
        ]

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

    def safe_by_type(self, model, ifc_class):
        """Safely fetches elements by type, returning an empty list if the type doesn't exist in the schema."""
        try:
            return model.by_type(ifc_class)
        except:
            return []

    def run(self, uploaded_ifc=None):
        st.subheader("🏢 IFC Spatial Model Explorer (3D & 2D)")

        # Fallback to local file uploader if no global file is supplied
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
            storeys = sorted(self.safe_by_type(model, "IfcBuildingStorey"), key=lambda s: s.Elevation if s.Elevation else 0)
            storey_map = {s.Name: float(s.Elevation if s.Elevation else 0) for s in storeys}
            
            all_spaces = self.safe_by_type(model, "IfcSpace")
            unique_room_types = sorted(list(set((s.LongName or s.Name or "Unknown").lower() for s in all_spaces)))

            # 2. Control UI Layout Controls
            st.write("### 🎯 Filter Options")
            col_sel1, col_sel2 = st.columns(2)
            with col_sel1:
                storey_options = ["All Levels"] + list(storey_map.keys())
                selected_level = st.selectbox("Select Floor Level", options=storey_options, key="viewer_floor_sel")
            with col_sel2:
                selected_rooms = st.multiselect(
                    "Select Room Types to Highlight", 
                    options=unique_room_types,
                    default=[r for r in unique_room_types if "classroom" in r or "office" in r][:1],
                    key="viewer_room_sel"
                )
            
            # --- Toggle for details ---
            show_details = st.toggle("Show Details", value=False, help="Enable borders around rooms, and show stairs, landings, doors, and windows.")

            # Generate dynamic shade-of-green dictionary for selected types
            color_mapping = {}
            for idx, r_type in enumerate(selected_rooms):
                color_mapping[r_type] = self.green_palette[idx % len(self.green_palette)]

            # 3. Process Spatial Geometries and Allocations
            processed_rooms = []
            room_counts = {r_type: 0 for r_type in selected_rooms}
            
            for space in all_spaces:
                r_type = (space.LongName or space.Name or "Unknown").lower()
                r_num = space.Name or "N/A"
                
                geom = self.get_element_geometry(space)
                if not geom: continue
                
                floor = self.find_closest_floor(geom["center"][2], storey_map)
                
                # Floor filter guard rails
                if selected_level != "All Levels" and floor != selected_level:
                    continue
                    
                is_highlighted = r_type in selected_rooms
                if is_highlighted:
                    room_counts[r_type] += 1

                processed_rooms.append({
                    "object": space,
                    "type": r_type,
                    "number": r_num,
                    "floor": floor,
                    "geom": geom,
                    "highlighted": is_highlighted
                })

            # 4. Render 3D Model View Tab
            st.write("#### 📦 3D Model Interactive Visualization")
            fig3d = go.Figure()

            # Handle 3D Legend Tracking to tie trace visibility collections into clean groups
            tracked_3d_legends = set()

            for rm in processed_rooms:
                v = rm["geom"]["verts"]
                f = rm["geom"]["faces"]
                edges = rm["geom"]["edges"]
                full_label = f"{rm['type'].upper()} ({rm['number']})"
                
                color = color_mapping[rm["type"]] if rm["highlighted"] else "#E5E7E9"
                opacity = 0.7 if rm["highlighted"] else 0.15
                
                show_in_legend = False
                legend_group_name = "Other Spaces"
                if rm["highlighted"]:
                    legend_group_name = rm["type"].title()
                    if legend_group_name not in tracked_3d_legends:
                        show_in_legend = True
                        tracked_3d_legends.add(legend_group_name)

                # Dynamically construct keyword arguments to support conditional contour properties
                mesh_kwargs = {
                    "x": v[:, 0], "y": v[:, 1], "z": v[:, 2],
                    "i": f[:, 0], "j": f[:, 1], "k": f[:, 2],
                    "color": color, "opacity": opacity, 
                    "name": legend_group_name,
                    "legendgroup": legend_group_name,
                    "showlegend": show_in_legend,
                    "lighting": dict(ambient=0.6, diffuse=0.5)
                }

                fig3d.add_trace(go.Mesh3d(**mesh_kwargs))

                # Inject strict explicit 3D wireframe borders around rooms if details toggle is enabled
                if show_details:
                    edge_x, edge_y, edge_z = [], [], []
                    for i in range(0, len(edges), 2):
                        p1, p2 = v[edges[i]], v[edges[i+1]]
                        edge_x.extend([p1[0], p2[0], None])
                        edge_y.extend([p1[1], p2[1], None])
                        edge_z.extend([p1[2], p2[2], None])
                    
                    fig3d.add_trace(go.Scatter3d(
                        x=edge_x, y=edge_y, z=edge_z,
                        mode="lines",
                        line=dict(color="#000000", width=2),
                        legendgroup=legend_group_name,
                        showlegend=False,
                        hoverinfo="skip"
                    ))

                # Inject room tracking labels inside 3D space matrix
                if rm["highlighted"] or selected_level != "All Levels":
                    fig3d.add_trace(go.Scatter3d(
                        x=[rm["geom"]["center"][0]], 
                        y=[rm["geom"]["center"][1]], 
                        z=[np.max(v[:, 2]) + 0.1],
                        text=[full_label], mode="text",
                        textfont=dict(size=9, color="black"),
                        legendgroup=legend_group_name,
                        showlegend=False
                    ))

            # --- 3D Context Injector for Stairs, Landings, Doors, Windows ---
            if show_details:
                detail_elements = [
                    ("IfcStair", "#2c3e50", 0.7, "Stairs & Landings"),
                    ("IfcStairFlight", "#2c3e50", 0.7, "Stairs & Landings"),
                    ("IfcLanding", "#34495e", 0.7, "Stairs & Landings"),
                    ("IfcDoor", "#d35400", 0.6, "Doors & Windows"),
                    ("IfcWindow", "#2980b9", 0.4, "Doors & Windows")
                ]
                
                for ifc_class, color, opacity, group_name in detail_elements:
                    elements = self.safe_by_type(model, ifc_class)
                    show_group_legend = group_name not in tracked_3d_legends
                    if show_group_legend and elements:
                        tracked_3d_legends.add(group_name)
                        
                    for elem in elements:
                        e_geom = self.get_element_geometry(elem)
                        if e_geom:
                            floor = self.find_closest_floor(e_geom["center"][2], storey_map)
                            if selected_level != "All Levels" and floor != selected_level:
                                continue
                            
                            ev = e_geom["verts"]
                            ef = e_geom["faces"]
                            fig3d.add_trace(go.Mesh3d(
                                x=ev[:, 0], y=ev[:, 1], z=ev[:, 2],
                                i=ef[:, 0], j=ef[:, 1], k=ef[:, 2],
                                color=color, opacity=opacity,
                                name=group_name,
                                legendgroup=group_name,
                                showlegend=show_group_legend
                            ))
                            show_group_legend = False

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
                st.write("#### 📊 Match Target Aggregates")
                if selected_rooms:
                    for r_type, count in room_counts.items():
                        st.metric(label=f"Found: '{r_type.title()}'", value=count)
                else:
                    st.write("No space categories currently targeted for matching.")

            with col_table:
                st.write("#### 📋 Targeted Rooms Spatial Registry")
                matched_records = [
                    {"Floor Level": rm["floor"], "Room Type": rm["type"].title(), "Room Number": rm["number"]}
                    for rm in processed_rooms if rm["highlighted"]
                ]
                if matched_records:
                    st.dataframe(pd.DataFrame(matched_records), use_container_width=True, hide_index=True)
                else:
                    st.caption("No dynamic matches found in current structural view criteria.")

            # 6. Render Isolated 2D Section View Tab inside a bounded view frame
            st.divider()
            st.write("#### 📐 Architectural 2D Plan Projections")
            
            plan_level = selected_level if selected_level != "All Levels" else list(storey_map.keys())[0]
            st.caption(f"Showing localized extraction for **{plan_level}**.")

            with st.spinner(f"Slicing 2D geometric projections for {plan_level}..."):
                # Use a smaller, standardized figure size footprint
                fig2d, ax = plt.subplots(figsize=(7, 5))
                
                # Isolate and draw spatial structural boundaries
                walls = self.safe_by_type(model, "IfcWall")
                for wall in walls:
                    w_geom = self.get_element_geometry(wall)
                    if w_geom and self.find_closest_floor(w_geom["center"][2], storey_map) == plan_level:
                        for line in self.get_2d_lines(w_geom):
                            ax.plot([line[0][0], line[1][0]], [line[0][1], line[1][1]], color="#2C3E50", lw=1.0)

                # --- 2D Context Extractions for Toggle Elements ---
                if show_details:
                    # Configuration map for details style on 2D layout: (IFC class, Line Color, Line Width)
                    detail_2d_configs = [
                        ("IfcStair", "#4A4A4A", 1.2),
                        ("IfcStairFlight", "#4A4A4A", 1.2),  # Pale Black
                        ("IfcLanding", "#555555", 1.0),      # Pale Black
                        ("IfcDoor", "#E67E22", 1.0),         # Distinct Brownish Orange for clarity
                        ("IfcWindow", "#3498DB", 1.0)        # Light Blue for glass structures
                    ]
                    for ifc_class, line_color, line_width in detail_2d_configs:
                        elements = self.safe_by_type(model, ifc_class)
                        for elem in elements:
                            e_geom = self.get_element_geometry(elem)
                            if e_geom and self.find_closest_floor(e_geom["center"][2], storey_map) == plan_level:
                                for line in self.get_2d_lines(e_geom):
                                    ax.plot([line[0][0], line[1][0]], [line[0][1], line[1][1]], color=line_color, lw=line_width)

                for rm in processed_rooms:
                    if rm["floor"] == plan_level:
                        # Draw 2D Room Boundaries if detail toggle is on
                        if show_details:
                            for line in self.get_2d_lines(rm["geom"]):
                                ax.plot([line[0][0], line[1][0]], [line[0][1], line[1][1]], color="#000000", lw=0.8, linestyle="--")

                        # Clean and empty plain text markers—no shading fills, no legends
                        ax.text(
                            rm["geom"]["center"][0], rm["geom"]["center"][1], 
                            f"{rm['type'].upper()}\n#{rm['number']}", 
                            fontsize=5, ha="center", va="center",
                            bbox=dict(boxstyle="round,pad=0.2", fc="white", alpha=0.8, ec="none")
                        )

                ax.set_aspect("equal")
                ax.axis("off")

                # Put the rendering output in a strict visually contained box layout
                with st.container(border=True):
                    col_center_plan, _ = st.columns([2, 1])
                    with col_center_plan:
                        st.pyplot(fig2d, use_container_width=False)

                # JPEG Export Channel
                buf = BytesIO()
                fig2d.savefig(buf, format="jpg", dpi=150, bbox_inches='tight')
                st.download_button(
                    label=f"📥 Download {plan_level} Floor Plan Layout (JPEG)",
                    data=buf.getvalue(),
                    file_name=f"Floor_Plan_{plan_level}.jpg",
                    mime="image/jpeg"
                )

        except Exception as e:
            st.error(f"Execution pipeline halted: {str(e)}")
        finally:
            if 'tmp_path' in locals() and os.path.exists(tmp_path):
                os.remove(tmp_path)

if __name__ == "__main__":
    # Standard standalone app execution setup
    st.set_page_config(page_title="IFC Multi-Dimensional Explorer", layout="wide")
    app = IntegratedIFCViewer()
    app.run()