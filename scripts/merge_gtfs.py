"""Merges the national SNCF and Transilien GTFS feeds, filtered to the target date.

Output: a minimal GTFS feed (data/processed/merged/) holding only the RAIL
services visible on the target day, plus data/processed/trip_meta.csv carrying
each trip's commercial category and time offset.

Two service days are loaded:
  - D   : every service, offset 0
  - D-1 : only those spilling past midnight (arrival >= 24:00:00), offset
          -86400 s. Without them the map would be empty from 00:00 to 05:00 and
          the night trains, which leave the evening before, would be missing.
GTFS times past 24:00:00 are never taken modulo: they are converted to raw
seconds and then shifted explicitly.

Classification: the national feed encodes the commercial brand in stop_id
(`StopPoint:OCE<brand>-<uic>`), present and unique for 100% of trips. That is
the source of truth, not a heuristic on train numbers.
"""
import os, json, datetime
import pandas as pd

RAW, OUT = "data/raw", "data/processed"
TARGET_DATE = os.environ.get("TARGET_DATE", datetime.date.today().strftime("%Y%m%d"))

BRAND = {
    "TGV INOUI": "highspeed", "OUIGO": "highspeed", "Lyria": "highspeed",
    "ICE": "highspeed", "Train": "highspeed",          # 'Train' = Paris-Brussels (LGV Nord)
    "INTERCITES": "longdistance",
    "INTERCITES de nuit": "night",
    "Train TER": "regional", "TramTrain": "regional", "Navette": "regional",
    "Car TER": None, "Car a reservation": None, "Car à réservation": None,   # coaches: excluded
}
CAT_RT = {"highspeed": 101, "longdistance": 102, "night": 105, "regional": 106}


def gtfs_sec(s):
    """'25:13:00' -> 90780. Pas de modulo: les heures > 24h restent > 86400."""
    p = s.str.split(":", expand=True).astype(int)
    return p[0]*3600 + p[1]*60 + p[2]


def active_services(d, date):
    dt = datetime.datetime.strptime(date, "%Y%m%d").date()
    dow = ["monday","tuesday","wednesday","thursday","friday","saturday","sunday"][dt.weekday()]
    act = set()
    p = f"{d}/calendar.txt"
    if os.path.exists(p):
        cal = pd.read_csv(p, dtype=str)
        act |= set(cal.loc[(cal[dow]=="1") & (cal.start_date<=date) & (cal.end_date>=date), "service_id"])
    p = f"{d}/calendar_dates.txt"
    if os.path.exists(p):
        cd = pd.read_csv(p, dtype=str)
        day = cd[cd.date == date]
        act |= set(day.loc[day.exception_type=="1","service_id"])
        act -= set(day.loc[day.exception_type=="2","service_id"])
    return act


def load_day(d, date):
    trips = pd.read_csv(f"{d}/trips.txt", dtype=str, low_memory=False)
    st = pd.read_csv(f"{d}/stop_times.txt", dtype=str, low_memory=False,
                     usecols=lambda c: c in {"trip_id","arrival_time","departure_time",
                                             "stop_id","stop_sequence"})
    act = active_services(d, date)
    trips = trips[trips.service_id.isin(act)].copy()
    st = st[st.trip_id.isin(set(trips.trip_id))].copy()
    return trips, st


def classify_national(trips, st):
    sp = st[st.stop_id.str.startswith("StopPoint:OCE", na=False)]
    b = sp.stop_id.str.extract(r"^StopPoint:OCE(.*?)-")[0]
    per = sp.assign(b=b).dropna(subset=["b"]).groupby("trip_id").b.agg(lambda s: s.mode().iat[0])
    unknown = set(per.dropna().unique()) - set(BRAND)
    if unknown:
        print(f"  !! marques inconnues, traitees en 'regional': {sorted(unknown)}")
    trips = trips.copy()
    trips["brand"] = trips.trip_id.map(per)
    trips["cat"] = trips.trip_id.map(per.map(lambda x: BRAND.get(x, "regional")))
    return trips


