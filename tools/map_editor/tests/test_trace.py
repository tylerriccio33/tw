"""The magnetic trace extractor, driven under node.

static/trace.js is deliberately pure, so it runs without a browser.
Each case here is a small script that requires the module and prints
JSON. The test then compares that against the tracing gesture's expected
output.

These skip when node is missing. Node is a nice-to-have for the suite,
not a reason a checkout can't run the rest.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

TRACE_JS = Path(__file__).resolve().parents[1] / "static" / "trace.js"

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not installed"
)


def run_js(body: str):
    script = f"const Trace = require({str(TRACE_JS)!r});\n{body}"
    result = subprocess.run(
        ["node", "-e", script],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(f"node failed:\n{result.stderr}")
    return json.loads(result.stdout)


# A unit square traced counter-clockwise, 10 units on a side. Corners at
# arc positions 0, 10, 20, 30; total 40.
SQUARE = "[[0,0],[10,0],[10,10],[0,10]]"


def test_a_trace_takes_the_short_way_round_a_closed_ring():
    points = run_js(f"""
        const pl = Trace.buildPolyline({SQUARE}, true);
        const a = Trace.nearestOnPolyline(pl, 2, 0);   // bottom edge
        const b = Trace.nearestOnPolyline(pl, 10, 4);  // right edge
        console.log(JSON.stringify(Trace.extractSubpath(pl, a, b)));
    """)
    # Short way passes the (10,0) corner only.
    assert points[0] == [2, 0]
    assert points[-1] == [10, 4]
    assert points[1:-1] == [[10, 0]]


def test_the_long_way_round_is_available_on_request():
    points = run_js(f"""
        const pl = Trace.buildPolyline({SQUARE}, true);
        const a = Trace.nearestOnPolyline(pl, 2, 0);
        const b = Trace.nearestOnPolyline(pl, 10, 4);
        console.log(JSON.stringify(
            Trace.extractSubpath(pl, a, b, {{longWay: true}})));
    """)
    # The other direction wraps past three corners.
    assert points[0] == [2, 0]
    assert points[-1] == [10, 4]
    assert points[1:-1] == [[0, 0], [0, 10], [10, 10]]


def test_both_directions_together_cover_the_whole_ring():
    result = run_js(f"""
        const pl = Trace.buildPolyline({SQUARE}, true);
        const a = Trace.nearestOnPolyline(pl, 2, 0);
        const b = Trace.nearestOnPolyline(pl, 10, 4);
        const short = Trace.extractSubpath(pl, a, b);
        const long = Trace.extractSubpath(pl, a, b, {{longWay: true}});
        console.log(JSON.stringify({{short: short.length, long: long.length}}));
    """)
    # Every corner appears in exactly one of the two directions.
    assert result["short"] + result["long"] == 4 + 4


def test_a_trace_on_one_segment_returns_just_its_endpoints():
    points = run_js(f"""
        const pl = Trace.buildPolyline({SQUARE}, true);
        const a = Trace.nearestOnPolyline(pl, 2, 0);
        const b = Trace.nearestOnPolyline(pl, 7, 0);
        console.log(JSON.stringify(Trace.extractSubpath(pl, a, b)));
    """)
    assert points == [[2, 0], [7, 0]]


def test_an_open_polyline_traces_the_direct_span_only():
    points = run_js("""
        const pl = Trace.buildPolyline([[0,0],[10,0],[20,0],[30,0]], false);
        const a = Trace.nearestOnPolyline(pl, 5, 0);
        const b = Trace.nearestOnPolyline(pl, 25, 0);
        console.log(JSON.stringify(Trace.extractSubpath(pl, a, b)));
    """)
    assert points == [[5, 0], [10, 0], [20, 0], [25, 0]]


def test_an_open_polyline_traced_backwards_returns_points_in_walk_order():
    points = run_js("""
        const pl = Trace.buildPolyline([[0,0],[10,0],[20,0],[30,0]], false);
        const a = Trace.nearestOnPolyline(pl, 25, 0);
        const b = Trace.nearestOnPolyline(pl, 5, 0);
        console.log(JSON.stringify(Trace.extractSubpath(pl, a, b)));
    """)
    assert points == [[25, 0], [20, 0], [10, 0], [5, 0]], (
        "the path has to run from the first click to the second, not "
        "always left to right"
    )


def test_nearest_locks_onto_the_closest_of_several_boundaries():
    result = run_js("""
        const near = Trace.buildPolyline([[0,0],[10,0],[10,10],[0,10]], true);
        const far  = Trace.buildPolyline([[100,100],[110,100],[110,110]], true);
        const hit = Trace.nearestOnPolylines([near, far], 5, 1);
        console.log(JSON.stringify({line: hit.lineIndex, seg: hit.segIndex}));
    """)
    assert result["line"] == 0
    assert result["seg"] == 0


def test_simplify_drops_collinear_noise_but_keeps_the_corners():
    points = run_js("""
        const line = [[0,0],[1,0.01],[2,0],[3,0.01],[4,0],[4,4]];
        console.log(JSON.stringify(Trace.simplify(line, 0.5)));
    """)
    assert points == [[0, 0], [4, 0], [4, 4]]


def test_simplify_keeps_detail_larger_than_the_tolerance():
    points = run_js("""
        const line = [[0,0],[2,3],[4,0]];
        console.log(JSON.stringify(Trace.simplify(line, 0.5)));
    """)
    assert points == [[0, 0], [2, 3], [4, 0]]


def test_decimate_enforces_a_hard_vertex_ceiling():
    result = run_js("""
        const many = [];
        for (let i = 0; i < 500; i++) many.push([i, 0]);
        const out = Trace.decimate(many, 10);
        console.log(JSON.stringify({
            n: out.length, first: out[0], last: out[out.length - 1]}));
    """)
    assert result["n"] == 10
    assert result["first"] == [0, 0]
    assert result["last"] == [499, 0], "the endpoint must survive decimation"


# ---------------------------------------------------------------------------
# snap-radius edge cases
#
# trace.js itself has no snap radius constant - editor.js's snapPoint()
# applies one on top of nearestOnPolyline/nearestOnPolylines' returned
# .dist. These tests exercise the primitives that a radius cutoff is
# built on: exact-distance ties, degenerate (zero-length) segments, and
# "nothing in range" (an empty candidate list, standing in for a caller
# whose radius check filtered out every candidate).
# ---------------------------------------------------------------------------


def test_nearest_with_no_candidate_lines_returns_nothing():
    # Stand-in for "no candidates within the snap radius": the caller
    # would have filtered every polyline out before calling this, leaving
    # an empty list. Must not throw - just report no hit.
    result = run_js("""
        const hit = Trace.nearestOnPolylines([], 5, 5);
        console.log(JSON.stringify(hit));
    """)
    assert result is None


def test_nearest_breaks_an_exact_distance_tie_toward_the_first_line():
    # Two boundaries equidistant from the query point (a point sitting
    # exactly on the perpendicular bisector between them - the "exactly
    # at the snap radius boundary between two candidates" case). Ties
    # must resolve deterministically rather than depending on iteration
    # order or floating point noise: nearestOnPolylines only replaces
    # its best match on a strict '<', so the first candidate at the
    # minimum distance wins.
    result = run_js("""
        const a = Trace.buildPolyline([[0,0],[10,0]], false);
        const b = Trace.buildPolyline([[0,10],[10,10]], false);
        const hit = Trace.nearestOnPolylines([a, b], 5, 5);
        console.log(JSON.stringify({line: hit.lineIndex, dist: hit.dist}));
    """)
    assert result["line"] == 0
    assert result["dist"] == 5


def test_nearest_on_a_zero_length_segment_does_not_produce_nan():
    # Two coincident points collapse a segment to zero length.
    # closestPointOnSegment's lenSq === 0 guard must take over instead of
    # dividing by zero - a NaN distance would silently sort as "never the
    # nearest", making that point permanently unsnappable.
    result = run_js("""
        const pl = Trace.buildPolyline([[5,5],[5,5],[20,5]], false);
        const hit = Trace.nearestOnPolyline(pl, 5, 8);
        console.log(JSON.stringify(hit));
    """)
    assert result["point"] == [5, 5]
    assert result["dist"] == 3


def test_nearest_on_a_single_point_closed_polyline_is_the_point_itself():
    # A closed "polyline" with exactly one point still has to report a
    # sensible (zero-length) segment rather than indexing past the array
    # or dividing by zero.
    result = run_js("""
        const pl = Trace.buildPolyline([[7,7]], true);
        const hit = Trace.nearestOnPolyline(pl, 100, 100);
        console.log(JSON.stringify(hit));
    """)
    assert result["point"] == [7, 7]
    assert result["dist"] == pytest.approx((93**2 + 93**2) ** 0.5)


def test_a_query_point_exactly_on_the_boundary_has_zero_distance():
    # The degenerate "snap radius of 0" case from the caller's point of
    # view: a point already sitting exactly on the boundary must report
    # dist == 0 so any positive radius (including a very small one)
    # still snaps it.
    points = run_js(f"""
        const pl = Trace.buildPolyline({SQUARE}, true);
        const hit = Trace.nearestOnPolyline(pl, 10, 0);
        console.log(JSON.stringify({{dist: hit.dist, point: hit.point}}));
    """)
    assert points["dist"] == 0
    assert points["point"] == [10, 0]


def test_trace_between_applies_simplification_and_the_ceiling():
    result = run_js("""
        const pts = [];
        for (let i = 0; i < 400; i++) pts.push([i, (i % 2) * 0.01]);
        const pl = Trace.buildPolyline(pts, false);
        const a = Trace.nearestOnPolyline(pl, 0, 0);
        const b = Trace.nearestOnPolyline(pl, 399, 0);
        const out = Trace.traceBetween(pl, a, b, {epsilon: 0.5, maxVertices: 50});
        console.log(JSON.stringify({n: out.length}));
    """)
    assert result["n"] <= 50
    assert result["n"] < 400, "a near-straight run should collapse"
