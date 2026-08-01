import streamlit as st
import ifcopenshell
import ifcopenshell.geom
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.patches as patches
import tempfile
import os
import pandas as pd
from io import BytesIO

class IFCAnalyzer:
    def __init__(self):
        self.settings = ifcopenshell.geom.settings()
        self.settings.set(self.settings.USE_WORLD_COORDS, True)

    def get_2d_lines(self, shape):
        """Extracts individual 2D line segments to prevent 'black scratches'."""
        if not shape: return []
        verts = np.array(shape.geometry.verts).reshape(-1, 3)
        edges = shape.geometry.edges
        lines = []
        for i in range(0, len(edges), 2):
            p1 = verts[edges[i]]
            p2 = verts[edges[i+1]]
            lines.append([(p1[0], p1[1]), (p2[0], p2[1])])
        return lines

    def get_element_data(self, element):
        """Extracts geometry and bounding box info for an IFC element."""
        try:
            if element.Representation:
                shape = ifcopenshell.geom.create_shape(self.settings, element)
                verts = np.array(shape.geometry.verts).reshape(-1, 3)
                z_mean = verts[:, 2].mean()
                x_min, x_max = verts[:, 0].min(), verts[:, 0].max()
                y_min, y_max = verts[:, 1].min(), verts[:, 1].max()
                return (x_min+x_max)/2, (y_min+y_max)/2, z_mean, shape, (x_min, y_min, x_max, y_max)
        except: pass
        return None, None, None, None, None

    def find_closest_floor(self, z_val, storey_map):
        """Matches a Z-coordinate to the nearest building storey."""
        if z_val is None: return None
        sorted_elevs = sorted(storey_map.items(), key=lambda x: x[1])
        for i, (name, elev) in enumerate(sorted_elevs):
            next_elev = sorted_elevs[i+1][1] if i+1 < len(sorted_elevs) else elev + 5.0
            if elev <= z_val < next_elev: return name
        return min(storey_map.keys(), key=lambda n: abs(z_val - storey_map[n]))

    def run(self, uploaded_ifc):
        """Main rendering function to be called within a Streamlit tab."""
        if not uploaded_ifc:
            st.info("Upload an IFC file to begin accessibility analysis.")
            return

        # Create temporary file to read with ifcopenshell
        with tempfile.NamedTemporaryFile(delete=False, suffix=".ifc") as tmp:
            tmp.write(uploaded_ifc.getbuffer())
            tmp_path = tmp.name

        try:
            ifc = ifcopenshell.open(tmp_path)
            
            # 1. Map Storeys and extract all unique names
            storeys = sorted(ifc.by_type("IfcBuildingStorey"), key=lambda s: s.Elevation if s.Elevation else 0)
            storey_map = {s.Name: float(s.Elevation if s.Elevation else 0) for s in storeys}
            
            all_spaces = ifc.by_type("IfcSpace")
            unique_room_names = sorted(list(set((s.LongName or s.Name or "Unknown").lower() for s in all_spaces)))

            # 2. UI Layout for the Tab
            st.header("🏗️ Accessibility Viewer")
            col_ctrl, col_plan = st.columns([1, 3])
            
            with col_ctrl:
                st.subheader("Plan Settings")
                selected_level = st.selectbox("1. Select Floor", list(storey_map.keys()))
                
                # Default to 'classroom' if available
                default_selection = [r for r in unique_room_names if "classroom" in r]
                selected_targets = st.multiselect(
                    "2. Highlight Room Types", 
                    options=unique_room_names, 
                    default=default_selection
                )
                st.caption("Note: Selected rooms are colored (green/red) based on elevator access.")

            # 3. Process Accessibility Logic
            full_report = {name: {"has_elevator": False, "rooms": []} for name in storey_map.keys()}
            for space in all_spaces:
                _, _, z, _, _ = self.get_element_data(space)
                floor = self.find_closest_floor(z, storey_map)
                name = (space.LongName or space.Name or "Unknown").lower()
                
                if "elevator" in name or "asansor" in name:
                    full_report[floor]["has_elevator"] = True
                if name in selected_targets:
                    full_report[floor]["rooms"].append(name)

            # 4. Drawing Logic
            with col_plan:
                st.subheader(f"Plan View: {selected_level}")
                
                with st.spinner(f"Generating architectural view for {selected_level}..."):
                    fig, ax = plt.subplots(figsize=(12, 10))
                    has_elev = full_report[selected_level]["has_elevator"]
                    
                    # Draw Walls for current floor
                    walls = ifc.by_type("IfcWall")
                    for wall in walls:
                        _, _, z, shape, _ = self.get_element_data(wall)
                        if self.find_closest_floor(z, storey_map) == selected_level:
                            for line in self.get_2d_lines(shape):
                                ax.plot([line[0][0], line[1][0]], [line[0][1], line[1][1]], color="#333", lw=1)

                    # Draw Spaces
                    for space in all_spaces:
                        x, y, z, _, b = self.get_element_data(space)
                        if self.find_closest_floor(z, storey_map) == selected_level:
                            s_name = (space.LongName or space.Name or "Unknown").lower()
                            is_elev = "elevator" in s_name or "asansor" in s_name
                            
                            # Determine fill color
                            color = None
                            if is_elev: 
                                color = "#90EE90" # Green
                            elif s_name in selected_targets:
                                color = "#90EE90" if has_elev else "#FF9999" # Red if no elevator

                            if color:
                                ax.add_patch(patches.Rectangle((b[0], b[1]), b[2]-b[0], b[3]-b[1], facecolor=color, alpha=0.4))
                            
                            # Always draw the label
                            ax.text(x, y, s_name.upper(), fontsize=6, ha="center", 
                                    bbox=dict(boxstyle="round,pad=0.2", fc="white", alpha=0.7, ec="none"))

                    ax.set_aspect("equal")
                    ax.axis("off")
                    st.pyplot(fig)

                    # 5. JPEG Download
                    buf = BytesIO()
                    fig.savefig(buf, format="jpg", dpi=150, bbox_inches='tight')
                    st.download_button(
                        label="📥 Download Plan as JPEG",
                        data=buf.getvalue(),
                        file_name=f"Accessibility_Plan_{selected_level}.jpg",
                        mime="image/jpeg"
                    )

            # 6. Summary Table
            st.divider()
            st.header("♿ Accessibility Summary Report")
            
            report_data = []
            for f, d in full_report.items():
                status = "✅ Accessible" if d["has_elevator"] else "❌ Inaccessible"
                rooms_str = ", ".join(set(d["rooms"])).title() if d["rooms"] else "—"
                report_data.append([f, status, rooms_str])

            report_df = pd.DataFrame(report_data, columns=["Floor Level", "Access Status", "Room List"])
            st.table(report_df)

        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)