def build(date, tag, offset):
    """Retourne (trips, stop_times) prefixes, pour un jour de service donne."""
    out_tr, out_st = [], []

    # ---------- national ----------
    ntr, nst = load_day(f"{RAW}/sncf-national", date)
    ntr = classify_national(ntr, nst)
    n_bus = ntr.cat.isna().sum()
    ntr = ntr[ntr.cat.notna()].copy()
    ntr["num"] = ntr.trip_headsign

    # ---------- transilien ----------
    ttr, tst = load_day(f"{RAW}/transilien", date)
    trt = pd.read_csv(f"{RAW}/transilien/routes.txt", dtype=str)
    rt = trt.set_index("route_id").route_type.astype(int)
    keep = ttr.route_id.map(rt) == 2
    t_bus = (~keep).sum()
    ttr = ttr[keep].copy()
    ttr["cat"] = "regional"
    ttr["brand"] = ttr.route_id.map(trt.set_index("route_id").route_short_name)
    ttr["num"] = ttr.trip_short_name.fillna("").str.split("-").str[0]

    # ---------- cross-feed dedup (same number + same departure time) ----------
    def first_dep(st):
        s = st.assign(seq=st.stop_sequence.astype(int))
        return s.sort_values(["trip_id","seq"]).groupby("trip_id").departure_time.first()
    ntr["dep"] = ntr.trip_id.map(first_dep(nst))
    ttr["dep"] = ttr.trip_id.map(first_dep(tst))
    nkeys = set(zip(ntr.num.fillna(""), ntr.dep.fillna("")))
    dup = ttr.apply(lambda r: (r["num"], r["dep"]) in nkeys, axis=1)
    ttr = ttr[~dup].copy()

    print(f"[{date} {tag}] national {len(ntr):,} (-{n_bus:,} autocars) | "
          f"transilien {len(ttr):,} (-{t_bus:,} bus, -{int(dup.sum()):,} doublons)")

    for tr, st, p in [(ntr, nst, f"N{tag}:"), (ttr, tst, f"T{tag}:")]:
        tr = tr.copy(); st = st[st.trip_id.isin(set(tr.trip_id))].copy()
        tr["trip_id"] = p + tr.trip_id; tr["route_id"] = p + tr.route_id
        st["trip_id"] = p + st.trip_id; st["stop_id"] = p + st.stop_id
        out_tr.append(tr); out_st.append(st)

    trips = pd.concat(out_tr, ignore_index=True)
    stimes = pd.concat(out_st, ignore_index=True)

    # ---------- D-1: keep only what spills past midnight ----------
    if offset:
        last = stimes.assign(a=gtfs_sec(stimes.arrival_time)).groupby("trip_id").a.max()
        cross = set(last[last >= 86400].index)
        print(f"[{date} {tag}] trips debordant apres minuit: {len(cross):,} / {len(trips):,}")
        trips = trips[trips.trip_id.isin(cross)]
        stimes = stimes[stimes.trip_id.isin(cross)]
    trips = trips.copy(); trips["t_offset"] = offset
    return trips, stimes


