"""Precomputes trajectories sampled at a fixed step (step 3).

For each trip:
  - the shape (map-matched by pfaedle) is parameterised by its
    shape_dist_traveled, in metres; stops are given in the SAME unit, so they
    are placed directly, with no reprojection or conversion
  - position is interpolated linearly in distance between two stops, and stays
    FIXED during the dwell at a station: each stop yields two nodes,
    (arrival, s) and (departure, s)
  - sampling every STEP seconds, bounded to the day [00:00, 24:00)

Hourly slicing
--------------
deck.gl processes EVERY vertex of a TripsLayer on every frame, including those
outside the trail window: 2 M vertices cap at ~16 fps whether 18 or 1,400 trains
are visible. Each trajectory is therefore cut into one-hour slices, with a
TRAIL-second overlap at the start of each slice so the trail stays continuous
across an hour boundary. Rendering then activates one slice at a time, roughly
1/24 of the geometry.

Binary output per category, consumed as-is by deck.gl TripsLayer:
  <cat>/pos.bin   Float32  interleaved lon,lat
  <cat>/time.bin  Float32  seconds since midnight
  <cat>/idx.bin   Uint32   startIndices (n+1), counted in vertices
meta.json gives, per category, the path range of each hourly slice.
"""
import os, json, math, datetime, collections, time
import numpy as np
import pandas as pd
from shapely.geometry import LineString, Point

OUT = "data/processed"
WEB = f"{OUT}/web"
SHAPED = f"{OUT}/gtfs-shaped"
STEP = 30                      # sampling step, seconds
DAY = 86400
BUCKET = 3600                  # render slice duration, seconds
TRAIL = 600                    # trail length, seconds (= slice overlap)
NB = DAY // BUCKET
CATS = ["highspeed", "longdistance", "night", "regional"]
def _date_des_donnees():
    """La date vient du manifeste ecrit par merge_gtfs, jamais de l'environnement:
    un ecart entre les deux produirait une carte datee d'un jour et peuplee d'un
    autre, sans aucun signal."""
    p = f"{OUT}/manifest.json"
    if not os.path.exists(p):
        raise SystemExit("ERREUR: data/processed/manifest.json absent. "
                         "Lancer scripts/merge_gtfs.py d'abord.")
    man = json.load(open(p))
    env = os.environ.get("TARGET_DATE")
    if env and env != man["target_date"]:
        raise SystemExit(
            f"ERREUR: TARGET_DATE={env} mais les donnees fusionnees portent sur "
            f"{man['target_date']}. Relancer merge_gtfs.py pour la date voulue.")
    return man["target_date"]


TARGET_DATE = _date_des_donnees()


def gtfs_sec(s):
    p = s.str.split(":", expand=True).astype(np.int64)
    return (p[0]*3600 + p[1]*60 + p[2]).to_numpy()


def cumdist(lon, lat):
    """Distance cumulee en metres, latitude locale par segment (erreur < 0,1 %)."""
    latm = np.radians((lat[:-1] + lat[1:]) / 2.0)
    dx = np.diff(lon) * 111320.0 * np.cos(latm)
    dy = np.diff(lat) * 110540.0
    return np.concatenate([[0.0], np.cumsum(np.hypot(dx, dy))])


def metres(lon1, lat1, lon2, lat2):
    k = np.cos(np.radians((lat1 + lat2) / 2.0))
    return np.hypot((lon2 - lon1) * 111320.0 * k, (lat2 - lat1) * 110540.0)


