#!/usr/bin/env python3
"""Regenerate src/geom/geo.rs (Europe coastline + city positions) and src/geom/elev.bin
(real elevation) from public-domain data. Self-contained: downloads the source
GeoJSON and DEM tiles on demand and writes the Rust module in place.

    python3 bake/gen_geo.py

Sources: Natural Earth 50m land polygons (coastline) and AWS Open Data
terrarium tiles (elevation). Stdlib only — no Pillow, no numpy, no GDAL.
"""
import json, math, os, struct, sys, urllib.request, zlib

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
# The script moved from godot-client/tools/ to bake/ in the Unreal migration,
# and its outputs (geo.rs, elev.bin) now live beside the other geometry sources
# in bake/src/geom/, consumed by the `bake` binary, which is the only
# thing left that reads them.
SRC = os.path.join(ROOT, "bake", "src", "geom")
CONFIG = os.path.join(ROOT, "bake", "config", "geo.yaml")
CAMPAIGN = os.path.join(ROOT, "engine", "campaign", "britain.yaml")
CACHE = os.path.join(HERE, "land50.geojson")
TILES = os.path.join(HERE, "tiles")                         # cached DEM tiles
URL = ("https://raw.githubusercontent.com/nvkelso/natural-earth-vector/"
       "master/geojson/ne_50m_land.geojson")

if not os.path.exists(CACHE):
    print(f"downloading {URL}", file=sys.stderr)
    urllib.request.urlretrieve(URL, CACHE)


# --- Config ---------------------------------------------------------------
# All the geographic framing lives in ../config/geo.yaml, not here. Python has no
# stdlib YAML reader and this script is intentionally dependency-free, so we parse
# the small, self-authored subset the file uses: top-level `key: value`, one level
# of nested `key: value` blocks, and a list of inline `- { k: v, ... }` flow maps.

def _scalar(s):
    s = s.strip().strip('"\'')
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        return s

def _flow_map(s):
    """Parse an inline `{ a: 1, b: two }` mapping."""
    out = {}
    for part in s.strip().lstrip("{").rstrip("}").split(","):
        k, _, v = part.partition(":")
        out[k.strip()] = _scalar(v)
    return out

def load_config(path):
    lines = [ln.split("#", 1)[0].rstrip() for ln in open(path)]
    lines = [ln for ln in lines if ln.strip()]
    cfg, i, n = {}, 0, len(lines)
    while i < n:
        assert not lines[i][:1].isspace(), f"unexpected indent: {lines[i]!r}"
        key, _, val = lines[i].strip().partition(":")
        key, val = key.strip(), val.strip()
        i += 1
        if val:                                   # top-level scalar
            cfg[key] = _scalar(val)
        elif i < n and lines[i].lstrip().startswith("- "):   # list of flow maps
            items = []
            while i < n and lines[i].lstrip().startswith("- "):
                items.append(_flow_map(lines[i].strip()[2:]))
                i += 1
            cfg[key] = items
        else:                                     # nested one-level map
            sub = {}
            while i < n and lines[i][:1].isspace():
                k2, _, v2 = lines[i].strip().partition(":")
                sub[k2.strip()] = _scalar(v2.strip())
                i += 1
            cfg[key] = sub
    return cfg

def province_city_names(path):
    """Settlement names in province order from the engine campaign file. Reads
    the `city` field of each entry in the `provinces:` block (a one-line
    `- { name: Province, city: Settlement, ... }`) — the settlement, not the
    province, because that is what geo.yaml's cities are and where POS seats them
    (province Lothian holds the city Edinburgh)."""
    cities, in_block = [], False
    for raw in open(path):
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        if not line[:1].isspace():                 # a top-level key
            in_block = line.strip().startswith("provinces:")
        elif in_block and line.lstrip().startswith("- "):
            cities.append(_flow_map(line.strip()[2:])["city"])
    return cities

CFG = load_config(CONFIG)

# Projection center and lon/lat clip box.
LON0, LAT0, K = CFG["projection"]["lon0"], CFG["projection"]["lat0"], CFG["projection"]["k"]
LON_MIN, LON_MAX = CFG["bbox"]["lon_min"], CFG["bbox"]["lon_max"]
LAT_MIN, LAT_MAX = CFG["bbox"]["lat_min"], CFG["bbox"]["lat_max"]

# A point inside the clipped continental fragment; rings containing it are dropped.
CONTINENT_PT = (CFG["continent_point"]["lon"], CFG["continent_point"]["lat"])

