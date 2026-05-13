# BSF Chapter Coverage Map

Interactive Streamlit application for visualizing chapter service areas and ZIP code coverage in real-time.

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

## Running the App

```bash
streamlit run app.py
```

The app will open in your browser at `http://localhost:8501`

## Data Requirements

The app expects CSV files in `c:/Users/moein/Downloads/`:
- `Chapters_2020_zip codes.csv` - ZIP code to chapter mappings
- `chapter locations lat_long_Chapter City.csv` - Chapter coordinates

## Usage

1. **Select Chapters**: Use the sidebar to choose which chapters to display
2. **Adjust Radii**: Check "Allow adjustable radii?" to modify coverage radius for each chapter
3. **View Map**: The interactive map shows radius circles for each selected chapter
4. **Analyze Coverage**: Use the "Analyze Coverage Gaps" button to identify unmapped areas

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
