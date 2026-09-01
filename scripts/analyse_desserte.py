"""Rail service analysis: what the map shows by its absences.

Produces data/processed/desserte.json, consumed by the site and the README.

Reference sources (downloaded once, cached in data/raw/ref/):
  - French communes with population and centroid, via geo.api.gouv.fr
  - simplified department outlines

All distances are AS THE CROW FLIES. No road isochrone is computed: that would
need a routing engine over the road network, which is a different project.
Thresholds are therefore stated in kilometres, never in minutes.
"""
import json, os, sys, math, urllib.request, collections
import numpy as np
import pandas as pd
from scipy.spatial import cKDTree
from shapely.geometry import shape, Point
from shapely.strtree import STRtree

S = "data/processed/gtfs-shaped"
REF = "data/raw/ref"
OUT = "data/processed/desserte.json"   # overridable as the 1st argument
URL_COMMUNES = ("https://geo.api.gouv.fr/communes?fields=nom,code,population,centre,"
                "codeDepartement&format=json&geometry=centre")
URL_DEPS = ("https://raw.githubusercontent.com/gregoiredavid/france-geojson/master/"
            "departements-version-simplifiee.geojson")
K = math.cos(math.radians(46.6))


def cache(url, chemin):
    if not os.path.exists(chemin):
        os.makedirs(os.path.dirname(chemin), exist_ok=True)
        print(f"  telechargement {os.path.basename(chemin)}")
        urllib.request.urlretrieve(url, chemin)
    return json.load(open(chemin))


def proj(P):
    return np.stack([P[:, 0] * 111320.0 * K, P[:, 1] * 110540.0], 1)


def autocars_par_departement(dep_de, noms_dep):
    """Nombre d'autocars TER desservant chaque departement, depuis le feed brut."""
    import re
    R = "data/raw/sncf-national"
    tr = pd.read_csv(f"{R}/trips.txt", dtype=str)
    st = pd.read_csv(f"{R}/stop_times.txt", dtype=str, usecols=["trip_id", "stop_id"])
    sp = pd.read_csv(f"{R}/stops.txt", dtype=str)
    man = json.load(open("data/processed/manifest.json"))
    cd = pd.read_csv(f"{R}/calendar_dates.txt", dtype=str)
    act = set(cd[(cd.date == man["target_date"]) & (cd.exception_type == "1")].service_id)
    marque = st[st.stop_id.str.startswith("StopPoint:OCE", na=False)].copy()
    marque["b"] = marque.stop_id.str.extract(r"^StopPoint:OCE(.*?)-")[0]
    par = marque.dropna(subset=["b"]).groupby("trip_id").b.agg(lambda x: x.mode().iat[0])
    tr["b"] = tr.trip_id.map(par)
    cars = set(tr[(tr.service_id.isin(act)) & (tr.b.isin(["Car TER", "Car a reservation",
                                                          "Car à réservation"]))].trip_id)
    sc = st[st.trip_id.isin(cars)].copy()
    sp["lon"] = sp.stop_lon.astype(float); sp["lat"] = sp.stop_lat.astype(float)
    pos = sp.drop_duplicates("stop_id").set_index("stop_id")
    sc["lon"] = sc.stop_id.map(pos.lon); sc["lat"] = sc.stop_id.map(pos.lat)
    sc = sc.dropna(subset=["lon", "lat"])
    cache = {}
    out = collections.Counter()
    for tid, g in sc.groupby("trip_id"):
        deps = set()
        for r in g.itertuples():
            k = (round(r.lon, 2), round(r.lat, 2))
            if k not in cache:
                cache[k] = dep_de(r.lon, r.lat)
            if cache[k] is not None:
                deps.add(noms_dep[cache[k]])
        for d in deps:
            out[d] += 1
    return out, len(cars)


