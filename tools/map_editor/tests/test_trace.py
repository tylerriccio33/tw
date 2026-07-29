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
