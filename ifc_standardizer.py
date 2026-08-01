# tab_ifc_standardizer.py
# 📎 Reusable Streamlit tab: IFC Room & Element Renamer (writes back to SAME attribute)
# Usage (in main app):
#   from tab_ifc_standardizer import render_ifc_standardizer_tab
#   with tab_standardizer:
#       render_ifc_standardizer_tab(uploaded_ifc=uploaded_ifc)  # or leave None to show its own uploader

from typing import List, Dict, Tuple
import tempfile
import pandas as pd
import streamlit as st

# ---------------- Fixed room types (hard-coded targets) ----------------
ROOM_TYPES = [
    "classroom","laboratory","workshop","computer site","library","praying room",
    "meeting room","stair","emergency stair","yard","parking","wc","wc room",
    "staff wc","wc for disabled","drinking room","other",
]
FIXED_NEW_NAMES_ROOMS = {
    "classroom":"classroom",
    "laboratory":"laboratory",
    "workshop":"workshop",
    "computer site":"computer site",
    "library":"library",
    "praying room":"praying room",
    "meeting room":"meeting room",
    "stair":"stair",
    "emergency stair":"emergency stair",
    "yard":"yard",
    "parking":"parking",
    "wc":"wc",
    "wc room":"wc room",
    "staff wc":"staff wc",
    "wc for disabled":"wc for disabled",
    "drinking room":"drinking room",
}

# ---------------- Fixed element types (hard-coded targets) ----------------
ELEM_TYPES = ["student chair", "laboratory chair", "meeting room chair", "white board", "drinking tap", "other"]
FIXED_NEW_NAMES_ELEMS = {
    "student chair":"student chair",
    "laboratory chair":"laboratory chair",
    "meeting room chair":"meeting room chair",
    "white board":"white board",
    "drinking tap":"drinking tap"
}

# ---------------- IFC helpers ----------------
def load_ifc_from_upload(uploaded_file):
    import ifcopenshell
    data = uploaded_file.read()
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".ifc")
    tmp.write(data)
    tmp.flush(); tmp.close()
    f = ifcopenshell.open(tmp.name)
    return f, tmp.name, data  # also return raw bytes so we can reopen fresh for export

def collect_space_longnames(f) -> List[Tuple[int, str]]:
    spaces = list(f.by_type("IfcSpace")) or []
    out = []
    for s in spaces:
        lnm = getattr(s, "LongName", "") or ""
        out.append((s.id(), lnm))
    return out

def _display_name_for_element(e) -> Tuple[str, str]:
    for attr in ("LongName", "Name", "ObjectType", "PredefinedType"):
        val = getattr(e, attr, None)
        if val:
            s = str(val).strip()
            if s:
                return s, attr
    return "", ""

def collect_element_display(f) -> List[Tuple[int, str, str, str]]:
    targets = ["IfcFurnishingElement","IfcFurniture","IfcFlowTerminal","IfcSanitaryTerminal","IfcFlowController"]
    out = []
    for t in targets:
        try:
            for e in f.by_type(t) or []:
                disp, src = _display_name_for_element(e)
                out.append((e.id(), disp, t, src))
        except Exception:
            pass
    # dedupe by id
    seen = set(); uniq = []
    for rid, disp, t, src in out:
        if rid in seen: continue
        seen.add(rid); uniq.append((rid, disp, t, src))
    return uniq

# ---------------- Apply changes (write back to SAME attribute) ----------------
def apply_changes_to_same_attr(f, df: pd.DataFrame):
    """
    Expects df with columns: ifc_id, Names (new), write_attr, will_change
    Writes 'Names (new)' back to ent.<write_attr>.
    """
    changes = df[df.get("will_change", False) == True]
    # Build id->entity map across products and spaces
    by_id = {}
    for t in ["IfcProduct", "IfcSpace"]:
        try:
            for e in f.by_type(t) or []:
                by_id[e.id()] = e
        except Exception:
            pass
    for _, row in changes.iterrows():
        ent = by_id.get(int(row["ifc_id"]))
        if not ent:
            continue
        new_val = str(row["Names (new)"])
        attr = str(row.get("write_attr", "")).strip()
        if attr and hasattr(ent, attr):
            setattr(ent, attr, new_val)

