# yard_requirements_2_2_2.py — 🏫 Code 2-2-2 (Yard + Related Checks)
# Usage:
#     from yard_requirements_2_2_2 import run_yard_checks
#     run_yard_checks(ifc)

from __future__ import annotations
import tempfile
from io import StringIO
from typing import Optional, Dict, List, Tuple
import re
import pandas as pd
import streamlit as st

# ---------- Optional deps ----------
try:
    import ifcopenshell
except Exception:
    ifcopenshell = None
try:
    import ifcopenshell.geom as ifcgeom
except Exception:
    ifcgeom = None
try:
    from shapely.geometry import Polygon
    from shapely.ops import unary_union
    _HAS_SHAPELY = True
except Exception:
    _HAS_SHAPELY = False

# ---------- Constants ----------
CODE_ID = "2-2-2"
SCHOOL_TYPES = ["ebtedaei dore 1", "ebtedaei dore 2", "motevasete dore 1", "motevasete dore 2"]

QUEUE_YARD_COEFS = [1.2, 1.4, 1.5, 1.7]
PLAYGROUND_MIN_M2 = [110, 136, 360, 589]
WC_PER_WC_COEFS = [3.2, 3.2, 3.6, 3.6]
DRINKING_PER_STUD_COEFS = [1.4, 1.4, 1.6, 1.6]
JANITOR_MIN_M2 = 55.0
GREEN_PER_STUD_COEF = 0.5

YARD_STANDARD_KEY = "yard"
WC_STANDARD_KEY = "wc"
DRINKING_STANDARD_KEY = "drinking room"
JANITOR_STANDARD_KEY = "janitor room"
GREEN_STANDARD_KEY = "green area"
STANDARD_STUDENT_CHAIR = "student chair"

# ---------- Helper fns ----------
NUM_TOKEN_RE = re.compile(r"(?:^|\s)(?:#?\d+)(?=\s|$)")
def strip_numeric_tokens(s: str) -> str:
    s = " ".join(s.strip().split())
    s = NUM_TOKEN_RE.sub(" ", s)
    return " ".join(s.split())
def canonicalize(label: str) -> str:
    return strip_numeric_tokens(label).lower().strip() if label else ""
def best_room_label(space) -> str:
    for attr in ("LongName", "Name"):
        v = getattr(space, attr, None)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""
def _si_prefix_scale(prefix):
    m = {"EXA":1e18,"PETA":1e15,"TERA":1e12,"GIGA":1e9,"MEGA":1e6,"KILO":1e3,
         "HECTO":1e2,"DECA":1e1,"DECI":1e-1,"CENTI":1e-2,"MILLI":1e-3,"MICRO":1e-6,
         "NANO":1e-9,"PICO":1e-12,"FEMTO":1e-15,"ATTO":1e-18}
    return m.get((prefix or "").upper(),1.0)
def get_length_scale_m(ifc):
    try:
        proj=ifc.by_type("IfcProject")[0]; ua=getattr(proj,"UnitsInContext",None)
        for u in ua.Units or []:
            if u.is_a("IfcSIUnit") and getattr(u,"UnitType",None)=="LENGTHUNIT":
                name=getattr(u,"Name","METRE"); p=getattr(u,"Prefix",None)
                if name=="METRE": return _si_prefix_scale(p) if p else 1.0
    except: pass
    return 1.0
def get_area_scale_m2(ifc): s=get_length_scale_m(ifc); return s*s
def area_from_shape_mesh(shape)->float:
    if not _HAS_SHAPELY or shape is None: return 0.0
    try: geom=shape.geometry; verts=geom.verts; faces=geom.faces
    except: return 0.0
    coords=[(verts[i],verts[i+1],verts[i+2]) for i in range(0,len(verts),3)]
    polys=[]
    for i in range(0,len(faces),3):
        try:
            a=coords[faces[i]]; b=coords[faces[i+1]]; c=coords[faces[i+2]]
            p=Polygon([(a[0],a[1]),(b[0],b[1]),(c[0],c[1])])
            if p.is_valid and p.area>0: polys.append(p)
        except: pass
    if not polys: return 0.0
    try: return unary_union(polys).area
    except: return Polygon([pt for p in polys for pt in p.exterior.coords]).convex_hull.area
def _settings(): s=ifcgeom.settings(); s.set(s.USE_WORLD_COORDS,True); return s
def total_area_for_standard_key(ifc,key:str)->float:
    if ifcgeom is None or not _HAS_SHAPELY: return 0.0
    target=key.lower(); settings=_settings(); scale=get_area_scale_m2(ifc); total=0.0
    for sp in ifc.by_type("IfcSpace"):
        if canonicalize(best_room_label(sp))!=target: continue
        try: shape=ifcgeom.create_shape(settings,sp)
        except: continue
        a=area_from_shape_mesh(shape)
        if a>0: total+=a*scale
    return total
