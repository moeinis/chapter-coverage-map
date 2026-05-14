from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

# Ensure project root is importable when executing as `python scripts/ci_api_smoke.py`.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import map_api


def main() -> None:
    client = TestClient(map_api.app)

    health = client.get("/api/health")
    assert health.status_code == 200, f"health status={health.status_code}"
    health_payload = health.json()
    assert health_payload.get("status") == "ok", f"unexpected health payload: {health_payload}"

    chapters = client.get("/api/chapters")
    assert chapters.status_code == 200, f"chapters status={chapters.status_code}"
    chapters_payload = chapters.json()
    chapter_map = chapters_payload.get("chapters", {})
    assert isinstance(chapter_map, dict), "chapters payload must include object 'chapters'"
    assert len(chapter_map) == 13, f"expected 13 chapters, got {len(chapter_map)}"
    assert "Hawaii" in chapter_map, "expected chapter 'Hawaii'"

    # Regression check: invalid chapter selections should fail fast (no data files required).
    invalid = client.post(
        "/api/polygons",
        json={
            "circles": [{"name": "NotARealChapter", "radius_miles": 50}],
            "stride": 6,
            "covered_limit": 80,
            "include_uncovered": False,
            "uncovered_limit": 0,
        },
    )
    assert invalid.status_code == 400, f"invalid polygons status={invalid.status_code}"
    detail = invalid.json().get("detail", "")
    assert "valid chapter" in str(detail).lower(), f"unexpected error detail: {detail}"

    print("ci_api_smoke_ok")


if __name__ == "__main__":
    main()
