import streamlit as st
import math
from datetime import datetime
import pandas as pd
import folium
from folium.plugins import Draw
from streamlit_folium import st_folium
from pathlib import Path
import zipfile
import requests
import shapefile


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
st.caption("Drag/resize circles live to update covered ZIP highlights.")


CHAPTERS = {
    "Chicagoland": {"lat": 41.8781, "lon": -87.6298, "city": "Chicago, IL", "radius_miles": 50},
    "Colorado Springs": {"lat": 38.8339, "lon": -104.8202, "city": "Colorado Springs, CO", "radius_miles": 50},
    "Dayton": {"lat": 39.7589, "lon": -84.1997, "city": "Dayton, OH", "radius_miles": 50},
    "Fayetteville": {"lat": 35.0528, "lon": -78.8784, "city": "Fort Bragg, NC", "radius_miles": 50},
    "Jacksonville": {"lat": 30.3322, "lon": -81.6557, "city": "Jacksonville, FL", "radius_miles": 100},
    "Tampa": {"lat": 27.9752, "lon": -82.4994, "city": "Tampa, FL", "radius_miles": 50},
    "Tennessee": {"lat": 36.1627, "lon": -86.7816, "city": "Tennessee", "radius_miles": 60},
    "Utah": {"lat": 40.7608, "lon": -111.8911, "city": "Salt Lake City, UT", "radius_miles": 75},
    "Seattle": {"lat": 47.6062, "lon": -122.3321, "city": "Seattle, WA", "radius_miles": 60},
    "Boston": {"lat": 42.3601, "lon": -71.0589, "city": "Boston, MA", "radius_miles": 75},
    "New York": {"lat": 40.7128, "lon": -74.0059, "city": "New York, NY", "radius_miles": 100},
    "Southern California": {"lat": 32.7157, "lon": -117.1611, "city": "San Diego, CA", "radius_miles": 75},
    "Hawaii": {"lat": 21.3099, "lon": -157.8581, "city": "Honolulu, HI", "radius_miles": 200},
}

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
ZCTA_ZIP_PATH = DATA_DIR / "tl_2020_us_zcta520.zip"
ZCTA_DIR = DATA_DIR / "tl_2020_us_zcta520"
ZCTA_SHP_PATH = ZCTA_DIR / "tl_2020_us_zcta520.shp"
ZCTA_SOURCE_URL = "https://www2.census.gov/geo/tiger/TIGER2020/ZCTA520/tl_2020_us_zcta520.zip"


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


@st.cache_data(show_spinner=False)
def load_zip_table() -> pd.DataFrame:
    data = pd.read_csv("c:/Users/moein/Downloads/Chapters_2020_zip codes.csv")
    data.columns = [c.strip() for c in data.columns]
    data = data[["Zip Code", "BSF Chapter", "State"]].copy()
    data = data.dropna(subset=["Zip Code", "BSF Chapter"])
    data["Zip Code"] = data["Zip Code"].astype(str).str.extract(r"(\d+)")[0].str.zfill(5)
    data = data.dropna(subset=["Zip Code"])
    data = data.drop_duplicates(subset=["Zip Code"]) 
    return data


@st.cache_data(show_spinner=False)
def geocode_zip_centroids(zip_df: pd.DataFrame) -> pd.DataFrame:
    try:
        import pgeocode  # lazy import so app still loads if package is missing

        nomi = pgeocode.Nominatim("us")
        geo = nomi.query_postal_code(zip_df["Zip Code"].tolist())[["postal_code", "latitude", "longitude"]]
        geo = geo.rename(columns={"postal_code": "Zip Code"})
        geo["Zip Code"] = geo["Zip Code"].astype(str).str.zfill(5)
        merged = zip_df.merge(geo, on="Zip Code", how="left")
        merged = merged.dropna(subset=["latitude", "longitude"])
        return merged
    except Exception:
        return pd.DataFrame()


@st.cache_data(show_spinner=False)
def load_zip_polygons(zip_codes: tuple[str, ...], point_stride: int = 3) -> list[dict]:
    shp_path = ensure_zcta_files()
    wanted = set(zip_codes)
    reader = shapefile.Reader(shp_path)
    fields = [f[0] for f in reader.fields[1:]]
    features = []

    for sr in reader.shapeRecords():
        rec = dict(zip(fields, sr.record))
        zcta = str(rec.get("ZCTA5CE20", "")).zfill(5)
        if zcta not in wanted:
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

        features.append(
            {
                "type": "Feature",
                "properties": {"zip": zcta},
                "geometry": geometry,
            }
        )

    return features


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


def circle_from_drawn_polygon(feature: dict) -> dict | None:
    geom = feature.get("geometry", {})
    if geom.get("type") != "Polygon":
        return None
    coords = geom.get("coordinates", [])
    if not coords or not coords[0]:
        return None
    ring = coords[0]
    lats = [pt[1] for pt in ring]
    lons = [pt[0] for pt in ring]
    center_lat = sum(lats) / len(lats)
    center_lon = sum(lons) / len(lons)
    edge_lat, edge_lon = ring[0][1], ring[0][0]
    radius = haversine_miles(center_lat, center_lon, edge_lat, edge_lon)
    return {
        "name": "Drawn circle",
        "lat": center_lat,
        "lon": center_lon,
        "radius_miles": max(radius, 0.1),
    }


