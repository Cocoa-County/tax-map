/* taxmap static site: parcels, districts, calculators, and sources. No backend. */
(function () {
  "use strict";

  const state = {
    config: null, manifest: null, districts: null, formulas: {}, sources: null,
    tilesLoaded: new Map(), tilesLoading: new Map(), attributes: new Map(), featureById: new Map(),
    search: null, selected: null, outlineLayers: new Map(), visibleOutlines: new Set(), groupInfo: new Map(),
  };
  const $ = (id) => document.getElementById(id);
  const F = window.TaxmapFormula;

  // ------------------------------------------------------------------ helpers
  function el(tag, attrs, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs || {})) {
      if (value === null || value === undefined || value === false) continue;
      if (key === "class") node.className = value;
      else if (key === "html") node.innerHTML = value; // sanitized notes or escaped MathML
      else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
      else node.setAttribute(key, value === true ? "" : value);
    }
    for (const child of children.flat()) {
      if (child === null || child === undefined || child === false) continue;
      node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return node;
  }
  const money = (v) => (v === null || v === undefined || Number.isNaN(v)) ? "—"
    : Number(v).toLocaleString(undefined, { style: "currency", currency: "USD" });
  const numberText = (v) => typeof v === "number"
    ? v.toLocaleString(undefined, { maximumFractionDigits: 6 }) : (v === null || v === undefined || v === "" ? "—" : String(v));

  async function getJSON(path) {
    const response = await fetch(path);
    if (!response.ok) throw new Error(`${path}: HTTP ${response.status}`);
    return response.json();
  }

  function normalizeApn(value) {
    if (value === null || value === undefined) return "";
    const text = String(value).trim();
    const mode = state.districts ? state.districts.apn_normalize : "alnum";
    if (mode === "none") return text;
    if (mode === "digits") return text.replace(/\D/g, "");
    return text.replace(/[^0-9A-Za-z]/g, "").toUpperCase();
  }

  // Apply a display pattern such as "###-###-###" when the APN has exactly that many characters.
  function formatApn(value) {
    if (value === null || value === undefined) return "unknown";
    const pattern = state.config && state.config.apn_display_pattern;
    const raw = normalizeApn(value);
    if (!pattern || raw.length !== (pattern.match(/#/g) || []).length) return String(value);
    let i = 0;
    return pattern.replace(/#/g, () => raw[i++]);
  }

  function intersects(a, b) {
    return a && b && a[0] <= b[2] && a[2] >= b[0] && a[1] <= b[3] && a[3] >= b[1];
  }

  function openModal(title, body) {
    $("modal-title").textContent = title;
    const container = $("modal-body");
    container.replaceChildren(body);
    container.scrollTop = 0;
    if (!$("modal").open) $("modal").showModal();
  }

  // ------------------------------------------------------------------ provenance
  function resourceLink(id) {
    const resource = state.sources && state.sources.resources[id];
    if (!resource) return el("span", {}, id);
    return resource.url ? el("a", { href: resource.url, target: "_blank", rel: "noopener" }, resource.title || id)
      : el("span", {}, resource.title || id);
  }

  function evidenceList(items) {
    if (!items || !items.length) return null;
    return el("ul", { class: "evidence" }, items.map((e) => el("li", {},
      resourceLink(e.resource), e.page ? ` p. ${e.page}` : "", e.locator ? ` (${e.locator})` : "",
      e.quote ? el("q", {}, ` ${e.quote}`) : "", e.note ? ` — ${e.note}` : "")));
  }

  // ------------------------------------------------------------------ membership and inputs
  function memberGroups(apn, attrs) {
    const key = normalizeApn(apn);
    const entry = state.districts.index[key] || {};
    const groups = new Set(entry.g || []);
    const matches = (rule) => rule.type === "attribute" && attrs
      && rule.values.map(String).includes(F.textValue(attrs[rule.attribute]));
    for (const district of state.districts.districts) {
      if (matches(district.rule)) groups.add(district.id);
      for (const zone of district.zones) {
        if (matches(zone.rule) || (zone.rule.type === "parent" && groups.has(district.id))) groups.add(zone.id);
      }
    }
    return { groups, amounts: entry.a || {}, rollRows: entry.r || {} };
  }

  function resolveInputs(formula, apn, attrs) {
    const { groups, rollRows } = apn ? memberGroups(apn, attrs) : { groups: new Set(), rollRows: {} };
    const values = {}, origins = {};
    for (const spec of formula.inputs) {
      let value = null, origin = "default";
      const source = spec.source;
      if (source && apn) {
        if (source.parcel) {
          for (const field of Array.isArray(source.parcel) ? source.parcel : [source.parcel]) {
            const v = attrs ? attrs[field] : undefined;
            if (v !== null && v !== undefined && v !== "") { value = v; origin = `parcel field ${field}`; break; }
          }
        } else if (source.roll) {
          const row = rollRows[source.roll];
          value = row && source.column ? row[source.column] : null;
          if (value !== null && value !== undefined && value !== "") origin = `roll ${source.roll}`;
        } else if (source.member) {
          value = groups.has(source.member) ? 1 : 0;
          origin = `membership in ${(state.groupInfo.get(source.member) || {}).name || source.member}`;
        }
      }
      if (value === null || value === undefined || value === "") {
        value = spec.default === undefined ? null : spec.default;
        origin = value === null ? "missing" : "default";
      }
      if (value !== null) value = spec.type === "text" ? F.textValue(value) : F.toNumber(value);
      values[spec.name] = value;
      origins[spec.name] = origin;
    }
    return { values, origins };
  }

  // ------------------------------------------------------------------ map
  let map, parcelLayer, outlineGroup, selectedLayer;
  let lastParcelClick, parcelClickVersion = 0;
  let parcelSelectionVersion = 0;
  const parcelHit = window.TaxmapParcelHit;

  async function onParcelClick(event, feature) {
    const version = ++parcelClickVersion;
    ++parcelSelectionVersion;
    const point = [event.latlng.lng, event.latlng.lat];
    try {
      // Centroid-based tiles can hold overlapping footprints in different files.
      await Promise.all(state.manifest.tiles.filter((tile) => parcelHit.inBounds(point, tile.bounds))
        .map((tile) => loadTile(tile.id)));
      if (version !== parcelClickVersion) return;
      let matches = parcelHit.find(point, state.featureById.values(), normalizeApn);
      if (!matches.length) matches = [state.featureById.get(String(feature.id))];
      await Promise.all([...new Set(matches.map((entry) => entry.tile))].map(tileAttributes));
      if (version !== parcelClickVersion) return;
      const current = matches.findIndex((entry) => state.selected
        && (String(entry.feature.id) === String(state.selected.id)
          || (normalizeApn(state.selected.apn) && normalizeApn(entry.feature.properties.apn) === normalizeApn(state.selected.apn))));
      const repeat = current >= 0 && lastParcelClick && event.latlng.distanceTo(lastParcelClick) < 15;
      const target = matches[repeat ? (current + 1) % matches.length : 0];
      lastParcelClick = event.latlng;
      await selectParcel(target.feature.id, target.tile, matches);
    } catch (error) {
      if (version === parcelClickVersion) showMessage(`Could not load parcels at this location (${error.message}).`);
    }
  }

  function parcelStyle(feature) {
    const { groups } = memberGroups(feature.properties.apn, null);
    let color = null;
    for (const group of groups) {
      const info = state.groupInfo.get(group);
      if (info && (!color || info.zone)) color = info.color;
    }
    return color ? { color, weight: 1, fillColor: color, fillOpacity: .35 }
      : { color: "#475569", weight: .6, fillColor: "#94a3b8", fillOpacity: .08 };
  }

  function initMap() {
    map = L.map("map", { preferCanvas: true, zoomControl: true });
    const osm = L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 20, maxNativeZoom: 19, attribution: "&copy; OpenStreetMap contributors" });
    const imagery = L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}", {
      maxZoom: 20, maxNativeZoom: 19, attribution: "Imagery &copy; Esri" });
    osm.addTo(map);
    outlineGroup = L.layerGroup().addTo(map);
    parcelLayer = L.geoJSON(null, { style: parcelStyle, onEachFeature: (feature, layer) => {
      layer.on("click", (event) => onParcelClick(event, feature));
    } }).addTo(map);
    L.control.layers({ "Streets": osm, "Imagery": imagery }, { "Parcels": parcelLayer, "District outlines": outlineGroup }).addTo(map);
    L.control.scale().addTo(map);
    const b = state.manifest.bounds;
    map.fitBounds([[b[1], b[0]], [b[3], b[2]]]);
    map.on("moveend", loadVisibleTiles);
    loadVisibleTiles();
  }

  function viewBounds() {
    const b = map.getBounds();
    return [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()];
  }

  function loadVisibleTiles() {
    const allowed = map.getZoom() >= state.manifest.minimum_zoom;
    $("zoom-hint").hidden = allowed;
    if (!allowed) return;
    const view = viewBounds();
    for (const tile of state.manifest.tiles) if (intersects(tile.bounds, view)) loadTile(tile.id);
  }

  function loadTile(id) {
    if (state.tilesLoaded.has(id)) return Promise.resolve();
    if (!state.tilesLoading.has(id)) {
      state.tilesLoading.set(id, getJSON(`data/tiles/${id}.geojson`).then((data) => {
        for (const feature of data.features) state.featureById.set(String(feature.id), parcelHit.prepare(feature, id));
        parcelLayer.addData(data);
        state.tilesLoaded.set(id, true);
      }));
    }
    return state.tilesLoading.get(id);
  }

  async function tileAttributes(tile) {
    if (!state.attributes.has(tile)) state.attributes.set(tile, getJSON(`data/attributes/${tile}.json`));
    return state.attributes.get(tile);
  }

  function drawOutlines() {
    outlineGroup.clearLayers();
    for (const feature of state.districts.outlines.features || []) {
      const id = feature.properties.id;
      if (!state.visibleOutlines.has(id)) continue;
      const info = state.groupInfo.get(id) || {};
      const layer = L.geoJSON(feature, { interactive: false, style: {
        color: info.color || "#2563eb", weight: info.zone ? 2 : 3, dashArray: info.zone ? "4 4" : null, fill: false } });
      outlineGroup.addLayer(layer);
      state.outlineLayers.set(id, layer);
    }
  }

  function zoomToGroup(id) {
    const feature = (state.districts.outlines.features || []).find((f) => f.properties.id === id);
    if (!feature) return false;
    state.visibleOutlines.add(id);
    drawOutlines();
    map.fitBounds(L.geoJSON(feature).getBounds(), { padding: [20, 20] });
    return true;
  }

  // ------------------------------------------------------------------ search
  async function ensureSearch() {
    if (!state.search) state.search = getJSON("data/search.json");
    return state.search;
  }

  function searchRows(rows, query) {
    const q = query.trim().toLowerCase();
    const qApn = normalizeApn(query);
    if (q.length < 2) return [];
    const out = [];
    for (const row of rows) {
      const apnMatch = qApn && normalizeApn(row[1]).startsWith(qApn);
      if (apnMatch || (row[2] && row[2].toLowerCase().includes(q))) {
        out.push(row);
        if (out.length >= 25) break;
      }
    }
    return out;
  }

  function initSearch() {
    const input = $("search-input"), list = $("search-results"), status = $("search-status");
    let active = -1, results = [];
    const dismiss = () => {
      list.hidden = true;
      status.hidden = true;
      input.setAttribute("aria-expanded", "false");
      input.removeAttribute("aria-activedescendant");
    };
    const render = () => {
      list.replaceChildren(...results.map((row, i) => el("li", {
        id: `search-option-${i}`, role: "option", "aria-selected": i === active ? "true" : "false",
        onmousedown: (event) => { event.preventDefault(); choose(i); },
      }, row[2] || formatApn(row[1]), el("small", {}, `APN ${formatApn(row[1])}`))));
      list.hidden = !results.length;
      input.setAttribute("aria-expanded", String(results.length > 0));
      if (active >= 0) input.setAttribute("aria-activedescendant", `search-option-${active}`);
      else input.removeAttribute("aria-activedescendant");
      status.textContent = input.value.trim().length >= 2 && !results.length ? "No matching parcels. Try an APN or another address." : "";
      status.hidden = !status.textContent;
      if (active >= 0) list.children[active].scrollIntoView({ block: "nearest" });
    };
    const choose = async (i) => {
      const row = results[i];
      if (!row) return;
      dismiss();
      input.value = row[2] || row[1];
      try {
        await goToRow(row);
      } catch (error) {
        showMessage(`Could not load parcel (${error.message}).`);
      }
    };
    input.addEventListener("input", async () => {
      const query = input.value;
      results = [];
      active = -1;
      dismiss();
      if (query.trim().length < 2) return;
      status.textContent = "Searching parcels…";
      status.hidden = false;
      let rows;
      try {
        rows = await ensureSearch();
      } catch (error) {
        if (input.value === query) {
          status.textContent = `Could not search parcels (${error.message}). Reload to try again.`;
          status.hidden = false;
        }
        return;
      }
      if (input.value !== query || document.activeElement !== input) return;
      results = searchRows(rows, query);
      active = results.length ? 0 : -1;
      render();
    });
    input.addEventListener("keydown", (event) => {
      if (event.key === "ArrowDown") { active = Math.min(results.length - 1, active + 1); render(); event.preventDefault(); }
      else if (event.key === "ArrowUp") { active = Math.max(0, active - 1); render(); event.preventDefault(); }
      else if (event.key === "Enter") { choose(active); event.preventDefault(); }
      else if (event.key === "Escape") dismiss();
    });
    input.addEventListener("focus", () => { if (input.value.trim().length >= 2) render(); });
    input.addEventListener("blur", () => setTimeout(dismiss, 150));
  }

  async function goToRow(row) {
    ++parcelClickVersion;
    ++parcelSelectionVersion;
    lastParcelClick = null;
    const [id, , , tile, bounds] = row;
    if (bounds) map.fitBounds([[bounds[1], bounds[0]], [bounds[3], bounds[2]]], { maxZoom: 18 });
    if (tile !== "no-geometry") await loadTile(tile);
    await selectParcel(id, tile);
  }

  async function selectByApn(apn) {
    const rows = await ensureSearch();
    const key = normalizeApn(apn);
    const row = rows.find((r) => normalizeApn(r[1]) === key);
    if (row) await goToRow(row);
    else showMessage(`No parcel found for APN ${apn}.`);
  }

  // ------------------------------------------------------------------ parcel panel
  function setPanelOpen(open) {
    $("panel").hidden = !open;
    $("open-panel").hidden = open;
    $("open-panel").setAttribute("aria-expanded", String(open));
    if (open) {
      $("panel").classList.remove("expanded");
      $("sheet-handle").setAttribute("aria-expanded", "false");
      $("sheet-handle").setAttribute("aria-label", "Expand parcel details");
      $("sheet-handle").textContent = "Expand";
    }
    if (map) requestAnimationFrame(() => map.invalidateSize());
  }

  function showMessage(text) {
    $("panel-body").replaceChildren(el("p", { class: "muted" }, text));
    setPanelOpen(true);
  }

  async function selectParcel(id, tileHint, overlaps = null) {
    const selectionVersion = ++parcelSelectionVersion;
    const known = state.featureById.get(String(id));
    const tile = tileHint || (known && known.tile);
    if (!tile) return;
    const attrs = (await tileAttributes(tile))[String(id)] || {};
    const apn = attrs[state.manifest.apn_field] ?? (known && known.feature.properties.apn);
    if (overlaps === null && known) {
      const point = parcelHit.representativePoint(known.feature.geometry);
      if (point) {
        await Promise.all(state.manifest.tiles.filter((entry) => parcelHit.inBounds(point, entry.bounds))
          .map((entry) => loadTile(entry.id)));
        overlaps = parcelHit.find(point, state.featureById.values(), normalizeApn);
      }
    }
    const overlapChoices = await Promise.all((overlaps || []).map(async (entry) => {
      const properties = (await tileAttributes(entry.tile))[String(entry.feature.id)] || {};
      return { ...entry, attrs: properties };
    }));
    if (selectionVersion !== parcelSelectionVersion) return;
    state.selected = { id, tile, attrs, apn, feature: known && known.feature, overlaps: overlapChoices };
    if (selectedLayer) selectedLayer.remove();
    if (known && known.feature.geometry) {
      selectedLayer = L.geoJSON(known.feature, { interactive: false,
        style: { color: "#f59e0b", weight: 3, fillOpacity: .1 } }).addTo(map);
    }
    setHash({ apn });
    renderParcel();
  }

  function formulasFor(groups) {
    return Object.values(state.formulas).filter((f) => groups.has(f.district));
  }

  function billLabels(district, detailed = false) {
    const labels = district.bill_labels || [];
    if (!labels.length) return null;
    return el("div", { class: "bill-labels" },
      el("p", { class: "eyebrow" }, "Look for on your bill"),
      labels.map((item) => el("div", {},
        el("p", { class: "bill-label" }, el("code", {}, item.code), " ", el("strong", {}, item.label)),
        item.note ? el("p", { class: "muted" }, item.note) : null,
        detailed && item.fiscal_year ? el("p", { class: "muted" }, `Verified bill year: ${item.fiscal_year}`) : null,
        detailed ? evidenceList(item.evidence) : null)));
  }

  function renderParcel() {
    const { attrs, apn } = state.selected;
    const { groups, amounts } = memberGroups(apn, attrs);
    const address = state.manifest.address_field ? attrs[state.manifest.address_field] : null;
    const body = [el("div", { class: "parcel-heading" },
      el("p", { class: "eyebrow" }, "Selected parcel"),
      el("h2", {}, address || `APN ${formatApn(apn)}`),
      address ? el("p", { class: "muted" }, `APN ${formatApn(apn)}`) : null)];
    const districts = state.districts.districts.filter((d) => groups.has(d.id));
    if (normalizeApn(apn) && (state.config.parcel_links || []).length) {
      body.push(el("div", { class: "parcel-official-links" },
        el("div", { class: "row-actions" }, state.config.parcel_links.map((link) => {
          const url = link.url_template.replace(/\{apn(_display)?\}/g, (_, display) =>
            encodeURIComponent(display ? formatApn(apn) : normalizeApn(apn)));
          return el("a", { class: "official-link", href: url, target: "_blank", rel: "noopener noreferrer" }, link.label,
            el("span", { "aria-hidden": "true" }, " ↗"));
        })),
        el("p", { class: "muted" }, "Opens the official county site. Bill availability and tax year are determined by the county.")));
    }
    const listedTotal = Object.values(amounts).filter((amount) => typeof amount === "number").reduce((a, b) => a + b, 0);
    if (Object.values(amounts).some((amount) => typeof amount === "number")) {
      body.push(el("div", { class: "parcel-total" }, el("span", {}, "Listed annual assessments"),
        el("strong", {}, money(listedTotal)), el("small", {}, "Official roll amounts · Not the full property tax bill")));
    }
    if (state.selected.overlaps.length > 1) {
      body.push(el("section", { class: "overlap-chooser", "aria-label": "Properties at this location" },
        el("h3", {}, `${state.selected.overlaps.length} properties at this location`),
        el("p", { class: "muted" }, "Units or shared areas overlap here. Choose a property below, or click the same map location again to cycle."),
        el("div", { class: "overlap-list" }, state.selected.overlaps.map((entry) => {
          const itemApn = entry.attrs[state.manifest.apn_field] ?? entry.feature.properties.apn;
          const itemAddress = state.manifest.address_field ? entry.attrs[state.manifest.address_field] : null;
          const active = String(entry.feature.id) === String(state.selected.id)
            || (normalizeApn(apn) && normalizeApn(itemApn) === normalizeApn(apn));
          const labels = (state.manifest.label_fields || []).map((field) => entry.attrs[field]).filter(Boolean);
          return el("button", { type: "button", class: `overlap-option ghost${active ? " active" : ""}`,
            "aria-current": active ? "true" : null,
            onclick: async () => {
              ++parcelClickVersion;
              try {
                await selectParcel(entry.feature.id, entry.tile, state.selected.overlaps);
                const currentOption = $("panel-body").querySelector('.overlap-option[aria-current="true"]');
                if (currentOption) currentOption.focus({ preventScroll: true });
              } catch (error) {
                showMessage(`Could not load parcel (${error.message}).`);
              }
            } },
          el("strong", {}, itemAddress || `APN ${formatApn(itemApn)}`),
          el("span", {}, `APN ${formatApn(itemApn)}${active ? " · Viewing" : ""}`),
          labels.length ? el("small", {}, labels.join(" · ")) : null);
        }))));
    }
    body.push(el("h3", {}, "Districts", el("span", { class: "count" }, districts.length)));
    if (!districts.length) body.push(el("p", { class: "muted" }, "Not in any mapped district."));
    for (const district of districts) {
      const zones = district.zones.filter((z) => groups.has(z.id));
      const amount = amounts[district.id];
      const formulas = formulasFor(new Set([district.id]));
      body.push(el("div", { class: "card" },
        el("div", { class: "card-head" },
          el("strong", { title: district.name }, el("span", { class: "swatch", style: `background:${district.color}` }), " ", district.short_name || district.name),
          el("span", { class: "amount" }, amount !== undefined ? money(amount) : "")),
        billLabels(district),
        zones.length ? el("div", {}, zones.map((z) => el("span", { class: "chip" },
          el("span", { class: "swatch", style: `background:${z.color}` }), z.name))) : null,
        el("p", { class: "muted" }, amount !== undefined ? `Official ${district.fiscal_year || ""} roll amount`
          : (district.rolls.length ? "Not on the assessment roll" : "No published roll; see calculator estimate")),
        el("div", { class: "row-actions" },
          formulas.map((f) => el("button", { type: "button", "aria-label": `Calculate ${f.name}`, onclick: () => openCalculator(f.id) },
            formulas.length === 1 ? "Calculator" : f.name)),
          el("button", { type: "button", class: "ghost", onclick: () => openDistrict(district.id) }, "Details"))));
    }
    if (districts.some((district) => (district.bill_labels || []).length)) {
      body.push(el("p", { class: "muted" },
        "Match both the description and code in the bill's Special taxes and assessments section. These are reference labels, not confirmation of a charge on this parcel. Other bill charges are not mapped here; the county bill is authoritative."));
    }
    body.push(el("details", {}, el("summary", {}, "Parcel attributes"),
      el("table", { class: "kv" }, Object.entries(attrs).map(([k, v]) => el("tr", {}, el("th", {}, k), el("td", {}, numberText(v)))))));
    body.push(el("div", { class: "row-actions" },
      el("button", { type: "button", class: "ghost", onclick: copyLink }, "Copy link"),
      state.selected.feature ? el("button", { type: "button", class: "ghost", onclick: downloadSelected }, "Download GeoJSON") : null));
    $("panel-body").replaceChildren(...body);
    $("open-panel").textContent = "Parcel details";
    setPanelOpen(true);
    const overlapList = $("panel-body").querySelector(".overlap-list");
    const currentOption = overlapList && overlapList.querySelector('[aria-current="true"]');
    if (currentOption) overlapList.scrollTop = Math.max(0, currentOption.offsetTop - overlapList.clientHeight + currentOption.offsetHeight);
  }

  function copyLink() {
    navigator.clipboard && navigator.clipboard.writeText(location.href);
  }

  function downloadSelected() {
    const { feature, attrs, apn } = state.selected;
    const data = { type: "FeatureCollection", features: [{ type: "Feature", geometry: feature.geometry, properties: attrs }] };
    const url = URL.createObjectURL(new Blob([JSON.stringify(data)], { type: "application/geo+json" }));
    const link = el("a", { href: url, download: `parcel-${normalizeApn(apn) || state.selected.id}.geojson` });
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  // ------------------------------------------------------------------ calculator
  function openCalculator(formulaId) {
    const formula = state.formulas[formulaId];
    if (!formula) return;
    const selected = state.selected;
    const { values, origins } = resolveInputs(formula, selected && selected.apn, selected && selected.attrs);
    const official = selected ? memberGroups(selected.apn, selected.attrs).amounts[formula.district] : undefined;
    const form = el("form", { class: "calc-inputs", onsubmit: (e) => e.preventDefault() });
    const summary = el("div", { class: "calculator-summary", "aria-live": "polite", "aria-atomic": "true" });
    const output = el("div");
    const tooltip = el("div", { id: "formula-tooltip", class: "formula-tooltip", role: "tooltip", hidden: true });
    let activeSymbol;
    function hideTooltip() {
      if (activeSymbol) activeSymbol.removeAttribute("aria-describedby");
      activeSymbol = null;
      tooltip.hidden = true;
    }
    function showTooltip(event) {
      const symbol = event.target.closest(".formula-symbol");
      if (!symbol) return;
      hideTooltip();
      activeSymbol = symbol;
      tooltip.textContent = symbol.getAttribute("aria-label");
      symbol.setAttribute("aria-describedby", tooltip.id);
      tooltip.hidden = false;
      const rect = symbol.getBoundingClientRect();
      const width = tooltip.offsetWidth;
      tooltip.style.left = `${Math.max(8, Math.min(rect.left, window.innerWidth - width - 8))}px`;
      tooltip.style.top = `${Math.max(8, Math.min(rect.bottom + 8, window.innerHeight - tooltip.offsetHeight - 8))}px`;
    }
    output.addEventListener("pointerover", showTooltip);
    output.addEventListener("focusin", showTooltip);
    output.addEventListener("pointerout", (event) => {
      if (activeSymbol && (activeSymbol.contains(event.relatedTarget) || activeSymbol.matches(":focus"))) return;
      hideTooltip();
    });
    output.addEventListener("focusout", hideTooltip);
    output.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && !tooltip.hidden) {
        event.preventDefault();
        hideTooltip();
      }
    });
    $("modal-body").onscroll = () => {
      if (activeSymbol && activeSymbol.matches(":focus")) showTooltip({ target: activeSymbol });
      else hideTooltip();
    };
    const fields = {};
    const propertyFields = [];
    const membershipFields = [];
    const edited = el("p", { class: "muted scenario-status" });
    for (const spec of formula.inputs) {
      const value = values[spec.name];
      const membership = spec.source && spec.source.member && (value === 0 || value === 1);
      const input = membership
        ? el("input", { name: spec.name, type: "checkbox", checked: value === 1 })
        : spec.source && spec.source.member
          ? el("select", { name: spec.name },
            el("option", { value: "", selected: value === null }, "Choose membership"),
            el("option", { value: "1", selected: value === 1 }, "In this district or zone"),
            el("option", { value: "0", selected: value === 0 }, "Not in this district or zone"))
        : el("input", { name: spec.name, type: spec.type === "text" ? "text" : "number", step: "any",
          value: value === null || value === undefined ? "" : String(value) });
      input.addEventListener("input", recompute);
      fields[spec.name] = input;
      const field = el("label", { class: membership ? "membership-input" : "" },
        el("span", {}, `${spec.label || spec.name}${spec.unit ? ` (${spec.unit})` : ""}`), input,
        el("span", { class: "hint" }, `${origins[spec.name]}${spec.description ? ` — ${spec.description}` : ""}`),
        spec.caveat ? el("span", { class: "hint bad" }, spec.caveat) : null);
      if (membership) membershipFields.push(field);
      else if (spec.source && (spec.source.parcel || spec.source.roll)) propertyFields.push(field);
      else form.append(field);
    }
    const propertyGroup = propertyFields.length ? el("details", { class: "input-group" },
      el("summary", {}, "Public-data inputs"), el("div", { class: "calc-inputs" }, propertyFields)) : null;
    if (propertyGroup) form.append(propertyGroup);
    if (membershipFields.length) form.append(el("details", { class: "input-group" }, el("summary", {}, "District membership"),
      el("div", { class: "calc-inputs" }, membershipFields)));
    const reset = el("button", { type: "button", class: "ghost", onclick: () => {
      for (const spec of formula.inputs) {
        const input = fields[spec.name], value = values[spec.name];
        if (input.type === "checkbox") input.checked = value === 1;
        else input.value = value === null || value === undefined ? "" : String(value);
      }
      recompute();
    } }, "Reset inputs");
    function currentValues() {
      const out = {};
      for (const spec of formula.inputs) {
        const input = fields[spec.name], raw = input.value;
        out[spec.name] = input.type === "checkbox" ? Number(input.checked)
          : raw === "" ? null : (spec.type === "text" ? raw : F.toNumber(raw));
      }
      return out;
    }
    function recompute() {
      hideTooltip();
      const inputValues = currentValues();
      const changed = formula.inputs.some((spec) => {
        const initial = values[spec.name];
        const normalized = initial === undefined || initial === "" ? null
          : spec.type === "text" ? initial : F.toNumber(initial);
        return inputValues[spec.name] !== normalized;
      });
      edited.textContent = changed ? "What-if scenario · Original data unchanged" : "Original inputs · Edit to explore a scenario";
      reset.disabled = !changed;
      const disclosureState = [...output.querySelectorAll("details")].map((details) => details.open);
      const result = F.evaluate(formula, inputValues);
      if (!summary.childElementCount && result.value === null && propertyGroup) propertyGroup.open = true;
      const display = window.TaxmapFormulaDisplay;
      const symbols = display.symbols(formula, inputValues, result);
      function equationBlock(ast, name, value, label) {
        return el("section", { class: "formula-block" },
          el("h4", {}, label),
          el("div", { class: "formula-equation", html: display.equation(ast, symbols, { name, label }) }),
          el("div", { class: "formula-equation live", html: display.equation(ast, symbols,
            { name, value, substitute: true, label: `${label}, with current values` }) }));
      }
      const parts = [];
      const summaryParts = [];
      if (!result.applicable) summaryParts.push(el("p", { class: "calc-result" }, "Not applicable"), el("p", { class: "muted" }, `Condition: ${formula.applies_when}`));
      else if (result.value === null) summaryParts.push(el("p", { class: "warn" }, result.missing || result.error));
      else summaryParts.push(el("div", { class: "calc-result" }, el("span", {}, "Estimated annual assessment"), el("strong", {}, money(result.value))));
      if (typeof official === "number" && result.value !== null && result.value !== undefined && result.applicable) {
        const diff = result.value - official;
        const match = Math.abs(diff) <= (formula.tolerance || 0.01) + 1e-9;
        summaryParts.push(el("p", { class: `roll-comparison ${match ? "ok" : "muted"}` }, `Official roll: ${money(official)} — `,
          match ? "matches" : `differs by ${money(diff)}`));
      }
      summary.replaceChildren(...summaryParts);
      parts.push(equationBlock(formula.ast, null, result.applicable ? result.value : null, "Assessment formula"));
      if (formula.terms.length) parts.push(el("details", {}, el("summary", {}, "Calculation steps"),
        formula.terms.map((t) => equationBlock(t.ast, t.name, result.terms[t.name], t.label || t.name))));
      parts.push(el("details", {}, el("summary", {}, "What the symbols mean"),
        el("p", { class: "muted" }, "Blue: inputs · Amber: adopted constants · Green: calculated values. Focus or hover a symbol for its explanation."),
        el("table", { class: "grid symbol-glossary" },
          el("tr", {}, el("th", {}, "Symbol"), el("th", {}, "Meaning"), el("th", { class: "num" }, "Current value")),
          Object.entries(symbols).map(([name, entry]) => el("tr", {},
            el("td", { class: `symbol-${entry.kind}` }, name),
            el("td", {}, entry.label || name, entry.description ? el("div", { class: "muted" }, entry.description) : null),
            el("td", { class: "num" }, entry.kind === "lookup" ? "Table" : numberText(entry.value),
              entry.unit ? ` ${entry.unit}` : ""))))));
      if (result.lookups) {
        parts.push(el("h3", {}, "Table lookups"), el("ul", {}, Object.entries(result.lookups).map(([name, hit]) =>
          el("li", {}, `${formula.lookups[name].label || name}: key ${hit.key} → ${numberText(hit.value)}`,
            hit.label ? ` (${hit.label})` : "",
            Object.keys(hit.details || {}).length ? el("table", { class: "kv" },
              Object.entries(hit.details).map(([key, value]) => el("tr", {},
                el("th", {}, key), el("td", {}, numberText(value))))) : null))));
      }
      output.replaceChildren(...parts);
      [...output.querySelectorAll("details")].forEach((details, i) => {
        if (disclosureState[i] !== undefined) details.open = disclosureState[i];
      });
    }
    const constants = Object.entries(formula.constants);
    const body = el("div", {},
      formula.description ? el("p", {}, formula.description) : null,
      selected ? el("p", { class: "muted" }, `Parcel APN ${formatApn(selected.apn)}. Values are prefilled from public data; edit them to explore what-if scenarios.`)
        : el("p", { class: "muted" }, "Enter values to estimate an assessment."),
      summary,
      el("div", { class: "calculator-layout" }, el("section", { class: "calculator-input-panel" },
        el("div", { class: "input-panel-heading" }, el("h3", {}, "Parcel inputs"), reset), edited, form),
        el("section", { class: "calculator-formulas" }, output)),
      el("details", {}, el("summary", {}, "Expression source"), el("div", { class: "expression" }, formula.expression)),
      formula.applies_when ? el("p", { class: "muted" }, `Applies when: ${formula.applies_when}`) : null,
      constants.length ? el("details", {}, el("summary", {}, "Constants"), el("table", { class: "grid" }, constants.map(([name, c]) =>
        el("tr", {}, el("td", {}, c.label || name), el("td", { class: "num" }, numberText(c.value), c.unit ? ` ${c.unit}` : ""),
          el("td", {}, evidenceList((formula.constant_evidence || {})[name])))))) : null,
      Object.keys(formula.lookups).length ? el("details", {}, el("summary", {}, "Lookup tables"),
        Object.entries(formula.lookups).map(([name, table]) => el("div", {}, el("h3", {}, table.label || name),
          el("table", { class: "grid" }, el("tr", {}, el("th", {}, "Keys"), el("th", {}, "Description"), el("th", { class: "num" }, "Value")),
            table.entries.map((e) => el("tr", {}, el("td", {}, e.keys), el("td", {}, e.label || ""), el("td", { class: "num" }, numberText(e.value))))),
          evidenceList((formula.lookup_evidence || {})[name])))) : null,
      formula.caveats && formula.caveats.length ? el("div", { class: "warn" }, el("strong", {}, "Caveats"),
        el("ul", {}, formula.caveats.map((c) => el("li", {}, c)))) : null,
      el("h3", {}, "Sources"), evidenceList(formula.evidence) || el("p", { class: "muted" }, "No formula-level citations recorded."),
      tooltip);
    recompute();
    openModal(formula.name, body);
    setHash({ apn: selected && selected.apn, calc: formulaId });
  }

  // ------------------------------------------------------------------ districts, sources, about
  function openDistrict(id) {
    const district = state.districts.districts.find((d) => d.id === id);
    if (!district) return;
    const rollTotal = district.rolls.length ? money(district.roll_total) : "No roll";
    const body = el("div", {},
      district.description ? el("p", {}, district.description) : null,
      billLabels(district, true),
      el("table", { class: "kv" },
        el("tr", {}, el("th", {}, "Agency"), el("td", {}, district.agency || "—")),
        el("tr", {}, el("th", {}, "Fiscal year"), el("td", {}, district.fiscal_year || "—")),
        el("tr", {}, el("th", {}, "Parcels"), el("td", {}, numberText(district.members),
          district.stated_count ? ` (document states ${district.stated_count})` : "")),
        el("tr", {}, el("th", {}, "Roll total"), el("td", {}, rollTotal,
          district.stated_total ? ` (document states ${money(district.stated_total)})` : ""))),
      district.zones.length ? el("div", {}, el("h3", {}, "Zones"), el("ul", {}, district.zones.map((z) =>
        el("li", {}, el("span", { class: "swatch", style: `background:${z.color}` }), ` ${z.name} — ${z.members} parcels`,
          z.description ? `: ${z.description}` : "")))) : null,
      el("div", { class: "row-actions" },
        el("button", { type: "button", onclick: () => { $("modal").close(); zoomToGroup(id) || showMessage("No outline built for this district."); } }, "Show on map"),
        district.formulas.map((f) => state.formulas[f] ? el("button", { type: "button", class: "ghost", onclick: () => openCalculator(f) },
          `Calculator: ${state.formulas[f].name}`) : null)),
      district.notes_html ? el("div", { html: district.notes_html }) : null,
      el("h3", {}, "Sources"), evidenceList(district.evidence) || el("p", { class: "muted" }, "No citations recorded."));
    openModal(district.name, body);
    setHash({ district: id });
  }

  function renderDistrictList() {
    const items = [];
    for (const district of state.districts.districts) {
      const rows = [district, ...district.zones.map((z) => Object.assign({ zone: true }, z))];
      for (const group of rows) {
        const hasOutline = (state.districts.outlines.features || []).some((f) => f.properties.id === group.id);
        const box = el("input", { type: "checkbox", checked: state.visibleOutlines.has(group.id), disabled: !hasOutline,
          "aria-label": `Show outline for ${group.name}`, onchange: (e) => {
            if (e.target.checked) state.visibleOutlines.add(group.id); else state.visibleOutlines.delete(group.id);
            drawOutlines();
          } });
        items.push(el("div", { class: `district-item${group.zone ? " zone" : ""}` },
          el("label", { class: "outline-toggle", title: `Show outline for ${group.name}` }, box),
          el("span", {}, el("span", { class: "swatch", style: `background:${group.color}` }), " ",
            group.zone ? group.name : el("button", { type: "button", class: "link", title: group.name,
              "aria-label": group.name, onclick: () => openDistrict(group.id) }, group.short_name || group.name),
            el("div", { class: "muted" }, `${numberText(group.members)} parcels`,
              !group.zone && group.rolls.length ? ` · roll ${money(group.roll_total)}` : "")),
          hasOutline ? el("button", { type: "button", class: "ghost", onclick: () => { $("modal").close(); zoomToGroup(group.id); } }, "Zoom") : el("span")));
      }
    }
    const calculators = Object.values(state.formulas);
    return el("div", {},
      el("p", { class: "muted" }, "Toggle approximate outlines built from member parcels. Outlines are not official boundary diagrams."),
      items,
      calculators.length ? el("div", {}, el("h3", {}, "Calculators"), el("div", { class: "row-actions" },
        calculators.map((f) => el("button", { type: "button", class: "ghost", onclick: () => openCalculator(f.id) }, f.name)))) : null);
  }

  function openSources() {
    const data = state.sources;
    const statusOrder = { verified: 0, screened: 1, lead: 2, unreadable: 3, rejected: 4 };
    const sources = [...data.sources].sort((a, b) => (statusOrder[a.status] ?? 9) - (statusOrder[b.status] ?? 9));
    const body = el("div", {},
      el("h3", {}, "Documents and data"),
      el("ul", {}, Object.values(data.resources).map((r) => el("li", {}, resourceLink(r.id),
        el("span", { class: "muted" }, ` ${r.kind}${r.retrieved_at ? `, retrieved ${r.retrieved_at.slice(0, 10)}` : ""}`)))),
      sources.length ? el("div", {}, el("h3", {}, "Research inventory"), el("table", { class: "grid" },
        el("tr", {}, el("th", {}, "Source"), el("th", {}, "Status"), el("th", {}, "Finding")),
        sources.map((s) => el("tr", {},
          el("td", {}, s.url ? el("a", { href: s.url, target: "_blank", rel: "noopener" }, s.title) : (s.resource ? resourceLink(s.resource) : s.title),
            s.district ? el("div", { class: "muted" }, s.district) : null),
          el("td", {}, s.status), el("td", {}, s.finding || s.notes || ""))))) : null,
      data.rolls.length ? el("div", {}, el("h3", {}, "Assessment rolls"), el("ul", {}, data.rolls.map((r) =>
        el("li", {}, `${r.id} (${r.fiscal_year || "year unknown"})`, evidenceList(r.evidence))))) : null);
    openModal("Sources", body);
  }

  function openAbout(pageId) {
    const pages = state.config.pages || [];
    const page = pages.find((p) => p.id === pageId);
    if (page) { openModal(page.title, el("div", { html: page.html })); return; }
    openModal("About this map", el("div", {},
      el("p", {}, state.config.disclaimer),
      el("p", { class: "muted" }, `Built ${state.config.built_at}. ${numberText(state.manifest.feature_count)} parcels.`),
      pages.length ? el("ul", {}, pages.map((p) => el("li", {}, el("button", { type: "button", class: "link", onclick: () => openAbout(p.id) }, p.title)))) : null));
  }

  // ------------------------------------------------------------------ deep links
  let suppressHash = false;
  function setHash(params) {
    const parts = Object.entries(params).filter(([, v]) => v !== null && v !== undefined && v !== "")
      .map(([k, v]) => `${k}=${encodeURIComponent(v)}`);
    suppressHash = true;
    history.replaceState(null, "", parts.length ? `#${parts.join("&")}` : location.pathname + location.search);
    setTimeout(() => { suppressHash = false; }, 0);
  }

  async function applyHash() {
    if (suppressHash) return;
    const params = new URLSearchParams(location.hash.slice(1));
    if (params.get("apn")) await selectByApn(params.get("apn"));
    if (params.get("calc")) openCalculator(params.get("calc"));
    else if (params.get("district")) { zoomToGroup(params.get("district")); openDistrict(params.get("district")); }
    else if (params.get("page")) openAbout(params.get("page"));
  }

  // ------------------------------------------------------------------ boot
  async function main() {
    try {
      const [config, manifest, districts, formulas, sources] = await Promise.all([
        getJSON("data/config.json"), getJSON("data/manifest.json"), getJSON("data/districts.json"),
        getJSON("data/formulas.json"), getJSON("data/sources.json")]);
      Object.assign(state, { config, manifest, districts, sources });
      for (const f of formulas.formulas) state.formulas[f.id] = f;
    } catch (error) {
      showMessage(`Could not load map data (${error.message}). Serve this folder over HTTP.`);
      return;
    }
    document.title = state.config.title;
    $("title").textContent = state.config.title;
    $("subtitle").textContent = state.config.subtitle || "";
    $("disclaimer").textContent = state.config.disclaimer;
    $("intro-title").textContent = state.config.title;
    $("intro-subtitle").textContent = state.config.subtitle || "";
    $("intro-disclaimer").textContent = state.config.disclaimer;
    for (const district of state.districts.districts) {
      state.groupInfo.set(district.id, { name: district.name, color: district.color, zone: false });
      if (district.rule.type === "list") state.visibleOutlines.add(district.id);
      for (const zone of district.zones) state.groupInfo.set(zone.id, { name: zone.name, color: zone.color, zone: true, district: district.id });
    }
    initMap();
    drawOutlines();
    initSearch();
    $("open-districts").addEventListener("click", () => openModal("Districts", renderDistrictList()));
    $("open-sources").addEventListener("click", openSources);
    $("open-about").addEventListener("click", () => openAbout());
    $("open-panel").addEventListener("click", () => {
      setPanelOpen(true);
      $("close-panel").focus({ preventScroll: true });
    });
    $("close-panel").addEventListener("click", () => {
      setPanelOpen(false);
      $("open-panel").focus({ preventScroll: true });
    });
    $("sheet-handle").addEventListener("click", () => {
      const panel = $("panel");
      const expanded = panel.classList.toggle("expanded");
      $("sheet-handle").setAttribute("aria-expanded", String(expanded));
      $("sheet-handle").setAttribute("aria-label", expanded ? "Reduce parcel details" : "Expand parcel details");
      $("sheet-handle").textContent = expanded ? "Reduce" : "Expand";
    });
    $("modal").addEventListener("close", () => {
      if (state.selected) setHash({ apn: state.selected.apn }); else setHash({});
    });
    $("panel-body").replaceChildren(
      el("p", { class: "eyebrow" }, "Explore your community"),
      el("h2", {}, "Explore parcels"),
      el("p", {}, "Search for an address or APN, or zoom in and click a parcel to see its districts, official assessment amounts, and calculators."),
      el("button", { type: "button", onclick: () => openModal("Districts", renderDistrictList()) }, "Browse districts"));
    setPanelOpen(false);
    await new Promise((resolve) => {
      $("title-modal").addEventListener("close", resolve, { once: true });
      $("title-modal").addEventListener("keydown", (event) => {
        if (event.key === "Escape") {
          event.preventDefault();
          $("title-modal").close();
        }
      });
      $("title-modal").showModal();
    });
    $("search-input").focus({ preventScroll: true });
    window.addEventListener("hashchange", applyHash);
    await applyHash();
  }

  main();
})();
