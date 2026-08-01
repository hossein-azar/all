# app.py
import os, tempfile
import pandas as pd
import streamlit as st

# 👉 set page config FIRST
st.set_page_config(page_title="📚School regulations Checker platform", layout="wide")
st.title("✿ School Regulations Checker Platform ✿")

import ifcopenshell
import ifcopenshell.geom as ifcgeom
from shapely.geometry import Polygon, Point
from shapely.ops import unary_union

# -------------------------
# Global IFC uploader
# -------------------------
with st.sidebar:
    st.subheader("IFC")
    up_global = st.file_uploader(
        "Upload IFC (.ifc / .ifczip)",
        type=["ifc", "ifczip"],
        key="global_ifc_upload"
    )

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

if "ifc" not in st.session_state:
    st.info("Upload an IFC file in the sidebar to begin Chapter 4.")
    st.stop()

ifc = st.session_state.ifc

# -------------------------
# Tab Imports
# -------------------------
from classroom_size_checker import run_classroom_size_check
from prayingroom_floor_checker import run_praying_room_check
from door_direction_chcecker import run_classroom_door_outward_check
from window_direction_chcecker import run_classroom_window_outward_check
from window_sill_chcecker import run_classroom_window_sill_simple
from stair_checker import run_stair_check
from door_stair import run_classroom_stair_check
from wc_size_checker import run_wc_size_check 
from drinking_tap_checker import run_drinking_tap_vs_classrooms_check
from wc_drinkingroom_distance_checker import run_wc_drinkingroom_distance_check
from ramps import run_ramp_compliance_check # 👈 Double check this line matches file name!

tabs = st.tabs([
    "Class Size",
    "prayingroom level", 
    "Door Direction", 
    "Window Direction", 
    "Window OKB",
    "stair",
    "door to stair",
    "WC Size",
    "Drinking tap",
    "WC to Drinking room ",
    "Ramp Check" 
])

with tabs[0]:
    run_classroom_size_check(ifc=ifc)  

with tabs[1]:
    run_praying_room_check(ifc=ifc)

with tabs[2]:
    run_classroom_door_outward_check(ifc=ifc)

with tabs[3]:
    run_classroom_window_outward_check(ifc=ifc)

with tabs[4]:
    run_classroom_window_sill_simple(ifc=ifc)

with tabs[5]:
    run_stair_check(ifc=ifc)

with tabs[6]:
    run_classroom_stair_check(ifc=ifc)

with tabs[7]:
    run_wc_size_check(ifc=ifc)

with tabs[8]:
    run_drinking_tap_vs_classrooms_check(ifc=ifc)

with tabs[9]:
    run_wc_drinkingroom_distance_check(ifc=ifc)

with tabs[10]:
    run_ramp_compliance_check(ifc=ifc) 

