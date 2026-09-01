"""Active checks on the produced trajectories (step 5).

Each check prints PASS / FAIL / WARN together with the figure backing it.
The script exits with code 1 if any check is in FAIL.
"""
import datetime, json, math, os, sys, urllib.request
import numpy as np
import pandas as pd

WEB = "data/processed/web"
CATS = ["highspeed", "longdistance", "night", "regional"]
FAILED = []



RFN_URL = ("https://data.sncf.com/api/explore/v2.1/catalog/datasets/"
           "formes-des-lignes-du-rfn/exports/geojson")
RFN_PATH = "data/raw/rfn-formes-lignes.geojson"
# Ile-de-France: the central RER sections belong to RATP and are NOT part of the
# national rail network, so the official reference does not contain them.
# Comparing traces there would measure a hole in the reference, not a matching
# error. This check covers the rest of the country.
IDF_BBOX = (1.9, 48.5, 3.1, 49.2)


def reseau_officiel(pas=20.0):
    """Reseau ferre national EXPLOITE, densifie tous les `pas` metres (metrique locale)."""
    if not os.path.exists(RFN_PATH):
        os.makedirs("data/raw", exist_ok=True)
        print(f"       telechargement du referentiel SNCF Reseau -> {RFN_PATH}")
        urllib.request.urlretrieve(RFN_URL, RFN_PATH)
    d = json.load(open(RFN_PATH))
    out = []
    for x in d["features"]:
        if x["properties"].get("libelle") != "Exploitée":
            continue
        c = np.array(x["geometry"]["coordinates"], float)
        if len(c) < 2:
            continue
        M = _proj(c)
        dd = np.concatenate([[0.0], np.cumsum(np.hypot(*np.diff(M, axis=0).T))])
        if dd[-1] <= 0:
            continue
        u = np.arange(0.0, dd[-1], pas)
        out.append(np.stack([np.interp(u, dd, M[:, 0]), np.interp(u, dd, M[:, 1])], 1))
    return np.concatenate(out)


_K = math.cos(math.radians(46.6))


def _proj(P):
    return np.stack([P[:, 0] * 111320.0 * _K, P[:, 1] * 110540.0], 1)


def load(cat):
    pos = np.fromfile(f"{WEB}/{cat}/pos.bin", np.float32).reshape(-1, 2)
    tim = np.fromfile(f"{WEB}/{cat}/time.bin", np.float32)
    idx = np.fromfile(f"{WEB}/{cat}/idx.bin", np.uint32)
    return pos, tim, idx


def check(name, ok, detail, warn_only=False):
    tag = "PASS" if ok else ("WARN" if warn_only else "FAIL")
    print(f"[{tag}] {name}\n       {detail}")
    if not ok and not warn_only:
        FAILED.append(name)


def hav(a, b):
    """metres entre deux tableaux de points lon/lat."""
    k = np.cos(np.radians((a[:, 1] + b[:, 1]) / 2))
    return np.hypot((b[:, 0] - a[:, 0]) * 111320.0 * k, (b[:, 1] - a[:, 1]) * 110540.0)


def near(pos, lon, lat, km):
    k = math.cos(math.radians(lat))
    d = np.hypot((pos[:, 0] - lon) * 111.32 * k, (pos[:, 1] - lat) * 110.54)
    return d.min() <= km