with st.sidebar:
    st.subheader("⚙️ Controls")
    selected_chapters = st.multiselect(
        "Chapters to show",
        list(CHAPTERS.keys()),
        default=["Chicagoland", "Colorado Springs", "Jacksonville", "Tampa", "Utah"],
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
    max_zip_points = st.slider("ZIP markers on map", min_value=200, max_value=6000, value=2000, step=200)
    polygon_stride = st.selectbox("Polygon detail (faster ↔ smoother)", options=[1, 2, 3, 4], index=2)
    show_zip_polygons = st.checkbox("Show ZIP polygons on editable map", value=True)
    st.info("Tip: Use map edit mode to drag/resize circles live.")
    st.caption(datetime.now().strftime("Updated %Y-%m-%d %H:%M:%S"))

zip_table = load_zip_table()
zip_geo = geocode_zip_centroids(zip_table)

if zip_geo.empty:
    st.warning("ZIP geocoding not available yet. Install `pgeocode` to enable ZIP highlighting.")

zip_polygons = []
if show_zip_polygons and not zip_table.empty:
    with st.spinner("Loading ZIP polygons..."):
        try:
            zip_polygons = load_zip_polygons(tuple(zip_table["Zip Code"].tolist()), point_stride=polygon_stride)
        except Exception as ex:
            st.warning(f"ZIP polygon layer could not be loaded: {ex}")

chapter_circles = circles_from_sidebar(selected_chapters)

if "drawn_features" not in st.session_state:
    st.session_state["drawn_features"] = []

all_circles = chapter_circles.copy()
for feature in st.session_state["drawn_features"]:
    parsed = circle_from_drawn_polygon(feature)
    if parsed is not None:
        all_circles.append(parsed)

zip_geo_with_coverage = pd.DataFrame()
if not zip_geo.empty:
    covered = []
    for row in zip_geo.itertuples(index=False):
        is_covered = any(
            haversine_miles(row.latitude, row.longitude, c["lat"], c["lon"]) <= c["radius_miles"]
            for c in all_circles
        )
        covered.append(is_covered)
    zip_geo_with_coverage = zip_geo.copy()
    zip_geo_with_coverage["covered"] = covered

m = folium.Map(location=[39.7, -98.5], zoom_start=4, tiles="CartoDB positron")

for c in chapter_circles:
    folium.Circle(
        location=[c["lat"], c["lon"]],
        radius=c["radius_miles"] * 1609.34,
        color="#2563eb",
        weight=2,
        fill=True,
        fill_color="#60a5fa",
        fill_opacity=0.16,
        popup=f"{c['name']} ({c['radius_miles']:.0f} mi)",
    ).add_to(m)
    folium.Marker(
        [c["lat"], c["lon"]],
        tooltip=c["name"],
        popup=f"{CHAPTERS[c['name']]['city']}<br>{c['radius_miles']:.0f} mi",
    ).add_to(m)

if not zip_geo_with_coverage.empty:
    coverage_lookup = {
        str(row["Zip Code"]).zfill(5): bool(row["covered"]) for _, row in zip_geo_with_coverage.iterrows()
    }

    if show_zip_polygons and zip_polygons:
        polygons_to_draw = zip_polygons[:max_zip_points]
        for feature in polygons_to_draw:
            z = feature["properties"]["zip"]
            covered_flag = coverage_lookup.get(z, False)
            fill_color = "#16a34a" if covered_flag else "#dc2626"
            border_color = "#14532d" if covered_flag else "#7f1d1d"

            folium.GeoJson(
                data=feature,
                style_function=lambda _f, fc=fill_color, bc=border_color: {
                    "fillColor": fc,
                    "color": bc,
                    "weight": 0.8,
                    "fillOpacity": 0.35,
                },
                highlight_function=lambda _f: {
                    "weight": 2.2,
                    "fillOpacity": 0.55,
                },
                tooltip=f"ZIP {z} — {'Covered' if covered_flag else 'Not covered'}",
            ).add_to(m)
    else:
        to_plot_main = zip_geo_with_coverage.rename(
            columns={"Zip Code": "zip", "BSF Chapter": "chapter", "State": "state"}
        ).head(max_zip_points)
        for row in to_plot_main.itertuples(index=False):
            color = "#16a34a" if row.covered else "#dc2626"
            folium.CircleMarker(
                location=[row.latitude, row.longitude],
                radius=3,
                color=color,
                fill=True,
                fill_color=color,
                fill_opacity=0.85,
                popup=f"ZIP {row.zip}<br>{row.chapter}<br>{'Covered' if row.covered else 'Not covered'}",
            ).add_to(m)

draw = Draw(
    export=False,
    draw_options={
        "polyline": False,
        "rectangle": False,
        "polygon": False,
        "marker": False,
        "circlemarker": False,
        "circle": True,
    },
    edit_options={"edit": True, "remove": True},
)
draw.add_to(m)

map_state = st_folium(
    m,
    height=620,
    width=None,
    returned_objects=["all_drawings"],
    key="coverage_map",
)

new_drawings = map_state.get("all_drawings") or []
if new_drawings != st.session_state["drawn_features"]:
    st.session_state["drawn_features"] = new_drawings
    st.rerun()

if not zip_geo_with_coverage.empty:

    with st.expander("ZIP coverage details", expanded=True):
        c1, c2, c3 = st.columns(3)
        c1.metric("Total ZIPs", int(len(zip_geo_with_coverage)))
        c2.metric("Covered ZIPs", int(zip_geo_with_coverage["covered"].sum()))
        c3.metric("Uncovered ZIPs", int((~zip_geo_with_coverage["covered"]).sum()))

        sample = zip_geo_with_coverage[["Zip Code", "BSF Chapter", "State", "covered"]].copy()
        sample["covered"] = sample["covered"].map({True: "✅ Covered", False: "❌ Not covered"})
        st.dataframe(sample.head(500), use_container_width=True)

    st.caption(
        f"Live map is showing first {min(max_zip_points, len(zip_geo_with_coverage)):,} ZIP polygons "
        "(green = covered, red = not covered)."
    )
