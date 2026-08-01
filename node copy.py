import streamlit as st
import ifcopenshell
import ifcopenshell.geom
import networkx as nx
import numpy as np
import plotly.graph_objects as go

st.set_page_config(layout="wide")
st.title("⚡ Optimized IFC Human Path Finder")

def process_ifc_spaces_fast(file_path):
    """Processes spaces lightning-fast by extracting bounding boxes instead of full 3D meshes."""
    ifc_file = ifcopenshell.open(file_path)
    spaces = ifc_file.by_type("IfcSpace")
    
    settings = ifcopenshell.geom.settings()
    settings.set("use-world-coords", True)
    
    space_data = {}
    
    for space in spaces:
        try:
            shape = ifcopenshell.geom.create_shape(settings, space)
            verts = np.array(shape.geometry.verts).reshape(-1, 3)
            faces = np.array(shape.geometry.faces).reshape(-1, 3)
            
            # Identify Room Types
            name = (space.Name or "").lower()
            long_name = (space.LongName or "").lower()
            is_hall = any(h in name or h in long_name for h in ["hall", "corridor", "circulation", "lobby"])
            
            # Floor boundaries based on 2D Bounding Box Projections
            min_x, min_y, min_z = np.min(verts, axis=0)
            max_x, max_y, max_z = np.max(verts, axis=0)
            
            space_data[space.GlobalId] = {
                "name": space.Name or space.GlobalId,
                "is_hall": is_hall,
                "node_z": min_z + 1.0,  # 1m Elevation
                "bbox": (min_x, max_x, min_y, max_y),
                "verts": verts,
                "faces": faces
            }
        except Exception:
            continue
            
    return space_data

def generate_fast_grids(space_data, grid_spacing=0.8, wall_clearance=0.5):
    """Generates space grids instantly using cached room box dimensions."""
    all_grid_nodes = {}
    node_counter = 0
    
    for sp_id, data in space_data.items():
        min_x, max_x, min_y, max_y = data["bbox"]
        z = data["node_z"]
        
        # Pull grid boundaries inward to account for wall clearance rules
        bx_min, bx_max = min_x + wall_clearance, max_x - wall_clearance
        by_min, by_max = min_y + wall_clearance, max_y - wall_clearance
        
        room_nodes = []
        if bx_min >= bx_max or by_min >= by_max:
            # Fallback for small/narrow spaces
            room_nodes.append({"id": f"n_{node_counter}", "pos": (np.mean([min_x, max_x]), np.mean([min_y, max_y]), z)})
            node_counter += 1
        else:
            x_coords = np.arange(bx_min, bx_max, grid_spacing)
            y_coords = np.arange(by_min, by_max, grid_spacing)
            
            for x in x_coords:
                for y in y_coords:
                    room_nodes.append({"id": f"n_{node_counter}", "pos": (x, y, z)})
                    node_counter += 1
                    
        all_grid_nodes[sp_id] = room_nodes
        
    return all_grid_nodes

def build_fast_graph(space_data, room_grids, max_internal_dist=1.2, max_doorway_dist=2.0):
    """Assembles node links instantly without crashing execution threads."""
    G = nx.Graph()
    
    # Register Nodes
    for sp_id, nodes in room_grids.items():
        for n in nodes:
            G.add_node(n["id"], pos=n["pos"], room_id=sp_id, is_hall=space_data[sp_id]["is_hall"])
            
    # Step 1: Link Internal Room Meshes
    for sp_id, nodes in room_grids.items():
        coords = np.array([n["pos"] for n in nodes])
        num_nodes = len(nodes)
        for i in range(num_nodes):
            for j in range(i + 1, num_nodes):
                dist = np.linalg.norm(coords[i] - coords[j])
                if dist <= max_internal_dist:
                    G.add_edge(nodes[i]["id"], nodes[j]["id"], weight=dist)
                    
    # Step 2: Link Neighboring Rooms strictly via Hallway Nodes
    sp_ids = list(space_data.keys())
    for i in range(len(sp_ids)):
        for j in range(i + 1, len(sp_ids)):
            id_a, id_b = sp_ids[i], sp_ids[j]
            
            # Apply your absolute constraint: Normal rooms can only attach to hallways!
            if not (space_data[id_a]["is_hall"] or space_data[id_b]["is_hall"]):
                continue
                
            # Quick bounding box proximity check before inspecting deep array matrixes
            box_a = space_data[id_a]["bbox"]
            box_b = space_data[id_b]["bbox"]
            
            # If bounding boxes are completely isolated, skip processing instantly
            if (box_a[0] > box_b[1] + max_doorway_dist or box_b[0] > box_a[1] + max_doorway_dist or
                box_a[2] > box_b[3] + max_doorway_dist or box_b[2] > box_a[3] + max_doorway_dist):
                continue
                
            # Perform targeted edge connecting transitions
            nodes_a = room_grids[id_a]
            nodes_b = room_grids[id_b]
            best_pair = None
            min_d = float('inf')
            
            for na in nodes_a:
                for nb in nodes_b:
                    d = np.linalg.norm(np.array(na["pos"]) - np.array(nb["pos"]))
                    if d < min_d:
                        min_d = d
                        best_pair = (na["id"], nb["id"])
                        
            if min_d <= max_doorway_dist and best_pair:
                G.add_edge(best_pair[0], best_pair[1], weight=min_d)
                
    return G

