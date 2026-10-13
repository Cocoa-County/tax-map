(function (root) {
  "use strict";

  function ringContains(point, ring) {
    const [x, y] = point;
    let inside = false;
    for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
      const [xi, yi] = ring[i], [xj, yj] = ring[j];
      const cross = (x - xi) * (yj - yi) - (y - yi) * (xj - xi);
      if (cross === 0 && x >= Math.min(xi, xj) && x <= Math.max(xi, xj)
        && y >= Math.min(yi, yj) && y <= Math.max(yi, yj)) return true;
      if ((yi > y) !== (yj > y) && x < (xj - xi) * (y - yi) / (yj - yi) + xi) inside = !inside;
    }
    return inside;
  }

  function polygons(geometry) {
    if (!geometry) return [];
    if (geometry.type === "Polygon") return [geometry.coordinates];
    if (geometry.type === "MultiPolygon") return geometry.coordinates;
    return [];
  }

  function contains(point, geometry) {
    return polygons(geometry).some((rings) => rings.length && ringContains(point, rings[0])
      && !rings.slice(1).some((ring) => ringContains(point, ring)));
  }

  function ringArea(ring) {
    if (!ring.length) return 0;
    // Translate before the shoelace sum to avoid cancellation for small parcel footprints.
    const [ox, oy] = ring[0];
    let area = 0;
    for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
      area += (ring[j][0] - ox) * (ring[i][1] - oy) - (ring[i][0] - ox) * (ring[j][1] - oy);
    }
    return Math.abs(area) / 2;
  }

  function prepare(feature, tile) {
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity, area = 0;
    for (const rings of polygons(feature.geometry)) {
      area += rings.length ? Math.max(0, ringArea(rings[0]) - rings.slice(1).reduce((sum, ring) => sum + ringArea(ring), 0)) : 0;
      for (const ring of rings) for (const [x, y] of ring) {
        minX = Math.min(minX, x);
        minY = Math.min(minY, y);
        maxX = Math.max(maxX, x);
        maxY = Math.max(maxY, y);
      }

    }
    return { feature, tile, area, bounds: [minX, minY, maxX, maxY] };
  }

  function representativePoint(geometry) {
    for (const rings of polygons(geometry)) {
      const ys = [...new Set(rings.flatMap((ring) => ring.map((p) => p[1])))].sort((a, b) => a - b);
      if (ys.length < 2) continue;
      const scans = [(ys[0] + ys[ys.length - 1]) / 2,
        ...ys.slice(1).map((y, i) => (y + ys[i]) / 2)];
      for (const y of scans) {
        const xs = [];
        for (const ring of rings) for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
          const [xi, yi] = ring[i], [xj, yj] = ring[j];
          if ((yi > y) !== (yj > y)) xs.push(xi + (y - yi) * (xj - xi) / (yj - yi));
        }
        xs.sort((a, b) => a - b);
        for (let i = 1; i < xs.length; i += 2) {
          const point = [(xs[i - 1] + xs[i]) / 2, y];
          if (xs[i] > xs[i - 1] && contains(point, { type: "Polygon", coordinates: rings })) return point;
        }
      }
    }
    return null;
  }

  function inBounds(point, bounds) {
    return point[0] >= bounds[0] && point[0] <= bounds[2] && point[1] >= bounds[1] && point[1] <= bounds[3];
  }

  function find(point, entries, normalizeApn) {
    const candidates = Array.from(entries).filter((entry) => inBounds(point, entry.bounds)
      && contains(point, entry.feature.geometry));
    candidates.sort((a, b) => a.area - b.area
      || String(a.feature.id).localeCompare(String(b.feature.id), "en", { numeric: true }));
    const seen = new Set();
    return candidates.filter((entry) => {
      const apn = normalizeApn(entry.feature.properties.apn);
      const key = apn ? `apn:${apn}` : `id:${entry.feature.id}`;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
  }

  const api = { contains, prepare, representativePoint, inBounds, find };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.TaxmapParcelHit = api;
})(typeof window !== "undefined" ? window : globalThis);
