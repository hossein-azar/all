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
    st.info("Upload an IFC file in the sidebar to begin Chapter 2.")
    st.stop()

# Convenience local var (already-opened IFC)
ifc = st.session_state.ifc

# -------------------------
# Tabs
# -------------------------
from classroom_checker import run_classroom_checker
from laboratory_checker import run_laboratory_checker
from praying_room_checker import render_praying_room_area_check
from wc_number_checker import render_wc_number_check
from staff_wc_checker import render_staff_wc_check
from disabled_wc_checker import render_disabled_wc_check
from meeting_room_checker import render_meeting_room_seats_check
from area_per_capita_app import run_area_per_capita_app

tabs = st.tabs([
    "Classroom", "Laboratory", "Praying Room", "Meeting Room",
    "WC Check", "Staff WC", "Disabled WC", "Area per Capita"
])

with tabs[0]:
    run_classroom_checker(ifc)

with tabs[1]:
    run_laboratory_checker(ifc)

with tabs[2]:
    render_praying_room_area_check(ifc)

with tabs[3]:
    render_meeting_room_seats_check(ifc)

with tabs[4]:
    render_wc_number_check(ifc)

with tabs[5]:
    render_staff_wc_check(ifc)

with tabs[6]:
    render_disabled_wc_check(ifc)

with tabs[7]:
    run_area_per_capita_app(ifc)
