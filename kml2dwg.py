#!/usr/bin/env python3
"""
kml2dwg.py  -  KML/KMZ  ->  DXF + DWG (AutoCAD) + PDF  "AS PLAN DRAWING" (FTTH)

Fitur:
  * Baca KML/KMZ (folder: FEEDER, Distribusi, ODC, ODP, T, TE, TL, CLOSURE)
  * Basemap OpenStreetMap otomatis: jalan + nama jalan, rumah/bangunan, rel, sungai
  * Simbol sesuai legenda (T6, T7, T9, TL, TE, ODC, ODP, closure, RBS)
  * Layout A3 + title block (legend, cable type, tanda tangan) : ALL, FEEDER, 1, 2, ...
  * Output: .dxf (model + layout paper space), .dwg (butuh ODA File Converter), .pdf multi halaman

Pakai:
  python kml2dwg.py "file.kml" -o hasil
  python kml2dwg.py "file.kml" -o hasil --osm-file osm_cache.json   # tanpa internet
"""
import argparse, datetime, html, json, math, os, re, shutil, subprocess, sys
import tempfile, urllib.parse, urllib.request, zipfile
import xml.etree.ElementTree as ET

# ----------------------------------------------------------------- konfigurasi
META = {
    "project": "FTTH STASIUN KARANGANTU",
    "drawing_type": "AS PLAN DRAWING",
    "contractor": "PT.SARANA MANDIRI USAHA UNGGUL",
    "owner": "PT.INTEGRASI JARINGAN EKOSISTEM",
    "created": ("ANI", "Survey & Design"),
    "checked": ("", "Project Manager"),
    "qc": ("", "Quality Control"),
    "spv": ("", "SPV Of Planning"),
    "ver1": ("T.Handy Nugroho", "Head Of Planning\n& Roll Out Access"),
    "ver2": ("Syevan Noer", "VP Of Project"),
    "scale": "N.T.S",
}
PAPER_W, PAPER_H = 420.0, 297.0          # A3 mm
FRAME = 10.0
PANEL_W = 75.0
MAP_X0, MAP_Y0 = FRAME, FRAME
MAP_X1, MAP_Y1 = PAPER_W - FRAME - PANEL_W, PAPER_H - FRAME
MAP_W, MAP_H = MAP_X1 - MAP_X0, MAP_Y1 - MAP_Y0
NICE_SCALES = [500, 750, 1000, 1500, 2000, 2500, 3000, 4000, 5000, 6000, 7500, 10000, 15000, 20000]

BLACK, WHITE = (0, 0, 0), (255, 255, 255)
GREY, RED = (150, 150, 150), (210, 20, 20)
YELLOW, ORANGE = (255, 215, 0), (255, 127, 0)
BLUE, MAGENTA = (0, 0, 255), (255, 0, 255)
GREEN = (0, 200, 0)
ODP_COL = {"8": BLUE, "16": (255, 110, 30), "24": MAGENTA}

# nama layer -> (warna rgb, z-order)
LAYERS = {
    "BASE_WATER": ((160, 200, 240), 1), "BASE_BUILDING": ((120, 120, 120), 2),
    "BASE_ROAD_MINOR": ((90, 90, 90), 3), "BASE_ROAD_MAJOR": ((30, 30, 30), 4),
    "BASE_RAIL": ((60, 60, 60), 4), "BASE_ROAD_NAME": ((0, 0, 0), 5),
    "DIST_CABLE": (MAGENTA, 6), "FEEDER_CABLE": (ORANGE, 7), "KML_OTHER": ((0, 120, 0), 7),
    "POLE_TE": (GREY, 8), "POLE_TL": ((230, 80, 20), 8), "POLE_T6": (YELLOW, 8),
    "POLE_T7": (BLACK, 8), "POLE_T9": (BLUE, 8), "CLOSURE": (BLACK, 8), "RBS": (BLACK, 8),
    "ODP": (BLUE, 9), "ODC": (RED, 9),
    "LABEL_POLE": (BLACK, 10), "LABEL_ODP": ((0, 0, 140), 10), "LABEL_ODC": ((150, 0, 0), 10),
    "LABEL_CABLE": ((160, 70, 0), 10),
    "TITLEBLOCK": (BLACK, 11), "VIEWPORT": (BLACK, 0),
}
FEEDER_FROZEN = ["ODC", "ODP", "DIST_CABLE", "LABEL_ODC", "LABEL_ODP"]


def zof(layer):
    return LAYERS.get(layer, (BLACK, 8))[1]


# ------------------------------------------------------------------ KML parser
def local(t):
    return t.rsplit("}", 1)[-1]


def child(el, name):
    for c in el:
        if local(c.tag) == name:
            return c
    return None


def text_of(el, name):
    c = child(el, name)
    return (c.text or "").strip() if c is not None else ""


def parse_coords(txt):
    pts = []
    for tok in (txt or "").split():
        p = tok.split(",")
        if len(p) >= 2:
            try:
                pts.append((float(p[0]), float(p[1])))
            except ValueError:
                pass
    return pts


def load_kml(path):
    if path.lower().endswith(".kmz"):
        with zipfile.ZipFile(path) as z:
            n = [x for x in z.namelist() if x.lower().endswith(".kml")][0]
            root = ET.fromstring(z.read(n))
    else:
        root = ET.parse(path).getroot()
    out = []

    def pm(el, path_):
        name = text_of(el, "name")
        desc = html.unescape(text_of(el, "description"))
        desc = re.sub(r"<[^>]+>", " ", desc).replace("\xa0", " ")
        desc = re.sub(r"\s+", " ", desc).strip()
        for g in el.iter():
            t = local(g.tag)
            if t in ("Point", "LineString", "LinearRing"):
                c = child(g, "coordinates")
                pts = parse_coords(c.text if c is not None else "")
                if pts:
                    kind = {"Point": "point", "LineString": "line", "LinearRing": "poly"}[t]
                    out.append(dict(path=path_, name=name, desc=desc, kind=kind, coords=pts))

    def walk(el, path_):
        for c in el:
            t = local(c.tag)
            if t in ("Document", "Folder"):
                nm = text_of(c, "name")
                walk(c, path_ + [nm] if t == "Folder" else path_)
            elif t == "Placemark":
                pm(c, path_)

    walk(root, [])
    return out


