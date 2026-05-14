import streamlit as st
import math
import json
import os
import logging
from datetime import datetime
import pandas as pd
import numpy as np
import pydeck as pdk
from pathlib import Path
import zipfile
import requests
import shapefile

from shared_config import CHAPTERS


LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
if not logging.getLogger().handlers:
    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL, logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
logger = logging.getLogger("chapter_coverage_map.streamlit")


st.set_page_config(
    page_title="Chapter Coverage Map",
    page_icon="📍",
    layout="wide",
    initial_sidebar_state="expanded"
)

st.markdown(
    """
<style>
    .main-header {
        font-size: 2.2rem;
        font-weight: 700;
        color: #1f77b4;
        text-align: center;
        margin-bottom: 0.35rem;
    }
</style>
""",
    unsafe_allow_html=True,
)

st.markdown('<h1 class="main-header">📍 BSF Chapter Coverage Map</h1>', unsafe_allow_html=True)
st.caption("Adjust chapter radius sliders live to update covered ZIP boundaries highlighted in real time.")
st.caption("Build: 2026-05-13-streamlit-polished")


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
ZCTA_ZIP_PATH = DATA_DIR / "tl_2020_us_zcta520.zip"
ZCTA_DIR = DATA_DIR / "tl_2020_us_zcta520"
ZCTA_SHP_PATH = ZCTA_DIR / "tl_2020_us_zcta520.shp"
ZCTA_SOURCE_URL = "https://www2.census.gov/geo/tiger/TIGER2020/ZCTA520/tl_2020_us_zcta520.zip"
PROJECT_ZIP_CSV_ENV = "BSF_ZIP_TABLE_PATH"
ENV_PATH = BASE_DIR / ".env"
DEFAULT_ZIP_TABLE_PATHS = [
    BASE_DIR / "data" / "Chapters_2020_zip codes.csv",
    Path("c:/Users/moein/Downloads/Chapters_2020_zip codes.csv"),
]
MAX_RENDERED_POLYGONS = 800
MAX_RENDERED_LABELS = 1200

# Process-level polygon cache to avoid repeatedly reading large GeoJSON files each rerun.
POLYGON_CACHE_BY_STRIDE: dict[int, dict[str, dict]] = {}


def load_local_env() -> None:
    if not ENV_PATH.exists():
        return
    try:
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            raw = line.strip()
            if not raw or raw.startswith("#") or "=" not in raw:
                continue
            k, v = raw.split("=", 1)
            key = k.strip()
            val = v.strip().strip('"').strip("'")
            if key and key not in os.environ and val:
                os.environ[key] = val
    except Exception:
        pass


load_local_env()
logger.info("Streamlit app environment initialized")