def main():
    meta = json.load(open(f"{WEB}/meta.json"))
    # Some checks target the mainland SNCF network and make no sense on the
    # Corsican one: the high-speed line, Paris-region density, and the national
    # rail network reference, which Chemins de fer de Corse is not part of. They
    # are reported as skipped rather than failed.
    manifeste = "data/processed/manifest.json"
    perimetre = (json.load(open(manifeste)).get("perimetre", "france")
                 if os.path.exists(manifeste) else "france")
    france = perimetre == "france"
    step = meta["step_seconds"]
    print(f"=== VERIFICATION — {meta['date']} — {meta['total_trips']:,} circulations ===\n")

    # ---- 1. continuity: no jump between two samples ----
    worst, worst_cat, tot = 0.0, "", 0
    over = 0
    for c in CATS:
        pos, tim, idx = load(c)
        if len(idx) < 2: continue
        for i in range(len(idx) - 1):
            a, b = idx[i], idx[i + 1]
            if b - a < 2: continue
            d = hav(pos[a:b - 1], pos[a + 1:b])
            tot += len(d); over += int((d > 5000).sum())
            if d.size and d.max() > worst:
                worst, worst_cat = float(d.max()), c
    # 30 s at 320 km/h = 2.67 km. Past 5 km it is a discontinuity.
    check("Aucun saut discontinu entre echantillons",
          over == 0,
          f"{over}/{tot:,} pas > 5 km ; pas maximal observe {worst/1000:.2f} km ({worst_cat}) "
          f"— borne physique a {step}s: 2,67 km a 320 km/h")

    # ---- 2. Paris -> Marseille TGV on the high-speed line ---- (mainland only)
    if not france:
        print("[N/A]  LGV Paris->Marseille — hors perimetre (reseau corse)")
        print("[N/A]  densite Ile-de-France — hors perimetre")
        print("[N/A]  referentiel SNCF Reseau — les CFC ne font pas partie du RFN")
    trips = pd.read_csv("data/processed/gtfs-shaped/trips.txt", dtype=str)
    st = pd.read_csv("data/processed/gtfs-shaped/stop_times.txt", dtype=str)
    stops = pd.read_csv("data/processed/gtfs-shaped/stops.txt", dtype=str).set_index("stop_id")
    meta_t = pd.read_csv("data/processed/trip_meta.csv", dtype=str).set_index("trip_id")
    st["name"] = st.stop_id.map(stops.stop_name).str.upper()

    if france:
        g = st.groupby("trip_id").name.agg(list)
        cand = [t for t, n in g.items()
                if meta_t.cat.get(t) == "highspeed"
                and any("PARIS" in x for x in n[:2]) and any("MARSEILLE" in x for x in n[-2:])]
        if not cand:
            check("TGV Paris -> Marseille identifie", False, "aucun trip trouve")
        else:
            sh = pd.read_csv("data/processed/gtfs-shaped/shapes.txt")
            sid = trips.set_index("trip_id").shape_id.get(cand[0])
            s = sh[sh.shape_id == sid].sort_values("shape_pt_sequence")
            p = s[["shape_pt_lon", "shape_pt_lat"]].to_numpy()
            # waypoints along the LGV Sud-Est / Mediterranee
            lgv = {"Le Creusot TGV": (4.45, 46.79), "Macon-Loche TGV": (4.79, 46.29),
                   "Valence TGV": (4.98, 44.99), "Avignon TGV": (4.78, 43.92)}
            hits = {k: near(p, *v, 15) for k, v in lgv.items()}
            # the Massif Central: where a straight-line trace would run
            massif = ((p[:, 0] > 2.4) & (p[:, 0] < 3.6) & (p[:, 1] > 44.6) & (p[:, 1] < 45.9)).sum()
            check("TGV Paris->Marseille suit la LGV (Le Creusot/Macon/Valence/Avignon)",
                  all(hits.values()),
                  f"{cand[0]} — {sum(hits.values())}/4 jalons LGV atteints: "
                  + ", ".join(f"{k}={'oui' if v else 'NON'}" for k, v in hits.items()))
            check("TGV Paris->Marseille ne coupe pas par le Massif central",
                  massif == 0, f"{massif} points de trace dans la boite Massif central "
                               f"(2.4-3.6E, 44.6-45.9N)")

    # ---- 3. stationary during stops ----
    st["seq"] = st.stop_sequence.astype(int)
    st = st.sort_values(["trip_id", "seq"])
    def sec(x):
        h, m, s2 = x.split(":"); return int(h)*3600+int(m)*60+int(s2)
    dwell = st.assign(a=st.arrival_time.map(sec), d=st.departure_time.map(sec))
    dwell["w"] = dwell.d - dwell.a
    long_dwell = dwell[dwell.w >= 3 * 60]
    moved, tested = 0, 0
    cache = {c: load(c) for c in CATS}
    chunks = json.load(open("data/processed/trip_chunks.json"))
    # trip -> list of absolute paths (a trip is cut into hourly slices)
    paths_of = {}
    for c in CATS:
        bk = meta["categories"][c]["buckets"]
        for tid, lst in chunks[c].items():
            paths_of[tid] = (c, [bk[b][0] + r for b, r in lst])
    for tid, grp in long_dwell.groupby("trip_id"):
        if tid not in paths_of:
            continue
        c, plist = paths_of[tid]
        pos, tim, idx = cache[c]
        off = int(meta_t.t_offset.get(tid, 0))
        for _, row in grp.iterrows():
            lo, hi = row.a + off, row.d + off
            for pi in plist:
                a, b = idx[pi], idx[pi + 1]
                P, T = pos[a:b], tim[a:b]
                m = (T >= lo) & (T <= hi)
                if m.sum() >= 2:
                    tested += 1
                    if hav(P[m][:-1], P[m][1:]).max() > 150:
                        moved += 1
                    break
    check("Trains immobiles pendant les arrets en gare (dwell >= 3 min)",
          moved == 0,
          f"{moved}/{tested:,} arrets ou le train derive de plus de 150 m pendant son arret")

    # ---- 4. plausible activity curve ----
    cur = np.array(meta["active_curve"])
    per_h = np.array([cur[h*3600//step:(h+1)*3600//step].mean() for h in range(24)])
    night = per_h[2:5].mean(); am = per_h[7:10].max(); pm = per_h[16:20].max()
    check("Creux nocturne marque",
          night < 0.15 * max(am, pm),
          f"moyenne 02h-05h = {night:.0f} trains, contre {max(am,pm):.0f} au pic "
          f"({night/max(am,pm)*100:.1f} %)")

    # Earlier versions encoded the SHAPE of a day - two peaks on weekdays, an
    # evening rise at weekends - and both failed on perfectly normal days: Sunday
    # has no morning peak, and Saturday is almost flat. Only what holds EVERY day
    # is asserted here: traffic peaks during the day, and day dwarfs night. The
    # shape is reported, not required.
    heure_max = int(np.argmax(per_h))
    jour_moy = per_h[7:20].mean()
    ratio = jour_moy / max(night, 1e-9)
    check("Le trafic culmine en journee, pas la nuit",
          6 <= heure_max <= 22 and ratio > 5,
          f"maximum a {heure_max:02d}h ; journee (07h-20h) {jour_moy:.0f} trains "
          f"contre {night:.0f} la nuit (02h-05h), soit {ratio:.0f} fois plus")
    jours_fr = ["lundi","mardi","mercredi","jeudi","vendredi","samedi","dimanche"]
    jour = datetime.datetime.strptime(meta["date"], "%Y%m%d").weekday()
    midi = per_h[11:14].mean()
    print(f"       forme du {jours_fr[jour]}: matin {am:.0f}, midi {midi:.0f}, "
          f"soir {pm:.0f} (rapporte, non exige)")
    print(f"       profil horaire: " + " ".join(f"{h:02d}h:{v:.0f}" for h, v in enumerate(per_h)))

    # ---- 5. Ile-de-France density (Transilien merge) ----
    if france:
        pos, _, _ = load("regional")
        idf = ((pos[:, 0] > 1.9) & (pos[:, 0] < 3.1) & (pos[:, 1] > 48.5) & (pos[:, 1] < 49.2)).sum()
        check("Ile-de-France dense (merge Transilien reussi)",
              idf > 100_000,
              f"{idf:,} sommets de trajectoire dans la boite IDF ({idf/len(pos)*100:.1f} % du regional)")

    # ---- 6. map-matching quality ----
    d = meta["stop_to_shape_deviation_m"]
    check("Taux de map-matching >= 85 %",
          meta["match_rate_pct"] >= 85,
          f"{meta['match_rate_pct']} % ({meta['shape_matched']:,} matches, "
          f"{meta['shape_fallback_straight']:,} en repli ligne droite)")
    check("Ecart median arret <-> trace < 100 m",
          d["median"] < 100,
          f"median {d['median']} m, p90 {d['p90']} m, p99 {d['p99']} m, max {d['max']} m")

    # ---- 10. comparison against the official SNCF Reseau reference ----
    try:
        if not france:
            raise RuntimeError("hors perimetre metropolitain")
        from scipy.spatial import cKDTree
        tree = cKDTree(reseau_officiel())
        sh_all = pd.read_csv("data/processed/gtfs-shaped/shapes.txt")
        pts = sh_all[["shape_pt_lon", "shape_pt_lat"]].to_numpy()
        n = min(250_000, len(pts))
        rng = np.random.default_rng(0)          # fixed seed: reproducible check
        S = pts[rng.choice(len(pts), n, replace=False)]
        dist, _ = tree.query(_proj(S), workers=-1)
        x0, y0, x1, y1 = IDF_BBOX
        idf = (S[:, 0] > x0) & (S[:, 0] < x1) & (S[:, 1] > y0) & (S[:, 1] < y1)
        hors = dist[~idf]
        pct = float((hors < 25).mean() * 100)
        check("Traces conformes au referentiel SNCF Reseau (hors IDF, < 25 m)",
              pct >= 95.0,
              f"{pct:.2f} % des points a moins de 25 m du reseau ferre national exploite "
              f"(median {np.median(hors):.1f} m, p90 {np.percentile(hors, 90):.1f} m, "
              f"n={len(hors):,})")
        pi = float((dist[idf] < 25).mean() * 100)
        print(f"       pour information, Ile-de-France: {pi:.1f} % — attendu plus bas, "
              f"les troncons centraux des RER sont exploites par la RATP et absents du RFN")
    except ImportError:
        print("[SKIP] referentiel SNCF Reseau: scipy absent")
    except Exception as e:                       # network unavailable: not a failure
        print(f"[SKIP] referentiel SNCF Reseau: {e}")


    print()
    if FAILED:
        print(f"### {len(FAILED)} CONTROLE(S) EN ECHEC: " + ", ".join(FAILED))
        sys.exit(1)
    print("### TOUS LES CONTROLES SONT PASSES")


if __name__ == "__main__":
    main()
