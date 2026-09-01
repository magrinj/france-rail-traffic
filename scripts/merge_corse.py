"""Prepares one day of the Chemins de fer de Corse network.

The CFC feed is published on data.gouv.fr and ALREADY contains a shapes.txt, so
no map-matching is needed, unlike the SNCF feed. This script therefore writes
the structure precompute.py expects (data/processed/gtfs-shaped/) directly,
bypassing pfaedle.

Major limitation, to be surfaced everywhere: the feed has not been refreshed
since 3 March 2026 and its calendar only covers the week of 3-9 March 2026. It
cannot be overlaid on the rolling SNCF window.

  TARGET_DATE=20260304 python scripts/merge_corse.py
"""
import os, json, shutil, zipfile, urllib.request, datetime
import pandas as pd

URL = ("https://static.data.gouv.fr/resources/gtfs-transport-horaires-chemins-de-fer-"
       "corse-1/20260303-151209/chemin-de-fer-de-la-corse.zip")
RAW = "data/raw/corse"
OUT = "data/processed"
SHAPED = f"{OUT}/gtfs-shaped"
TARGET_DATE = os.environ.get("TARGET_DATE", "20260304")


def source():
    z = "data/raw/corse-cfc.zip"
    if not os.path.exists(z):
        os.makedirs("data/raw", exist_ok=True)
        print(f"  telechargement du GTFS des Chemins de fer de Corse")
        urllib.request.urlretrieve(URL, z)
    if not os.path.exists(RAW):
        with zipfile.ZipFile(z) as f:
            noms = f.namelist()
            if any("/" in n and not n.endswith("/") for n in noms):
                pass
            f.extractall(RAW)
    d = RAW
    sub = [x for x in os.listdir(d) if os.path.isdir(f"{d}/{x}")]
    return f"{d}/{sub[0]}" if sub else d


def services_actifs(d, date):
    dt = datetime.datetime.strptime(date, "%Y%m%d").date()
    dow = ["monday","tuesday","wednesday","thursday","friday","saturday","sunday"][dt.weekday()]
    act = set()
    p = f"{d}/calendar.txt"
    if os.path.exists(p):
        c = pd.read_csv(p, dtype=str)
        act |= set(c.loc[(c[dow] == "1") & (c.start_date <= date) & (c.end_date >= date), "service_id"])
    p = f"{d}/calendar_dates.txt"
    if os.path.exists(p):
        c = pd.read_csv(p, dtype=str)
        if len(c):
            j = c[c.date == date]
            act |= set(j.loc[j.exception_type == "1", "service_id"])
            act -= set(j.loc[j.exception_type == "2", "service_id"])
    return act


def main():
    d = source()
    tr = pd.read_csv(f"{d}/trips.txt", dtype=str)
    st = pd.read_csv(f"{d}/stop_times.txt", dtype=str)
    sp = pd.read_csv(f"{d}/stops.txt", dtype=str)
    rt = pd.read_csv(f"{d}/routes.txt", dtype=str)
    # shape_id as string on both sides: read as an int here, it would not join
    # against the shape_id in trips.txt and the filter would return zero traces.
    sh = pd.read_csv(f"{d}/shapes.txt", dtype={"shape_id": str})

    act = services_actifs(d, TARGET_DATE)
    tr = tr[tr.service_id.isin(act)].copy()
    if not len(tr):
        raise SystemExit(
            f"ERREUR: aucune circulation corse le {TARGET_DATE}. Le feed CFC ne couvre "
            f"que la semaine du 3 au 9 mars 2026 et n'a pas ete rafraichi depuis.")
    st = st[st.trip_id.isin(set(tr.trip_id))].copy()
    sp = sp[sp.stop_id.isin(set(st.stop_id))].dropna(subset=["stop_lat", "stop_lon"]).copy()
    sh = sh[sh.shape_id.isin(set(tr.shape_id.dropna()))].copy()

    # stops without coordinates -> unusable trips
    orph = set(st.stop_id) - set(sp.stop_id)
    if orph:
        bad = set(st[st.stop_id.isin(orph)].trip_id)
        st = st[~st.trip_id.isin(bad)]; tr = tr[~tr.trip_id.isin(bad)]
    cnt = st.groupby("trip_id").size()
    court = set(cnt[cnt < 2].index)
    if court:
        st = st[~st.trip_id.isin(court)]; tr = tr[~tr.trip_id.isin(court)]

    shutil.rmtree(SHAPED, ignore_errors=True)
    os.makedirs(SHAPED, exist_ok=True)
    sp["location_type"] = "0"; sp["parent_station"] = ""
    sp[["stop_id","stop_name","stop_lat","stop_lon","location_type",
        "parent_station"]].to_csv(f"{SHAPED}/stops.txt", index=False)
    rt2 = rt[rt.route_id.isin(set(tr.route_id))].copy(); rt2["route_type"] = "2"
    rt2.to_csv(f"{SHAPED}/routes.txt", index=False)
    tr.to_csv(f"{SHAPED}/trips.txt", index=False)
    st["stop_sequence"] = st.stop_sequence.astype(int)
    st.sort_values(["trip_id", "stop_sequence"]).to_csv(f"{SHAPED}/stop_times.txt", index=False)
    sh.sort_values(["shape_id", "shape_pt_sequence"]).to_csv(f"{SHAPED}/shapes.txt", index=False)

    # Every CFC service is regional: a metre-gauge network serving the island,
    # with no high-speed and no night trains.
    meta = pd.DataFrame({"trip_id": tr.trip_id, "cat": "regional", "route_type_ext": 106,
                         "brand": "Chemins de fer de Corse",
                         "trip_headsign": tr.get("trip_headsign", ""), "t_offset": 0})
    meta.to_csv(f"{OUT}/trip_meta.csv", index=False)
    dt = datetime.datetime.strptime(TARGET_DATE, "%Y%m%d").date()
    json.dump({"target_date": TARGET_DATE,
               "previous_date": (dt - datetime.timedelta(days=1)).strftime("%Y%m%d"),
               "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
               "trips": int(len(tr)), "perimetre": "corse"},
              open(f"{OUT}/manifest.json", "w"), indent=2)

    print(f"[corse {TARGET_DATE}] {len(tr)} circulations, {sp.stop_id.nunique()} arrets, "
          f"{sh.shape_id.nunique()} traces (shapes fournis par le feed)")


if __name__ == "__main__":
    main()