def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 3959.0
    d_lat = math.radians(lat2 - lat1)
    d_lon = math.radians(lon2 - lon1)
    a = (
        math.sin(d_lat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(d_lon / 2) ** 2
    )
    return 2 * r * math.asin(math.sqrt(a))


def covered_mask_for_circles(zip_geo_df: pd.DataFrame, circles: list[dict]) -> pd.Series:
    if zip_geo_df.empty or not circles:
        return pd.Series(False, index=zip_geo_df.index)

    lat = np.radians(zip_geo_df["latitude"].to_numpy(dtype=float))
    lon = np.radians(zip_geo_df["longitude"].to_numpy(dtype=float))
    covered = np.zeros(len(zip_geo_df), dtype=bool)
    earth_radius_miles = 3959.0

    for c in circles:
        c_lat = math.radians(float(c["lat"]))
        c_lon = math.radians(float(c["lon"]))
        d_lat = lat - c_lat
        d_lon = lon - c_lon

        a = np.sin(d_lat / 2.0) ** 2 + np.cos(c_lat) * np.cos(lat) * np.sin(d_lon / 2.0) ** 2
        a = np.clip(a, 0.0, 1.0)
        dist_miles = 2.0 * earth_radius_miles * np.arcsin(np.sqrt(a))
        covered |= dist_miles <= float(c["radius_miles"])

    return pd.Series(covered, index=zip_geo_df.index)


@st.cache_data(show_spinner=False, persist="disk")
def precompute_all_us_zip_chapter_distances() -> pd.DataFrame:
    """Disk-persisted distance matrix for all US ZIPs vs all 13 chapters."""
    zip_geo_df = load_all_us_zip_centroids()
    if zip_geo_df.empty:
        return pd.DataFrame()

    chapter_names = list(CHAPTERS.keys())
    chapter_lat = np.radians(np.array([CHAPTERS[c]["lat"] for c in chapter_names], dtype=float))
    chapter_lon = np.radians(np.array([CHAPTERS[c]["lon"] for c in chapter_names], dtype=float))

    zip_lat = np.radians(zip_geo_df["latitude"].to_numpy(dtype=float))
    zip_lon = np.radians(zip_geo_df["longitude"].to_numpy(dtype=float))

    d_lat = zip_lat[:, None] - chapter_lat[None, :]
    d_lon = zip_lon[:, None] - chapter_lon[None, :]

    a = np.sin(d_lat / 2.0) ** 2 + np.cos(zip_lat)[:, None] * np.cos(chapter_lat)[None, :] * np.sin(d_lon / 2.0) ** 2
    a = np.clip(a, 0.0, 1.0)
    dist_miles = 2.0 * 3959.0 * np.arcsin(np.sqrt(a))
    result = pd.DataFrame(dist_miles, columns=chapter_names)
    result.index = zip_geo_df["Zip Code"].astype(str).str.zfill(5).values
    return result


@st.cache_data(show_spinner=False)
def precompute_zip_chapter_distances(zip_geo_df: pd.DataFrame) -> pd.DataFrame:
    if zip_geo_df.empty:
        return pd.DataFrame()

    chapter_names = list(CHAPTERS.keys())
    chapter_lat = np.radians(np.array([CHAPTERS[c]["lat"] for c in chapter_names], dtype=float))
    chapter_lon = np.radians(np.array([CHAPTERS[c]["lon"] for c in chapter_names], dtype=float))

    zip_lat = np.radians(zip_geo_df["latitude"].to_numpy(dtype=float))
    zip_lon = np.radians(zip_geo_df["longitude"].to_numpy(dtype=float))

    d_lat = zip_lat[:, None] - chapter_lat[None, :]
    d_lon = zip_lon[:, None] - chapter_lon[None, :]

    a = np.sin(d_lat / 2.0) ** 2 + np.cos(zip_lat)[:, None] * np.cos(chapter_lat)[None, :] * np.sin(d_lon / 2.0) ** 2
    a = np.clip(a, 0.0, 1.0)
    earth_radius_miles = 3959.0
    dist_miles = 2.0 * earth_radius_miles * np.arcsin(np.sqrt(a))

    return pd.DataFrame(dist_miles, columns=chapter_names, index=zip_geo_df.index)


@st.cache_data(show_spinner=False)
def ensure_zcta_files() -> str:
    if not ZCTA_SHP_PATH.exists():
        if not ZCTA_ZIP_PATH.exists():
            resp = requests.get(ZCTA_SOURCE_URL, timeout=180)
            resp.raise_for_status()
            ZCTA_ZIP_PATH.write_bytes(resp.content)
        ZCTA_DIR.mkdir(exist_ok=True)
        with zipfile.ZipFile(ZCTA_ZIP_PATH, "r") as zf:
            zf.extractall(ZCTA_DIR)
    return str(ZCTA_SHP_PATH)


def resolve_zip_table_path() -> Path:
    env_path = os.getenv(PROJECT_ZIP_CSV_ENV)
    if env_path:
        p = Path(env_path)
        if p.exists():
            return p

    for p in DEFAULT_ZIP_TABLE_PATHS:
        if p.exists():
            return p

    expected = "\n - ".join([str(p) for p in DEFAULT_ZIP_TABLE_PATHS])
    raise FileNotFoundError(
        f"ZIP table CSV not found. Set {PROJECT_ZIP_CSV_ENV} or place the file at:\n - {expected}"
    )


@st.cache_data(show_spinner=False)
def load_zip_table() -> pd.DataFrame:
    csv_path = resolve_zip_table_path()
    data = pd.read_csv(csv_path)
    data.columns = [c.strip() for c in data.columns]
    data = data[["Zip Code", "BSF Chapter", "State"]].copy()
    data = data.dropna(subset=["Zip Code", "BSF Chapter"])
    data["Zip Code"] = data["Zip Code"].astype(str).str.extract(r"(\d+)")[0].str.zfill(5)
    data = data.dropna(subset=["Zip Code"])
    data = data.drop_duplicates(subset=["Zip Code"]) 
    return data


@st.cache_data(show_spinner=False)
def geocode_project_zip_centroids() -> pd.DataFrame:
    try:
        import pgeocode  # lazy import so app still loads if package is missing

        zip_df = load_zip_table()
        nomi = pgeocode.Nominatim("us")
        geo = nomi.query_postal_code(zip_df["Zip Code"].tolist())[["postal_code", "latitude", "longitude"]]
        geo = geo.rename(columns={"postal_code": "Zip Code"})
        geo["Zip Code"] = geo["Zip Code"].astype(str).str.zfill(5)
        merged = zip_df.merge(geo, on="Zip Code", how="left")
        merged = merged.dropna(subset=["latitude", "longitude"])
        return merged
    except Exception:
        return pd.DataFrame()


@st.cache_data(show_spinner=False, persist="disk")
def load_all_us_zip_centroids() -> pd.DataFrame:
    try:
        import pgeocode

        nomi = pgeocode.Nominatim("us")
        all_data = nomi._data.copy()
        if all_data.empty:
            return pd.DataFrame()

        geo = all_data[["postal_code", "state_code", "latitude", "longitude"]].copy()
        geo = geo.rename(columns={"postal_code": "Zip Code", "state_code": "State"})
        geo["Zip Code"] = geo["Zip Code"].astype(str).str.extract(r"(\d{5})")[0]
        geo = geo.dropna(subset=["Zip Code", "latitude", "longitude"])
        geo["Zip Code"] = geo["Zip Code"].astype(str).str.zfill(5)
        geo["BSF Chapter"] = "(not mapped)"
        geo = geo[["Zip Code", "BSF Chapter", "State", "latitude", "longitude"]]
        geo = geo.drop_duplicates(subset=["Zip Code"])
        return geo
    except Exception:
        return pd.DataFrame()


@st.cache_data(show_spinner=False, persist="disk")
def compute_chapter_center_zips() -> pd.DataFrame:
    all_zip_geo = load_all_us_zip_centroids()
    if all_zip_geo.empty:
        return pd.DataFrame(columns=["Chapter", "Center Zip Code", "State", "latitude", "longitude"])

    chapter_names = list(CHAPTERS.keys())
    chapter_lat = np.radians(np.array([CHAPTERS[c]["lat"] for c in chapter_names], dtype=float))
    chapter_lon = np.radians(np.array([CHAPTERS[c]["lon"] for c in chapter_names], dtype=float))

    zip_lat = np.radians(all_zip_geo["latitude"].to_numpy(dtype=float))
    zip_lon = np.radians(all_zip_geo["longitude"].to_numpy(dtype=float))

    d_lat = zip_lat[:, None] - chapter_lat[None, :]
    d_lon = zip_lon[:, None] - chapter_lon[None, :]

    a = np.sin(d_lat / 2.0) ** 2 + np.cos(zip_lat)[:, None] * np.cos(chapter_lat)[None, :] * np.sin(d_lon / 2.0) ** 2
    a = np.clip(a, 0.0, 1.0)
    earth_radius_miles = 3959.0
    dist_miles = 2.0 * earth_radius_miles * np.arcsin(np.sqrt(a))
    nearest_zip_idx = np.argmin(dist_miles, axis=0)

    nearest_rows = all_zip_geo.iloc[nearest_zip_idx].reset_index(drop=True)
    return pd.DataFrame(
        {
            "Chapter": chapter_names,
            "Center Zip Code": nearest_rows["Zip Code"].astype(str).str.zfill(5),
            "State": nearest_rows["State"].fillna(""),
            "latitude": nearest_rows["latitude"].to_numpy(dtype=float),
            "longitude": nearest_rows["longitude"].to_numpy(dtype=float),
        }
    )


def ensure_chapter_center_zips_present(zip_geo_df: pd.DataFrame) -> pd.DataFrame:
    center_zip_df = compute_chapter_center_zips()
    if center_zip_df.empty:
        return zip_geo_df

    center_rows = center_zip_df.rename(columns={"Center Zip Code": "Zip Code", "Chapter": "BSF Chapter"}).copy()
    center_rows = center_rows[["Zip Code", "BSF Chapter", "State", "latitude", "longitude"]]

    if zip_geo_df.empty:
        return center_rows.drop_duplicates(subset=["Zip Code"]).reset_index(drop=True)

    merged = zip_geo_df.copy()
    merged["Zip Code"] = merged["Zip Code"].astype(str).str.zfill(5)
    existing_zips = set(merged["Zip Code"])
    missing_center_rows = center_rows[~center_rows["Zip Code"].isin(existing_zips)].copy()
    if missing_center_rows.empty:
        return merged

    return pd.concat([merged, missing_center_rows], ignore_index=True).drop_duplicates(subset=["Zip Code"], keep="first")


@st.cache_data(show_spinner=False)
def load_zip_polygons_map(zip_codes: tuple[str, ...], point_stride: int = 3) -> dict[str, dict]:
    shp_path = ensure_zcta_files()
    reader = shapefile.Reader(shp_path)
    zcta_index_map = load_zcta_record_index()
    features_by_zip: dict[str, dict] = {}

    for raw_zip in zip_codes:
        zcta = str(raw_zip).zfill(5)
        rec_idx = zcta_index_map.get(zcta)
        if rec_idx is None:
            continue

        try:
            sr = reader.shapeRecord(rec_idx)
        except Exception:
            continue

        shape = sr.shape
        points = shape.points
        parts = list(shape.parts) + [len(points)]
        rings = []
        for i in range(len(parts) - 1):
            ring = points[parts[i]:parts[i + 1]]
            if point_stride > 1:
                ring = ring[::point_stride] + ([ring[-1]] if ring else [])
            if len(ring) >= 3:
                rings.append([[p[0], p[1]] for p in ring])

        if not rings:
            continue

        if shape.shapeType in (5, 15, 25, 31):
            geometry = {"type": "Polygon", "coordinates": rings}
        else:
            geometry = {"type": "Polygon", "coordinates": [rings[0]]}

        features_by_zip[zcta] = {
            "type": "Feature",
            "properties": {"zip": zcta},
            "geometry": geometry,
        }

    return features_by_zip


@st.cache_data(show_spinner=False)
def load_zcta_record_index() -> dict[str, int]:
    shp_path = ensure_zcta_files()
    reader = shapefile.Reader(shp_path)
    fields = [f[0] for f in reader.fields[1:]]
    if "ZCTA5CE20" not in fields:
        return {}

    zcta_idx = fields.index("ZCTA5CE20")
    out: dict[str, int] = {}
    for i, rec in enumerate(reader.iterRecords()):
        try:
            out[str(rec[zcta_idx]).zfill(5)] = i
        except Exception:
            continue
    return out


@st.cache_data(show_spinner=False)
def load_cached_polygons_for_zips(zip_codes: tuple[str, ...], point_stride: int = 3) -> dict[str, dict]:
    cache_path = DATA_DIR / f"project_zip_polygons_stride_{point_stride}.geojson"

    if point_stride not in POLYGON_CACHE_BY_STRIDE:
        cached_map: dict[str, dict] = {}
        if cache_path.exists():
            try:
                payload = json.loads(cache_path.read_text(encoding="utf-8"))
                features = payload.get("features", [])
                cached_map = {
                    str(f.get("properties", {}).get("zip", "")).zfill(5): f
                    for f in features
                    if f.get("properties", {}).get("zip")
                }
            except Exception:
                cached_map = {}
        POLYGON_CACHE_BY_STRIDE[point_stride] = cached_map

    cached_map = POLYGON_CACHE_BY_STRIDE[point_stride]

    requested = [str(z).zfill(5) for z in zip_codes]
    missing = tuple(z for z in requested if z not in cached_map)

    if missing:
        fetched = load_zip_polygons_map(missing, point_stride=point_stride)
        cached_map.update(fetched)
        try:
            feature_collection = {
                "type": "FeatureCollection",
                "features": list(cached_map.values()),
            }
            cache_path.write_text(json.dumps(feature_collection), encoding="utf-8")
        except Exception:
            pass

    return {z: cached_map[z] for z in requested if z in cached_map}


def circles_from_sidebar(selected_chapters: list[str]) -> list[dict]:
    circles = []
    for chapter in selected_chapters:
        base = CHAPTERS[chapter]
        radius = st.session_state.get(f"radius_{chapter}", base["radius_miles"])
        circles.append(
            {
                "name": chapter,
                "lat": base["lat"],
                "lon": base["lon"],
                "radius_miles": radius,
            }
        )
    return circles


with st.sidebar:
    st.subheader("⚙️ Controls")
    zip_dataset_scope = st.selectbox(
        "ZIP dataset scope",
        options=["Project ZIP table", "All US ZIP centroids"],
        index=1,
        help="Use full US ZIP centroid dataset for whole-US visibility, or project ZIP table for chapter-linked ZIPs only.",
    )
    view_mode = st.selectbox(
        "View mode",
        options=["Fast (recommended)", "Balanced", "Detailed"],
        index=0,
        help="Controls default map detail and payload size sent to the browser.",
    )
    performance_mode = view_mode == "Fast (recommended)"
    ultra_fast_mode = st.checkbox(
        "Ultra-fast circles-only mode",
        value=performance_mode,
        help="Skips ZIP coverage/polygon computation and renders chapter circles only.",
    )
    selected_chapters = st.multiselect(
        "Chapters to show",
        list(CHAPTERS.keys()),
        default=list(CHAPTERS.keys()),
    )
    for chapter in selected_chapters:
        default_radius = CHAPTERS[chapter]["radius_miles"]
        st.slider(
            f"{chapter} radius (mi)",
            min_value=10,
            max_value=250,
            value=default_radius,
            step=5,
            key=f"radius_{chapter}",
        )
    mode_defaults = {
        "Fast (recommended)": {"stride": 6, "covered": 120, "uncovered": 120, "labels": 400},
        "Balanced": {"stride": 4, "covered": 260, "uncovered": 200, "labels": 800},
        "Detailed": {"stride": 3, "covered": 450, "uncovered": 300, "labels": 1200},
    }
    defaults = mode_defaults[view_mode]

    covered_render_limit = st.slider(
        "Covered ZIP boundaries to render",
        min_value=20,
        max_value=MAX_RENDERED_POLYGONS,
        value=defaults["covered"],
        step=20,
        help="Hard-capped to keep browser payload below Streamlit message limits.",
        disabled=ultra_fast_mode,
    )
    uncovered_render_limit = st.slider(
        "Uncovered ZIP boundaries to render",
        min_value=0,
        max_value=MAX_RENDERED_POLYGONS,
        value=defaults["uncovered"],
        step=20,
        help="Uncovered ZIPs are optional and capped for stability.",
        disabled=ultra_fast_mode,
    )
    show_zip_labels = st.checkbox(
        "Show ZIP code labels",
        value=view_mode != "Fast (recommended)",
        help="Display ZIP code numbers on the map at each covered ZIP centroid.",
        disabled=ultra_fast_mode,
    )
    zip_label_limit = st.slider(
        "Max ZIP labels",
        min_value=100,
        max_value=MAX_RENDERED_LABELS,
        value=defaults["labels"],
        step=100,
        help="Limits label payload to keep map responsive.",
        disabled=ultra_fast_mode or not show_zip_labels,
    )
    label_min_zoom = st.slider(
        "ZIP labels visible from zoom",
        min_value=3.5,
        max_value=8.0,
        value=5.0,
        step=0.1,
        help="Zoom-aware labels: hide labels at low zoom to reduce clutter/render load.",
        disabled=ultra_fast_mode or not show_zip_labels,
    )
    map_zoom = st.slider(
        "Initial map zoom",
        min_value=3.0,
        max_value=7.0,
        value=3.6,
        step=0.1,
    )
    with st.expander("Advanced", expanded=False):
        default_polygon_stride_index = [1, 2, 3, 4, 5, 6].index(defaults["stride"])
        polygon_stride = st.selectbox(
            "ZIP boundary detail (lower=faster)",
            options=[1, 2, 3, 4, 5, 6],
            index=default_polygon_stride_index,
            disabled=ultra_fast_mode,
        )

    if "load_covered_requested" not in st.session_state:
        st.session_state["load_covered_requested"] = False
    if "load_uncovered_requested" not in st.session_state:
        st.session_state["load_uncovered_requested"] = False

    c_a, c_b = st.columns(2)
    if c_a.button("Load covered boundaries", disabled=ultra_fast_mode):
        st.session_state["load_covered_requested"] = True
        st.rerun()
    if c_b.button("Reset layers"):
        st.session_state["load_covered_requested"] = False
        st.session_state["load_uncovered_requested"] = False
        st.rerun()

    st.info("Tip: Start in Fast mode, then switch to Balanced/Detailed for closer inspection.")
    if ultra_fast_mode:
        st.caption("Ultra-fast mode active: ZIP coverage, polygons, and ZIP labels are skipped for maximum responsiveness.")
    st.caption("Legend: Yellow = chapter centroid ZIP • Green = covered ZIP • Red = uncovered ZIP")
    st.caption(f"Safety caps: polygons ≤ {MAX_RENDERED_POLYGONS}, labels ≤ {MAX_RENDERED_LABELS}")
    st.caption(datetime.now().strftime("Updated %Y-%m-%d %H:%M:%S"))

if ultra_fast_mode:
    zip_geo = pd.DataFrame()
else:
    if zip_dataset_scope == "Project ZIP table":
        try:
            _ = load_zip_table()
        except FileNotFoundError as ex:
            st.error(str(ex))
            st.stop()
        zip_geo = geocode_project_zip_centroids()
    else:
        zip_geo = load_all_us_zip_centroids()

chapter_center_zip_df = pd.DataFrame() if ultra_fast_mode else compute_chapter_center_zips()
if not zip_geo.empty:
    zip_geo = ensure_chapter_center_zips_present(zip_geo)

selected_center_zip_df = chapter_center_zip_df[chapter_center_zip_df["Chapter"].isin(selected_chapters)].copy() if not chapter_center_zip_df.empty else pd.DataFrame()
selected_center_zip_map = (
    selected_center_zip_df.groupby("Center Zip Code")["Chapter"].agg(lambda s: ", ".join(sorted(set(s)))).to_dict()
    if not selected_center_zip_df.empty
    else {}
)

if zip_geo.empty:
    logger.warning("zip_geo_empty dataset_scope=%s", zip_dataset_scope)
    st.warning("ZIP geocoding not available yet. Install `pgeocode` to enable ZIP highlighting.")
else:
    if zip_dataset_scope == "All US ZIP centroids":
        st.caption(f"Using full US ZIP centroid dataset: {len(zip_geo):,} ZIPs loaded.")
    else:
        st.caption(f"Using project ZIP dataset: {len(zip_geo):,} ZIPs loaded.")
    logger.info("zip_dataset_loaded scope=%s count=%d", zip_dataset_scope, len(zip_geo))

chapter_circles = circles_from_sidebar(selected_chapters)
all_circles = chapter_circles.copy()

zip_geo_with_coverage = pd.DataFrame()
if not zip_geo.empty:
    zip_geo_with_coverage = zip_geo.copy()
    zip_geo_with_coverage["Zip Code"] = zip_geo_with_coverage["Zip Code"].astype(str).str.zfill(5)
    if zip_dataset_scope == "All US ZIP centroids":
        if "distance_index_ready" not in st.session_state:
            st.session_state["distance_index_ready"] = False
        if not st.session_state["distance_index_ready"]:
            with st.spinner("Building ZIP coverage index… (first run only, cached to disk after)"):
                _dm_all = precompute_all_us_zip_chapter_distances()
            st.session_state["distance_index_ready"] = True
        else:
            _dm_all = precompute_all_us_zip_chapter_distances()
        # align by zip code (safe after ensure_chapter_center_zips_present reindex)
        distance_matrix = _dm_all.reindex(zip_geo_with_coverage["Zip Code"].values)
        distance_matrix = distance_matrix.reset_index(drop=True)
        distance_matrix.index = zip_geo_with_coverage.index
    else:
        distance_matrix = precompute_zip_chapter_distances(zip_geo_with_coverage)
    covered = np.zeros(len(zip_geo_with_coverage), dtype=bool)
    for c in all_circles:
        chapter_name = c["name"]
        if chapter_name in distance_matrix.columns:
            covered |= distance_matrix[chapter_name].to_numpy(dtype=float) <= float(c["radius_miles"])
    zip_geo_with_coverage["covered"] = covered
    zip_geo_with_coverage["is_center_zip"] = zip_geo_with_coverage["Zip Code"].isin(selected_center_zip_map)
    zip_geo_with_coverage["center_chapter"] = zip_geo_with_coverage["Zip Code"].map(selected_center_zip_map).fillna("")

covered_total = int(zip_geo_with_coverage["covered"].sum()) if not zip_geo_with_coverage.empty else 0

chapter_df = pd.DataFrame(chapter_circles)
# Fixed CONUS center — keeps Hawaii from skewing the view
center_lat, center_lon = 39.7, -98.5

layers: list[pdk.Layer] = []

if not chapter_df.empty:
    chapter_df = chapter_df.copy()
    chapter_df["radius_meters"] = chapter_df["radius_miles"] * 1609.34
    chapter_df["fill_color"] = [[37, 99, 235, 45]] * len(chapter_df)
    chapter_df["line_color"] = [[37, 99, 235, 235]] * len(chapter_df)
    chapter_pickable = not performance_mode

    layers.append(
        pdk.Layer(
            "ScatterplotLayer",
            data=chapter_df,
            get_position="[lon, lat]",
            get_radius="radius_meters",
            get_fill_color="fill_color",
            get_line_color="line_color",
            stroked=True,
            filled=True,
            line_width_min_pixels=2,
            radius_min_pixels=8,
            pickable=chapter_pickable,
        )
    )
    # Chapter name labels — visible when zoomed into a chapter area
    layers.append(
        pdk.Layer(
            "TextLayer",
            data=chapter_df,
            get_position="[lon, lat]",
            get_text="name",
            get_color=[15, 23, 70, 230],
            get_size=25000,
            size_units="'meters'",
            size_min_pixels=10,
            size_max_pixels=20,
            min_zoom=4,
            get_angle=0,
            get_text_anchor="'middle'",
            get_alignment_baseline="'center'",
            pickable=False,
        )
    )

# Render US Census ZIP polygons with coverage highlighting
coverage_lookup = {}
center_zip_lookup = {}
center_chapter_lookup = {}
zip_polygons: list[dict] = []
missing_covered_zips: list[str] = []
if not zip_geo_with_coverage.empty:
    # Separate covered ZIPs (always needed for counts/message).
    covered_zips = tuple(zip_geo_with_coverage.loc[zip_geo_with_coverage["covered"], "Zip Code"].astype(str).str.zfill(5).tolist())
    st.session_state["covered_zips_all"] = covered_zips

    if st.session_state.get("load_covered_requested", False) and covered_zips:
        if not coverage_lookup:
            _zips_v = zip_geo_with_coverage["Zip Code"].astype(str).str.zfill(5)
            coverage_lookup = dict(zip(_zips_v, zip_geo_with_coverage["covered"].astype(bool)))
            center_zip_lookup = dict(zip(_zips_v, zip_geo_with_coverage["is_center_zip"].astype(bool)))
            center_chapter_lookup = dict(zip(_zips_v, zip_geo_with_coverage["center_chapter"].astype(str)))
        covered_batch = covered_zips[: min(covered_render_limit, MAX_RENDERED_POLYGONS)]
        with st.spinner(f"Loading {len(covered_batch)} covered ZIP boundaries..."):
            try:
                polygon_map = load_cached_polygons_for_zips(covered_batch, point_stride=polygon_stride)
                zip_polygons.extend([polygon_map[z] for z in covered_batch if z in polygon_map])
                missing_covered_zips = [z for z in covered_batch if z not in polygon_map]
            except Exception as ex:
                st.warning(f"Covered ZIP boundaries could not load: {ex}")

    if st.session_state.get("load_uncovered_requested", False):
        if not coverage_lookup:
            _zips_v = zip_geo_with_coverage["Zip Code"].astype(str).str.zfill(5)
            coverage_lookup = dict(zip(_zips_v, zip_geo_with_coverage["covered"].astype(bool)))
            center_zip_lookup = dict(zip(_zips_v, zip_geo_with_coverage["is_center_zip"].astype(bool)))
            center_chapter_lookup = dict(zip(_zips_v, zip_geo_with_coverage["center_chapter"].astype(str)))
        uncovered_zips = tuple(zip_geo_with_coverage.loc[~zip_geo_with_coverage["covered"], "Zip Code"].astype(str).str.zfill(5).tolist())
        uncovered_batch = uncovered_zips[: min(uncovered_render_limit, MAX_RENDERED_POLYGONS)]
        with st.spinner(f"Loading {len(uncovered_batch)} uncovered ZIP boundaries..."):
            try:
                polygon_map = load_cached_polygons_for_zips(uncovered_batch, point_stride=polygon_stride)
                zip_polygons.extend([polygon_map[z] for z in uncovered_batch if z in polygon_map])
            except Exception as ex:
                st.warning(f"Uncovered ZIP boundaries could not load: {ex}")
# Render ZIP polygons with coverage highlighting
if zip_polygons:
    polygon_features = []
    for feature in zip_polygons:
        z = feature["properties"]["zip"]
        covered_flag = coverage_lookup.get(z, False)
        center_zip_flag = center_zip_lookup.get(z, False)
        fill_color = [255, 220, 0, 180] if center_zip_flag else ([22, 163, 74, 90] if covered_flag else [220, 38, 38, 90])
        line_color = [200, 170, 0, 255] if center_zip_flag else ([22, 101, 52, 210] if covered_flag else [127, 29, 29, 210])
        polygon_features.append(
            {
                "type": "Feature",
                "properties": {
                    **feature.get("properties", {}),
                    "covered": covered_flag,
                    "is_center_zip": center_zip_flag,
                    "center_chapter": center_chapter_lookup.get(z, ""),
                    "fill_color": fill_color,
                    "line_color": line_color,
                },
                "geometry": feature["geometry"],
            },
        )

    layers.append(
        pdk.Layer(
            "GeoJsonLayer",
            data={"type": "FeatureCollection", "features": polygon_features},
            stroked=True,
            filled=True,
            get_fill_color="properties.fill_color",
            get_line_color="properties.line_color",
            line_width_min_pixels=1,
            pickable=not performance_mode,
        )
    )

# ZIP code label layer — show ZIP number at each covered centroid
zip_labels_rendered = 0
if show_zip_labels and not zip_geo_with_coverage.empty:
    label_points = zip_geo_with_coverage[zip_geo_with_coverage["covered"]].copy()
    if not label_points.empty:
        label_cap = min(zip_label_limit, MAX_RENDERED_LABELS)
        if len(label_points) > label_cap:
            step = max(1, len(label_points) // label_cap)
            label_points = label_points.iloc[::step].head(label_cap).copy()
        else:
            label_points = label_points.copy()
        if "longitude" in label_points.columns and "latitude" in label_points.columns:
            label_points = label_points.rename(columns={"longitude": "lon", "latitude": "lat"})
        label_points["label"] = label_points["Zip Code"].astype(str).str.zfill(5)
        label_points["text_color"] = label_points["is_center_zip"].map(
            lambda is_center: [255, 220, 0, 255] if bool(is_center) else [30, 30, 30, 220]
        )
        zip_labels_rendered = len(label_points)
        layers.append(
            pdk.Layer(
                "TextLayer",
                data=label_points,
                get_position="[lon, lat]",
                get_text="label",
                get_color="text_color",
                get_size=600,
                size_units="'meters'",
                size_min_pixels=0,
                size_max_pixels=5,
                min_zoom=label_min_zoom,
                get_angle=0,
                get_text_anchor="'middle'",
                get_alignment_baseline="'center'",
                pickable=False,
            )
        )

_total_zips = int(len(zip_geo_with_coverage)) if not zip_geo_with_coverage.empty else 0
_cov_pct = f"{100 * covered_total / _total_zips:.1f}%" if _total_zips > 0 else "N/A"
logger.info(
    "coverage_stats chapters=%d total_zips=%d covered=%d rendered_polygons=%d labels=%d",
    len(selected_chapters),
    _total_zips,
    covered_total,
    len(zip_polygons),
    zip_labels_rendered,
)
status_c1, status_c2, status_c3, status_c4, status_c5 = st.columns(5)
status_c1.metric("Chapters", len(selected_chapters))
status_c2.metric("Covered ZIPs", f"{covered_total:,}")
status_c3.metric("Coverage", _cov_pct)
status_c4.metric("Boundaries shown", len(zip_polygons))
status_c5.metric("ZIP labels", f"{zip_labels_rendered:,}")

# Performance HUD: quick visibility into active speed/safety settings.
payload_score = len(zip_polygons) * 3 + zip_labels_rendered
if ultra_fast_mode:
    payload_class = "Ultra-light"
elif payload_score <= 1200:
    payload_class = "Light"
elif payload_score <= 2600:
    payload_class = "Medium"
else:
    payload_class = "Heavy"

labels_state = "Off"
if show_zip_labels and not ultra_fast_mode:
    labels_state = f"On (zoom ≥ {label_min_zoom:.1f})"

hud_mode = "Ultra-fast" if ultra_fast_mode else view_mode
hud_limits = f"C:{covered_render_limit} U:{uncovered_render_limit} L:{zip_label_limit if show_zip_labels else 0}"
hud_render = f"P:{len(zip_polygons)}  Z:{zip_labels_rendered}"

hud_a, hud_b, hud_c, hud_d = st.columns(4)
hud_a.metric("Perf mode", hud_mode)
hud_b.metric("Payload class", payload_class)
hud_c.metric("Active caps", hud_limits)
hud_d.metric("Rendered now", hud_render)
st.caption(f"Labels: {labels_state} • Initial zoom: {map_zoom:.1f} • Stride: {polygon_stride if not ultra_fast_mode else 'N/A'}")

tooltip_html = "<b>Chapter:</b> {name}<br/><b>Radius:</b> {radius_miles} mi"
if not performance_mode:
    tooltip_html += "<br/><b>ZIP:</b> {zip}<br/><b>Covered:</b> {covered}<br/><b>Center ZIP for:</b> {center_chapter}"

deck = pdk.Deck(
    layers=layers,
    initial_view_state=pdk.ViewState(latitude=center_lat, longitude=center_lon, zoom=map_zoom, pitch=0),
    tooltip=None if performance_mode else {"html": tooltip_html, "style": {"backgroundColor": "#111827", "color": "white"}},
    map_provider="carto",
    map_style="light",
)

map_tab, details_tab = st.tabs(["Map", "Details"])

with map_tab:
    st.pydeck_chart(deck, width="stretch", height=680)

if ultra_fast_mode:
    with details_tab:
        st.info("Ultra-fast mode is on. ZIP coverage calculations and polygon rendering are intentionally skipped for speed.")

if not zip_geo_with_coverage.empty:
    with details_tab:
        with st.expander("ZIP coverage details", expanded=not performance_mode):
            c1, c2, c3 = st.columns(3)
            covered_count = int(zip_geo_with_coverage["covered"].sum())
            uncovered_count = int((~zip_geo_with_coverage["covered"]).sum())
            c1.metric("Total ZIPs", int(len(zip_geo_with_coverage)))
            c2.metric("Covered ZIPs", covered_count)
            c3.metric("Uncovered ZIPs", uncovered_count)

            if selected_chapters and not zip_geo_with_coverage.empty:
                # Per-chapter stats
                ch_stats_rows = []
                center_zip_lookup_by_chapter = (
                    selected_center_zip_df.set_index("Chapter")["Center Zip Code"].to_dict()
                    if not selected_center_zip_df.empty else {}
                )
                for c in chapter_circles:
                    ch_name = c["name"]
                    if ch_name in distance_matrix.columns:
                        ch_mask = distance_matrix[ch_name].to_numpy(dtype=float) <= float(c["radius_miles"])
                        ch_covered = int(ch_mask.sum())
                    else:
                        ch_covered = 0
                    ch_stats_rows.append({
                        "Chapter": ch_name,
                        "City": CHAPTERS[ch_name]["city"],
                        "Radius (mi)": c["radius_miles"],
                        "Covered ZIPs": ch_covered,
                        "Centroid ZIP": center_zip_lookup_by_chapter.get(ch_name, ""),
                    })
                ch_stats_df = pd.DataFrame(ch_stats_rows)
                st.write("**Chapter coverage summary**")
                st.dataframe(ch_stats_df, width="stretch", hide_index=True)

            loaded_covered = min(covered_render_limit, len(st.session_state.get("covered_zips_all") or [])) if st.session_state.get("load_covered_requested", False) else 0
            total_covered = len(st.session_state.get("covered_zips_all") or [])
            if not st.session_state.get("load_covered_requested", False):
                st.write("Map showing circles only for instant load. Click **Load covered boundaries** in the sidebar.")
            elif total_covered > loaded_covered > 0:
                st.write(f"Map showing **{loaded_covered} of {total_covered}** covered ZIP boundaries (capped for browser stability).")
            elif total_covered > 0:
                st.write(f"Map showing **{loaded_covered}** covered ZIP boundaries.")

            if uncovered_count > 0 and not st.session_state.get("load_uncovered_requested", False):
                if st.button(f"Load uncovered ZIPs on map (max {uncovered_render_limit})"):
                    st.session_state["load_uncovered_requested"] = True
                    st.rerun()
            elif st.session_state.get("load_uncovered_requested", False):
                st.write("Map includes uncovered boundaries.")

            show_zip_table = st.checkbox("Show ZIP details table", value=False)
            if show_zip_table:
                table_mode = st.radio(
                    "ZIP table mode",
                    options=["Covered only", "Covered + Uncovered"],
                    horizontal=True,
                    index=0,
                )
                table_rows = st.slider("ZIP rows in table", min_value=100, max_value=1000, value=250, step=50)
                sample = zip_geo_with_coverage[["Zip Code", "BSF Chapter", "State", "covered"]].copy()
                if table_mode == "Covered only":
                    sample = sample[sample["covered"]]
                sample["covered"] = sample["covered"].map({True: "✅ Covered", False: "❌ Not covered"})
                st.dataframe(sample.head(table_rows), width="stretch")

            covered_export = zip_geo_with_coverage[zip_geo_with_coverage["covered"]][["Zip Code", "BSF Chapter", "State"]].copy()
            st.download_button(
                "Download covered ZIP CSV",
                data=covered_export.to_csv(index=False),
                file_name="covered_zips.csv",
                mime="text/csv",
            )

        st.caption(
            f"Live map is showing {len(zip_polygons):,} US Census ZIP boundaries with real-time coverage highlighting."
        )