# ---------------- Session helpers ----------------
def _set_df(key: str, df: pd.DataFrame):
    st.session_state[key] = df.copy()

def _get_df(key: str) -> pd.DataFrame:
    return st.session_state.get(key, pd.DataFrame(columns=["ifc_id","write_attr","Names (old)","Names (new)","will_change"]))

# ---------------- Main render function (CALL THIS IN YOUR APP) ----------------
def render_ifc_standardizer_tab(uploaded_ifc=None, *, show_uploader_in_sidebar=True, sidebar_label="Upload .ifc"):
    """
    Render the IFC Standardizer UI (Rooms + Elements) inside a parent app/tab.

    Args:
        uploaded_ifc: st.file_uploader return value from the parent app (optional).
        show_uploader_in_sidebar (bool): If True and uploaded_ifc is None, renders its own uploader in sidebar.
        sidebar_label (str): Label text for the uploader if shown.

    Returns:
        None (renders UI)
    """
    # Sticky tab row CSS (safe to include multiple times)
    st.markdown("""
    <style>
    div[role="tablist"] {
      position: sticky; top: 0; z-index: 999;
      background: var(--background-color);
      padding-top: 6px; margin-top: -6px;
      border-bottom: 1px solid rgba(0,0,0,0.08);
    }
    </style>
    """, unsafe_allow_html=True)

    # Uploader (optional – only if parent didn't pass one)
    up = uploaded_ifc
    if up is None and show_uploader_in_sidebar:
        with st.sidebar:
            st.markdown("### 1) Upload IFC")
            up = st.file_uploader(sidebar_label, type=["ifc"])
            st.caption("Original file is not modified. A new IFC is generated for download.")

    if up is None:
        st.info("Upload an IFC file to begin.")
        return

    # Open IFC once for UI lists (keep raw bytes for a fresh export later)
    try:
        import ifcopenshell  # noqa
        f, _, raw_ifc_bytes = load_ifc_from_upload(up)
    except Exception as e:
        st.error(f"Could not open IFC: {e}")
        return

    # Keep original bytes in session so export always starts from clean model
    st.session_state["raw_ifc_bytes"] = raw_ifc_bytes

    # ---------- Rooms data ----------
    rows_rooms = collect_space_longnames(f)  # (id, LongName)
    unique_room_names = sorted({lnm for _, lnm in rows_rooms if lnm}) if rows_rooms else []

    # ---------- Elements data ----------
    rows_elems_full = collect_element_display(f)  # (id, display_name, ifc_type, source_attr)
    name_counts: Dict[str, int] = {}
    for _, disp, _t, _src in rows_elems_full:
        dn = (disp or "").strip()
        if dn:
            name_counts[dn] = name_counts.get(dn, 0) + 1
    unique_elem_names = sorted(name_counts.keys())

    # Tabs inside the Standardizer
    tab_rooms, tab_elems = st.tabs(["Rooms", "Furniture / Elements"])

    # ---------------- TAB: ROOMS ----------------
    with tab_rooms:
        if not rows_rooms:
            st.warning("No IfcSpace elements found.")
            _set_df("df_prev_rooms", pd.DataFrame(columns=["ifc_id","room type","write_attr","Names (old)","Names (new)","will_change"]))
        else:
            st.subheader("2) Select Room Names of each Room type")
            st.caption("Only selected rooms are renamed. Unselected rooms remain unchanged.")
            cols = st.columns(2)
            selections_rooms: Dict[str, List[str]] = {}
            for i, rtype in enumerate(ROOM_TYPES):
                with cols[i % 2]:
                    st.markdown(f"#### {rtype}")
                    selections_rooms[rtype] = st.multiselect(
                        f"Select room Names for {rtype}",
                        options=unique_room_names,
                        default=[],
                        key=f"rooms_sel_{rtype}"
                    )

            st.markdown("---")
            st.subheader("3) Standard Targets")
            c1, c2 = st.columns([1, 1])
            with c1:
                st.markdown("**Room types and Standard Names**")
                st.dataframe(
                    pd.DataFrame([{"Room type": k, "Standard Names": v} for k, v in FIXED_NEW_NAMES_ROOMS.items()]),
                    use_container_width=True, hide_index=True
                )
            with c2:
                other_target_rooms = st.text_input("Rename selected ‘other’ rooms to:", value="", placeholder="(Optional) e.g., storage")

            # Build preview for rooms and stash in session
            recs = []
            if rows_rooms:
                sel_sets = {k: set(v) for k, v in selections_rooms.items()}
                for rid, lnm in rows_rooms:
                    new_val, changed, picked = lnm, False, ""
                    for rtype, sset in sel_sets.items():
                        if lnm in sset:
                            picked = rtype
                            tgt = (other_target_rooms.strip() if rtype == "other"
                                   else FIXED_NEW_NAMES_ROOMS.get(rtype, "").strip())
                            if tgt and tgt != lnm:
                                new_val, changed = tgt, True
                            break
                    recs.append({
                        "ifc_id": rid,
                        "room type": picked,
                        "write_attr": "LongName",  # Rooms always LongName
                        "Names (old)": lnm,
                        "Names (new)": new_val,
                        "will_change": changed
                    })

            df_prev_rooms = pd.DataFrame(recs).sort_values(
                by=["will_change", "room type", "Names (old)"], ascending=[False, True, True]
            ) if recs else pd.DataFrame(columns=["ifc_id","room type","write_attr","Names (old)","Names (new)","will_change"])
            _set_df("df_prev_rooms", df_prev_rooms)

            st.markdown("---")
            st.subheader("4) Preview")
            st.caption("Rows marked **True** will be updated. (Rooms write to LongName.)")
            st.dataframe(
                df_prev_rooms[["ifc_id", "room type", "Names (old)", "Names (new)", "will_change"]],
                use_container_width=True
            )

    # ---------------- TAB: FURNITURE / ELEMENTS ----------------
    with tab_elems:
        if not rows_elems_full:
            st.warning("No furniture/elements found in supported IFC classes.")
            df_prev_elems = pd.DataFrame(columns=["ifc_id","element type","write_attr","Names (old)","Names (new)","will_change"])
            _set_df("df_prev_elems", df_prev_elems)
        else:
            st.subheader("2) Type Elements Name to find and Rename")
            st.caption("Only matched elements are renamed. Unmatched elements remain unchanged.")
            st.caption("⚠️ Check number of elements found before Rename process.")

            cols = st.columns(2)
            elem_filters: Dict[str, str] = {}
            for i, etype in enumerate(ELEM_TYPES):
                with cols[i % 2]:
                    elem_filters[etype] = st.text_input(
                        f"Type a keyword for {etype}",
                        value="",
                        placeholder=f"e.g., 'student chair', 'tap'",
                        key=f"elems_filter_{etype}"
                    )
                    q = elem_filters[etype].strip().lower()
                    matched_names = [n for n in unique_elem_names if q and q in n.lower()]
                    total_matches = sum(name_counts[n] for n in matched_names) if matched_names else 0
                    st.write(f"**Matches:** {len(matched_names)} unique — **{total_matches} element(s)**")
                    if matched_names:
                        mini = pd.DataFrame(
                            [{"Name": n, "count": name_counts[n]} for n in matched_names]
                        ).sort_values("count", ascending=False)
                        st.dataframe(mini, use_container_width=True, hide_index=True, height=120)  # ~3 rows, scrollable

            st.markdown("---")
            st.subheader("3) Standard Targets")
            c1, c2 = st.columns([1, 1])
            with c1:
                st.markdown("**Element types and Standard Names**")
                st.dataframe(
                    pd.DataFrame([{"Element type": k, "Standard Name": v} for k, v in FIXED_NEW_NAMES_ELEMS.items()]),
                    use_container_width=True, hide_index=True
                )
            with c2:
                other_target_elems = st.text_input("Rename matched ‘other’ elements to:", value="", placeholder="(Optional) e.g., equipment")

            # Build preview for elements and stash in session
            recs = []
            for rid, disp, _t, src_attr in rows_elems_full:
                current_display = disp or ""
                new_val, picked, changed = current_display, "", False
                for etype in ELEM_TYPES:
                    q = elem_filters.get(etype, "").strip().lower()
                    if q and q in current_display.lower():
                        picked = etype
                        tgt = (other_target_elems.strip() if etype == "other"
                               else FIXED_NEW_NAMES_ELEMS.get(etype, "").strip())
                        if tgt and tgt != current_display:
                            new_val, changed = tgt, True
                        break
                recs.append({
                    "ifc_id": rid,
                    "element type": picked,
                    "write_attr": src_attr,          # write back to SAME attribute
                    "Names (old)": current_display,  # shown value = source field’s value
                    "Names (new)": new_val,
                    "will_change": changed,
                })

            df_prev_elems = pd.DataFrame(recs).sort_values(
                by=["will_change", "element type", "Names (old)"], ascending=[False, True, True]
            )
            _set_df("df_prev_elems", df_prev_elems)

            st.markdown("---")
            st.subheader("4) Preview")
            st.caption("Rows marked **True** will be updated.")
            st.dataframe(
                df_prev_elems[["ifc_id", "element type", "Names (old)", "Names (new)", "will_change"]],
                use_container_width=True
            )

    # ---------------- SHARED EXPORT (single combined export) ----------------
    st.markdown("---")
    st.subheader("5) Export standardized IFC (Rooms + Elements)")

    df_rooms = _get_df("df_prev_rooms")
    df_elems = _get_df("df_prev_elems")

    # Ensure columns exist
    for df in (df_rooms, df_elems):
        for col in ["ifc_id","write_attr","Names (old)","Names (new)","will_change"]:
            if col not in df.columns:
                df[col] = pd.Series(dtype=object)

    rooms_changes = int(df_rooms["will_change"].sum()) if not df_rooms.empty else 0
    elems_changes = int(df_elems["will_change"].sum()) if not df_elems.empty else 0
    st.write(f"Planned changes — **Rooms:** {rooms_changes} | **Elements:** {elems_changes}")

    # Combined DF
    if not df_rooms.empty or not df_elems.empty:
        df_combined = pd.concat([
            df_rooms[["ifc_id","write_attr","Names (old)","Names (new)","will_change"]],
            df_elems[["ifc_id","write_attr","Names (old)","Names (new)","will_change"]],
        ], ignore_index=True)
    else:
        df_combined = pd.DataFrame(columns=["ifc_id","write_attr","Names (old)","Names (new)","will_change"])

    if df_combined.empty or not df_combined["will_change"].any():
        st.info("No changes detected yet. Select rooms and/or type element filters to propose renames.")
        return

    if st.button("⚙️ Prepare IFC for Download", key="build_ifc_once"):
        try:
            import ifcopenshell
            raw_bytes = st.session_state.get("raw_ifc_bytes", None)
            if not raw_bytes:
                st.error("Original IFC bytes missing from session. Please re-upload the IFC.")
            else:
                tmp_in = tempfile.NamedTemporaryFile(delete=False, suffix=".ifc")
                tmp_in.write(raw_bytes); tmp_in.flush(); tmp_in.close()
                f_fresh = ifcopenshell.open(tmp_in.name)

                # Apply combined changes
                apply_changes_to_same_attr(f_fresh, df_combined)

                # Write out
                out_tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".ifc")
                out_path = out_tmp.name
                out_tmp.close()
                f_fresh.write(out_path)
                with open(out_path, "rb") as rf:
                    out_bytes = rf.read()
                st.session_state["export_bytes"] = out_bytes
                st.success("✅ IFC prepared. Use the button below to download.")
        except Exception as e:
            st.error(f"Export failed: {e}")

    if "export_bytes" in st.session_state and st.session_state["export_bytes"]:
        st.download_button(
            label="💾 Download IFC (Rooms + Elements renamed)",
            data=st.session_state["export_bytes"],
            file_name="Standardized_model.ifc",
            mime="application/octet-stream",
            key="download_ifc_unified"
        )
