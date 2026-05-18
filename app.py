import streamlit as st
import math
import json
import os
import logging
import subprocess
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

BASE_DIR = Path(__file__).resolve().parent


def resolve_build_version() -> str:
    env_build = os.getenv("APP_BUILD_VERSION", "").strip()
    if env_build:
        return env_build

    try:
        rev = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(BASE_DIR),
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        if rev:
            return f"git-{rev}"
    except Exception:
        pass

    return "local-dev"


BUILD_VERSION = resolve_build_version()

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
st.caption("ZIP boundaries inside circles auto-update as chapter radii change.")
st.caption(f"Build: {BUILD_VERSION}")


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
DEFAULT_LABEL_MIN_ZOOM = 3.4
PERSIST_POLYGON_CACHE_TO_DISK = os.getenv("PERSIST_POLYGON_CACHE_TO_DISK", "0").strip() == "1"
ABSOLUTE_RENDER_CAP = 1500
ALL_US_RENDER_CAP = 1000

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


def load_zip_polygons_map(zip_codes: tuple[str, ...], point_stride: int = 3) -> dict[str, dict]:
    reader = get_zcta_reader()
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


@st.cache_resource(show_spinner=False)
def get_zcta_reader() -> shapefile.Reader:
    shp_path = ensure_zcta_files()
    return shapefile.Reader(shp_path)


@st.cache_resource(show_spinner=False)
def load_zcta_record_index() -> dict[str, int]:
    reader = get_zcta_reader()
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
        if PERSIST_POLYGON_CACHE_TO_DISK:
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


def compute_zip_label_style(current_zoom: float, size_scale: float = 1.0) -> dict[str, float]:
    zoom_factor = max(0.0, float(current_zoom) - 3.0)
    return {
        "size_meters": (450 + (zoom_factor * 170)) * size_scale,
        "min_pixels": max(3, int(round((4 + (zoom_factor * 1.5)) * size_scale))),
        "max_pixels": max(7, int(round((9 + (zoom_factor * 2.5)) * size_scale))),
        "min_zoom": DEFAULT_LABEL_MIN_ZOOM,
    }