def classify(items):
    D = dict(odc=[], odp=[], poles=[], feeder=[], dist=[], other=[])
    for it in items:
        last = it["path"][-1].upper().strip() if it["path"] else ""
        name, up = it["name"].strip(), it["name"].upper().strip()
        c = it["coords"]
        if it["kind"] == "line":
            if "FEEDER" in last:
                m = re.search(r"PANJANG\s*:?\s*([\d.,]+)\s*M", it["desc"].upper())
                D["feeder"].append(dict(name=name, length=m.group(1) if m else "", desc=it["desc"], pts=c))
            else:
                D["dist"].append(dict(pts=c))
            continue
        if it["kind"] == "poly":
            D["other"].append(dict(pts=c))
            continue
        lon, lat = c[0]
        num = re.search(r"(\d+)", name)
        num = int(num.group(1)) if num else None
        if "ODC" in last or (up.startswith("ODC") and "ODP" not in up):
            D["odc"].append(dict(name=name, num=num, ll=(lon, lat)))
        elif "ODP" in last or up.startswith("ODP"):
            D["odp"].append(dict(name=name, num=num, ll=(lon, lat)))
        else:
            kind = None
            if last.endswith("/TL") or last == "TL":
                kind = "TL"
            elif last.endswith("/TE") or last == "TE":
                kind = "RBS" if up == "RBS" else "TE"
            elif "CLOSURE" in last:
                kind = "CL"
            elif last.endswith("/T") or last == "T":
                if "CLOSURE" in up:
                    kind = "CL"
                elif "9" in up:
                    kind = "T9"
                elif "7M" in up or "7 M" in up or "7METER" in up:
                    kind = "T7"
                else:
                    kind = "T6"
            else:
                kind = "RBS" if up == "RBS" else "T6"
            D["poles"].append(dict(kind=kind, name=name, ll=(lon, lat)))
    return D


