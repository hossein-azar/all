import streamlit as st
from pathlib import Path

st.set_page_config(page_title="File Downloads", layout="centered")

def render_file_downloads():
    base_path = Path(__file__).parent

    st.title("📁 School Building Files")

    # ---------------- Regulation File ----------------
    st.subheader("📄 School Building Regulations")

    st.write(
        "Here’s the file of regulations for school buildings (code 697). "
        "Use this document to understand design requirements, standards, "
        "and compliances rules."
    )

    regulation_file = base_path / "school regulation 697.pdf"

    if regulation_file.exists():
        # Added unique key: reg_btn
        if st.button("Prepare Regulation File", key="reg_btn"):
            st.download_button(
                label="⬇️ Download School Regulation 697 (PDF)",
                data=regulation_file.read_bytes(),
                file_name=regulation_file.name,
                mime="application/pdf",
                key="reg_pdf_download",
            )
    else:
        st.error("❌ Regulation file not found.")

    st.divider()

    # ---------------- Revit Template ----------------
    st.subheader("🏗️ School Revit Template")

    st.write(
        "Here is a Revit **template (.RTE)** to help model a standard school building. "
        "It includes predefined settings, levels, and styles to speed up your workflow."
    )

    revit_template = base_path / "school revit template.rte"

    if revit_template.exists():
        # Added unique key: revit_btn
        if st.button("Prepare Revit Template", key="revit_btn"):
            st.download_button(
                label="⬇️ Download School Revit Template (RTE)",
                data=revit_template.read_bytes(),
                file_name=revit_template.name,
                mime="application/octet-stream",
                key="revit_rte_download",
            )
    else:
        st.error("❌ Revit template file not found.")

# Run the UI
render_file_downloads()