with st.sidebar:
    st.subheader("⚙️ Controls")
    safe_mode = st.checkbox(
        "Safe mode (fastest)",
        value=True,
        help="Applies fast defaults to keep rendering responsive.",
    )
    selected_chapters = st.multiselect(
        "Chapters to show",
        list(CHAPTERS.keys()),
        default=list(CHAPTERS.keys()),
    )

    defaults = {"stride": 6, "max_rendered_covered_zips": 900}

    map_zoom = st.slider(
        "Initial map zoom",
        min_value=3.0,
        max_value=7.0,
        value=3.6,
        step=0.1,
    )
    show_zip_numbers = st.checkbox(
        "Show ZIP code labels",
        value=True,
        help="Turn ZIP number labels on or off.",
    )
    zip_label_size = st.slider(
        "ZIP label size",
        min_value=0.6,
        max_value=1.8,
        value=1.0,
        step=0.1,
        disabled=not show_zip_numbers,
        help="Adjust ZIP label font size relative to the map zoom.",
    )

    show_chapter_radius_controls = st.checkbox(
        "Customize per-chapter radius",
        value=False,
        help="Hidden by default to keep the UI clean.",
    )
    near_realtime_mode = st.checkbox(
        "Near real-time mode",
        value=True,
        help="While adjusting radii, show a fast preview and render full ZIP boundaries only after Apply.",
    )
    if show_chapter_radius_controls:
        st.caption("Adjust sliders, then click **Apply radius changes** for a single fast refresh.")
        with st.form("radius_controls_form", clear_on_submit=False):
            radius_updates: dict[str, int] = {}
            for chapter in selected_chapters:
                default_radius = int(st.session_state.get(f"radius_{chapter}", CHAPTERS[chapter]["radius_miles"]))
                radius_updates[chapter] = st.slider(
                    f"{chapter} radius (mi)",
                    min_value=10,
                    max_value=250,
                    value=default_radius,
                    step=5,
                    key=f"radius_form_{chapter}",
                )
            apply_radius_changes = st.form_submit_button("Apply radius changes", use_container_width=True)

        if apply_radius_changes:
            for chapter, radius in radius_updates.items():
                st.session_state[f"radius_{chapter}"] = int(radius)
            st.session_state["_radius_just_applied"] = True
            st.rerun()

    with st.expander("Advanced settings", expanded=False):
        minimal_basemap = st.checkbox(
            "Minimal base map",
            value=True,
            help="Faster initial load by skipping external map tiles.",
        )
        zip_dataset_scope = st.selectbox(
            "ZIP dataset scope",
            options=["Project ZIP table", "All US ZIP centroids"],
            index=0,
            help="Project ZIP table is faster; switch to full US ZIP centroids only when you need nationwide coverage.",
        )
        transparent_3d_fill = st.checkbox(
            "3D transparent ZIP fill",
            value=False,
            help="Adds subtle depth while keeping ZIP polygons transparent and easy to read.",
        )
        default_polygon_stride_index = [1, 2, 3, 4, 5, 6].index(defaults["stride"])
        polygon_stride = st.selectbox(
            "ZIP boundary detail",
            options=[1, 2, 3, 4, 5, 6],
            index=default_polygon_stride_index,
        )
        render_all_covered_boundaries = st.checkbox(
            "Render all covered ZIP boundaries (slow)",
            value=False,
            help="Off = much faster UI. Full coverage stats remain accurate either way.",
        )
        max_rendered_covered_zips = st.slider(
            "Max covered ZIP boundaries to render",
            min_value=200,
            max_value=5000,
            value=defaults["max_rendered_covered_zips"],
            step=100,
            disabled=render_all_covered_boundaries,
            help="Limits map draw load for speed. Does not change coverage calculations.",
        )

    if safe_mode:
        minimal_basemap = True
        zip_dataset_scope = "Project ZIP table"
        polygon_stride = 6
        render_all_covered_boundaries = False
        max_rendered_covered_zips = min(int(max_rendered_covered_zips), 700)
        show_zip_numbers = False
        st.caption("Safe mode active: project ZIP scope, high stride, capped boundaries, labels off.")

    st.caption("Legend: Yellow = chapter centroid ZIP • Green = covered ZIP • Red = uncovered ZIP")
    st.caption(datetime.now().strftime("Updated %Y-%m-%d %H:%M:%S"))

if zip_dataset_scope == "Project ZIP table":
    try:
        _ = load_zip_table()
    except FileNotFoundError as ex:
        st.error(str(ex))
        st.stop()
    zip_geo = geocode_project_zip_centroids()
else:
    zip_geo = load_all_us_zip_centroids()

chapter_center_zip_df = compute_chapter_center_zips()
if not zip_geo.empty:
    zip_geo = ensure_chapter_center_zips_present(zip_geo)

selected_center_zip_df = chapter_center_zip_df[chapter_center_zip_df["Chapter"].isin(selected_chapters)].copy() if not chapter_center_zip_df.empty else pd.DataFrame()
selected_center_zip_map = (
    selected_center_zip_df.groupby("Center Zip Code")["Chapter"].agg(lambda s: ", ".join(sorted(set(s)))).to_dict()
    if not selected_center_zip_df.empty
    else {}
)
selected_center_zip_keys = tuple(sorted(selected_center_zip_map.keys()))

if zip_geo.empty:
    logger.warning("zip_geo_empty dataset_scope=%s", zip_dataset_scope)
    st.warning("ZIP geocoding not available yet. Install `pgeocode` to enable ZIP highlighting.")
elif not zip_geo.empty:
    if zip_dataset_scope == "All US ZIP centroids":
        st.caption(f"Using full US ZIP centroid dataset: {len(zip_geo):,} ZIPs loaded.")
    else:
        st.caption(f"Using project ZIP dataset: {len(zip_geo):,} ZIPs loaded.")
    logger.info("zip_dataset_loaded scope=%s count=%d", zip_dataset_scope, len(zip_geo))

chapter_circles = circles_from_sidebar(selected_chapters)
all_circles = chapter_circles.copy()
coverage_signature = (
    zip_dataset_scope,
    tuple((c["name"], float(c["radius_miles"])) for c in sorted(all_circles, key=lambda x: x["name"])),
)

