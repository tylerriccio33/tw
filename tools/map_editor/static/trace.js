// Magnetic tracing: pulling a run of vertices straight off an existing
// boundary instead of clicking along it by hand.
//
// Tracing a coastline point-by-point is the single most tedious thing in
// this editor, and the coastline is already there as geometry - the whole
// job is picking which stretch of it you want. Click once to lock onto a
// boundary, move to see the stretch light up, click again to take it.
//
// Everything here is pure: polylines in, points out, no DOM. That's what
// lets tests/test_trace.py run it under node.

(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.Trace = api;
})(typeof self !== "undefined" ? self : this, function () {
  const EPS = 1e-9;

  function mod(value, span) {
    return ((value % span) + span) % span;
  }

  function dist(ax, ay, bx, by) {
    return Math.hypot(ax - bx, ay - by);
  }

  // A polyline carries its cumulative arc length so a click can be turned
  // into a single scalar position along the boundary. Comparing two
  // scalars is what makes "which way round?" a one-line decision instead
  // of a graph walk.
  function buildPolyline(points, closed) {
    const cum = [0];
    const count = closed ? points.length : points.length - 1;
    for (let i = 0; i < count; i++) {
      const a = points[i];
      const b = points[(i + 1) % points.length];
      cum.push(cum[i] + dist(a[0], a[1], b[0], b[1]));
    }
    return { points, closed: !!closed, cum, total: cum[cum.length - 1] };
  }

  function segmentCount(polyline) {
    return polyline.closed
      ? polyline.points.length
      : polyline.points.length - 1;
  }

  function closestPointOnSegment(px, py, a, b) {
    const dx = b[0] - a[0];
    const dy = b[1] - a[1];
    const lenSq = dx * dx + dy * dy;
    if (lenSq === 0) return { point: [a[0], a[1]], t: 0 };
    let t = ((px - a[0]) * dx + (py - a[1]) * dy) / lenSq;
    t = Math.max(0, Math.min(1, t));
    return { point: [a[0] + t * dx, a[1] + t * dy], t };
  }

  function nearestOnPolyline(polyline, x, y) {
    let best = null;
    const segments = segmentCount(polyline);
    for (let i = 0; i < segments; i++) {
      const a = polyline.points[i];
      const b = polyline.points[(i + 1) % polyline.points.length];
      const hit = closestPointOnSegment(x, y, a, b);
      const d = dist(x, y, hit.point[0], hit.point[1]);
      if (!best || d < best.dist) {
        best = { segIndex: i, t: hit.t, point: hit.point, dist: d };
      }
    }
    return best;
  }

  function nearestOnPolylines(polylines, x, y) {
    let best = null;
    for (let i = 0; i < polylines.length; i++) {
      const hit = nearestOnPolyline(polylines[i], x, y);
      if (hit && (!best || hit.dist < best.dist)) {
        best = Object.assign({ lineIndex: i }, hit);
      }
    }
    return best;
  }

  function arcPosition(polyline, hit) {
    const a = polyline.points[hit.segIndex];
    const b = polyline.points[(hit.segIndex + 1) % polyline.points.length];
    return polyline.cum[hit.segIndex] + hit.t * dist(a[0], a[1], b[0], b[1]);
  }

  // Which way around the ring? The short way is what you meant nearly
  // every time - the long way is one modifier key away for when you're
  // deliberately wrapping a whole landmass.
  function extractSubpath(polyline, hitA, hitB, options) {
    const longWay = !!(options && options.longWay);
    const startArc = arcPosition(polyline, hitA);
    const endArc = arcPosition(polyline, hitB);
    const between = [];

    if (polyline.closed) {
      const forwardSpan = mod(endArc - startArc, polyline.total);
      const backwardSpan = polyline.total - forwardSpan;
      const goForward = longWay
        ? forwardSpan > backwardSpan
        : forwardSpan <= backwardSpan;
      const span = goForward ? forwardSpan : backwardSpan;

      for (let i = 0; i < polyline.points.length; i++) {
        const rel = goForward
          ? mod(polyline.cum[i] - startArc, polyline.total)
          : mod(startArc - polyline.cum[i], polyline.total);
        if (rel > EPS && rel < span - EPS) between.push([rel, polyline.points[i]]);
      }
    } else {
      const low = Math.min(startArc, endArc);
      const high = Math.max(startArc, endArc);
      for (let i = 0; i < polyline.points.length; i++) {
        const arc = polyline.cum[i];
        if (arc > low + EPS && arc < high - EPS) {
          const rel = startArc <= endArc ? arc - startArc : startArc - arc;
          between.push([rel, polyline.points[i]]);
        }
      }
    }

    between.sort((p, q) => p[0] - q[0]);
    return [hitA.point].concat(between.map((entry) => entry[1]), [hitB.point]);
  }

  // A single click-pair on a real coastline can pull in hundreds of
  // vertices. Simplify keeps the shape and drops the ones that carry no
  // information at the zoom you're working at.
  function simplify(points, epsilon) {
    if (points.length < 3 || epsilon <= 0) return points.slice();

    const keep = new Array(points.length).fill(false);
    keep[0] = keep[points.length - 1] = true;
    const stack = [[0, points.length - 1]];

    while (stack.length) {
      const [first, last] = stack.pop();
      let maxDist = -1;
      let index = -1;
      const a = points[first];
      const b = points[last];
      for (let i = first + 1; i < last; i++) {
        const hit = closestPointOnSegment(points[i][0], points[i][1], a, b);
        const d = dist(points[i][0], points[i][1], hit.point[0], hit.point[1]);
        if (d > maxDist) {
          maxDist = d;
          index = i;
        }
      }
      if (maxDist > epsilon && index > 0) {
        keep[index] = true;
        stack.push([first, index], [index, last]);
      }
    }

    return points.filter((_, i) => keep[i]);
  }

  // A hard ceiling regardless of epsilon. A polygon with ten thousand
  // vertices makes the whole editor crawl, and no border needs it.
  function decimate(points, maxCount) {
    if (points.length <= maxCount) return points.slice();
    const out = [];
    const step = (points.length - 1) / (maxCount - 1);
    for (let i = 0; i < maxCount; i++) out.push(points[Math.round(i * step)]);
    out[out.length - 1] = points[points.length - 1];
    return out;
  }

  function traceBetween(polyline, hitA, hitB, options) {
    const opts = options || {};
    let points = extractSubpath(polyline, hitA, hitB, opts);
    if (opts.epsilon) points = simplify(points, opts.epsilon);
    if (opts.maxVertices) points = decimate(points, opts.maxVertices);
    return points;
  }

  return {
    buildPolyline,
    segmentCount,
    closestPointOnSegment,
    nearestOnPolyline,
    nearestOnPolylines,
    arcPosition,
    extractSubpath,
    simplify,
    decimate,
    traceBetween,
  };
});
