import streamlit as st

# 1. Page Configuration (Sets browser tab title & layout)
st.set_page_config(
    page_title="My Project Hub",
    page_icon="📁",
    layout="wide"
)

# 2. Main Page Header
st.title("Welcome to My Application Dashboard 🚀")
st.markdown("---")

# 3. Welcome Message & Instructions
st.write("""
### Overview
Use the **sidebar menu on the left** to navigate through the different chapters and tools:

* **Guide**: Introduction and usage guidelines.
* **Chapter 2**: Analysis and calculations for Chapter 2.
* **Chapter 4**: Spatial geometry and data tools for Chapter 4.
* **Chapter 5**: Advanced metrics and results for Chapter 5.
""")

# 4. (Optional) Quick Callout / Info Box
st.info("👈 Select a module from the sidebar to get started!")
