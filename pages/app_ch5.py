# app.py
# Streamlit web UI for Classroom & Laboratory Capacity Checker (IFC + Shapely)

import os, tempfile
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import pandas as pd
import streamlit as st

# 👉 set page config FIRST
st.set_page_config(page_title="📚School regulations Checker platform", layout="wide")
st.title("✿ School Regulations Checker Platform ✿")

# IFC + geometry
import ifcopenshell
import ifcopenshell.geom as ifcgeom
from shapely.geometry import Polygon, Point
from shapely.ops import unary_union

# -------------------------
# Global IFC uploader (single source of truth for all tabs)
# -------------------------
with st.sidebar:
    st.subheader("IFC")
    up_global = st.file_uploader(
        "Upload IFC (.ifc / .ifczip)",
        type=["ifc", "ifczip"],
        key="global_ifc_upload",
        help="Upload once here. All tabs reuse this IFC."
    )

# Only open once and cache in session_state
if ("ifc" not in st.session_state) and (up_global is not None):
    suffix = ".ifczip" if up_global.name.lower().endswith(".ifczip") else ".ifc"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as _tmp:
        _tmp.write(up_global.getbuffer())
        _tmp_path = _tmp.name
    try:
        st.session_state.ifc = ifcopenshell.open(_tmp_path)
        st.session_state.ifc_name = up_global.name
    except Exception as e:
        st.error(f"IFC open error: {e}")

# Hard guard: stop UI until IFC is available
if "ifc" not in st.session_state:
    st.info("Upload an IFC file in the sidebar to begin Chapter 5.")
    st.stop()

# Convenience local var (already-opened IFC)
ifc = st.session_state.ifc

# -------------------------
# Tabs
# -------------------------
from floors_checker import render_floors_with_classrooms
from parking_checker import render_parking_checker
from class_count_checker import render_class_count_checker
from location_checker import run_distance_check
from level_hight import run_classroom_level_check
from wall_checker import run_wall_height_check
from basement_checker import run_basement_check
from window_checker import run_classroom_window_check

tabs = st.tabs([
    "Class Count",
    "Floors",
    "Parking",
    "Location", 
    "Levels Height", 
    "Walls Height",
    "Basements",
    "Window",
    
])
with tabs[0]:
    render_class_count_checker()

with tabs[1]:
    render_floors_with_classrooms() 

with tabs[2]:
    render_parking_checker()

with tabs[3]:
    run_distance_check()

with tabs[4]:
    run_classroom_level_check()

with tabs[5]: 
    run_wall_height_check()

with tabs[6]: 
    run_basement_check()

with tabs[7]:
    run_classroom_window_check()
