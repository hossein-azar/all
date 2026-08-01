import streamlit as st
import pandas as pd
import requests
import math
import json
import os
import folium
from streamlit_folium import st_folium

# --- 1. Database Configuration ---
DB_FILE = "database.json"

def load_data():
    """Loads the JSON database directly."""
    if not os.path.exists(DB_FILE):
        st.error(f"❌ Database file '{DB_FILE}' not found! Please create it.")
        return {}
    
    try:
        with open(DB_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except json.JSONDecodeError:
        st.error(f"❌ Error decoding '{DB_FILE}'. Please check the JSON format.")
        return {}

# --- 2. Helper Functions ---

def haversine_distance(lat1, lon1, lat2, lon2):
    """
    Calculate the great circle distance between two points 
    on the earth (specified in decimal degrees) in meters.
    """
    R = 6371000  # Radius of earth in meters
    
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    
    a = math.sin(dphi / 2)**2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    
    return R * c

def get_location_name(lat, lon):
    """Reverse geocoding using OpenStreetMap (Nominatim)."""
    try:
        url = f"https://nominatim.openstreetmap.org/reverse?format=json&lat={lat}&lon={lon}"
        headers = {'User-Agent': 'StreamlitDistanceApp/1.0'}
        response = requests.get(url, headers=headers, timeout=5)
        if response.status_code == 200:
            data = response.json()
            address = data.get('address', {})
            city = address.get('city') or address.get('town') or address.get('village') or address.get('county')
            state = address.get('state', '')
            country = address.get('country', '')
            
            location_parts = [p for p in [city, state, country] if p]
            return ", ".join(location_parts) if location_parts else "Unknown Location"
        else:
            return "Location not found"
    except Exception as e:
        return "Service unavailable"

# --- 3. Main Function ---

def run_distance_check():
    st.caption("Code: 5-1-1-1")
    st.header("📍 Distance Violation Checker")
    st.markdown("Check if School base point violates minimum distance rules for restricted categories.")

    # Load Database
    db_data = load_data()
    
    if not db_data:
        st.stop() # Stop execution if data is missing

    # --- Input Section ---
    st.subheader("1. Define School Base Point")
    
    # Toggle for input method
    input_method = st.radio("Input Method:", ["Manual Coordinates", "Select on Map"], horizontal=True)
    
    base_lat = None
    base_lon = None

    if input_method == "Manual Coordinates":
        col1, col2 = st.columns(2)
        with col1:
            lat_input = st.text_input("Latitude", value="", placeholder="Enter base latitude")
        with col2:
            lon_input = st.text_input("Longitude", value="", placeholder="Enter base longitude")
        
        if lat_input and lon_input:
            try:
                base_lat = float(lat_input)
                base_lon = float(lon_input)
            except ValueError:
                st.error("Invalid coordinates entered.")

    else:
        st.write("Click on the map to select a location:")
        # Initialize map
        m = folium.Map(location=[40.7128, -74.0060], zoom_start=11)
        # Render map
        map_data = st_folium(m, height=400, width=700)
        
        if map_data and map_data.get("last_clicked"):
            base_lat = map_data["last_clicked"]["lat"]
            base_lon = map_data["last_clicked"]["lng"]
            st.success(f"📍 Selected Point: {base_lat:.6f}, {base_lon:.6f}")

    # --- Calculation Logic ---
    if base_lat is not None and base_lon is not None:
        
        location_name = get_location_name(base_lat, base_lon)
        # Fix Google Maps URL format (removed the '0' typo from original code)
        map_url = f"https://www.google.com/maps?q={base_lat},{base_lon}"
        
        st.markdown(f"**[🌐 Open Location in Google Maps]({map_url})**")
        st.info(f"**Estimated Location:** {location_name}")
        st.divider()
        
        st.subheader("2. Violation Analysis")
        
        violations_list = []
        summary_stats = []
        
        for category, data in db_data.items():
            limit = data.get('limit_m', 0)
            points = data.get('points', [])
            cat_violation_count = 0
            
            for point in points:
                dist = haversine_distance(base_lat, base_lon, point['lat'], point['lon'])
                
                if dist < limit:
                    cat_violation_count += 1
                    violations_list.append({
                        "Category": category,
                        "Point Name": point['name'],
                        "Distance (m)": round(dist, 2),
                        "Limit (m)": limit
                    })
            
            summary_stats.append({
                "Category": category,
                "Total Points": len(points),
                "Violations": cat_violation_count,
                "Limit (m)": limit
            })

        # --- Display Results ---
        st.write("### Summary by Category")
        df_summary = pd.DataFrame(summary_stats)
        st.dataframe(df_summary, use_container_width=True)
        
        st.write("### ⚠️ Detailed Violations")
        if violations_list:
            df_violations = pd.DataFrame(violations_list)
            df_violations = df_violations.sort_values(by="Distance (m)")
            st.dataframe(df_violations, use_container_width=True)
        else:
            st.success("✅ No distance violations detected for this base point.")

    elif input_method == "Manual Coordinates" and (not lat_input or not lon_input):
        st.write("Waiting for coordinates...")

if __name__ == "__main__":
    run_distance_check()