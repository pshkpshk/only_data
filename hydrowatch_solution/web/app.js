/* HydroWatch Amur — интерактивная карта динамики затопления (Leaflet, без сборки). */
(function () {
  const LAYERS = ["water_pre", "water_peak", "flood", "receded"];
  const COLORS = { water_pre: "#1f77b4", water_peak: "#00b0f6", flood: "#e63946", receded: "#ffb703" };
  const TITLES = { water_pre: "Вода «до»", water_peak: "Вода «пик»", flood: "Прирост (затопление)", receded: "Убыль" };

  const $ = (id) => document.getElementById(id);
  const map = L.map("map", { zoomControl: true }).setView([50.3, 127.6], 8);
  const basemaps = {
    "OpenStreetMap": L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19, attribution: "© OpenStreetMap" }),
    "Спутник (Esri)": L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}", { maxZoom: 18, attribution: "Esri World Imagery" }),
    "Без подложки": L.tileLayer("", { attribution: "" }),
  };
  basemaps["OpenStreetMap"].addTo(map);
  L.control.layers(basemaps, {}, { position: "topright", collapsed: true }).addTo(map);
  L.control.scale({ metric: true, imperial: false }).addTo(map);

  const legend = L.control({ position: "bottomright" });
  legend.onAdd = function () {
    const div = L.DomUtil.create("div", "legend");
    div.innerHTML = LAYERS.map((l) => `<div><i style="background:${COLORS[l]}"></i>${TITLES[l]}</div>`).join("") +
      `<div><i style="background:transparent;border:1px dashed #38bdf8"></i>граница пары / запроса</div>`;
    return div;
  };
  legend.addTo(map);

  const state = { pairs: [], overlays: {}, visible: { water_pre: false, water_peak: true, flood: true, receded: false }, footprints: null, queryLayer: null, geometry: null, job: null, swipe: false };

  // ------------------------------------------------------------------ catalog
  async function loadPairs() {
    const r = await fetch("/api/pairs");
    const data = await r.json();
    state.pairs = data.pairs;
    const sel = $("pairSelect");
    sel.innerHTML = "";
    state.footprints = L.featureGroup().addTo(map);
    data.pairs.forEach((p) => {
      const opt = document.createElement("option");
      opt.value = p.pair_id;
      opt.textContent = `${p.event_name} — ${p.aoi_name} (${p.date_pre} → ${p.date_peak})`;
      sel.appendChild(opt);
      L.geoJSON(p.footprint_wgs84, { style: { color: "#38bdf8", weight: 1, dashArray: "4 4", fill: false } })
        .bindTooltip(`${p.aoi_name}<br>${p.event_name}`, { sticky: true })
        .on("click", () => { sel.value = p.pair_id; onPairChange(); })
        .addTo(state.footprints);
    });
    const preferred = data.pairs.find((p) => p.pair_id === "flood_2021_08_zeya__svobodny") || data.pairs[0];
    sel.value = preferred.pair_id;
    onPairChange(true);
  }

  function currentPair() { return state.pairs.find((p) => p.pair_id === $("pairSelect").value); }

  function onPairChange(initial) {
    const p = currentPair();
    if (!p) return;
    $("datePre").value = p.date_pre;
    $("datePeak").value = p.date_peak;
    const b = p.bbox_wgs84;
    map.fitBounds([[b[1], b[0]], [b[3], b[2]]], { padding: [10, 10] });
    if (!initial) clearOverlays();
    runAnalysis();
  }

  // ------------------------------------------------------------------ geometry input
  function parseGeometryInput() {
    const text = $("geometryInput").value.trim();
    if (!text) return null;
    const value = JSON.parse(text);
    if (Array.isArray(value)) return { bbox: value };
    return { geometry: value };
  }

  function showQueryGeometry(geom) {
    if (state.queryLayer) { map.removeLayer(state.queryLayer); state.queryLayer = null; }
    if (!geom) return;
    state.queryLayer = L.geoJSON(geom, { style: { color: "#f8fafc", weight: 2, dashArray: "6 4", fill: false } }).addTo(map);
  }

  $("useViewBtn").onclick = () => {
    const b = map.getBounds();
    $("geometryInput").value = JSON.stringify([+b.getWest().toFixed(5), +b.getSouth().toFixed(5), +b.getEast().toFixed(5), +b.getNorth().toFixed(5)]);
  };
  $("clearGeomBtn").onclick = () => { $("geometryInput").value = ""; showQueryGeometry(null); };

  // simple rectangle drawing (mousedown → drag → mouseup)
  let drawing = false, drawStart = null, drawRect = null;
  $("drawBtn").onclick = () => {
    drawing = true; map.dragging.disable(); map.getContainer().style.cursor = "crosshair";
    setStatus("Нарисуйте прямоугольник: зажмите левую кнопку мыши и протяните.");
  };
  map.on("mousedown", (e) => { if (!drawing) return; drawStart = e.latlng; drawRect = L.rectangle([drawStart, drawStart], { color: "#f8fafc", weight: 2, dashArray: "6 4", fill: false }).addTo(map); });
  map.on("mousemove", (e) => { if (drawing && drawRect) drawRect.setBounds(L.latLngBounds(drawStart, e.latlng)); });
  map.on("mouseup", (e) => {
    if (!drawing || !drawRect) return;
    const b = L.latLngBounds(drawStart, e.latlng);
    $("geometryInput").value = JSON.stringify([+b.getWest().toFixed(5), +b.getSouth().toFixed(5), +b.getEast().toFixed(5), +b.getNorth().toFixed(5)]);
    map.removeLayer(drawRect); drawRect = null; drawing = false; drawStart = null;
    map.dragging.enable(); map.getContainer().style.cursor = "";
    setStatus("bbox записан — нажмите «Рассчитать».");
  });

  // ------------------------------------------------------------------ analysis
  function setStatus(text, isError) { const s = $("status"); s.textContent = text; s.className = "status" + (isError ? " error" : ""); }

  function setStatusHtml(html, isError) { const s = $("status"); s.innerHTML = html; s.className = "status" + (isError ? " error" : ""); }
  const pairLabel = (id) => { const p = state.pairs.find((x) => x.pair_id === id); return p ? `${p.event_name} — ${p.aoi_name} (${p.date_pre} → ${p.date_peak})` : id; };

  // Даты, отличные от дат выбранной пары, переключают запрос в режим «территория + даты»:
  // сервер сам подбирает подготовленную пару наблюдений (допуск ±date_tolerance_days) или
  // возвращает список доступных пар для этой территории.
  function datesDifferFromPair(p) {
    const pre = $("datePre").value, peak = $("datePeak").value;
    return !!p && ((pre && pre !== p.date_pre) || (peak && peak !== p.date_peak));
  }
  ["datePre", "datePeak"].forEach((id) => { $(id).onchange = () => {
    if (datesDifferFromPair(currentPair())) setStatus("Даты изменены — нажмите «Рассчитать»: сервис подберёт подготовленную пару наблюдений на эти даты (±20 дней) или перечислит доступные.");
  }; });

  function applyAvailablePair(pairId) {
    const p = state.pairs.find((x) => x.pair_id === pairId);
    if (!p) return;
    $("pairSelect").value = pairId;
    $("datePre").value = p.date_pre; $("datePeak").value = p.date_peak;
    runAnalysis();
  }

  async function runAnalysis() {
    let geomReq = null;
    try { geomReq = parseGeometryInput(); } catch (e) { setStatus("Не удалось разобрать геометрию: " + e.message, true); return; }
    const selected = currentPair();
    const byDates = datesDifferFromPair(selected);
    const body = { date_pre: $("datePre").value || null, date_peak: $("datePeak").value || null, clip_to_geometry: true };
    if (geomReq) Object.assign(body, geomReq);
    if (byDates) { if (!geomReq) body.bbox = selected.bbox_wgs84; }   // территория = выбранный район
    else body.pair_id = $("pairSelect").value;
    setStatus(byDates ? "Подбор подготовленной пары по территории и датам…" : "Расчёт…");
    $("runBtn").disabled = true;
    try {
      const r = await fetch("/api/analyze", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      const data = await r.json();
      if (!r.ok) {
        const d = data.detail;
        if (d && d.available_pairs) {
          const items = d.available_pairs.map((a) => `<li>${pairLabel(a.pair_id)} — покрытие ${(100 * a.overlap).toFixed(0)} % <a href="#" data-pair="${a.pair_id}">применить</a></li>`).join("");
          setStatusHtml(`Территория покрыта, но подготовленной пары наблюдений на даты ${body.date_pre || "—"} → ${body.date_peak || "—"} нет (допуск ±20 дней). Доступные пары:<ul>${items}</ul>`, true);
          $("status").querySelectorAll("a[data-pair]").forEach((a) => { a.onclick = (ev) => { ev.preventDefault(); applyAvailablePair(a.dataset.pair); }; });
          return;
        }
        throw new Error(typeof d === "string" ? d : JSON.stringify(d));
      }
      state.job = data;
      state.geometry = geomReq ? (geomReq.geometry || bboxToPolygon(geomReq.bbox)) : null;
      showQueryGeometry(state.geometry);
      const switched = data.pair.pair_id !== $("pairSelect").value;
      if (switched) $("pairSelect").value = data.pair.pair_id;
      renderReport(data);
      await loadOverlays(data);
      if (byDates) {
        // поля дат приводим к фактическим датам съёмки использованной пары
        $("datePre").value = data.pair.date_pre; $("datePeak").value = data.pair.date_peak;
        setStatus(`Запрос ${body.date_pre || "—"} → ${body.date_peak || "—"}: использована подготовленная пара ${pairLabel(data.pair.pair_id)}${switched ? " (переключено)" : ""}; в полях — фактические даты съёмки.`);
      } else setStatus(`Готово: ${pairLabel(data.pair.pair_id)}.`);
    } catch (e) {
      setStatus("Ошибка: " + e.message, true);
    } finally {
      $("runBtn").disabled = false;
    }
  }
  $("runBtn").onclick = runAnalysis;
  $("pairSelect").onchange = () => onPairChange(false);

  function bboxToPolygon(b) { return { type: "Polygon", coordinates: [[[b[0], b[1]], [b[2], b[1]], [b[2], b[3]], [b[0], b[3]], [b[0], b[1]]]] }; }

  // ------------------------------------------------------------------ overlays
  function clearOverlays() {
    Object.values(state.overlays).forEach((o) => { if (o.layer) map.removeLayer(o.layer); if (o.url) URL.revokeObjectURL(o.url); });
    state.overlays = {};
  }

  async function loadOverlays(job) {
    clearOverlays();
    await Promise.all(LAYERS.map(async (layer) => {
      const r = await fetch(job.downloads.overlays_png[layer]);
      if (!r.ok) return;
      const bounds = JSON.parse(r.headers.get("X-Bounds"));
      const url = URL.createObjectURL(await r.blob());
      const overlay = L.imageOverlay(url, bounds, { opacity: +$("opacity").value, interactive: false, className: "ov-" + layer });
      state.overlays[layer] = { layer: overlay, url, bounds };
      if (state.visible[layer]) overlay.addTo(map);
    }));
    // z-order: pre < peak < receded < flood
    ["water_pre", "water_peak", "receded", "flood"].forEach((l) => { const o = state.overlays[l]; if (o && map.hasLayer(o.layer)) o.layer.bringToFront(); });
    applySwipe();
  }

  function buildToggles() {
    const box = $("layerToggles");
    box.innerHTML = "";
    LAYERS.forEach((layer) => {
      const row = document.createElement("label");
      row.className = "toggle";
      row.innerHTML = `<input type="checkbox" ${state.visible[layer] ? "checked" : ""}/> <span class="sw" style="background:${COLORS[layer]}"></span> ${TITLES[layer]}`;
      row.querySelector("input").onchange = (e) => {
        state.visible[layer] = e.target.checked;
        const o = state.overlays[layer];
        if (!o) return;
        if (e.target.checked) { o.layer.addTo(map); } else { map.removeLayer(o.layer); }
        applySwipe();
      };
      box.appendChild(row);
    });
  }
  $("opacity").oninput = (e) => Object.values(state.overlays).forEach((o) => o.layer.setOpacity(+e.target.value));

  // ------------------------------------------------------------------ swipe compare (pre | peak)
  let handle = null, swipeX = null;
  $("swipeToggle").onchange = (e) => { state.swipe = e.target.checked; if (state.swipe) { state.visible.water_pre = state.visible.water_peak = true; buildToggles(); ["water_pre", "water_peak"].forEach((l) => state.overlays[l] && state.overlays[l].layer.addTo(map)); } applySwipe(); };

  function applySwipe() {
    const container = map.getContainer();
    if (!state.swipe) {
      if (handle) { handle.remove(); handle = null; }
      LAYERS.forEach((l) => { const el = imgOf(l); if (el) el.style.clipPath = ""; });
      return;
    }
    if (!handle) {
      handle = document.createElement("div"); handle.id = "swipeHandle"; container.appendChild(handle);
      swipeX = container.clientWidth / 2;
      let dragging = false;
      handle.addEventListener("mousedown", (e) => { dragging = true; e.preventDefault(); map.dragging.disable(); });
      window.addEventListener("mousemove", (e) => { if (!dragging) return; const rect = container.getBoundingClientRect(); swipeX = Math.min(Math.max(e.clientX - rect.left, 0), rect.width); updateClip(); });
      window.addEventListener("mouseup", () => { if (dragging) { dragging = false; map.dragging.enable(); } });
      map.on("move zoom resize", updateClip);
    }
    updateClip();
  }
  function imgOf(layer) { const o = state.overlays[layer]; return o && o.layer.getElement ? o.layer.getElement() : null; }
  function updateClip() {
    if (!state.swipe || !handle) return;
    const container = map.getContainer();
    handle.style.left = swipeX + "px";
    const pre = imgOf("water_pre"), peak = imgOf("water_peak");
    // clip in *container* pixels: image element is positioned by Leaflet inside the map pane
    [["pre", pre, true], ["peak", peak, false]].forEach(([_, el, leftSide]) => {
      if (!el) return;
      const rect = el.getBoundingClientRect(), crect = container.getBoundingClientRect();
      const x = swipeX + crect.left - rect.left; // split position in image pixels
      const w = rect.width;
      const frac = Math.min(Math.max(x / w, 0), 1) * 100;
      el.style.clipPath = leftSide ? `inset(0 ${100 - frac}% 0 0)` : `inset(0 0 0 ${frac}%)`;
    });
    LAYERS.filter((l) => l !== "water_pre" && l !== "water_peak").forEach((l) => { const el = imgOf(l); if (el) el.style.clipPath = ""; });
  }

  // ------------------------------------------------------------------ report rendering
  const fmt = (v, d = 2) => (v === null || v === undefined) ? "—" : Number(v).toLocaleString("ru-RU", { maximumFractionDigits: d, minimumFractionDigits: d });
  const pct = (v) => (v === null || v === undefined) ? "—" : (100 * v).toFixed(2) + " %";

  function renderReport(r) {
    $("reportCard").hidden = false;
    const w = r.water_surface, c = r.change, a = r.area;
    const rows = [
      ["head", "Территория"],
      ["Проанализировано", `${fmt(a.analyzed_area.ha)} га · ${fmt(a.analyzed_area.km2, 2)} км²`],
      ["Покрытие запроса растром пары", pct(a.analyzed_share_of_query)],
      ["head", "Водное зеркало"],
      [`«До» ${w.pre.date}`, `<span class="pre">${fmt(w.pre.ha)} га</span> · ${fmt(w.pre.km2)} км² · ${pct(w.pre.share_of_area)}`],
      [`«Пик» ${w.peak.date}`, `<span class="peak">${fmt(w.peak.ha)} га</span> · ${fmt(w.peak.km2)} км² · ${pct(w.peak.share_of_area)}`],
      ["Постоянная вода (GSW ≥ 80 %)", w.permanent ? `${fmt(w.permanent.ha)} га` : "нет AUX"],
      ["head", "Изменение"],
      ["Прирост — новое затопление", `<span class="flood big">${fmt(c.flood_gain.ha)} га</span><br>${fmt(c.flood_gain.km2)} км² · ${pct(c.flood_gain.share_of_area)} территории`],
      ["Убыль — ушедшая вода", `<span class="receded">${fmt(c.receded_loss.ha)} га</span> · ${fmt(c.receded_loss.km2)} км²`],
      ["Нетто-изменение зеркала", `${c.net_change.sign}${fmt(c.net_change.ha)} га (${c.net_change.pct_of_pre === null ? "—" : c.net_change.sign + c.net_change.pct_of_pre + " %"})`],
    ];
    $("reportSummary").innerHTML = `<table class="kv">${rows.map(([k, v]) => k === "head" ? `<tr class="head"><td colspan="2">${v}</td></tr>` : `<tr><td>${k}</td><td>${v}</td></tr>`).join("")}</table>`;

    const lc = r.landcover_breakdown || [];
    $("landcover").innerHTML = lc.length
      ? `<table class="kv">${lc.map((x) => `<tr><td>${x.class}</td><td>${fmt(x.flood_ha)} га · ${pct(x.share_of_flood)}</td></tr>`).join("")}</table>`
      : `<p class="status">${r.landcover_note}</p>`;
    const hp = r.hand_profile_of_flood || [];
    $("handProfile").innerHTML = hp.length
      ? `<table class="kv">${hp.map((x) => `<tr><td>HAND ${x.hand_range}</td><td>${fmt(x.flood_ha)} га · ${pct(x.share)}</td></tr>`).join("")}</table>`
      : `<p class="status">нет данных (AUX не смонтирован или затопления нет)</p>`;

    const d = r.downloads;
    const links = [["Отчёт JSON", d.report_json], ["Отчёт CSV", d.report_csv]]
      .concat(LAYERS.map((l) => [`GeoJSON: ${TITLES[l]}`, d.vectors_geojson[l]]))
      .concat([["Контур области", d.outline_geojson], ["GeoTIFF маска (3 канала)", `/api/pairs/${r.pair.pair_id}/mask.tif`]]);
    $("downloads").innerHTML = links.map(([t, u]) => `<a href="${u}" target="_blank" rel="noopener">${t}</a>`).join("");
    $("reportJson").textContent = JSON.stringify(r, null, 2);
  }

  buildToggles();
  loadPairs().catch((e) => setStatus("Не удалось загрузить каталог пар: " + e.message, true));
})();