# --- Streamlit UI Code ---
uploaded_file = st.sidebar.file_uploader("Upload IFC model", type=["ifc"])

if uploaded_file:
    with open("temp.ifc", "wb") as f:
        f.write(uploaded_file.getbuffer())
        
    with st.spinner("⚡ Running ultra-optimized graph calculations..."):
        spaces = process_ifc_spaces_fast("temp.ifc")
        grids = generate_fast_grids(spaces)
        graph = build_fast_graph(spaces, grids)
        
    if not spaces:
        st.error("No valid spaces found.")
    else:
        st.success(f"Processed {len(spaces)} rooms instantly!")
        
        # Target selections
        room_options = {sp_id: data["name"] for sp_id, data in spaces.items()}
        st.sidebar.markdown("---")
        start_room = st.sidebar.selectbox("Start Room", options=list(room_options.keys()), format_func=lambda x: room_options[x])
        end_room = st.sidebar.selectbox("End Room", options=list(room_options.keys()), format_func=lambda x: room_options[x])
        
        path_nodes = []
        if start_room and end_room:
            try:
                s_node = grids[start_room][0]["id"]
                e_node = grids[end_room][0]["id"]
                path_nodes = nx.shortest_path(graph, source=s_node, target=e_node, weight="weight")
            except Exception:
                st.sidebar.warning("No corridor connection between selected spaces.")

        # --- Plotly Scene Rendering ---
        fig = go.Figure()
        
        # 1. Pale Gray Semi-Transparent Room Outlines
        for sp_id, data in spaces.items():
            fig.add_trace(go.Mesh3d(
                x=data["verts"][:, 0], y=data["verts"][:, 1], z=data["verts"][:, 2],
                i=data["faces"][:, 0], j=data["faces"][:, 1], k=data["faces"][:, 2],
                color="lightgray", opacity=0.05, hoverinfo="skip"
            ))
            
        # 2. Green Human Paths
        if path_nodes:
            px = [graph.nodes[n]["pos"][0] for n in path_nodes]
            py = [graph.nodes[n]["pos"][1] for n in path_nodes]
            pz = [graph.nodes[n]["pos"][2] for n in path_nodes]
            
            fig.add_trace(go.Scatter3d(
                x=px, y=py, z=pz, mode="lines+markers",
                line=dict(color="limegreen", width=6),
                marker=dict(size=4, color="darkgreen"),
                name="Human Path"
            ))
            
        fig.update_layout(
            scene=dict(
                xaxis=dict(backgroundcolor="rgb(20,20,20)", showbackground=True),
                yaxis=dict(backgroundcolor="rgb(20,20,20)", showbackground=True),
                zaxis=dict(backgroundcolor="rgb(20,20,20)", showbackground=True)
            ),
            margin=dict(r=0, l=0, b=0, t=0), height=700
        )
        st.plotly_chart(fig, use_container_width=True)
else:
    st.info("Upload an IFC model via the sidebar to view the optimized layout graph.")