def main():
    d = f"{OUT}/merged"; os.makedirs(d, exist_ok=True)
    dt = datetime.datetime.strptime(TARGET_DATE, "%Y%m%d").date()
    prev = (dt - datetime.timedelta(days=1)).strftime("%Y%m%d")

    tr_d, st_d = build(TARGET_DATE, "", 0)
    tr_p, st_p = build(prev, "p", -86400)
    trips = pd.concat([tr_d, tr_p], ignore_index=True)
    stimes = pd.concat([st_d, st_p], ignore_index=True)
    trips["service_id"] = "D"

    # ---------- routes / stops ----------
    def routes_for(src, pfxs):
        r = pd.read_csv(f"{RAW}/{src}/routes.txt", dtype=str)
        out = []
        for p in pfxs:
            x = r.copy(); x["route_id"] = p + x.route_id; out.append(x)
        return pd.concat(out, ignore_index=True)
    routes = pd.concat([routes_for("sncf-national", ["N:","Np:"]),
                        routes_for("transilien", ["T:","Tp:"])], ignore_index=True)
    routes = routes[routes.route_id.isin(set(trips.route_id))].copy()
    routes["route_type"] = "2"       # pfaedle: plain rail; the category lives in trip_meta
    routes["agency_id"] = "SNCF"

    def stops_for(src, pfxs):
        s = pd.read_csv(f"{RAW}/{src}/stops.txt", dtype=str)
        out = []
        for p in pfxs:
            x = s.copy(); x["stop_id"] = p + x.stop_id; out.append(x)
        return pd.concat(out, ignore_index=True)
    stops = pd.concat([stops_for("sncf-national", ["N:","Np:"]),
                       stops_for("transilien", ["T:","Tp:"])], ignore_index=True)
    stops = stops[stops.stop_id.isin(set(stimes.stop_id))].dropna(subset=["stop_lat","stop_lon"]).copy()
    stops["location_type"] = "0"; stops["parent_station"] = ""

    # ---------- cleanup ----------
    orphan = set(stimes.stop_id) - set(stops.stop_id)
    if orphan:
        bad = set(stimes[stimes.stop_id.isin(orphan)].trip_id)
        print(f"[nettoyage] {len(orphan)} stops sans coordonnees -> {len(bad)} trips retires")
        stimes = stimes[~stimes.trip_id.isin(bad)]; trips = trips[~trips.trip_id.isin(bad)]
    cnt = stimes.groupby("trip_id").size(); short = set(cnt[cnt < 2].index)
    if short:
        print(f"[nettoyage] {len(short)} trips a moins de 2 arrets retires")
        stimes = stimes[~stimes.trip_id.isin(short)]; trips = trips[~trips.trip_id.isin(short)]

    # ---------- write ----------
    pd.DataFrame([{"agency_id":"SNCF","agency_name":"SNCF","agency_url":"https://www.sncf.com",
                   "agency_timezone":"Europe/Paris"}]).to_csv(f"{d}/agency.txt", index=False)
    dow = ["monday","tuesday","wednesday","thursday","friday","saturday","sunday"][dt.weekday()]
    cal = {c:"0" for c in ["monday","tuesday","wednesday","thursday","friday","saturday","sunday"]}
    cal.update({"service_id":"D", dow:"1", "start_date":TARGET_DATE, "end_date":TARGET_DATE})
    pd.DataFrame([cal])[["service_id","monday","tuesday","wednesday","thursday","friday",
                         "saturday","sunday","start_date","end_date"]].to_csv(f"{d}/calendar.txt", index=False)
    routes[["route_id","agency_id","route_short_name","route_long_name","route_type",
            "route_color"]].to_csv(f"{d}/routes.txt", index=False)
    trips[["route_id","service_id","trip_id","trip_headsign","direction_id"]].to_csv(f"{d}/trips.txt", index=False)
    stops[["stop_id","stop_name","stop_lat","stop_lon","location_type",
           "parent_station"]].to_csv(f"{d}/stops.txt", index=False)
    stimes["stop_sequence"] = stimes.stop_sequence.astype(int)
    stimes.sort_values(["trip_id","stop_sequence"])[
        ["trip_id","arrival_time","departure_time","stop_id","stop_sequence"]
    ].to_csv(f"{d}/stop_times.txt", index=False)

    # Manifest: the processed date is a property of the DATA, not of the
    # environment. precompute reads it back from here rather than trusting
    # TARGET_DATE again; otherwise a mismatch between the two would go unnoticed
    # and the map would show a date that is not the trains' date.
    with open(f"{OUT}/manifest.json", "w") as f:
        json.dump({"target_date": TARGET_DATE, "previous_date": prev,
                   "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
                   "trips": int(len(trips))}, f, indent=2)

    trips["route_type_ext"] = trips.cat.map(CAT_RT)
    trips[["trip_id","cat","route_type_ext","brand","trip_headsign",
           "t_offset"]].to_csv(f"{OUT}/trip_meta.csv", index=False)

    # Guard for the automated nightly run: if SNCF publishes a broken feed, or
    # one that no longer covers the target date, fail here rather than putting an
    # empty map online.
    MIN_TRIPS = 5000
    if len(trips) < MIN_TRIPS:
        raise SystemExit(
            f"ERREUR: seulement {len(trips):,} circulations pour le {TARGET_DATE}, "
            f"il en faut au moins {MIN_TRIPS:,}. Le feed est-il a jour et couvre-t-il "
            f"cette date ?")

    print(f"\n=== FEED FUSIONNE -> {d} ===")
    print(f"trips {len(trips):,} | stop_times {len(stimes):,} | stops {len(stops):,} | routes {len(routes):,}")
    print(trips.cat.value_counts().to_string())
    print("\npar origine (J / J-1):")
    print(trips.t_offset.value_counts().to_string())


if __name__ == "__main__":
    main()
