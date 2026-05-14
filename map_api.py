from __future__ import annotations

import json
import os
import logging
from functools import lru_cache
from pathlib import Path
from typing import Dict, List
import zipfile

import numpy as np
import pandas as pd
import requests
import shapefile
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from shared_config import CHAPTERS


LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
if not logging.getLogger().handlers:
    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL, logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
logger = logging.getLogger("chapter_coverage_map.api")

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
WEB_DIR = BASE_DIR / "web"
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

app = FastAPI(title="Chapter Coverage Map API")
app.mount("/web", StaticFiles(directory=str(WEB_DIR)), name="web")
app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

# In-memory polygon cache to avoid repeatedly parsing large GeoJSON files on each request.
POLYGON_CACHE_BY_STRIDE: Dict[int, Dict[str, dict]] = {}


class CircleInput(BaseModel):
    name: str
    radius_miles: float


class PolygonRequest(BaseModel):
    circles: List[CircleInput]
    stride: int = 5
    covered_limit: int = 150
    include_uncovered: bool = False
    uncovered_limit: int = 0


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


load_local_env()
logger.info("API environment initialized")


@lru_cache(maxsize=1)
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


@lru_cache(maxsize=1)
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


@lru_cache(maxsize=1)
def geocode_zip_centroids() -> pd.DataFrame:
    import pgeocode

    zip_df = load_zip_table()
    nomi = pgeocode.Nominatim("us")
    geo = nomi.query_postal_code(zip_df["Zip Code"].tolist())[["postal_code", "latitude", "longitude"]]
    geo = geo.rename(columns={"postal_code": "Zip Code"})
    geo["Zip Code"] = geo["Zip Code"].astype(str).str.zfill(5)
    merged = zip_df.merge(geo, on="Zip Code", how="left")
    merged = merged.dropna(subset=["latitude", "longitude"]) 
    return merged


@lru_cache(maxsize=1)
def precompute_distance_matrix() -> pd.DataFrame:
    zip_geo_df = geocode_zip_centroids()
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


@lru_cache(maxsize=1)
def get_zcta_record_index() -> Dict[str, int]:
    """Build one-time index: ZIP -> shapefile record index."""
    shp_path = ensure_zcta_files()
    reader = shapefile.Reader(shp_path)
    fields = [f[0] for f in reader.fields[1:]]
    zcta_idx = fields.index("ZCTA5CE20") if "ZCTA5CE20" in fields else 0

    index_map: Dict[str, int] = {}
    for i, rec in enumerate(reader.iterRecords()):
        try:
            zcta = str(rec[zcta_idx]).zfill(5)
            index_map[zcta] = i
        except Exception:
            continue
    return index_map


def load_zip_polygons_map(zip_codes: tuple[str, ...], point_stride: int = 5) -> Dict[str, dict]:
    shp_path = ensure_zcta_files()
    reader = shapefile.Reader(shp_path)
    index_map = get_zcta_record_index()
    features_by_zip: Dict[str, dict] = {}

    # Direct lookup by record index (fast) instead of scanning whole shapefile per request.
    for zcta in zip_codes:
        rec_idx = index_map.get(str(zcta).zfill(5))
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

        geometry = {"type": "Polygon", "coordinates": rings}
        features_by_zip[zcta] = {
            "type": "Feature",
            "properties": {"zip": zcta},
            "geometry": geometry,
        }

    return features_by_zip


def load_cached_polygons_for_zips(zip_codes: tuple[str, ...], point_stride: int = 5) -> Dict[str, dict]:
    cache_path = DATA_DIR / f"project_zip_polygons_stride_{point_stride}.geojson"

    if point_stride not in POLYGON_CACHE_BY_STRIDE:
        cached_map: Dict[str, dict] = {}
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


def compute_coverage(circles: List[CircleInput]) -> pd.DataFrame:
    zip_geo = geocode_zip_centroids().copy()
    distance_matrix = precompute_distance_matrix()

    covered = np.zeros(len(zip_geo), dtype=bool)
    for c in circles:
        if c.name in distance_matrix.columns:
            covered |= distance_matrix[c.name].to_numpy(dtype=float) <= float(c.radius_miles)

    zip_geo["covered"] = covered
    return zip_geo


@app.get("/")
def root() -> FileResponse:
    return FileResponse(str(WEB_DIR / "index.html"))


@app.get("/api/chapters")
def get_chapters() -> dict:
    logger.info("chapters_requested count=%d", len(CHAPTERS))
    return {"chapters": CHAPTERS}


@app.get("/api/health")
def health() -> dict:
    logger.debug("health_check status=ok")
    return {"status": "ok", "service": "chapter-coverage-map-api"}


@app.post("/api/polygons")
def polygons(req: PolygonRequest) -> dict:
    stride = max(1, min(6, int(req.stride)))
    covered_limit = max(50, min(2000, int(req.covered_limit)))
    uncovered_limit = max(0, min(3000, int(req.uncovered_limit)))

    selected = [c for c in req.circles if c.name in CHAPTERS]
    if not selected:
        logger.warning("polygons_request_invalid no_valid_chapters")
        raise HTTPException(status_code=400, detail="At least one valid chapter is required.")

    zip_geo = compute_coverage(selected)
    zips = zip_geo["Zip Code"].astype(str).str.zfill(5)
    coverage_lookup = dict(zip(zips, zip_geo["covered"].astype(bool)))

    target_uncovered = uncovered_limit if req.include_uncovered else 0
    covered_zip_batch = tuple(zips[zip_geo["covered"]].tolist()[:covered_limit])
    uncovered_zip_batch = tuple(zips[~zip_geo["covered"]].tolist()[:target_uncovered])
    request_zip_batch = covered_zip_batch + uncovered_zip_batch

    polygon_map = load_cached_polygons_for_zips(request_zip_batch, point_stride=stride) if request_zip_batch else {}
    features = []
    for z in request_zip_batch:
        f = polygon_map.get(str(z).zfill(5))
        if not f:
            continue
        features.append(
            {
                "type": "Feature",
                "properties": {
                    **f.get("properties", {}),
                    "covered": bool(coverage_lookup.get(str(z).zfill(5), False)),
                },
                "geometry": f.get("geometry", {}),
            }
        )

    logger.info(
        "polygons_request_ok chapters=%d stride=%d covered_limit=%d include_uncovered=%s rendered=%d requested=%d",
        len(selected),
        stride,
        covered_limit,
        str(bool(req.include_uncovered)).lower(),
        len(features),
        len(request_zip_batch),
    )

    return {
        "featureCollection": {"type": "FeatureCollection", "features": features},
        "stats": {
            "total_zips": int(len(zip_geo)),
            "covered_total": int(zip_geo["covered"].sum()),
            "uncovered_total": int((~zip_geo["covered"]).sum()),
            "rendered_features": int(len(features)),
        },
    }
