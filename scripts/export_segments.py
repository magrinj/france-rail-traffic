"""Extracts the reusable dataset: the network seen as station-to-station segments.

Why this split rather than raw shapes.txt
-----------------------------------------
shapes.txt weighs 206 MB because every stretch of track is rewritten once per
trip that uses it: 144,772 occurrences for only ~9,400 distinct segments, a
factor of 15. More importantly, shapes.txt is keyed by trip_id values that are
valid for a single day. Splitting at stations yields a stable object: "the track
geometry between station A and station B", reusable for any date and any project.

What this dataset is NOT
------------------------
It is not the rail network geometry: SNCF Reseau already publishes that, in an
official and more precise version. What is published here is the ROUTING RESULT
- which tracks each train uses - which nobody publishes.

Output: data/processed/segments-reseau.geojson
"""
import json, os, sys, collections
import numpy as np
import pandas as pd
from shapely.geometry import LineString

S = "data/processed/gtfs-shaped"
OUT = "data/processed/segments-reseau.geojson"
WEB = "data/processed/web-reseau"          # binary version for the map
# Traffic classes for the site's "network" layer. A continuous gradient would
# require one colour per vertex; five classes render as five constant-colour
# PathLayers, far lighter.
CLASSES = [(1, 5), (6, 20), (21, 60), (61, 150), (151, 10**9)]
SIMPLIFY_WEB_M = 30.0   # background layer: invisible at national scale
                        # (1 px is ~700 m at the default zoom)
K = np.cos(np.radians(46.6))
# Douglas-Peucker simplification. 2 m is four times finer than the accuracy
# measured against the SNCF Reseau reference (7.7 m median offset), so nothing
# useful is lost, for half the volume.
SIMPLIFY_M = 2.0


def longueur_m(P):
    if len(P) < 2:
        return 0.0
    latm = np.radians((P[:-1, 1] + P[1:, 1]) / 2.0)
    return float(np.hypot(np.diff(P[:, 0]) * 111320.0 * np.cos(latm),
                          np.diff(P[:, 1]) * 110540.0).sum())


DOSSIER_WEB = None      # set by --web <directory>


def main():
    date = "inconnue"
    if os.path.exists("data/processed/manifest.json"):
        date = json.load(open("data/processed/manifest.json"))["target_date"]
    print(f"jour traite : {date}")
    st = pd.read_csv(f"{S}/stop_times.txt", dtype=str)
    tr = pd.read_csv(f"{S}/trips.txt", dtype=str).set_index("trip_id")
    sp = pd.read_csv(f"{S}/stops.txt", dtype=str).set_index("stop_id")
    meta = pd.read_csv("data/processed/trip_meta.csv", dtype=str).set_index("trip_id")

    st["seq"] = st.stop_sequence.astype(int)
    st = st.sort_values(["trip_id", "seq"])
    st["sdt"] = pd.to_numeric(st.shape_dist_traveled, errors="coerce")
    st["shape"] = st.trip_id.map(tr.shape_id)

    shapes = {}
    # shape_id forced to string (numeric identifiers at some operators), and
    # shape_dist_traveled recomputed when absent: the Chemins de fer de Corse feed
    # ships traces without cumulative distance.
    sh = (pd.read_csv(f"{S}/shapes.txt", dtype={"shape_id": str})
            .sort_values(["shape_id", "shape_pt_sequence"]))
    a_sdt = "shape_dist_traveled" in sh.columns
    for sid, g in sh.groupby("shape_id", sort=False):
        P = np.stack([g.shape_pt_lon.to_numpy(float), g.shape_pt_lat.to_numpy(float)], 1)
        if a_sdt and g.shape_dist_traveled.notna().all():
            D = g.shape_dist_traveled.to_numpy(float)
        else:
            latm = np.radians((P[:-1, 1] + P[1:, 1]) / 2.0)
            D = np.concatenate([[0.0], np.cumsum(np.hypot(
                np.diff(P[:, 0]) * 111320.0 * np.cos(latm), np.diff(P[:, 1]) * 110540.0))])
        shapes[sid] = (P, D)

    segs = {}                                   # (a,b) -> dict
    usage = collections.Counter()
    cats = collections.defaultdict(set)

    for tid, g in st.groupby("trip_id", sort=False):
        sid = g["shape"].iat[0]
        if sid not in shapes:
            continue
        P, D = shapes[sid]
        ids = g.stop_id.tolist(); d = g.sdt.tolist()
        cat = meta.cat.get(tid)
        for i in range(len(ids) - 1):
            a, b = ids[i], ids[i + 1]
            key = (a.split(":", 1)[-1] if a.startswith(("N:", "T:", "Np:", "Tp:")) else a,
                   b.split(":", 1)[-1] if b.startswith(("N:", "T:", "Np:", "Tp:")) else b)
            usage[key] += 1
            if cat:
                cats[key].add(cat)
            if key in segs:
                continue
            d0, d1 = d[i], d[i + 1]
            if not (np.isfinite(d0) and np.isfinite(d1)) or d1 <= d0:
                continue
            m = (D >= d0) & (D <= d1)
            pts = P[m]
            # force exact endpoints, otherwise the segment gets truncated
            p0 = np.array([[np.interp(d0, D, P[:, 0]), np.interp(d0, D, P[:, 1])]])
            p1 = np.array([[np.interp(d1, D, P[:, 0]), np.interp(d1, D, P[:, 1])]])
            pts = np.vstack([p0, pts, p1]) if len(pts) else np.vstack([p0, p1])
            if len(pts) < 2:
                continue
            if len(pts) > 2 and SIMPLIFY_M > 0:
                pts = np.array(LineString(pts).simplify(
                    SIMPLIFY_M * 1.1e-5, preserve_topology=False).coords)
            segs[key] = {"pts": np.round(pts, 6), "a": a, "b": b}

    print(f"occurrences de segments   {sum(usage.values()):,}")
    print(f"segments distincts        {len(segs):,}")

    feats = []
    for key, s in segs.items():
        na, nb = sp.stop_name.get(s["a"]), sp.stop_name.get(s["b"])
        feats.append({
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": s["pts"].tolist()},
            "properties": {
                "depart_id": key[0], "depart_nom": na,
                "arrivee_id": key[1], "arrivee_nom": nb,
                "longueur_m": round(longueur_m(s["pts"])),
                "circulations": usage[key],
                "categories": ",".join(sorted(cats[key])),
            }})
    fc = {"type": "FeatureCollection",
          "name": "segments-inter-gares-reseau-ferre-francais",
          "date_circulations": date,
          "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}},
          "features": feats}
    if DOSSIER_WEB is None:
        with open(OUT, "w") as f:
            json.dump(fc, f, ensure_ascii=False)
    ecrire_binaire(feats, DOSSIER_WEB)
    n_pts = sum(len(x["geometry"]["coordinates"]) for x in feats)
    print(f"points geometriques       {n_pts:,}")
    print(f"km cumules                {sum(x['properties']['longueur_m'] for x in feats)/1000:,.0f}")
    if DOSSIER_WEB is None:
        print(f"-> {OUT}  ({os.path.getsize(OUT)/1e6:.1f} Mo)")