def total_area_and_count_for_standard_key(ifc,key:str)->Tuple[float,int]:
    if ifcgeom is None or not _HAS_SHAPELY: return (0.0,0)
    target=key.lower(); settings=_settings(); scale=get_area_scale_m2(ifc)
    total=count=0
    for sp in ifc.by_type("IfcSpace"):
        if canonicalize(best_room_label(sp))!=target: continue
        try: shape=ifcgeom.create_shape(settings,sp)
        except: continue
        a=area_from_shape_mesh(shape)
        if a>0: total+=a*scale; count+=1
    return total,count
def build_furn_map(ifc):
    out={}; 
    def best(elem):
        for a in("Name","ObjectType"):
            v=getattr(elem,a,None)
            if isinstance(v,str) and v.strip(): return v.strip()
        return ""
    for e in ifc.by_type("IfcFurnishingElement"):
        l=best(e); 
        if l: 
            k=canonicalize(l); out.setdefault(k,{"display":l,"count":0}); out[k]["count"]+=1
    try:
        for e in ifc.by_type("IfcFurniture"):
            l=best(e); 
            if l:
                k=canonicalize(l); out.setdefault(k,{"display":l,"count":0}); out[k]["count"]+=1
    except: pass
    return out

# ---------- Main UI ----------
def run_yard_checks(ifc:Optional[object]):
    st.caption(f"Code: {CODE_ID}")

    if ifcopenshell is None: st.error("ifcopenshell missing."); st.stop()
    if ifc is None and st.session_state.get("ifc"): ifc=st.session_state.ifc
    if ifc is None: st.info("Upload an IFC file first."); st.stop()
    if ifcgeom is None or not _HAS_SHAPELY: st.error("Need ifcopenshell-geom + shapely"); st.stop()

    if "yard_furn_map" not in st.session_state:
        st.session_state.yard_furn_map=build_furn_map(ifc)

    st.subheader("1) School Type")
    school_choice=st.selectbox("Select school type",SCHOOL_TYPES,index=0,key="yard_school_type")
    idx_school=SCHOOL_TYPES.index(school_choice)

    st.subheader("2) Number of Students")
    students=sum(v["count"] for v in st.session_state.yard_furn_map.values()
                 if STANDARD_STUDENT_CHAIR in v["display"].lower())
    st.metric("Students Detected",students)
    st.markdown("---")

    # collect areas
    yard_area=total_area_for_standard_key(ifc,YARD_STANDARD_KEY)
    wc_area,wc_count=total_area_and_count_for_standard_key(ifc,WC_STANDARD_KEY)
    drink_area=total_area_for_standard_key(ifc,DRINKING_STANDARD_KEY)
    janitor_area=total_area_for_standard_key(ifc,JANITOR_STANDARD_KEY)
    green_area=total_area_for_standard_key(ifc,GREEN_STANDARD_KEY)

    st.subheader("Results")

    results=[]

    # --- Queue Yard ---
    per_cap=QUEUE_YARD_COEFS[idx_school]; req=students*per_cap
    ok=yard_area>=req; short=max(0,req-yard_area)
    if ok:
        st.success(f"""**Queue Yard ✅**
- Students: {students}
- Per-capita: {per_cap:.2f} m²/student
- Required: {req:.2f} m²  
- Available: {yard_area:.2f} m²""")
    else:
        st.error(f"""**Queue Yard ❌**
- Students: {students}
- Per-capita: {per_cap:.2f} m²/student
- Required: {req:.2f} m²  
- Available: {yard_area:.2f} m²  
- Shortfall: {short:.2f} m²""")
    results.append({"Check":"Queue Yard","Rule":f"{per_cap:.2f} m²/student",
                    "Required":f"{req:.2f}","Available":f"{yard_area:.2f}",
                    "Status":"OK" if ok else "NOT OK","Shortfall":f"{short:.2f}"})

    # --- Playground ---
    min_req=PLAYGROUND_MIN_M2[idx_school]; ok=yard_area>=min_req
    short=max(0,min_req-yard_area)
    if ok:
        st.success(f"""**Playground ✅**
- Minimum Required: {min_req:.2f} m²  
- Available: {yard_area:.2f} m²""")
    else:
        st.error(f"""**Playground ❌**
- Minimum Required: {min_req:.2f} m²  
- Available: {yard_area:.2f} m²  
- Shortfall: {short:.2f} m²""")
    results.append({"Check":"Playground","Rule":f"≥ {min_req:.2f} m²",
                    "Required":f"{min_req:.2f}","Available":f"{yard_area:.2f}",
                    "Status":"OK" if ok else "NOT OK","Shortfall":f"{short:.2f}"})

    # --- WC ---
    if wc_count==0:
        st.warning("No WC rooms found.")
    else:
        per_wc=WC_PER_WC_COEFS[idx_school]
        avg=wc_area/wc_count; need=per_wc*wc_count; short=max(0,need-wc_area)
        if avg>=per_wc:
            st.success(f"""**WC ✅**
- WC count: {wc_count}
- Area per WC: {avg:.2f} m² ≥ {per_wc:.2f} m²
- Total Available: {wc_area:.2f} m²""")
        else:
            st.error(f"""**WC ❌**
- WC count: {wc_count}
- Area per WC: {avg:.2f} m² < {per_wc:.2f} m²
- Required Total: {need:.2f} m²  
- Available: {wc_area:.2f} m²  
- Shortfall: {short:.2f} m²""")
        results.append({"Check":"WC","Rule":f"{per_wc:.2f} m²/WC",
                        "Required":f"{need:.2f}","Available":f"{wc_area:.2f}",
                        "Status":"OK" if avg>=per_wc else "NOT OK","Shortfall":f"{short:.2f}"})

    # --- Drinking Room ---
    coef=DRINKING_PER_STUD_COEFS[idx_school]; req=students*coef; short=max(0,req-drink_area)
    if drink_area==0: st.warning("No Drinking Room found.")
    elif drink_area>=req:
        st.success(f"""**Drinking Room ✅**
- Students: {students}
- Per-capita: {coef:.2f} m²/student
- Required: {req:.2f} m²  
- Available: {drink_area:.2f} m²""")
    else:
        st.error(f"""**Drinking Room ❌**
- Students: {students}
- Per-capita: {coef:.2f} m²/student
- Required: {req:.2f} m²  
- Available: {drink_area:.2f} m²  
- Shortfall: {short:.2f} m²""")
    results.append({"Check":"Drinking Room","Rule":f"{coef:.2f} m²/student",
                    "Required":f"{req:.2f}","Available":f"{drink_area:.2f}",
                    "Status":"OK" if drink_area>=req else "NOT OK","Shortfall":f"{short:.2f}"})

    # --- Janitor Room ---
    short=max(0,JANITOR_MIN_M2-janitor_area)
    if janitor_area==0: st.warning("No Janitor Room found.")
    elif janitor_area>=JANITOR_MIN_M2:
        st.success(f"""**Janitor Room ✅**
- Required ≥ {JANITOR_MIN_M2:.2f} m²  
- Available {janitor_area:.2f} m²""")
    else:
        st.error(f"""**Janitor Room ❌**
- Required ≥ {JANITOR_MIN_M2:.2f} m²  
- Available {janitor_area:.2f} m²  
- Shortfall {short:.2f} m²""")
    results.append({"Check":"Janitor Room","Rule":f"≥ {JANITOR_MIN_M2:.2f} m²",
                    "Required":f"{JANITOR_MIN_M2:.2f}","Available":f"{janitor_area:.2f}",
                    "Status":"OK" if janitor_area>=JANITOR_MIN_M2 else "NOT OK","Shortfall":f"{short:.2f}"})

    # --- Green Area ---
    req=students*GREEN_PER_STUD_COEF; short=max(0,req-green_area)
    if green_area==0: st.warning("No Green Area found.")
    elif green_area>=req:
        st.success(f"""**Green Area ✅**
- Students: {students}
- Coef: {GREEN_PER_STUD_COEF:.2f} m²/student
- Required: {req:.2f} m²  
- Available: {green_area:.2f} m²""")
    else:
        st.error(f"""**Green Area ❌**
- Students: {students}
- Coef: {GREEN_PER_STUD_COEF:.2f} m²/student
- Required: {req:.2f} m²  
- Available: {green_area:.2f} m²  
- Shortfall: {short:.2f} m²""")
    results.append({"Check":"Green Area","Rule":f"{GREEN_PER_STUD_COEF:.2f} m²/student",
                    "Required":f"{req:.2f}","Available":f"{green_area:.2f}",
                    "Status":"OK" if green_area>=req else "NOT OK","Shortfall":f"{short:.2f}"})

    st.markdown("### Summary")
    df=pd.DataFrame(results)
    st.dataframe(df,use_container_width=True,hide_index=True)
    csv_buf=StringIO(); df.to_csv(csv_buf,index=False)
    st.download_button("⬇️ Download CSV",csv_buf.getvalue(),"yard_2_2_2_summary.csv","text/csv")
