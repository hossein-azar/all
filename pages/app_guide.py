# app_guideline_standardizer.py
import streamlit as st

# Lazy import inside tab later avoids blocking when no IFC is uploaded
# from ifc_standardizer import render_ifc_standardizer_tab

# -------------------------------
# 🧭 App Config
# -------------------------------
st.set_page_config(page_title="Guideline For Regulation Check Platform", page_icon="🧭", layout="wide")

# -------------------------------
# 📘 Title
# -------------------------------
st.title("📘 Guideline For Regulation Check Platform")

# -------------------------------
# 📁 Local IFC uploader (only for this app)
# -------------------------------
with st.sidebar:
    st.header("📁 1) Upload IFC")
    local_ifc = st.file_uploader("Upload IFC (.ifc)", type=["ifc"], key="LOCAL_ifc")
    st.caption("This IFC upload is only for this app, not shared globally.")

# -------------------------------
# 📄 Guideline Text
# -------------------------------
GUIDELINE_TEXT = """
## 🧭 User Roadmap to Regulation Check Platform 

Follow these steps to correctly prepare and validate your Revit model for regulation checks.

---

### 🚀 Step 1 → Prepare Your IFC Export
- Open your **Revit model**.
- **Export** it to **IFC** format.
- Make sure your model follows **standard modeling practices** (each room and element correctly named and placed).

---

### 🏫 Step 2 → Include Standard Room Types
Your model **must contain** these room names exactly as listed below:

- classroom  
- laboratory  
- workshop  
- computer site  
- library  
- praying room  
- meeting room  
- stair  
- emergency stair  
- yard  
- parking  
- wc  
- wc room  
- staff wc  
- wc for disabled  
- drinking room
- green area
- janitor room

> 💡 *Each name must match exactly — lowercase and spacing included.*

---

### 🪑 Step 3 → Include Necessary Elements
Your IFC model should also contain the following furniture and components:

- student chair  
- laboratory chair  
- meeting room chair  
- white board  
- drinking tap  
- yard wall
- roof wall

> ⚠️ *These names must also match exactly for the app to detect and analyze them.*

---

### 🧩 Step 4 → Standardize IFC (Optional but Recommended)
If your IFC names differ or are inconsistent:
- 🔧 **Option 1:** Manually rename them in your Revit model before exporting.  
- ⚙️ **Option 2 (Recommended):** Use the **IFC Standardizer Tool** provided in this platform.  
  → Upload your IFC there  
  → Download the standardized IFC  
  → Continue with that file in regulation checks.

---

### 🧾 Step 5 → Run Regulation Checks
- Upload your **IFC file** in the sidebar of platform.  
- Go through the **Chapters** and **Tabs** to:
  - ✅ Verify compliance with architectural regulations.  
  - 🔍 Identify missing or incorrect modeling details.  
  - 🛠️ Apply corrections in Revit as needed.

---

### 🎯 Goal
By following this roadmap, you ensure:
- Consistent room and element naming  
- Compatibility with all automated regulation checks  
- Reliable results and faster model validation  

---

📘 *Tip:* If an item is missing or mismatched, the platform will flag it and show what needs to be fixed in your model.
"""

# -------------------------------
# 🧾 Tabs
# -------------------------------
tab_guideline, tab_standardizer, tab_viewer, tab_3d_accessibility, tab_router, tab_Files = st.tabs([
    "📘 Guideline", 
    "🛠️ Standard IFC", 
    "📦 3D/2D Viewer",
    "♿ Ramp checker",
    "🚶‍♂️ Disabled Path Checker",
    "📄 Files"
])

# --- Tab 1: Guideline ---
with tab_guideline:
    st.markdown(GUIDELINE_TEXT, unsafe_allow_html=True)

# --- Tab 2: IFC Standardizer ---
with tab_standardizer:
    if local_ifc is None:
        st.info("Upload an IFC file in the sidebar to use the Standard IFC tab.")
    else:
        try:
            from ifc_standardizer import render_ifc_standardizer_tab
            render_ifc_standardizer_tab(uploaded_ifc=local_ifc, show_uploader_in_sidebar=False)
        except Exception as e:
            st.error(f"⚠️ Could not load the IFC Standardizer module: {e}")

# --- Tab 3: IFC 3D/2D Viewer ---
with tab_viewer:
    try:
        from view import IntegratedIFCViewer
        viewer_instance = IntegratedIFCViewer()
        # Passes global ifc; if None, view.py automatically displays its standalone file uploader
        viewer_instance.run(uploaded_ifc=local_ifc)
    except Exception as e:
        st.error(f"⚠️ Could not load the Spatial Model Viewer module: {e}")

# --- Tab 5: 3D Accessibility ---
with tab_3d_accessibility:
    if local_ifc is None:
        st.info("Upload an IFC file in the sidebar to use the 3D Accessibility tab.")
    else:
        try:
            from accessibility_3d import render_3d_accessibility_tab
            render_3d_accessibility_tab(uploaded_ifc=local_ifc)
        except Exception as e:
            st.error(f"⚠️ Could not load the 3D Accessibility module: {e}")

# --- Tab: Human Path Router ---
with tab_router:
    try:
        from path_disabled import render_navigation_with_routing_tab
        render_navigation_with_routing_tab(uploaded_ifc=local_ifc)
    except Exception as e:
        st.error(f"⚠️ Could not load the Human Path Router module: {e}")

# --- Tab 6: Files ---
with tab_Files:
    try:
        from files import render_file_downloads
        render_file_downloads()
    except Exception as e:
        st.error(f"⚠️ Could not load the Files module: {e}")