# Land-mesh extents (emitted into geo.rs) and elevation grid.
HALF_W, HALF_H = CFG["mesh"]["half_w"], CFG["mesh"]["half_h"]
ELEV_W, ELEV_H = CFG["mesh"]["elev_w"], CFG["mesh"]["elev_h"]
ELEV_Z = CFG["mesh"]["elev_zoom"]   # terrarium zoom
ELEV_BLUR = CFG["mesh"]["elev_blur"]  # low-pass radius in cells; see build_elevation()
TILE_URL = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"

def project(lon, lat):
    x = (lon - LON0) * K * math.cos(math.radians(LAT0))
    z = -(lat - LAT0) * K   # north (high lat) -> up (negative Z)
    return (x, z)

def unproject(x, z):
    lon = x / (K * math.cos(math.radians(LAT0))) + LON0
    lat = LAT0 - z / K
    return (lon, lat)

# --- Terrarium DEM tiles --------------------------------------------------
# Elevation is encoded in the RGB channels: (R*256 + G + B/256) - 32768 metres.

def decode_png(raw):
    """Minimal PNG reader: 8-bit RGB/RGBA, non-interlaced. Returns (w, h, bpp, pixels)."""
    assert raw[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    pos, idat, w, h, bpp = 8, b"", None, None, None
    while pos < len(raw):
        ln = struct.unpack(">I", raw[pos:pos + 4])[0]
        typ = raw[pos + 4:pos + 8]
        data = raw[pos + 8:pos + 8 + ln]
        pos += 12 + ln
        if typ == b"IHDR":
            w, h, depth, ctype = struct.unpack(">IIBB", data[:10])
            assert depth == 8 and ctype in (2, 6), f"unsupported PNG {depth=} {ctype=}"
            bpp = 3 if ctype == 2 else 4
        elif typ == b"IDAT":
            idat += data
        elif typ == b"IEND":
            break
    buf = zlib.decompress(idat)
    stride = w * bpp
    out = bytearray(w * h * bpp)
    prev = bytearray(stride)
    p = 0
    for y in range(h):
        f = buf[p]; p += 1
        line = bytearray(buf[p:p + stride]); p += stride
        if f == 1:
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 255
        elif f == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 255
        elif f == 3:
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((a + prev[i]) >> 1)) & 255
        elif f == 4:
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                b = prev[i]
                c = prev[i - bpp] if i >= bpp else 0
                pp = a + b - c
                pa, pb, pc = abs(pp - a), abs(pp - b), abs(pp - c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + pr) & 255
        out[y * stride:(y + 1) * stride] = line
        prev = line
    return w, h, bpp, out

