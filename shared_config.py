from __future__ import annotations

# Single source of truth for chapter definitions used by both Streamlit and FastAPI.
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