def ecrire_binaire(feats, dossier=None):
    """Couche reseau du site: une paire de fichiers par classe de trafic."""
    global WEB
    if dossier: WEB = dossier
    os.makedirs(WEB, exist_ok=True)
    par_classe = {i: [] for i in range(len(CLASSES))}
    for f in feats:
        c = f["properties"]["circulations"]
        for i, (lo, hi) in enumerate(CLASSES):
            if lo <= c <= hi:
                par_classe[i].append(f["geometry"]["coordinates"]); break
    meta = []
    for i, (lo, hi) in enumerate(CLASSES):
        POS, IDX = [], [0]
        for coords in par_classe[i]:
            P = np.asarray(coords, float)
            if len(P) > 2:
                P = np.asarray(LineString(P).simplify(
                    SIMPLIFY_WEB_M * 1.1e-5, preserve_topology=False).coords)
            if len(P) < 2:
                continue
            POS.append(P.astype(np.float32).ravel()); IDX.append(IDX[-1] + len(P))
        pos = np.concatenate(POS) if POS else np.zeros(0, np.float32)
        idx = np.array(IDX, np.uint32)
        pos.tofile(f"{WEB}/c{i}-pos.bin"); idx.tofile(f"{WEB}/c{i}-idx.bin")
        meta.append({"min": lo, "max": (None if hi > 10**8 else hi),
                     "segments": len(idx) - 1, "sommets": int(len(pos) // 2)})
    json.dump({"classes": meta}, open(f"{WEB}/meta.json", "w"))
    tot = sum(m["sommets"] for m in meta)
    octets = sum(os.path.getsize(f"{WEB}/{f}") for f in os.listdir(WEB))
    print(f"couche reseau: {tot:,} sommets, {octets/1e6:.1f} Mo -> {WEB}")
    for m in meta:
        borne = f"{m['min']}-{m['max']}" if m["max"] else f"{m['min']}+"
        print(f"   {borne:>9} circulations/j : {m['segments']:>5} segments")


if __name__ == "__main__":
    # --web <directory>: produce only the binary layer, in that directory. Used
    # by build_window to generate a network PER DAY, since each segment's traffic
    # varies sharply between a Tuesday and a Sunday.
    if "--web" in sys.argv:
        DOSSIER_WEB = sys.argv[sys.argv.index("--web") + 1]
        SEULEMENT_WEB = True
    main()