distance_matrix: pd.DataFrame = pd.DataFrame()
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
    covered_cache_key = (coverage_signature, len(zip_geo_with_coverage))
    cached_covered = st.session_state.get("_covered_mask") if st.session_state.get("_covered_cache_key") == covered_cache_key else None
    if cached_covered is not None:
        covered = cached_covered
    else:
        active_chapters = [c for c in all_circles if c["name"] in distance_matrix.columns]
        if active_chapters:
            dm = distance_matrix[[c["name"] for c in active_chapters]].to_numpy(dtype=float)
            radii = np.array([float(c["radius_miles"]) for c in active_chapters], dtype=float)
            covered = (dm <= radii[None, :]).any(axis=1)
        else:
            covered = np.zeros(len(zip_geo_with_coverage), dtype=bool)
        st.session_state["_covered_mask"] = covered
        st.session_state["_covered_cache_key"] = covered_cache_key
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
    center_zip_lookup_by_chapter = (
        selected_center_zip_df.set_index("Chapter")["Center Zip Code"].to_dict()
        if not selected_center_zip_df.empty else {}
    )
    chapter_df["radius_meters"] = chapter_df["radius_miles"] * 1609.34
    chapter_df["fill_color"] = [[37, 99, 235, 45]] * len(chapter_df)
    chapter_df["line_color"] = [[37, 99, 235, 235]] * len(chapter_df)
    chapter_pickable = False

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
    # Chapter name labels — skip in Safe Mode for faster draw.
    if not safe_mode:
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
center_chapter_lookup: dict[str, str] = selected_center_zip_map.copy() if selected_center_zip_map else {}
center_zip_set: set[str] = set(selected_center_zip_keys)
zip_polygons: list[dict] = []
covered_zips: tuple[str, ...] = ()
covered_zips_all: tuple[str, ...] = ()
skipped_covered_zip_count = 0
rendered_covered_zip_set: set[str] = set()
if not zip_geo_with_coverage.empty:
    covered_zips_all = tuple(
        zip_geo_with_coverage.loc[zip_geo_with_coverage["covered"], "Zip Code"].astype(str).str.zfill(5).tolist()
    )
    requested_render_cap = len(covered_zips_all) if render_all_covered_boundaries else int(max_rendered_covered_zips)
    hard_cap = ALL_US_RENDER_CAP if zip_dataset_scope == "All US ZIP centroids" else ABSOLUTE_RENDER_CAP
    effective_render_cap = max(1, min(requested_render_cap, hard_cap))
    covered_zips = covered_zips_all[:effective_render_cap]

    # Near real-time preview: while editing radii, defer heavy covered-boundary rendering
    # until Apply is clicked. Center ZIPs still render.
    just_applied = bool(st.session_state.get("_radius_just_applied", False))
    defer_covered_boundaries = bool(near_realtime_mode and show_chapter_radius_controls and not just_applied)
    if defer_covered_boundaries:
        covered_zips = tuple()

    rendered_covered_zip_set = set(covered_zips)
    skipped_covered_zip_count = max(0, len(covered_zips_all) - len(covered_zips))
    center_zips = tuple(selected_center_zip_keys)
    render_zips = tuple(dict.fromkeys([*covered_zips, *center_zips]))
    st.session_state["inside_zips_all"] = covered_zips_all

    if just_applied:
        st.session_state["_radius_just_applied"] = False

    if render_zips:
        map_key = f"_zip_polygon_map_stride_{polygon_stride}"
        polygon_map_for_stride: dict[str, dict] = st.session_state.get(map_key, {})
        missing_render_zips = tuple(z for z in render_zips if z not in polygon_map_for_stride)

        if missing_render_zips:
            with st.spinner(f"Loading {len(missing_render_zips):,} new ZIP boundaries..."):
                try:
                    fetched_missing = load_cached_polygons_for_zips(missing_render_zips, point_stride=polygon_stride)
                    polygon_map_for_stride.update(fetched_missing)
                    st.session_state[map_key] = polygon_map_for_stride
                except Exception as ex:
                    st.warning(f"ZIP boundaries could not load: {ex}")

        zip_polygons = [polygon_map_for_stride[z] for z in render_zips if z in polygon_map_for_stride]
        st.session_state["_zip_polygons"] = zip_polygons
    else:
        st.session_state["_zip_polygons"] = []
        zip_polygons = []