def main():
    communes = cache(URL_COMMUNES, f"{REF}/communes.json")
    deps = cache(URL_DEPS, f"{REF}/departements.geojson")

    st = pd.read_csv(f"{S}/stop_times.txt", dtype=str)
    sp = pd.read_csv(f"{S}/stops.txt", dtype=str)
    meta = pd.read_csv("data/processed/trip_meta.csv", dtype=str).set_index("trip_id")
    manifest = json.load(open("data/processed/manifest.json"))

    sp["lon"] = sp.stop_lon.astype(float); sp["lat"] = sp.stop_lat.astype(float)
    sp["nom"] = sp.stop_name
    # A physical station = a name AND a position. The feed carries several
    # identifiers per station (one per commercial brand) that must be merged, but
    # grouping on the name alone would merge distinct places that share it - RER B's
    # Luxembourg stop and Luxembourg city station, for instance, whose average fell
    # in the Ardennes.
    sp["cle"] = sp.nom + "@" + sp.lat.round(2).astype(str) + "," + sp.lon.round(2).astype(str)
    gares = (sp.groupby("cle")
               .agg(nom=("nom", "first"), lon=("lon", "mean"), lat=("lat", "mean"))
               .reset_index())
    print(f"gares desservies (noms distincts) : {len(gares):,}")

    st["cle"] = st.stop_id.map(sp.set_index("stop_id").cle)
    st["nom"] = st.stop_id.map(sp.set_index("stop_id").nom)
    def sec(x):
        h, m, s = x.split(":"); return int(h)*3600 + int(m)*60 + int(s)
    st["t"] = st.arrival_time.map(sec)
    st["cat"] = st.trip_id.map(meta.cat)

    # ---------- attach stations to departments ----------
    formes = [shape(f["geometry"]) for f in deps["features"]]
    noms_dep = [f["properties"]["nom"] for f in deps["features"]]
    codes_dep = [f["properties"]["code"] for f in deps["features"]]
    arbre = STRtree(formes)
    def dep_de(lon, lat):
        p = Point(lon, lat)
        for i in arbre.query(p):
            if formes[i].contains(p):
                return i
        return None
    gares["dep"] = [dep_de(r.lon, r.lat) for r in gares.itertuples()]
    hors = gares.dep.isna().sum()
    print(f"gares hors contours (etranger, outre-mer, imprecision) : {hors}")

    dep_par_gare = dict(zip(gares.cle, gares.dep))
    st["dep"] = st.cle.map(dep_par_gare)

    # ---------- 1. service by department ----------
    par_dep = []
    for i, nom in enumerate(noms_dep):
        sous = st[st.dep == i]
        if not len(sous):
            # no service at all: distinct from a gap between two trains
            par_dep.append({"code": codes_dep[i], "nom": nom, "trains": 0,
                            "arrets": 0, "gares": 0, "trou_max_h": None,
                            "trou_debut": None, "sans_desserte": True})
            continue
        trains = sous.trip_id.nunique()
        # Longest window with no stop at all BETWEEN 06:00 AND 22:00. Measured over
        # 24 h, the largest gap would always be the overnight trough, true in every
        # department and therefore meaningless.
        JOUR0, JOUR1 = 6 * 3600, 22 * 3600
        h = np.sort(sous.t.to_numpy())
        h = h[(h >= JOUR0) & (h <= JOUR1)]
        bornes = np.concatenate([[JOUR0], h, [JOUR1]])
        ecarts = np.diff(bornes)
        j = int(ecarts.argmax())
        par_dep.append({
            "code": codes_dep[i], "nom": nom, "trains": int(trains),
            "arrets": int(len(sous)), "gares": int(sous.nom.nunique()),
            "trou_max_h": round(float(ecarts[j]) / 3600, 1),
            "trou_debut": f"{int(bornes[j])//3600:02d}:{int(bornes[j])%3600//60:02d}",
            "sans_desserte": False,
        })
    par_dep.sort(key=lambda d: d["trains"])

    # ---------- 2. population and distance to a served station ----------
    G = proj(gares[["lon", "lat"]].to_numpy())
    tree = cKDTree(G)
    com = [c for c in communes if c.get("population") and c.get("centre")]
    C = np.array([c["centre"]["coordinates"] for c in com], float)
    pop = np.array([c["population"] for c in com], float)
    dist, idx = tree.query(proj(C), workers=-1)
    dist_km = dist / 1000.0
    total = pop.sum()
    bandes = {}
    for seuil in (5, 10, 15, 20, 30):
        bandes[f"{seuil}km"] = round(float(pop[dist_km <= seuil].sum() / total * 100), 1)
    ordre = np.argsort(-dist_km)
    loin = [{"commune": com[i]["nom"], "dep": com[i].get("codeDepartement"),
             "population": int(pop[i]), "km": round(float(dist_km[i]), 1)}
            for i in ordre[:8]]

    # ---------- 3. centralisation ----------
    IDF = {"75","77","78","91","92","93","94","95"}
    idx_idf = {i for i, c in enumerate(codes_dep) if c in IDF}
    trips_idf = set(st[st.dep.isin(idx_idf)].trip_id)
    tous = st.trip_id.nunique()

    # ---------- 4. most isolated station ----------
    d2, _ = tree.query(G, k=2, workers=-1)
    voisine = d2[:, 1] / 1000.0
    # Restricted to stations inside a French department: a foreign terminus looks
    # isolated only because its neighbours are outside the dataset.
    o = [i for i in np.argsort(-voisine) if pd.notna(gares.dep.iloc[i])]
    isolees = [{"gare": gares.nom.iloc[i], "dep": noms_dep[int(gares.dep.iloc[i])],
                "voisine_km": round(float(voisine[i]), 1)} for i in o[:8]]

    # ---------- 5. excluded TER coaches: where do they serve? ----------
    # Coaches are excluded from the map (they are road services), but claiming they
    # cover the empty areas would be an assumption. It is measured instead: the
    # share of coaches in the third of departments least served by train, compared
    # against those departments' demographic weight.
    cars, n_cars = autocars_par_departement(dep_de, noms_dep)
    rang = {d["nom"]: i for i, d in enumerate(par_dep)}
    n_dep = len([d for d in par_dep if not d["sans_desserte"]])
    tiers = set(d["nom"] for d in par_dep[:n_dep // 3])
    tot_cars = sum(cars.values())
    cars_tiers = sum(v for k, v in cars.items() if k in tiers)
    trains_tiers = sum(d["trains"] for d in par_dep[:n_dep // 3])
    trains_tot = sum(d["trains"] for d in par_dep)

    res = {
        "autocars_exclus": {
            "circulations": n_cars,
            "dessertes_departementales": tot_cars,
            "part_dans_tiers_moins_desservi_pct": round(100 * cars_tiers / max(1, tot_cars), 1),
            "part_des_trains_dans_ce_tiers_pct": round(100 * trains_tiers / max(1, trains_tot), 1),
            "note": ("les autocars TER sont exclus de la carte: ce sont des services "
                     "routiers. Ce ratio dit s'ils compensent la ou le train manque."),
        },
        "date": manifest["target_date"],
        "gares_desservies": int(len(gares)),
        "par_departement": par_dep,
        "population": {
            "totale": int(total),
            "part_a_moins_de": bandes,
            "note": "distances a vol d'oiseau, pas des temps de trajet",
            "communes_les_plus_eloignees": loin,
        },
        "centralisation": {
            "circulations_totales": int(tous),
            "touchant_l_idf": int(len(trips_idf)),
            "part_idf_pct": round(len(trips_idf) / tous * 100, 1),
        },
        "gares_les_plus_isolees": isolees,
    }
    with open(OUT, "w") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)

    # ---------- report ----------
    print(f"\n=== DESSERTE — {manifest['target_date']} ===")
    vides = [d for d in par_dep if d["sans_desserte"]]
    print(f"\n-- departements sans AUCUNE desserte ferroviaire voyageurs --")
    for d in vides:
        print(f"  {d['nom']}")
    print(f"\n-- 10 departements les moins desservis (hors ci-dessus) --")
    for d in [x for x in par_dep if not x["sans_desserte"]][:10]:
        print(f"  {d['trains']:>5} trains  {d['gares']:>3} gares  "
              f"plus long trou de jour: {d['trou_max_h']:>4.1f} h a {d['trou_debut']}  {d['nom']}")
    print(f"\n-- 5 departements les mieux desservis --")
    for d in par_dep[-5:][::-1]:
        print(f"  {d['trains']:>5} trains  {d['gares']:>3} gares  {d['nom']}")
    print(f"\n-- population a moins de X d'une gare desservie (vol d'oiseau) --")
    for k, v in bandes.items():
        print(f"  {k:>5} : {v:>5.1f} %")
    print(f"\n-- centralisation --")
    print(f"  {res['centralisation']['part_idf_pct']} % des circulations touchent l'Ile-de-France")
    print(f"\n-- gares les plus isolees --")
    for g in isolees[:5]:
        print(f"  {g['voisine_km']:>6.1f} km de sa voisine  {g['gare']} ({g['dep']})")
    print(f"\n-> {OUT}")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        OUT = sys.argv[1]
    main()
