import shutil
import subprocess
from pathlib import Path

import pytest


APP_JS = Path(__file__).resolve().parents[1] / "src" / "taxmap" / "site" / "static" / "app.js"
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def test_map_boundary_and_parcel_zoom_lifecycle():
    script = r"""
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
let zoom = 14, fit, overlays, pendingTile, requests = 0;
const panes = {}, handlers = {}, active = new Set(), layers = [];
const hint = {};
const map = {
  createPane(name) { return panes[name] = {style: {}}; },
  getPane(name) { return panes[name]; },
  getZoom() { return zoom; },
  fitBounds(bounds) { fit = bounds; },
  on(event, handler) { handlers[event] = handler; },
  getBounds() { return {getWest: () => 0, getSouth: () => 0, getEast: () => 10, getNorth: () => 10}; },
  removeLayer(layer) { active.delete(layer); },
};
const makeLayer = (data, options = {}) => {
  const layer = {
    data, options, added: [],
    addTo() { active.add(this); return this; },
    remove() { active.delete(this); },
    addData(value) { this.added.push(value); },
  };
  layers.push(layer);
  return layer;
};
const L = {
  map: () => map, tileLayer: () => makeLayer(null), layerGroup: () => makeLayer(null),
  geoJSON: makeLayer,
  control: {
    layers(base, value) { overlays = value; return {addTo() {}}; },
    scale: () => ({addTo() {}}),
  },
};
const context = {
  L, console, Map, Set, URLSearchParams,
  window: {TaxmapFormula: {}, TaxmapParcelHit: {prepare: (feature, tile) => ({feature, tile})}},
  document: {getElementById: () => hint},
  history: {replaceState() {}}, location: {pathname: "/", search: ""},
  setTimeout: callback => callback(),
  fetch: async path => {
    requests++;
    if (path.includes("/tiles/")) return new Promise(resolve => {
      pendingTile = data => resolve({ok: true, json: async () => data});
    });
    throw new Error(`Unexpected request: ${path}`);
  },
};
let source = fs.readFileSync(process.argv[1], "utf8");
assert(source.includes("  main();"));
source = source.replace("  main();", `
  renderParcel = () => {};
  globalThis.testMap = {state, initMap, loadVisibleTiles, loadTile, selectParcel};
`);
vm.runInNewContext(source, context);
const api = context.testMap;
const geometry = {type: "Polygon", coordinates: [[[1,1],[2,1],[2,2],[1,1]]]};
const feature = {type: "Feature", id: 1, properties: {apn: "001"}, geometry};
const boundary = {type: "FeatureCollection", features: [{type: "Feature", geometry, properties: {}}]};
Object.assign(api.state, {
  config: {jurisdiction_boundary: {title: "Demo City", bounds: [1, 1, 2, 2]}},
  manifest: {minimum_zoom: 15, bounds: [0, 0, 10, 10], apn_field: "APN",
    tiles: [{id: "one", bounds: [0, 0, 3, 3]}]},
  boundary,
});
api.initMap();
assert.equal(JSON.stringify(fit), JSON.stringify([[1,1],[2,2]]));
assert.equal(overlays["Demo City boundary"].data, boundary);
assert.equal(overlays["Demo City boundary"].options.interactive, false);
assert.equal(overlays["Demo City boundary"].options.style.fill, false);
assert.equal(panes.parcels.style.display, "none");
assert.equal(hint.hidden, false);
assert.equal(requests, 0);
const boundaryLayer = overlays["Demo City boundary"];
const parcels = overlays.Parcels;
assert.equal(parcels.options.pane, "parcels");
function setZoom(value) { zoom = value; handlers.zoomend(); handlers.moveend(); }
(async () => {
  setZoom(15);
  assert.equal(panes.parcels.style.display, "");
  assert.equal(hint.hidden, true);
  assert.equal(requests, 1);
  setZoom(14);
  pendingTile({type: "FeatureCollection", features: [feature]});
  await api.loadTile("one");
  assert.equal(panes.parcels.style.display, "none");
  assert.equal(parcels.added.length, 1);
  api.state.attributes.set("one", Promise.resolve({"1": {APN: "001"}}));
  await api.selectParcel(1, "one", []);
  const selected = layers.at(-1);
  assert.equal(selected.options.pane, "parcels");
  assert.equal(api.state.selected.apn, "001");
  assert.equal(panes.parcels.style.display, "none");
  for (const value of [15, 16, 14, 15, 13, 16]) {
    setZoom(value);
    assert.equal(panes.parcels.style.display, value >= 15 ? "" : "none");
    assert.equal(hint.hidden, value >= 15);
    assert(active.has(boundaryLayer));
    assert(active.has(selected));
    assert.equal(api.state.selected.apn, "001");
  }
  assert.equal(requests, 1);
  map.removeLayer(parcels);
  setZoom(14);
  setZoom(16);
  assert(!active.has(parcels));
  assert(active.has(boundaryLayer));
  api.state.config = {};
  api.state.boundary = null;
  api.state.manifest.tiles = [];
  api.initMap();
  assert.equal(JSON.stringify(fit), JSON.stringify([[0,0],[10,10]]));
  assert.equal(Object.keys(overlays).length, 2);
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
    subprocess.run(["node", "-e", script, str(APP_JS)],
                   capture_output=True, text=True, encoding="utf-8", check=True)