# Render ZIP polygons with coverage highlighting
# Cache assembled polygon_features in session state — skip rebuild if inputs unchanged.
_pfeat_key = (coverage_signature, polygon_stride, selected_center_zip_keys)
if zip_polygons and st.session_state.get("_pfeat_cache_key") != _pfeat_key:
    _polygon_features: list[dict] = []
    _center_polygon_features: list[dict] = []
    _non_center_polygon_features: list[dict] = []
    for feature in zip_polygons:
        z = feature["properties"]["zip"]
        center_zip_flag = z in center_zip_set
        fill_color = [255, 215, 0, 255] if center_zip_flag else [22, 163, 74, 130]
        line_color = [0, 0, 0, 255] if center_zip_flag else [15, 15, 15, 210]
        _feature = {
            "type": "Feature",
            "properties": {
                "zip": z,
                "fill_color": fill_color,
                "line_color": line_color,
                "hover_title": f"ZIP {z}",
                "hover_type": "Center ZIP" if center_zip_flag else "Covered ZIP",
                "hover_detail": (
                    f"Center ZIP for: {center_chapter_lookup.get(z, '')}"
                    if center_zip_flag
                    else "Inside selected chapter radius"
                ),
            },
            "geometry": feature["geometry"],
        }
        _polygon_features.append(_feature)
        if center_zip_flag:
            _center_polygon_features.append(_feature)
        else:
            _non_center_polygon_features.append(_feature)
    st.session_state["_polygon_features"] = _polygon_features
    st.session_state["_center_polygon_features"] = _center_polygon_features
    st.session_state["_non_center_polygon_features"] = _non_center_polygon_features
    st.session_state["_pfeat_cache_key"] = _pfeat_key

polygon_features: list[dict] = st.session_state.get("_polygon_features", []) if zip_polygons else []
if polygon_features:
    center_polygon_features = st.session_state.get("_center_polygon_features")
    non_center_polygon_features = st.session_state.get("_non_center_polygon_features")
    if center_polygon_features is None or non_center_polygon_features is None:
        center_polygon_features = [f for f in polygon_features if f.get("properties", {}).get("hover_type") == "Center ZIP"]
        non_center_polygon_features = [f for f in polygon_features if f.get("properties", {}).get("hover_type") != "Center ZIP"]

    if non_center_polygon_features:
        layers.append(
            pdk.Layer(
                "GeoJsonLayer",
                data={"type": "FeatureCollection", "features": non_center_polygon_features},
                stroked=True,
                filled=True,
                get_fill_color="properties.fill_color",
                get_line_color="properties.line_color",
                line_width_min_pixels=2,
                extruded=transparent_3d_fill,
                wireframe=transparent_3d_fill,
                get_elevation=100,
                pickable=True,
                auto_highlight=True,
                highlight_color=[59, 130, 246, 180],
            )
        )

    if center_polygon_features:
        layers.append(
            pdk.Layer(
                "GeoJsonLayer",
                data={"type": "FeatureCollection", "features": center_polygon_features},
                stroked=True,
                filled=True,
                get_fill_color="properties.fill_color",
                get_line_color="properties.line_color",
                line_width_min_pixels=2,
                extruded=False,
                wireframe=False,
                get_elevation=0,
                pickable=True,
                auto_highlight=True,
                highlight_color=[0, 0, 0, 220],
            )
        )