def main():
    trips = pd.read_csv(f"{SHAPED}/trips.txt", dtype=str)
    st = pd.read_csv(f"{SHAPED}/stop_times.txt", dtype=str)
    stops = pd.read_csv(f"{SHAPED}/stops.txt", dtype=str).set_index("stop_id")
    meta = pd.read_csv(f"{OUT}/trip_meta.csv",
                       dtype={"trip_id": str, "cat": str, "t_offset": int}).set_index("trip_id")

    shapes = {}
    if os.path.exists(f"{SHAPED}/shapes.txt"):
        # shape_id forced to string: a feed whose identifiers are numeric (the
        # Chemins de fer de Corse one, for instance) would have them read as ints
        # here and as strings in trips.txt, and no trace would ever be found.
        sh = pd.read_csv(f"{SHAPED}/shapes.txt", dtype={"shape_id": str}) \
               .sort_values(["shape_id", "shape_pt_sequence"])
        for sid, g in sh.groupby("shape_id", sort=False):
            lon = g.shape_pt_lon.to_numpy(np.float64)
            lat = g.shape_pt_lat.to_numpy(np.float64)
            sdt = (g.shape_dist_traveled.to_numpy(np.float64)
                   if "shape_dist_traveled" in g.columns else None)
            ok = (sdt is not None and np.isfinite(sdt).all()
                  and sdt[-1] > 0 and np.all(np.diff(sdt) >= 0))
            shapes[sid] = (lon, lat, sdt if ok else cumdist(lon, lat), ok)
    print(f"shapes chargees: {len(shapes):,}")

    st["seq"] = st.stop_sequence.astype(int)
    st = st.sort_values(["trip_id", "seq"])
    st["arr"] = gtfs_sec(st.arrival_time)
    st["dep"] = gtfs_sec(st.departure_time)
    st["lon"] = st.stop_id.map(stops.stop_lon).astype(float)
    st["lat"] = st.stop_id.map(stops.stop_lat).astype(float)
    st["sdt"] = (pd.to_numeric(st["shape_dist_traveled"], errors="coerce")
                 if "shape_dist_traveled" in st.columns else np.nan)

    shp_of = trips.set_index("trip_id").get("shape_id", pd.Series(dtype=str))
    # slice -> path lists, per category
    buf = {c: [{"pos": [], "time": []} for _ in range(NB)] for c in CATS}
    chunks = {c: {} for c in CATS}          # trip -> [(bucket, rank within the slice)]
    n_matched = n_fallback = n_projected = 0
    dev_all = []
    active = np.zeros(DAY // STEP + 1, np.int32)
    # Per-timestep series feeding the site's statistics panel: how many trains
    # of each category are running, how many services have departed, and how many
    # train-kilometres have been covered since midnight.
    actifs_cat = {c: np.zeros(DAY // STEP + 1, np.int32) for c in CATS}
    depart_cum = np.zeros(DAY // STEP + 1, np.int32)
    km_pas = np.zeros(DAY // STEP + 1, np.float64)
    skipped = 0

    for tid, g in st.groupby("trip_id", sort=False):
        if tid not in meta.index:
            skipped += 1; continue
        m = meta.loc[tid]
        cat = m["cat"]; off = int(m["t_offset"])
        arr = g.arr.to_numpy(np.float64) + off
        dep = g.dep.to_numpy(np.float64) + off

        sid = shp_of.get(tid)
        sdt = g.sdt.to_numpy(np.float64)
        has_shape = isinstance(sid, str) and sid in shapes
        if has_shape:
            lon_s, lat_s, D, sdt_is_pfaedle = shapes[sid]
            stop_sdt_ok = (sdt_is_pfaedle and np.isfinite(sdt).all()
                           and np.all(np.diff(sdt) >= 0) and sdt[-1] > 0)
            if stop_sdt_ok:
                s = sdt                          # same unit as D: nothing to convert
            else:
                # pfaedle gave no usable stop distance: project each stop onto
                # the trace instead (local metric space)
                k = math.cos(math.radians(float(lat_s.mean())))
                line = LineString(np.stack([lon_s * 111320.0 * k, lat_s * 110540.0], 1))
                Dm = cumdist(lon_s, lat_s)
                s_m = np.maximum.accumulate(np.array(
                    [line.project(Point(x * 111320.0 * k, y * 110540.0))
                     for x, y in zip(g.lon, g.lat)]))
                if s_m[-1] <= 0:
                    skipped += 1; continue
                s = np.interp(s_m, Dm, D)
                n_projected += 1
            px = np.interp(s, D, lon_s); py = np.interp(s, D, lat_s)
            dev_all.append(metres(g.lon.to_numpy(), g.lat.to_numpy(), px, py))
            n_matched += 1
        else:
            # explicit fallback: straight lines between stations (counted in stats)
            lon_s = g.lon.to_numpy(np.float64); lat_s = g.lat.to_numpy(np.float64)
            D = cumdist(lon_s, lat_s); s = D
            n_fallback += 1

        if not np.all(np.diff(s) >= 0):
            s = np.maximum.accumulate(s)
        if D[-1] <= 0:
            skipped += 1; continue

        # time/distance nodes; dwell respected, two nodes per stop
        kt = np.empty(2 * len(arr)); ks = np.empty(2 * len(arr))
        kt[0::2] = arr; kt[1::2] = dep
        ks[0::2] = s;   ks[1::2] = s
        kt = np.maximum.accumulate(kt)

        t0 = max(0.0, math.ceil(kt[0] / STEP) * STEP)
        t1 = min(float(DAY), math.floor(kt[-1] / STEP) * STEP)
        if t1 <= t0:
            skipped += 1; continue
        ts = np.arange(t0, t1 + STEP, STEP)
        ts = ts[ts <= DAY]
        if len(ts) < 2:
            skipped += 1; continue

        dist = np.interp(ts, kt, ks)
        lon = np.interp(dist, D, lon_s)
        lat = np.interp(dist, D, lat_s)
        i0, i1 = int(ts[0]) // STEP, int(ts[-1]) // STEP
        active[i0:i1 + 1] += 1
        actifs_cat[cat][i0:i1 + 1] += 1
        depart_cum[i0] += 1
        # distance covered on each step, in km
        d_pas = np.abs(np.diff(dist)) / 1000.0
        np.add.at(km_pas, np.arange(i0, i0 + len(d_pas)).clip(0, len(km_pas) - 1), d_pas)

        # cut into hourly slices, with a TRAIL-second overlap upstream
        b0 = int(ts[0]) // BUCKET
        b1 = min(NB - 1, int(ts[-1]) // BUCKET)
        for b in range(b0, b1 + 1):
            lo = b * BUCKET - TRAIL
            hi = (b + 1) * BUCKET
            sel = (ts >= lo) & (ts < hi)
            if sel.sum() < 2:
                continue
            slot = buf[cat][b]
            chunks[cat].setdefault(tid, []).append((b, len(slot["pos"])))
            slot["pos"].append(np.stack([lon[sel], lat[sel]], 1).astype(np.float32).ravel())
            slot["time"].append(ts[sel].astype(np.float32))

    # ---------- write ----------
    os.makedirs(WEB, exist_ok=True)
    cat_meta = {}
    for c in CATS:
        d = f"{WEB}/{c}"; os.makedirs(d, exist_ok=True)
        POS, TIM, IDX = [], [], [0]
        ranges = []                              # [path_start, path_end) per slice
        for b in range(NB):
            slot = buf[c][b]
            start = len(IDX) - 1
            for p, t_ in zip(slot["pos"], slot["time"]):
                POS.append(p); TIM.append(t_); IDX.append(IDX[-1] + len(t_))
            ranges.append([start, len(IDX) - 1])
        pos = np.concatenate(POS) if POS else np.zeros(0, np.float32)
        tim = np.concatenate(TIM) if TIM else np.zeros(0, np.float32)
        idx = np.array(IDX, np.uint32)
        pos.tofile(f"{d}/pos.bin"); tim.tofile(f"{d}/time.bin"); idx.tofile(f"{d}/idx.bin")
        cat_meta[c] = {"trips": len(chunks[c]), "paths": len(idx) - 1,
                       "vertices": int(len(tim)), "buckets": ranges,
                       "bytes": int(pos.nbytes + tim.nbytes + idx.nbytes)}

    with open(f"{OUT}/trip_chunks.json", "w") as f:
        json.dump(chunks, f)

    dev = np.concatenate(dev_all) if dev_all else np.zeros(1)
    tot = sum(v["trips"] for v in cat_meta.values())
    peak_i = int(active.argmax())
    nightslice = active[:6 * 3600 // STEP]
    min_i = int(nightslice.argmin())
    hh = lambda i: f"{i*STEP//3600:02d}:{i*STEP%3600//60:02d}"
    peak_v = max(v["vertices"] // NB for v in cat_meta.values())

    stats = {
        "date": TARGET_DATE, "step_seconds": STEP, "day_seconds": DAY,
        # Build fingerprint, used as a client-side cache buster. Without it the
        # .bin files keep the same URL from one day to the next, and a visitor can
        # end up with today's date and yesterday's trajectories.
        "build": int(time.time()),
        "bucket_seconds": BUCKET, "trail_seconds": TRAIL,
        "total_trips": tot, "trips_skipped": skipped,
        "shape_matched": n_matched, "shape_fallback_straight": n_fallback,
        "stops_projected_on_shape": n_projected,
        "match_rate_pct": round(100 * n_matched / max(1, n_matched + n_fallback), 2),
        "stop_to_shape_deviation_m": {
            "median": round(float(np.median(dev)), 1),
            "p90": round(float(np.percentile(dev, 90)), 1),
            "p99": round(float(np.percentile(dev, 99)), 1),
            "max": round(float(dev.max()), 1)},
        "peak": {"time": hh(peak_i), "trains": int(active[peak_i])},
        "night_min": {"time": hh(min_i), "trains": int(nightslice[min_i])},
        "categories": {c: {**cat_meta[c],
                           "pct": round(100 * cat_meta[c]["trips"] / max(1, tot), 2)}
                       for c in CATS},
        "active_curve": active.tolist(),
        "active_par_categorie": {c: actifs_cat[c].tolist() for c in CATS},
        "departs_cumules": np.cumsum(depart_cum).tolist(),
        "km_cumules": np.round(np.cumsum(km_pas)).astype(int).tolist(),
        "pic_par_categorie": {c: int(actifs_cat[c].max()) for c in CATS},
        "km_total": int(round(km_pas.sum())),
        "total_bytes": sum(v["bytes"] for v in cat_meta.values()),
        "vertices_total": sum(v["vertices"] for v in cat_meta.values()),
    }
    with open(f"{WEB}/meta.json", "w") as f:
        json.dump(stats, f)
    lourds = {"active_curve", "active_par_categorie", "departs_cumules", "km_cumules"}
    short = {k: v for k, v in stats.items() if k not in lourds}
    for c in CATS:
        short["categories"][c] = {k: v for k, v in short["categories"][c].items()
                                  if k != "buckets"}
    with open(f"{OUT}/stats.json", "w") as f:
        json.dump(short, f, indent=2)
    print(json.dumps(short, indent=2))


if __name__ == "__main__":
    main()
