const map = new maplibregl.Map({
  container: "map",
  style: "https://demotiles.maplibre.org/style.json",
  center: [-98.5, 39.7],
  zoom: 3.4,
});

let chapters = {};
let includeUncovered = false;
let mapReady = false;
let pendingZipFeatureCollection = { type: "FeatureCollection", features: [] };
let selectedChapters = [];

function el(id) {
  return document.getElementById(id);
}

function chapterId(name) {
  return name.toLowerCase().replace(/[^a-z0-9]+/g, "-");
}

function chapterCard(name, cfg) {
  const id = chapterId(name);
  return `
    <div class="chapter">
      <label class="chapter-head">
        <input type="checkbox" id="sel-${id}" checked />
        <span class="chapter-title">${name}</span>
      </label>
      <div class="chapter-subtitle">${cfg.city}</div>
      <input type="range" min="10" max="250" step="5" value="${cfg.radius_miles}" id="rad-${id}" />
      <div class="value" id="val-${id}">${cfg.radius_miles} mi</div>
    </div>
  `;
}

async function loadChapters() {
  const res = await fetch("/api/chapters");
  const data = await res.json();
  chapters = data.chapters;

  const picks = Object.keys(chapters);
  selectedChapters = [...picks];
  el("chapterControls").innerHTML = picks.map((k) => chapterCard(k, chapters[k])).join("");

  for (const k of picks) {
    const id = chapterId(k);
    const slider = el(`rad-${id}`);
    const check = el(`sel-${id}`);
    slider.addEventListener("input", () => {
      el(`val-${id}`).textContent = `${slider.value} mi`;
      drawChapterRings();
    });
    check.addEventListener("change", () => {
      selectedChapters = picks.filter((name) => el(`sel-${chapterId(name)}`).checked);
      drawChapterRings();
    });
  }

  el("selectAllBtn").addEventListener("click", () => {
    for (const k of picks) el(`sel-${chapterId(k)}`).checked = true;
    selectedChapters = [...picks];
    drawChapterRings();
  });

  el("clearAllBtn").addEventListener("click", () => {
    for (const k of picks) el(`sel-${chapterId(k)}`).checked = false;
    selectedChapters = [];
    drawChapterRings();
    setZipData({ type: "FeatureCollection", features: [] });
    el("stats").innerHTML = "No chapters selected.";
  });
}

function getCircles() {
  return selectedChapters.map((name) => ({
    name,
    radius_miles: Number(el(`rad-${chapterId(name)}`).value),
  }));
}

function chapterRingsGeoJson() {
  const features = [];
  for (const c of getCircles()) {
    const cfg = chapters[c.name];
    const radiusKm = c.radius_miles * 1.60934;
    const poly = turf.circle([cfg.lon, cfg.lat], radiusKm, { steps: 64, units: "kilometers" });
    poly.properties = { name: c.name };
    features.push(poly);
  }
  return { type: "FeatureCollection", features };
}

function ensureLayers() {
  if (!map.getSource("chapter-rings")) {
    map.addSource("chapter-rings", { type: "geojson", data: chapterRingsGeoJson() });
    map.addLayer({
      id: "chapter-rings-fill",
      type: "fill",
      source: "chapter-rings",
      paint: { "fill-color": "#2563eb", "fill-opacity": 0.12 },
    });
    map.addLayer({
      id: "chapter-rings-line",
      type: "line",
      source: "chapter-rings",
      paint: { "line-color": "#1d4ed8", "line-width": 1.5 },
    });
  }

  if (!map.getSource("zip-polys")) {
    map.addSource("zip-polys", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
    map.addLayer({
      id: "zip-fill",
      type: "fill",
      source: "zip-polys",
      paint: {
        "fill-color": ["case", ["get", "covered"], "#16a34a", "#dc2626"],
        "fill-opacity": 0.35,
      },
    });
    map.addLayer({
      id: "zip-line",
      type: "line",
      source: "zip-polys",
      paint: {
        "line-color": ["case", ["get", "covered"], "#14532d", "#7f1d1d"],
        "line-width": 0.8,
      },
    });
  }
}

function drawChapterRings() {
  const src = map.getSource("chapter-rings");
  if (src) src.setData(chapterRingsGeoJson());
}

function setZipData(featureCollection) {
  pendingZipFeatureCollection = featureCollection;
  const src = map.getSource("zip-polys");
  if (src) src.setData(featureCollection);
}

async function refreshPolygons() {
  const applyBtn = el("applyBtn");
  const uncoveredBtn = el("loadUncoveredBtn");
  const startedAt = Date.now();

  applyBtn.disabled = true;
  uncoveredBtn.disabled = true;

  if (!getCircles().length) {
    el("stats").innerHTML = "Select at least one chapter.";
    applyBtn.disabled = false;
    uncoveredBtn.disabled = false;
    return;
  }

  const payload = {
    circles: getCircles(),
    stride: Number(el("stride").value),
    covered_limit: Number(el("coveredLimit").value),
    include_uncovered: includeUncovered,
    uncovered_limit: includeUncovered ? 500 : 0,
  };

  el("stats").innerHTML = "Loading polygons...";
  try {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 120000);

    const res = await fetch("/api/polygons", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      signal: controller.signal,
    });
    clearTimeout(timeout);

    if (!res.ok) {
      throw new Error(`Polygon API failed: ${res.status}`);
    }

    const data = await res.json();
    setZipData(data.featureCollection);

    el("stats").innerHTML = `
      <b>Total ZIPs:</b> ${data.stats.total_zips}<br/>
      <b>Covered:</b> ${data.stats.covered_total}<br/>
      <b>Uncovered:</b> ${data.stats.uncovered_total}<br/>
      <b>Rendered now:</b> ${data.stats.rendered_features}<br/>
      <b>Request time:</b> ${Date.now() - startedAt} ms
    `;
  } catch (e) {
    el("stats").innerHTML = `<b>Error:</b> ${String(e)}`;
  } finally {
    applyBtn.disabled = false;
    uncoveredBtn.disabled = false;
  }
}

el("coveredLimit").addEventListener("input", (e) => {
  el("coveredLimitValue").textContent = e.target.value;
});

el("applyBtn").addEventListener("click", async () => {
  includeUncovered = false;
  drawChapterRings();
  await refreshPolygons();
});

el("loadUncoveredBtn").addEventListener("click", async () => {
  includeUncovered = true;
  await refreshPolygons();
});

map.on("load", () => {
  mapReady = true;
  ensureLayers();
  drawChapterRings();
  setZipData(pendingZipFeatureCollection);
});

// Initialize controls/data immediately (don't block on basemap style load)
(async () => {
  try {
    await loadChapters();
    el("stats").innerHTML = "Ready. Click <b>Apply / Refresh</b> to load covered ZIP polygons for selected chapters.";
    if (mapReady) {
      drawChapterRings();
      setZipData(pendingZipFeatureCollection);
    }
  } catch (e) {
    el("stats").innerHTML = `<b>Error:</b> ${String(e)}`;
  }
})();