# ZIP code label layers.
zip_labels_rendered = 0
zip_label_style = compute_zip_label_style(map_zoom, size_scale=zip_label_size)
# Always show center ZIPs so the main chapter anchors are visible.
if not zip_geo_with_coverage.empty:
    center_label_points = zip_geo_with_coverage[zip_geo_with_coverage["is_center_zip"]].copy()
    if not center_label_points.empty:
        if "longitude" in center_label_points.columns and "latitude" in center_label_points.columns:
            center_label_points = center_label_points.rename(columns={"longitude": "lon", "latitude": "lat"})
        center_label_points["label"] = center_label_points["Zip Code"].astype(str).str.zfill(5)
        layers.append(
            pdk.Layer(
                "TextLayer",
                data=center_label_points,
                get_position="[lon, lat]",
                get_text="label",
                get_color=[20, 20, 20, 245],
                get_size=max(900, zip_label_style["size_meters"] * 1.2),
                size_units="'meters'",
                size_min_pixels=max(8, zip_label_style["min_pixels"] + 2),
                size_max_pixels=max(14, zip_label_style["max_pixels"] + 2),
                min_zoom=3.2,
                get_angle=0,
                get_text_anchor="'middle'",
                get_alignment_baseline="'center'",
                pickable=False,
            )
        )
        layers.append(
            pdk.Layer(
                "TextLayer",
                data=center_label_points,
                get_position="[lon, lat]",
                get_text="label",
                get_color=[255, 220, 0, 255],
                get_size=max(760, zip_label_style["size_meters"]),
                size_units="'meters'",
                size_min_pixels=max(6, zip_label_style["min_pixels"] + 1),
                size_max_pixels=max(12, zip_label_style["max_pixels"] + 1),
                min_zoom=3.2,
                get_angle=0,
                get_text_anchor="'middle'",
                get_alignment_baseline="'center'",
                pickable=False,
            )
        )
        zip_labels_rendered += len(center_label_points)

if show_zip_numbers and not zip_geo_with_coverage.empty:
    label_points = zip_geo_with_coverage[
        zip_geo_with_coverage["covered"]
        & ~zip_geo_with_coverage["is_center_zip"]
        & zip_geo_with_coverage["Zip Code"].astype(str).str.zfill(5).isin(rendered_covered_zip_set)
    ].copy()
    if not label_points.empty:
        if "longitude" in label_points.columns and "latitude" in label_points.columns:
            label_points = label_points.rename(columns={"longitude": "lon", "latitude": "lat"})
        label_points["label"] = label_points["Zip Code"].astype(str).str.zfill(5)
        label_points["text_color"] = [[35, 35, 35, 220]] * len(label_points)
        zip_labels_rendered += len(label_points)
        layers.append(
            pdk.Layer(
                "TextLayer",
                data=label_points,
                get_position="[lon, lat]",
                get_text="label",
                get_color="text_color",
                get_size=zip_label_style["size_meters"],
                size_units="'meters'",
                size_min_pixels=zip_label_style["min_pixels"],
                size_max_pixels=zip_label_style["max_pixels"],
                min_zoom=zip_label_style["min_zoom"],
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
status_c4.metric("Boundaries shown", f"{len(zip_polygons):,}")
status_c5.metric("ZIP labels", f"{zip_labels_rendered:,}")

if skipped_covered_zip_count > 0:
    st.caption(
        f"Rendering fast mode: {skipped_covered_zip_count:,} covered ZIP boundaries not drawn to keep the UI responsive and below Streamlit payload limits."
    )

if near_realtime_mode and show_chapter_radius_controls and not st.session_state.get("_radius_just_applied", False):
    st.caption("Near real-time preview active: full covered ZIP boundaries render after you click Apply.")

tooltip_html = "<b>{properties.hover_title}</b><br/><b>Type:</b> {properties.hover_type}<br/>{properties.hover_detail}"

deck_map_provider = None if minimal_basemap else "carto"
deck_map_style = None if minimal_basemap else "light"

deck = pdk.Deck(
    layers=layers,
    initial_view_state=pdk.ViewState(latitude=center_lat, longitude=center_lon, zoom=map_zoom, pitch=0),
    tooltip={"html": tooltip_html, "style": {"backgroundColor": "#111827", "color": "white"}},
    map_provider=deck_map_provider,
    map_style=deck_map_style,
)

map_tab, details_tab = st.tabs(["Map", "Details"])

with map_tab:
    st.pydeck_chart(deck, width="stretch", height=680)

if not zip_geo_with_coverage.empty:
    with details_tab:
        with st.expander("ZIP coverage details", expanded=False):
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

            total_inside = len(st.session_state.get("inside_zips_all") or [])
            loaded_inside = len(zip_polygons)
            if total_inside > 0:
                st.write(f"Map showing **{loaded_inside} of {total_inside}** ZIP boundaries inside circles.")
            else:
                st.write("No ZIP boundaries are inside the selected circles right now.")

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