# ------------------------------------------------------------------ proyeksi
class Proj:
    def __init__(self, lon0, lat0):
        zone = int((lon0 + 180) // 6) + 1
        try:
            from pyproj import Transformer
            epsg = (32700 if lat0 < 0 else 32600) + zone
            t = Transformer.from_crs(4326, epsg, always_xy=True)
            self.f = lambda lon, lat: t.transform(lon, lat)
            self.name = "EPSG:%d (UTM %d%s)" % (epsg, zone, "S" if lat0 < 0 else "N")
        except Exception:
            kx, ky = 111320 * math.cos(math.radians(lat0)), 110574.0
            self.f = lambda lon, lat: ((lon - lon0) * kx, (lat - lat0) * ky)
            self.name = "lokal (meter dari titik tengah)"


def project_data(D, P):
    f = P.f
    for k in ("odc", "odp", "poles"):
        for e in D[k]:
            e["xy"] = f(*e["ll"])
    for k in ("feeder", "dist", "other"):
        for e in D[k]:
            e["xy"] = [f(*p) for p in e["pts"]]


def bbox_of(points):
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


# ------------------------------------------------------------------ OSM
OVERPASS = ["https://overpass-api.de/api/interpreter",
            "https://overpass.kumi.systems/api/interpreter",
            "https://overpass.private.coffee/api/interpreter"]
MAJOR = {"motorway", "trunk", "primary", "secondary", "tertiary", "motorway_link",
         "trunk_link", "primary_link", "secondary_link", "tertiary_link"}
FOOT = {"footway", "path", "pedestrian", "cycleway", "steps", "corridor"}


def fetch_osm(bbox_ll, cache_path):
    w, s, e, n = bbox_ll
    b = "%f,%f,%f,%f" % (s, w, n, e)
    q = ('[out:json][timeout:180];(way["highway"](%s);way["building"](%s);way["railway"](%s);'
         'way["waterway"](%s);way["natural"="water"](%s););out geom tags;') % ((b,) * 5)
    last = None
    for url in OVERPASS:
        try:
            req = urllib.request.Request(url, data=urllib.parse.urlencode({"data": q}).encode(),
                                         headers={"User-Agent": "kml2dwg/1.0"})
            with urllib.request.urlopen(req, timeout=240) as r:
                data = json.loads(r.read().decode("utf-8"))
            with open(cache_path, "w", encoding="utf-8") as fh:
                json.dump(data, fh)
            return data
        except Exception as ex:  # noqa
            last = ex
            print("  Overpass gagal (%s): %s" % (url, ex))
    raise RuntimeError("Semua server Overpass gagal: %s" % last)


def parse_osm(data, P):
    O = dict(roads=[], buildings=[], rails=[], water_l=[], water_p=[])
    for el in data.get("elements", []):
        if el.get("type") != "way" or "geometry" not in el:
            continue
        tags = el.get("tags", {})
        pts = [P.f(g["lon"], g["lat"]) for g in el["geometry"]]
        if len(pts) < 2:
            continue
        closed = len(pts) > 3 and pts[0] == pts[-1]
        if "building" in tags and closed:
            O["buildings"].append(pts)
        elif "highway" in tags:
            hw = tags["highway"]
            if hw in ("corridor", "steps"):
                continue
            cls = "major" if hw in MAJOR else ("foot" if hw in FOOT else "minor")
            O["roads"].append(dict(name=tags.get("name", ""), cls=cls, pts=pts))
        elif "railway" in tags:
            O["rails"].append(pts)
        elif tags.get("natural") == "water" and closed:
            O["water_p"].append(pts)
        elif "waterway" in tags:
            O["water_l"].append(pts)
    return O


def dist(a, b):
    return math.hypot(b[0] - a[0], b[1] - a[1])


def road_labels(roads, h):
    """Posisi label nama jalan: [(x, y, angle, text)]"""
    out = []
    for r in roads:
        nm = r["name"]
        if not nm:
            continue
        w = 0.62 * h * len(nm)
        pts, since = r["pts"], 1e9
        for i in range(len(pts) - 1):
            a, b = pts[i], pts[i + 1]
            L = dist(a, b)
            since += L
            if L >= w * 1.25 and since >= w * 4:
                ang = math.degrees(math.atan2(b[1] - a[1], b[0] - a[0]))
                if ang > 90:
                    ang -= 180
                elif ang < -90:
                    ang += 180
                out.append(((a[0] + b[0]) / 2, (a[1] + b[1]) / 2, ang, nm))
                since = 0
    return out


# ------------------------------------------------------------------ canvas DXF
ALIGN_NAMES = {"mc": "MIDDLE_CENTER", "ml": "MIDDLE_LEFT", "mr": "MIDDLE_RIGHT", "tc": "TOP_CENTER",
               "bc": "BOTTOM_CENTER", "bl": "BOTTOM_LEFT", "tl": "TOP_LEFT", "br": "BOTTOM_RIGHT"}
VALID_LW = [0, 5, 9, 13, 15, 18, 20, 25, 30, 35, 40, 50, 53, 60, 70, 80, 90, 100, 106, 120, 140, 158, 200, 211]


class DxfCanvas:
    def __init__(self, layout, ltscale=1.0):
        from ezdxf.enums import TextEntityAlignment
        from ezdxf.colors import rgb2int
        self.l, self.TEA, self.rgb2int, self.lts = layout, TextEntityAlignment, rgb2int, ltscale

    def _a(self, layer, color, lw=None, dash=False):
        a = {"layer": layer}
        if color is not None:
            if tuple(color) == (0, 0, 0):
                a["color"] = 7
            else:
                a["true_color"] = self.rgb2int(tuple(color))
        if lw:
            a["lineweight"] = min(VALID_LW, key=lambda v: abs(v - lw * 100))
        if dash:
            a["linetype"] = "DASHED"
            a["ltscale"] = self.lts
        return a

    def line(self, pts, layer, color, lw=0.25, closed=False, dash=False):
        if len(pts) >= 2:
            self.l.add_lwpolyline([(p[0], p[1]) for p in pts], close=closed,
                                  dxfattribs=self._a(layer, color, lw, dash))

    def fill(self, pts, layer, color):
        h = self.l.add_hatch(dxfattribs=self._a(layer, color))
        h.paths.add_polyline_path([(p[0], p[1]) for p in pts], is_closed=True)

    def circle(self, c, r, layer, color, fill=None, lw=0.25):
        if fill is not None:
            h = self.l.add_hatch(dxfattribs=self._a(layer, fill))
            h.paths.add_edge_path().add_arc(c, r, 0, 360)
        self.l.add_circle(c, r, dxfattribs=self._a(layer, color, lw))

    def text(self, xy, s, h, layer, color=BLACK, rot=0, anchor="mc"):
        for i, ln in enumerate(s.split("\n")):
            t = self.l.add_text(ln, dxfattribs=dict(self._a(layer, color), height=h, rotation=rot))
            yy = xy[1] - i * h * 1.3
            t.set_placement((xy[0], yy), align=getattr(self.TEA, ALIGN_NAMES[anchor]))

    def lines_many(self, lst, layer, color, lw, dash=False):
        for p in lst:
            self.line(p, layer, color, lw, dash=dash)

    def polys_many(self, lst, layer, fill, edge, lw):
        for p in lst:
            if fill is not None:
                self.fill(p[:-1] if p[0] == p[-1] else p, layer, fill)
            self.line(p[:-1] if p[0] == p[-1] else p, layer, edge, lw, closed=True)


# ------------------------------------------------------------------ canvas Matplotlib
class MplCanvas:
    HA = {"m": "center", "l": "left", "r": "right"}

    def __init__(self, ax, ppu):
        self.ax, self.ppu = ax, ppu          # ppu = point per unit canvas
        self.MM = 72 / 25.4

    @staticmethod
    def c(col):
        return tuple(v / 255 for v in col)

    def line(self, pts, layer, color, lw=0.25, closed=False, dash=False):
        if len(pts) < 2:
            return
        xs = [p[0] for p in pts] + ([pts[0][0]] if closed else [])
        ys = [p[1] for p in pts] + ([pts[0][1]] if closed else [])
        self.ax.plot(xs, ys, color=self.c(color), lw=lw * self.MM, ls=(0, (5, 3)) if dash else "-",
                     solid_capstyle="round", zorder=zof(layer), clip_on=True)

    def fill(self, pts, layer, color):
        from matplotlib.patches import Polygon
        self.ax.add_patch(Polygon(pts, closed=True, fc=self.c(color), ec="none", zorder=zof(layer) - 0.01))

    def circle(self, c, r, layer, color, fill=None, lw=0.25):
        from matplotlib.patches import Circle
        self.ax.add_patch(Circle(c, r, fc=self.c(fill) if fill is not None else "none",
                                 ec=self.c(color), lw=lw * self.MM, zorder=zof(layer)))

    def text(self, xy, s, h, layer, color=BLACK, rot=0, anchor="mc"):
        va = {"m": "center", "t": "top", "b": "bottom"}[anchor[0]]
        ha = self.HA.get(anchor[1], "center")
        self.ax.text(xy[0], xy[1], s, fontsize=h * self.ppu * 1.35, color=self.c(color), rotation=rot,
                     rotation_mode="anchor", ha=ha, va=va, zorder=zof(layer) + 0.5, clip_on=True,
                     linespacing=1.0)

    def lines_many(self, lst, layer, color, lw, dash=False):
        from matplotlib.collections import LineCollection
        self.ax.add_collection(LineCollection(lst, colors=[self.c(color)], linewidths=lw * self.MM,
                                              linestyles=[(0, (5, 3))] if dash else "solid",
                                              zorder=zof(layer), capstyle="round"))

    def polys_many(self, lst, layer, fill, edge, lw):
        from matplotlib.collections import PolyCollection
        self.ax.add_collection(PolyCollection(lst, facecolors=[self.c(fill)] if fill is not None else "none",
                                              edgecolors=[self.c(edge)], linewidths=lw * self.MM,
                                              zorder=zof(layer)))


# ------------------------------------------------------------------ simbol
def tri(x, y, s, k=1.0):
    s = s * k
    return [(x, y + 0.577 * s), (x - 0.5 * s, y - 0.289 * s), (x + 0.5 * s, y - 0.289 * s)]


def sym_odc(cv, x, y, s, new=True, layer="ODC"):
    col = RED if new else GREY
    cv.fill(tri(x, y, s), layer, WHITE)
    cv.line(tri(x, y, s), layer, col, 0.4, closed=True)
    cv.line(tri(x, y, s, 0.55), layer, col, 0.3, closed=True)


def sym_odp(cv, x, y, s, col, layer="ODP"):
    cv.fill(tri(x, y, s), layer, WHITE)
    cv.line(tri(x, y, s), layer, col, 0.4, closed=True)
    cv.fill(tri(x, y, s, 0.5), layer, col)


def sym_pole(cv, x, y, s, kind, label=None, layer=None):
    fills = {"T6": YELLOW, "T7": BLACK, "T9": BLUE, "TL": (230, 80, 20), "TE": (200, 200, 200)}
    txt = {"T6": BLACK, "T7": WHITE, "T9": WHITE, "TL": WHITE, "TE": BLACK}
    layer = layer or "POLE_" + kind
    cv.circle((x, y), 0.5 * s, layer, BLACK, fill=fills[kind], lw=0.25)
    cv.text((x, y), label or kind, 0.42 * s, "LABEL_POLE", txt[kind], 0, "mc")


def sym_closure(cv, x, y, s, new=True, layer="CLOSURE"):
    col = BLACK
    L = [(x - 0.55 * s, y + 0.35 * s), (x - 0.55 * s, y - 0.35 * s), (x, y)]
    R = [(x + 0.55 * s, y + 0.35 * s), (x + 0.55 * s, y - 0.35 * s), (x, y)]
    cv.fill(L, layer, col)
    cv.fill(R, layer, col)


def sym_rect(cv, x, y, s, label, col, layer="RBS", fill=WHITE, tcol=None):
    r = [(x - 0.5 * s, y - 0.4 * s), (x + 0.5 * s, y - 0.4 * s), (x + 0.5 * s, y + 0.4 * s), (x - 0.5 * s, y + 0.4 * s)]
    cv.fill(r, layer, fill)
    cv.line(r, layer, col, 0.3, closed=True)
    cv.text((x, y), label, 0.3 * s, layer, tcol or col, 0, "mc")


def sym_coil(cv, x, y, s, col, layer="TITLEBLOCK"):
    for dx in (-0.28, 0.28):
        cv.circle((x + dx * s, y), 0.28 * s, layer, col, lw=0.3)


# ------------------------------------------------------------------ gambar basemap & fitur
def draw_basemap(cv, O, sym, th, rail_dash=True):
    """sym: ukuran simbol (unit kanvas); th: tinggi teks nama jalan"""
    if O is None:
        return
    cv.polys_many(O["water_p"], "BASE_WATER", (200, 225, 250), (130, 170, 210), 0.1)
    cv.lines_many(O["water_l"], "BASE_WATER", (120, 170, 230), 0.3)
    cv.polys_many(O["buildings"], "BASE_BUILDING", (225, 225, 225), (110, 110, 110), 0.08)
    for cls, layer, lw in (("foot", "BASE_ROAD_MINOR", 0.08), ("minor", "BASE_ROAD_MINOR", 0.15),
                           ("major", "BASE_ROAD_MAJOR", 0.3)):
        cv.lines_many([r["pts"] for r in O["roads"] if r["cls"] == cls], layer, LAYERS[layer][0], lw)
    cv.lines_many(O["rails"], "BASE_RAIL", (50, 50, 50), 0.35, dash=rail_dash)
    for x, y, ang, nm in road_labels([r for r in O["roads"] if r["cls"] != "foot"], th):
        cv.text((x, y), nm, th, "BASE_ROAD_NAME", (20, 20, 20), ang, "mc")


def draw_features(cv, D, s, feeder_only=False, odp_type="8"):
    th = 0.4 * s
    if not feeder_only:
        cv.lines_many([e["xy"] for e in D["dist"]], "DIST_CABLE", MAGENTA, 0.25)
    cv.lines_many([e["xy"] for e in D["feeder"]], "FEEDER_CABLE", ORANGE, 0.5)
    cv.lines_many([e["xy"] for e in D["other"]], "KML_OTHER", (0, 120, 0), 0.25)
    for e in D["feeder"]:
        if e["length"]:
            mid = e["xy"][len(e["xy"]) // 2]
            cv.text((mid[0] + 0.6 * s, mid[1] + 0.6 * s), "%s  %s M" % (e["name"], e["length"]), th,
                    "LABEL_CABLE", (160, 70, 0), 0, "ml")
    for p in D["poles"]:
        x, y = p["xy"]
        k = p["kind"]
        if k in ("T6", "T7", "T9", "TL", "TE"):
            sym_pole(cv, x, y, s, k)
        elif k == "CL":
            sym_closure(cv, x, y, s)
        else:
            sym_rect(cv, x, y, 1.4 * s, "RBS", BLACK)
    if feeder_only:
        return
    col = ODP_COL.get(odp_type, BLUE)
    for p in D["odp"]:
        x, y = p["xy"]
        sym_odp(cv, x, y, 0.85 * s, col)
        cv.text((x, y - 0.75 * s), p["name"].replace("ODP ", ""), 0.28 * s, "LABEL_ODP", (0, 0, 140), 0, "tc")
    for p in D["odc"]:
        x, y = p["xy"]
        sym_odc(cv, x, y, 1.15 * s)
        cv.text((x + 0.8 * s, y + 0.1 * s), p["name"], 0.4 * s, "LABEL_ODC", (150, 0, 0), 0, "ml")


# ------------------------------------------------------------------ title block
def box(cv, x0, y0, x1, y1, lw=0.35):
    cv.line([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], "TITLEBLOCK", BLACK, lw, closed=True)


def hline(cv, x0, x1, y, lw=0.35):
    cv.line([(x0, y), (x1, y)], "TITLEBLOCK", BLACK, lw)


def vline(cv, x, y0, y1, lw=0.35):
    cv.line([(x, y0), (x, y1)], "TITLEBLOCK", BLACK, lw)


def T(cv, x, y, s, h, anchor="mc", col=BLACK, rot=0):
    cv.text((x, y), s, h, "TITLEBLOCK", col, rot, anchor)


def title_block(cv, page, total, odp_type="8", date=None):
    m = META
    x0, x1 = PAPER_W - FRAME - PANEL_W, PAPER_W - FRAME
    xm = (x0 + x1) / 2
    cx = lambda a, b: (a + b) / 2
    # frame
    box(cv, FRAME, FRAME, PAPER_W - FRAME, PAPER_H - FRAME, 0.6)
    box(cv, 5, 5, PAPER_W - 5, PAPER_H - 5, 0.25)
    vline(cv, x0, FRAME, PAPER_H - FRAME, 0.6)
    y = PAPER_H - FRAME
    # header
    y1 = y - 12
    hline(cv, x0, x1, y1)
    T(cv, xm, cx(y, y1), "AS PLAN DRAWING", 5.0)
    # legend
    rows, rh = 11, 6.2
    yl = y1 - rows * rh
    hline(cv, x0, x1, yl)
    vline(cv, xm, yl, y1)
    left = [("NEW POLE 6", lambda x, yy: sym_pole(cv, x, yy, 4.2, "T6", layer="TITLEBLOCK")),
            ("NEW POLE 7", lambda x, yy: sym_pole(cv, x, yy, 4.2, "T7", layer="TITLEBLOCK")),
            ("NEW POLE 9", lambda x, yy: sym_pole(cv, x, yy, 4.2, "T9", layer="TITLEBLOCK")),
            ("NEW POLE 6", lambda x, yy: sym_pole(cv, x, yy, 4.2, "TL", layer="TITLEBLOCK")),
            ("EXISTING POLE", lambda x, yy: sym_pole(cv, x, yy, 4.2, "TE", layer="TITLEBLOCK")),
            ("NEW ODC", lambda x, yy: sym_odc(cv, x, yy, 4.8, True, "TITLEBLOCK")),
            ("EXISTING ODC", lambda x, yy: sym_odc(cv, x, yy, 4.8, False, "TITLEBLOCK")),
            ("NEW ODP 8 HP", lambda x, yy: sym_odp(cv, x, yy, 4.8, BLUE, "TITLEBLOCK")),
            ("NEW ODP 16 HP", lambda x, yy: sym_odp(cv, x, yy, 4.8, ODP_COL["16"], "TITLEBLOCK")),
            ("NEW ODP 24 HP", lambda x, yy: sym_odp(cv, x, yy, 4.8, MAGENTA, "TITLEBLOCK")),
            ("EXISTING ODP", lambda x, yy: sym_odp(cv, x, yy, 4.8, BLACK, "TITLEBLOCK"))]

    def hh(x, yy, col):
        sym_rect(cv, x, yy, 5.0, "HH", col, "TITLEBLOCK", WHITE)

    def hatch_box(x, yy):
        r = [(x - 3.5, yy - 1.6), (x + 3.5, yy - 1.6), (x + 3.5, yy + 1.6), (x - 3.5, yy + 1.6)]
        cv.line(r, "TITLEBLOCK", RED, 0.3, closed=True)
        for k in range(-5, 5):
            cv.line([(x + k * 0.9 - 1.6, yy - 1.6), (x + k * 0.9 + 1.6, yy + 1.6)], "TITLEBLOCK", RED, 0.15)

    right = [("EXISTING CLOSURE", lambda x, yy: sym_closure(cv, x, yy, 4.0, True, "TITLEBLOCK")),
             ("NEW CLOSURE", None),
             ("NEW COIL", lambda x, yy: sym_coil(cv, x, yy, 7, RED)),
             ("EXISTING COIL", lambda x, yy: sym_coil(cv, x, yy, 7, BLACK)),
             ("OPTIC DISTRIBUTION\nTERMINAL", lambda x, yy: sym_rect(cv, x, yy, 6, "RAK\nOTB", BLUE, "TITLEBLOCK")),
             ("OTHER HANDHOLE", lambda x, yy: hh(x, yy, (255, 110, 30))),
             ("NEW HANDHOLE", lambda x, yy: hh(x, yy, BLUE)),
             ("EXISTING HANDHOLE", lambda x, yy: sym_rect(cv, x, yy, 4.6, "", BLACK, "TITLEBLOCK")),
             ("BORING ALUR", hatch_box),
             ("CROSSING BRIDGE", lambda x, yy: cv.fill([(x - 3.5, yy - 1.3), (x + 3.5, yy - 1.3), (x + 3.5, yy + 1.3), (x - 3.5, yy + 1.3)], "TITLEBLOCK", (130, 130, 130))),
             ("BORING CROSSING", lambda x, yy: (cv.line([(x - 3.8, yy - 1.6), (x + 3.8, yy - 1.6), (x + 3.8, yy + 1.6), (x - 3.8, yy + 1.6)], "TITLEBLOCK", RED, 0.25, closed=True),
                                                 cv.circle((x, yy), 2.4, "TITLEBLOCK", RED, lw=0.25)))]
    for i, (lab, fn) in enumerate(left):
        yy = y1 - (i + 0.5) * rh
        if i:
            hline(cv, x0, xm, y1 - i * rh, 0.2)
        if fn:
            fn(x0 + 6.5, yy)
        T(cv, x0 + 13, yy, lab, 1.6, "ml")
    for i, (lab, fn) in enumerate(right):
        yy = y1 - (i + 0.5) * rh
        if i:
            hline(cv, xm, x1, y1 - i * rh, 0.2)
        if fn:
            fn(xm + 6.5, yy)
        T(cv, xm + 12.5 if fn else cx(xm, x1), yy, lab, 1.5, "ml" if fn else "mc")
    y = yl
    # cable type
    y1 = y - 6
    T(cv, xm, cx(y, y1), "CABLE TYPE", 3.6)
    hline(cv, x0, x1, y1)
    cab_l = [("DROPWIRE 1 CORE", BLACK, 0.2, False), ("AERIAL STRAND", (200, 200, 0), 0.2, False),
             ("UNDERGROUND STRAND", GREEN, 0.2, True), ("UG DROP DUCT", (80, 80, 255), 0.2, True),
             ("FEEDER CABLE ROUTE", BLUE, 0.3, False), ("RAIL WAY", BLACK, 1.2, False),
             ("SEWER LINE", MAGENTA, 0.2, True)]
    cab_r = [("FIBER 12/2T", RED), ("FIBER 24/2T", ORANGE), ("FIBER 48/4T", BLUE), ("FIBER 72/6T", MAGENTA),
             ("FIBER 96/8T", RED), ("FIBER 144/12T", GREEN), ("FIBER 288/24T", (90, 90, 90))]
    ch = 4.2
    xc = x0 + PANEL_W * 0.5
    vline(cv, xc, y1 - 7 * ch, y1)
    for i in range(7):
        yy = y1 - (i + 0.5) * ch
        if i:
            hline(cv, x0, x1, y1 - i * ch, 0.15)
        n, col, lw, dash = cab_l[i]
        T(cv, x0 + 1, yy, n, 1.1, "ml")
        cv.line([(x0 + 20, yy), (xc - 2, yy)], "TITLEBLOCK", col, lw, dash=dash)
        n2, c2 = cab_r[i]
        T(cv, xc + 1.5, yy, n2, 1.7, "ml")
        cv.line([(xc + 21, yy), (x1 - 2, yy)], "TITLEBLOCK", c2, 0.9)
    y = y1 - 7 * ch
    hline(cv, x0, x1, y)
    # logo
    y1 = y - 24
    star = [(0, 8), (2, 2.2), (8, 0), (2, -2.2), (0, -8), (-2, -2.2), (-8, 0), (-2, 2.2)]
    cv.fill([(xm + a * 0.9, cx(y, y1) + 3 + b * 0.9) for a, b in star], "TITLEBLOCK", (20, 120, 220))
    T(cv, xm, y1 + 4.5, "S T A R L I T E", 2.6)
    hline(cv, x0, x1, y1)
    y = y1
    # revisi
    y1 = y - 5
    xs1, xs2 = x0 + 15, x1 - 15
    T(cv, cx(x0, xs1), cx(y, y1), "DATE", 2.2)
    T(cv, cx(xs1, xs2), cx(y, y1), "REVISION", 2.2)
    T(cv, cx(xs2, x1), cx(y, y1), "SIGN", 2.2)
    hline(cv, x0, x1, y1)
    for i in range(3):
        hline(cv, x0, x1, y1 - 3.5 * (i + 1))
    vline(cv, xs1, y - 5 - 10.5, y)
    vline(cv, xs2, y - 5 - 10.5, y)
    y = y1 - 10.5
    hline(cv, x0, x1, y - 1.0)
    hline(cv, x0, x1, y)
    y -= 1.0
    # proyek
    y1 = y - 12
    T(cv, x0 + 2, y - 2.2, "Nama Proyek", 1.1, "ml")
    T(cv, x0 + 2, y - 3.7, "Project Name", 1.1, "ml")
    T(cv, xm, y1 + 4.5, META["project"], 2.4)
    hline(cv, x0, x1, y1)
    y = y1
    y1 = y - 9
    T(cv, x0 + 2, y - 2.2, "Tipe Gambar", 1.1, "ml")
    T(cv, x0 + 2, y - 3.7, "Drawing Type", 1.1, "ml")
    T(cv, xm, cx(y, y1) - 0.5, META["drawing_type"], 2.6)
    hline(cv, x0, x1, y1)
    y = y1
    for txt, hh_, ht in (("DRAWING SIGN & CHECK", 5.5, 3.2), (m["contractor"], 5.5, 2.4)):
        y1 = y - hh_
        T(cv, xm, cx(y, y1), txt, ht)
        hline(cv, x0, x1, y1)
        y = y1

    def two(ya, ha, tl, tr, p1, p2, lab1, lab2):
        yb = ya - ha
        vline(cv, xm, yb, ya)
        for xx0, xx1, tt, pp in ((x0, xm, tl, p1), (xm, x1, tr, p2)):
            c = cx(xx0, xx1)
            T(cv, c, ya - 2.2, tt[0], 1.5)
            T(cv, c, ya - 4.0, tt[1], 1.5)
            if pp[0]:
                T(cv, c, yb + 6.0, pp[0], 1.5)
            T(cv, c, yb + 4.5, "..............", 1.2) if not pp[0] else None
            T(cv, c, yb + 2.3, pp[1].split("\n")[0], 1.4)
            if "\n" in pp[1]:
                T(cv, c, yb + 0.9, pp[1].split("\n")[1], 1.4)
        hline(cv, x0, x1, yb)
        return yb

    y = two(y, 18, ("Dibuat Oleh", "Created by"), ("Diperiksa Oleh", "Checked by"), m["created"], m["checked"], 0, 0)
    y1 = y - 5.5
    T(cv, xm, cx(y, y1), m["owner"], 2.4)
    hline(cv, x0, x1, y1)
    y = y1
    y = two(y, 18, ("Disetujui Oleh", "Approved by"), ("Disetujui Oleh", "Approved by"), m["qc"], m["spv"], 0, 0)
    y = two(y, 18, ("Diketahui Oleh", "Verified by"), ("Diketahui Oleh", "Verified by"), m["ver1"], m["ver2"], 0, 0)
    # blok bawah
    ybot = FRAME
    xs = xm
    vline(cv, xs, ybot, y)
    T(cv, x0 + 2, y - 2.0, "Ref. Peta", 1.2, "ml")
    T(cv, x0 + 2, y - 3.5, "Map Ref.", 1.2, "ml")
    T(cv, x0 + 2, y - 14, "Arah", 1.2, "ml")
    T(cv, x0 + 2, y - 15.5, "Direction", 1.2, "ml")
    nx, ny = x0 + 17, ybot + 9
    cv.line([(nx, ny - 7), (nx, ny + 7)], "TITLEBLOCK", BLACK, 0.3)
    cv.line([(nx - 7, ny), (nx + 7, ny)], "TITLEBLOCK", BLACK, 0.3)
    cv.fill([(nx, ny + 7), (nx - 1.2, ny), (nx + 1.2, ny)], "TITLEBLOCK", BLACK)
    T(cv, nx, ny + 9.3, "N", 2.0)
    T(cv, nx, ny - 9.3, "S", 2.0)
    seg = (y - ybot) / 3
    T(cv, xs + 2, y - 2.0, "No. Gambar", 1.3, "ml")
    T(cv, xs + 2, y - 3.5, "Drawing No.", 1.3, "ml")
    hline(cv, xs, x1, y - seg)
    T(cv, xs + 2, y - seg - 2.0, "Tanggal", 1.3, "ml")
    T(cv, xs + 2, y - seg - 3.5, "Date", 1.3, "ml")
    d = date or datetime.date.today().strftime("%d / %m / %Y")
    T(cv, xs + 17, y - seg - 4.5, ": " + d, 2.0, "ml")
    hline(cv, xs, x1, y - 2 * seg)
    T(cv, xs + 2, y - 2 * seg - 2.0, "Skala", 1.3, "ml")
    T(cv, xs + 2, y - 2 * seg - 3.5, "Scale", 1.3, "ml")
    T(cv, xs + 17, y - 2 * seg - 4.5, ": " + META["scale"], 2.0, "ml")
    T(cv, xs + 2, ybot + seg - 2.0, "Halaman", 1.3, "ml")
    T(cv, xs + 2, ybot + seg - 3.5, "Page", 1.3, "ml")
    T(cv, cx(xs, x1) + 3, ybot + 3.5, "%d OF %d" % (page, total), 2.4)


def sheet_label(cv, text, scale):
    x = MAP_X0 + 6
    cv.text((MAP_X1 - 4, MAP_Y1 - 14), text, 18, "TITLEBLOCK", (110, 0, 0), 0, "mr")
    cv.text((MAP_X0 + 3, MAP_Y0 + 3), "SKALA 1 : %d (A3)" % scale, 2.2, "TITLEBLOCK", BLACK, 0, "bl")


# ------------------------------------------------------------------ sheet
def fit_extent(bb, margin_frac=0.07, pad=25):
    x0, y0, x1, y1 = bb
    w, h = (x1 - x0) + 2 * pad, (y1 - y0) + 2 * pad
    w *= 1 + margin_frac
    h *= 1 + margin_frac
    need = max(w / (MAP_W / 1000.0), h / (MAP_H / 1000.0))
    scale = next((s for s in NICE_SCALES if s >= need), int(math.ceil(need / 1000) * 1000))
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    vw, vh = MAP_W * scale / 1000.0, MAP_H * scale / 1000.0
    return dict(scale=scale, cx=cx, cy=cy, vw=vw, vh=vh)


def build_sheets(D, per_sheet):
    allpts = []
    for k in ("odc", "odp", "poles"):
        allpts += [e["xy"] for e in D[k]]
    for k in ("feeder", "dist"):
        for e in D[k]:
            allpts += e["xy"]
    ext_all = fit_extent(bbox_of(allpts))
    sheets = [dict(title="ALL", feeder_only=False, **ext_all),
              dict(title="FEEDER", feeder_only=True, **ext_all)]
    nums = sorted({e["num"] for e in D["odc"] if e["num"] is not None})
    for i in range(0, len(nums), per_sheet):
        grp = set(nums[i:i + per_sheet])
        pts = [e["xy"] for e in D["odc"] + D["odp"] if e["num"] in grp]
        if pts:
            sheets.append(dict(title=str(len(sheets) - 1), feeder_only=False,
                               odc_range="%d-%d" % (min(grp), max(grp)), **fit_extent(bbox_of(pts))))
    return sheets


# ------------------------------------------------------------------ DXF
def build_dxf(path, D, O, sheets, args, P):
    import ezdxf
    doc = ezdxf.new("R2018", setup=True)
    doc.header["$INSUNITS"] = 6
    doc.header["$MEASUREMENT"] = 1
    for name, (col, z) in LAYERS.items():
        attr = {"true_color": ezdxf.colors.rgb2int(col)} if col != BLACK else {"color": 7}
        if name == "VIEWPORT":
            attr["plot"] = 0
        doc.layers.add(name, dxfattribs=attr)
    ref = math.exp(sum(math.log(s["scale"]) for s in sheets) / len(sheets))
    sym = 3.2 * ref / 1000.0
    th = 1.5 * ref / 1000.0
    print("  DXF: simbol %.1f m, teks jalan %.1f m (skala rujukan 1:%d)" % (sym, th, ref))
    msp = doc.modelspace()
    cv = DxfCanvas(msp, ltscale=sym * 0.5)
    draw_basemap(cv, O, sym, th)
    draw_features(cv, D, sym, False, args.odp_type)
    if "Layout1" in doc.layouts.names():
        doc.layouts.rename("Layout1", "TEMP")
    first = True
    for i, sh in enumerate(sheets):
        nm = "%d-%s" % (i + 1, sh["title"])
        lay = doc.layouts.new(nm)
        lay.page_setup(size=(PAPER_W, PAPER_H), margins=(0, 0, 0, 0), units="mm", scale=(1, 1))
        vp = lay.add_viewport(center=((MAP_X0 + MAP_X1) / 2, (MAP_Y0 + MAP_Y1) / 2), size=(MAP_W, MAP_H),
                              view_center_point=(sh["cx"], sh["cy"]), view_height=sh["vh"])
        vp.dxf.layer = "VIEWPORT"
        if sh["feeder_only"]:
            try:
                vp.frozen_layers = FEEDER_FROZEN
            except Exception as ex:  # noqa
                print("  (frozen layer gagal: %s)" % ex)
        pc = DxfCanvas(lay)
        title_block(pc, i + 1, len(sheets), args.odp_type, args.date)
        sheet_label(pc, sh["title"], sh["scale"])
    try:
        doc.layouts.delete("TEMP")
    except Exception:
        pass
    doc.saveas(path)
    return path


def to_dwg(dxf, dwg, ver="ACAD2018"):
    cands = [shutil.which("ODAFileConverter"), shutil.which("ODAFileConverter.exe")]
    for base in (r"C:\Program Files\ODA", r"C:\Program Files (x86)\ODA", "/opt/ODAFileConverter", "/usr/bin"):
        if os.path.isdir(base):
            for r, _, fs in os.walk(base):
                for f in fs:
                    if f.lower().startswith("odafileconverter"):
                        cands.append(os.path.join(r, f))
    exe = next((c for c in cands if c and os.path.isfile(c)), None)
    if exe:
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            shutil.copy(dxf, a)
            subprocess.run([exe, a, b, ver, "DWG", "0", "1"], check=False)
            for f in os.listdir(b):
                if f.lower().endswith(".dwg"):
                    shutil.move(os.path.join(b, f), dwg)
                    return True
    d2 = shutil.which("dxf2dwg")
    if d2:
        subprocess.run([d2, "-o", dwg, dxf], check=False)
        return os.path.isfile(dwg)
    return False


# ------------------------------------------------------------------ PDF
def build_pdf(path, D, O, sheets, args):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_pdf import PdfPages
    MM = 72 / 25.4
    with PdfPages(path) as pdf:
        for i, sh in enumerate(sheets):
            fig = plt.figure(figsize=(PAPER_W / 25.4, PAPER_H / 25.4))
            axm = fig.add_axes([MAP_X0 / PAPER_W, MAP_Y0 / PAPER_H, MAP_W / PAPER_W, MAP_H / PAPER_H])
            axm.set_xlim(sh["cx"] - sh["vw"] / 2, sh["cx"] + sh["vw"] / 2)
            axm.set_ylim(sh["cy"] - sh["vh"] / 2, sh["cy"] + sh["vh"] / 2)
            axm.axis("off")
            axm.set_zorder(1)
            axp = fig.add_axes([0, 0, 1, 1])
            axp.set_xlim(0, PAPER_W)
            axp.set_ylim(0, PAPER_H)
            axp.axis("off")
            axp.patch.set_alpha(0)
            axp.set_zorder(2)
            sc = sh["scale"]
            ppu = MM * 1000.0 / sc
            wc = MplCanvas(axm, ppu)
            sym, th = 3.2 * sc / 1000.0, 1.5 * sc / 1000.0
            draw_basemap(wc, O, sym, th)
            draw_features(wc, D, sym, sh["feeder_only"], args.odp_type)
            pc = MplCanvas(axp, MM)
            title_block(pc, i + 1, len(sheets), args.odp_type, args.date)
            sheet_label(pc, sh["title"], sc)
            pdf.savefig(fig)
            plt.close(fig)
            print("  PDF hal %d/%d (%s, 1:%d)" % (i + 1, len(sheets), sh["title"], sc))


# ------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser(description="KML/KMZ -> DXF/DWG + PDF As Plan Drawing")
    ap.add_argument("kml")
    ap.add_argument("-o", "--out", default="hasil")
    ap.add_argument("--name", help="nama dasar file output")
    ap.add_argument("--osm-file", help="pakai file cache OSM (.json) alih-alih download")
    ap.add_argument("--no-osm", action="store_true", help="tanpa basemap")
    ap.add_argument("--margin", type=float, default=250, help="margin basemap (meter)")
    ap.add_argument("--odp-type", default="8", choices=["8", "16", "24"])
    ap.add_argument("--odc-per-sheet", type=int, default=9, help="jumlah ODC per halaman detail")
    ap.add_argument("--project", help="nama proyek di title block")
    ap.add_argument("--date", help='tanggal, mis. "11 / 04 / 2026" (default hari ini)')
    ap.add_argument("--dwg-version", default="ACAD2018")
    ap.add_argument("--pdf-only", action="store_true")
    args = ap.parse_args()
    if args.project:
        META["project"] = args.project
    os.makedirs(args.out, exist_ok=True)
    base = args.name or os.path.splitext(os.path.basename(args.kml))[0]

    print("[1/5] Baca KML ...")
    D = classify(load_kml(args.kml))
    print("  ODC %d | ODP %d | tiang/closure %d | feeder %d | distribusi %d" % (
        len(D["odc"]), len(D["odp"]), len(D["poles"]), len(D["feeder"]), len(D["dist"])))
    lls = [e["ll"] for k in ("odc", "odp", "poles") for e in D[k]]
    for k in ("feeder", "dist"):
        for e in D[k]:
            lls += e["pts"]
    lon0 = sum(p[0] for p in lls) / len(lls)
    lat0 = sum(p[1] for p in lls) / len(lls)
    P = Proj(lon0, lat0)
    project_data(D, P)
    print("  Proyeksi: " + P.name)

    print("[2/5] Basemap OSM ...")
    O = None
    if not args.no_osm:
        w, s, e, n = (min(p[0] for p in lls), min(p[1] for p in lls), max(p[0] for p in lls), max(p[1] for p in lls))
        dm = args.margin / 111320.0
        bb = (w - dm, s - dm, e + dm, n + dm)
        cache = os.path.join(args.out, base + "_osm.json")
        try:
            if args.osm_file:
                data = json.load(open(args.osm_file, encoding="utf-8"))
            else:
                data = fetch_osm(bb, cache)
                print("  cache OSM: " + cache)
            O = parse_osm(data, P)
            print("  jalan %d | bangunan %d | rel %d | air %d" % (
                len(O["roads"]), len(O["buildings"]), len(O["rails"]), len(O["water_l"]) + len(O["water_p"])))
        except Exception as ex:
            print("  !! Basemap gagal: %s\n  !! Lanjut tanpa basemap. Pakai --osm-file jika ada cache." % ex)
    sheets = build_sheets(D, args.odc_per_sheet)
    print("[3/5] Halaman: " + ", ".join("%s(1:%d)" % (s_["title"], s_["scale"]) for s_ in sheets))

    outs = []
    if not args.pdf_only:
        print("[4/5] DXF/DWG ...")
        dxf = os.path.join(args.out, base + ".dxf")
        build_dxf(dxf, D, O, sheets, args, P)
        outs.append(dxf)
        dwg = os.path.join(args.out, base + ".dwg")
        if to_dwg(dxf, dwg, args.dwg_version):
            outs.append(dwg)
        else:
            print("  DWG: ODA File Converter tidak ditemukan. Buka .dxf di AutoCAD lalu SAVEAS .dwg,\n"
                  "       atau pasang ODA File Converter (gratis) lalu jalankan ulang.")
    print("[5/5] PDF ...")
    pdf = os.path.join(args.out, base + ".pdf")
    build_pdf(pdf, D, O, sheets, args)
    outs.append(pdf)
    print("\nSelesai:\n  " + "\n  ".join(outs))


if __name__ == "__main__":
    main()