def tile_xy(lon, lat, z):
    """Web-Mercator tile coordinates (fractional)."""
    n = 2 ** z
    x = (lon + 180.0) / 360.0 * n
    y = (1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n
    return x, y

def fetch_tile(z, x, y):
    os.makedirs(TILES, exist_ok=True)
    path = os.path.join(TILES, f"{z}_{x}_{y}.png")
    if not os.path.exists(path):
        urllib.request.urlretrieve(TILE_URL.format(z=z, x=x, y=y), path)
    with open(path, "rb") as f:
        return decode_png(f.read())

class Mosaic:
    """Lazily-decoded grid of terrarium tiles, sampled in global pixel space."""

    def __init__(self, z):
        self.z = z
        self.cache = {}
        self.size = 256

    def _tile(self, tx, ty):
        if (tx, ty) not in self.cache:
            self.cache[(tx, ty)] = fetch_tile(self.z, tx, ty)
        return self.cache[(tx, ty)]

    def px(self, gx, gy):
        """Elevation in metres at integer global pixel (gx, gy)."""
        n = 2 ** self.z
        tx, ty = gx // self.size, gy // self.size
        tx = min(max(tx, 0), n - 1)
        ty = min(max(ty, 0), n - 1)
        w, h, bpp, buf = self._tile(tx, ty)
        ox = min(max(gx - tx * self.size, 0), w - 1)
        oy = min(max(gy - ty * self.size, 0), h - 1)
        i = (oy * w + ox) * bpp
        return (buf[i] * 256 + buf[i + 1] + buf[i + 2] / 256.0) - 32768.0

    def at(self, lon, lat):
        """Bilinear elevation in metres. Point-sampled, not box-averaged: we are
        downsampling, and averaging flattens exactly the peaks we want."""
        fx, fy = tile_xy(lon, lat, self.z)
        gx, gy = fx * self.size - 0.5, fy * self.size - 0.5
        x0, y0 = math.floor(gx), math.floor(gy)
        tx, ty = gx - x0, gy - y0
        a = self.px(x0, y0); b = self.px(x0 + 1, y0)
        c = self.px(x0, y0 + 1); d = self.px(x0 + 1, y0 + 1)
        return (a + (b - a) * tx) + ((c + (d - c) * tx) - (a + (b - a) * tx)) * ty

def box_blur(grid, w, h, radius):
    """Separable box blur over a float grid."""
    tmp = [0.0] * (w * h)
    for r in range(h):
        row = r * w
        for c in range(w):
            lo, hi = max(0, c - radius), min(w - 1, c + radius)
            tmp[row + c] = sum(grid[row + i] for i in range(lo, hi + 1)) / (hi - lo + 1)
    out = [0.0] * (w * h)
    for c in range(w):
        for r in range(h):
            lo, hi = max(0, r - radius), min(h - 1, r + radius)
            out[r * w + c] = sum(tmp[i * w + c] for i in range(lo, hi + 1)) / (hi - lo + 1)
    return out

def build_elevation():
    """Resample the DEM onto the world-XZ grid terrain.rs samples.

    Two things happen here that matter downstream:

    * Bathymetry is clamped away. We only ever use land height (the coastline
      mask owns the shore), and leaving -6000 m ocean trenches in would drag
      coastal land downward once blurred.
    * The result is low-passed. terrain.rs exaggerates elevation ~46x to make
      the Alps visible at continental zoom — which multiplies *slope* by 46 too,
      turning real 2000m-over-10km relief into 84-degree knife edges. Blurring
      first keeps the massif shape and drops the shredded crunch.
    """
    mos = Mosaic(ELEV_Z)
    grid = []
    for r in range(ELEV_H):
        z = -HALF_H + (2.0 * HALF_H) * r / (ELEV_H - 1)
        for c in range(ELEV_W):
            x = -HALF_W + (2.0 * HALF_W) * c / (ELEV_W - 1)
            lon, lat = unproject(x, z)
            grid.append(max(0.0, mos.at(lon, lat)))
        if r % 48 == 0:
            print(f"// elevation row {r}/{ELEV_H} ({len(mos.cache)} tiles)", file=sys.stderr)
    for _ in range(2):   # two box passes ~ a gaussian
        grid = box_blur(grid, ELEV_W, ELEV_H, ELEV_BLUR)
    return [max(0, min(32767, int(round(m)))) for m in grid]

# Provinces, in engine order, at real city coordinates — from config/geo.yaml.
CITIES = [(c["name"], c["lon"], c["lat"]) for c in CFG["cities"]]

# geo.rs POS is indexed by ProvinceId, so this list must line up 1:1 with the
# provinces in engine/campaign/britain.yaml. A mismatch would silently seat a
# city on the wrong province, so fail loudly at generation time instead.
_cities = [name for name, _, _ in CITIES]
_settlements = province_city_names(CAMPAIGN)
if _cities != _settlements:
    extra = [n for n in _cities if n not in _settlements]
    missing = [n for n in _settlements if n not in _cities]
    detail = ""
    if extra:
        detail += f"\n  in geo.yaml but not britain.yaml: {extra}"
    if missing:
        detail += f"\n  in britain.yaml but not geo.yaml: {missing}"
    if not detail:                      # same set, different order
        detail = (f"\n  same settlements, wrong order:"
                  f"\n    geo.yaml:     {_cities}"
                  f"\n    britain.yaml: {_settlements}")
    sys.exit("gen_geo.py: config/geo.yaml cities do not match the settlements in "
             "engine/campaign/britain.yaml (by province order)." + detail)

# --- Sutherland-Hodgman clip against the lon/lat bbox (convex) -------------
def clip_edge(poly, inside, intersect):
    out = []
    n = len(poly)
    for i in range(n):
        a = poly[i]
        b = poly[(i + 1) % n]
        ina, inb = inside(a), inside(b)
        if ina:
            out.append(a)
            if not inb:
                out.append(intersect(a, b))
        elif inb:
            out.append(intersect(a, b))
    return out

def clip_bbox(poly):
    def lerp(a, b, t): return (a[0] + (b[0]-a[0])*t, a[1] + (b[1]-a[1])*t)
    # left
    poly = clip_edge(poly, lambda p: p[0] >= LON_MIN,
                     lambda a,b: lerp(a,b,(LON_MIN-a[0])/(b[0]-a[0])))
    if not poly: return poly
    poly = clip_edge(poly, lambda p: p[0] <= LON_MAX,
                     lambda a,b: lerp(a,b,(LON_MAX-a[0])/(b[0]-a[0])))
    if not poly: return poly
    poly = clip_edge(poly, lambda p: p[1] >= LAT_MIN,
                     lambda a,b: lerp(a,b,(LAT_MIN-a[1])/(b[1]-a[1])))
    if not poly: return poly
    poly = clip_edge(poly, lambda p: p[1] <= LAT_MAX,
                     lambda a,b: lerp(a,b,(LAT_MAX-a[1])/(b[1]-a[1])))
    return poly

# --- Douglas-Peucker simplify (in projected space) ------------------------
def dp(points, eps):
    if len(points) < 3: return points
    dmax, idx = 0.0, 0
    a, b = points[0], points[-1]
    for i in range(1, len(points)-1):
        d = seg_dist(points[i], a, b)
        if d > dmax: dmax, idx = d, i
    if dmax > eps:
        left = dp(points[:idx+1], eps)
        right = dp(points[idx:], eps)
        return left[:-1] + right
    return [a, b]

def seg_dist(p, a, b):
    (px,pz),(ax,az),(bx,bz) = p,a,b
    ex,ez = bx-ax, bz-az
    l2 = ex*ex+ez*ez
    if l2 < 1e-9: return math.hypot(px-ax, pz-az)
    t = max(0.0, min(1.0, ((px-ax)*ex+(pz-az)*ez)/l2))
    cx,cz = ax+ex*t, az+ez*t
    return math.hypot(px-cx, pz-cz)

def ring_area(poly):
    s = 0.0
    n = len(poly)
    for i in range(n):
        x1,z1 = poly[i]; x2,z2 = poly[(i+1)%n]
        s += x1*z2 - x2*z1
    return abs(s)*0.5

def rings_of(geom):
    if geom["type"] == "Polygon":
        yield geom["coordinates"][0]
    elif geom["type"] == "MultiPolygon":
        for poly in geom["coordinates"]:
            yield poly[0]

def pip_lonlat(poly, px, pz):
    """Point-in-polygon in raw lon/lat space (ray cast)."""
    c = False
    n = len(poly); j = n - 1
    for i in range(n):
        ax, az = poly[i]; bx, bz = poly[j]
        if (az > pz) != (bz > pz):
            t = (pz - az) / (bz - az)
            if px < ax + t * (bx - ax):
                c = not c
        j = i
    return c

d = json.load(open(CACHE))
out_rings = []
for f in d["features"]:
    for ring in rings_of(f["geometry"]):
        clipped = clip_bbox([(pt[0], pt[1]) for pt in ring])
        if len(clipped) < 3:
            continue
        # Drop the continent: the Eurasian mainland, clipped to the bbox, leaves
        # a France/Belgium wedge in the southeast corner. It is the only kept ring
        # that contains a point in inland Picardy.
        if pip_lonlat(clipped, *CONTINENT_PT):
            continue
        proj = [project(lon, lat) for (lon, lat) in clipped]
        proj = dp(proj, 2.5)
        if len(proj) < 3:
            continue
        if ring_area(proj) < 70.0:   # drop tiny slivers, keep real islands
            continue
        out_rings.append(proj)

out_rings.sort(key=ring_area, reverse=True)
total_pts = sum(len(r) for r in out_rings)
print(f"// rings={len(out_rings)} points={total_pts}", file=sys.stderr)

def inside(polys, px, pz):
    c = False
    for poly in polys:
        n = len(poly); j = n-1
        for i in range(n):
            ax,az = poly[i]; bx,bz = poly[j]
            if (az>pz) != (bz>pz):
                t = (pz-az)/(bz-az)
                if px < ax + t*(bx-ax): c = not c
            j = i
    return c

def centroid(poly):
    return (sum(p[0] for p in poly)/len(poly), sum(p[1] for p in poly)/len(poly))

def nearest_ring(px, pz):
    best, bd = None, 1e18
    for r in out_rings:
        for (x, z) in r:
            d = (px-x)**2 + (pz-z)**2
            if d < bd: bd, best = d, r
    return best

def dist_to_coast(px, pz):
    best = 1e18
    for poly in out_rings:
        n = len(poly)
        for i in range(n):
            best = min(best, seg_dist((px, pz), poly[i], poly[(i + 1) % n]))
    return best

def snap_to_land(px, pz):
    """Nudge a coastal city inland until it has real clearance from the water.

    Being merely *inside* the coastline is not enough. terrain.rs anchors the
    waterline exactly on the coastline polygon and ramps up to full land over
    ~7 units, so a city sitting 1 unit inside sits at ~0.2 units of height —
    nominally dry, but drowned by any further adjustment. Stockholm is the sharp
    case: its archipelago is simplified away entirely by Natural Earth 50m plus
    Douglas-Peucker, so the projected point lands right on the shoreline.

    CLEARANCE is deliberately small. These are ports — Stockholm, Venice, Naples
    and Copenhagen are *supposed* to be on the water, and demanding real inland
    margin is how you end up with Stockholm 400 km into the Swedish interior.

    The search takes the *nearest* qualifying point rather than walking toward a
    landmass centroid, which is what caused that: Stockholm's archipelago is
    simplified away by Natural Earth 50m, so the true position lands in open
    Baltic, and a centroid walk drags it most of the way to Germany before it
    finds land. Spiralling outward moves each city the minimum distance that
    makes it dry.
    """
    CLEARANCE = 3.0

    def ok(x, z):
        return inside(out_rings, x, z) and dist_to_coast(x, z) >= CLEARANCE

    if ok(px, pz):
        return (px, pz)
    for r in range(1, 60):                       # radius in world units
        best, bd = None, 1e18
        for k in range(max(8, r * 6)):           # denser rings as radius grows
            a = 2.0 * math.pi * k / max(8, r * 6)
            x, z = px + math.cos(a) * r, pz + math.sin(a) * r
            if ok(x, z):
                d = (x - px) ** 2 + (z - pz) ** 2
                if d < bd:
                    bd, best = d, (x, z)
        if best:
            return best
    return (px, pz)

def fmt_pts(pts):
    return ",\n".join(f"        ({x:.1f}, {z:.1f})" for x, z in pts)

elev = build_elevation()
with open(os.path.join(SRC, "elev.bin"), "wb") as o:
    o.write(struct.pack(f"<{len(elev)}h", *elev))
lo, hi = min(elev), max(elev)
print(f"// elevation {ELEV_W}x{ELEV_H} range {lo}..{hi} m", file=sys.stderr)

with open(os.path.join(SRC, "geo.rs"), "w") as o:
    o.write("// GENERATED from Natural Earth 50m land + terrarium DEM tiles\n")
    o.write("// (both public domain). Do not edit by hand.\n")
    o.write("// Regenerate with: python3 bake/gen_geo.py\n\n")
    o.write("/// Elevation grid dimensions and the world-XZ extents it covers.\n")
    o.write("/// `terrain.rs` builds its mesh over exactly these extents.\n")
    o.write(f"pub const ELEV_W: usize = {ELEV_W};\n")
    o.write(f"pub const ELEV_H: usize = {ELEV_H};\n")
    o.write(f"pub const HALF_W: f32 = {HALF_W};\n")
    o.write(f"pub const HALF_H: f32 = {HALF_H};\n\n")
    o.write("/// Real elevation in metres, row-major `i16` little-endian, `ELEV_W * ELEV_H`.\n")
    o.write("/// Rows run north (-Z) to south (+Z), columns west (-X) to east (+X).\n")
    o.write("pub static ELEV: &[u8] = include_bytes!(\"elev.bin\");\n\n")
    o.write("/// Real European city positions in world XZ, indexed by ProvinceId.\n")
    o.write("#[rustfmt::skip]\n")
    o.write(f"pub const POS: [(f32, f32); {len(CITIES)}] = [\n")
    for name, lon, lat in CITIES:
        x, z = snap_to_land(*project(lon, lat))
        o.write(f"    ({x:.1f}, {z:.1f}), // {name}\n")
    o.write("];\n\n")
    o.write("/// Real European coastline loops in world XZ (Natural Earth, clipped).\n")
    o.write("#[rustfmt::skip]\n")
    o.write("pub const RAW_GEO: &[&[(f32, f32)]] = &[\n")
    for r in out_rings:
        o.write("    &[\n")
        o.write(fmt_pts(r))
        o.write(",\n    ],\n")
    o.write("];\n")

# City-on-land sanity check after snapping.
bad = [name for name, lon, lat in CITIES
       if not inside(out_rings, *snap_to_land(*project(lon, lat)))]
print(f"// cities off-land after snap: {bad}", file=sys.stderr)
