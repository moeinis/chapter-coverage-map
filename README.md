# BSF Chapter Coverage Map

This repository includes two UIs:

1. **Primary UI (recommended):** Streamlit (`app.py`)
2. Optional experimental UI: MapLibre + FastAPI (`map_api.py` + `web/`)

The Streamlit app is the production-ready default experience.

## Features

✨ **Interactive Map Visualization**
- Radius circles around each chapter location
- Real-time adjustable radii for coverage analysis
- Hover tooltips and clickable markers

🎯 **Chapter Selection**
- Select which chapters to display
- Adjust coverage radius for each chapter dynamically
- View multiple chapters simultaneously

📍 **ZIP Code Coverage**
- Load and display ZIP code to chapter mappings
- Analyze coverage gaps
- Identify uncovered areas

📊 **Coverage Analysis**
- Summary statistics (total ZIPs, chapters, states)
- Coverage gap detection
- Export data as CSV

## Installation

1. Install dependencies:
```bash
pip install -r requirements.txt
```

For reproducible production installs, prefer:

```bash
pip install -r requirements.lock.txt
```

## Run (Production: Streamlit)

```bash
streamlit run app.py
```

Open: `http://localhost:8501`

### Recommended Windows launch (stable)

Use the included startup script to cleanly stop stale listeners on port 8501 and run one fresh app instance:

```powershell
.\start_app.ps1
```

Health check:

```powershell
Invoke-WebRequest -Uri "http://localhost:8501/_stcore/health" -UseBasicParsing
```

## Optional Run (MapLibre + FastAPI)

```bash
python -m uvicorn map_api:app --host 0.0.0.0 --port 8601
```

Open: `http://localhost:8601`

API health check:

```bash
curl http://localhost:8601/api/health
```

## Docker (Streamlit production image)

Build:

```bash
docker build -t chapter-coverage-map:latest .
```

Run:

```bash
docker run --rm -p 8501:8501 chapter-coverage-map:latest
```

Then open `http://localhost:8501`.

## CI smoke checks

A GitHub Actions workflow is included at `.github/workflows/ci-smoke.yml`.

It validates:
- Python compile check (`app.py`, `map_api.py`)
- API module import smoke test (`map_api`)

### Why this is faster

- No Streamlit full-script rerun per UI interaction
- Client-side map rendering via MapLibre (GPU-accelerated)
- On-demand ZIP polygon loading through API limits

## Data Requirements

Required CSV:
- `Chapters_2020_zip codes.csv` (ZIP code to chapter mappings)

The app resolves this file in this order:
1. Environment variable `BSF_ZIP_TABLE_PATH`
2. `data/Chapters_2020_zip codes.csv`
3. `c:/Users/moein/Downloads/Chapters_2020_zip codes.csv` (legacy fallback)

### Recommended setup

Set an environment variable for portability:

```bash
setx BSF_ZIP_TABLE_PATH "C:\path\to\Chapters_2020_zip codes.csv"
```

## Usage (Fast frontend)

1. Open `http://localhost:8601`
2. Adjust chapter radius sliders (live circle updates)
3. Click **Apply / Refresh** to load covered ZIP polygons
4. Click **Load Uncovered Layer** only when needed
5. Tune boundary detail and covered ZIP limits for speed/quality

## Chapter Coverage Areas

- **Chicagoland**: 50-mile radius around zip 60120
- **Colorado Springs**: 50-mile radius around zip 80903
- **Dayton**: 50-mile radius around zip 45324
- **Fayetteville**: 50-mile radius around zip 95437
- **Jacksonville**: 100-mile radius around zip 32212
- **Tampa**: 50-mile radius around zip 33608
- **Tennessee**: 60-mile radius around zip 42223
- **Utah**: 75-mile radius around zip 84056
- **Seattle (Puget Sound)**: 60-mile radius
- **Boston (New England)**: 75-mile radius
- **New York**: 100-mile radius
- **Southern California**: 75-mile radius around San Diego
- **Hawaii**: 200-mile radius (statewide)

## Author

BSF (Blue Star Families)
