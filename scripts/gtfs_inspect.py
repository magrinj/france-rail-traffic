"""Inspection report on the GTFS feeds (step 1)."""
import os, sys, collections, datetime, csv
import pandas as pd

RAW = "data/raw"
TARGET_DATE = os.environ.get("TARGET_DATE", datetime.date.today().strftime("%Y%m%d"))

RT = {0:"tram",1:"metro",2:"rail",3:"bus",4:"ferry",5:"cable",6:"gondola",7:"funicular",
      100:"Railway",101:"High Speed Rail",102:"Long Distance",103:"Inter Regional",
      105:"Sleeper",106:"Regional",107:"Tourist",108:"Rail Shuttle",109:"Suburban",
      114:"Long Distance Intl",117:"Intl Rail",200:"Coach",700:"Bus",715:"Demand"}

def report(name, d):
    print(f"\n{'='*70}\n  {name}  ({d})\n{'='*70}")
    files = sorted(os.listdir(d))
    print(f"fichiers      : {', '.join(files)}")
    has_shapes = "shapes.txt" in files
    print(f"shapes.txt    : {'PRESENT' if has_shapes else 'ABSENT -> map-matching requis'}")

    trips = pd.read_csv(f"{d}/trips.txt", dtype=str, low_memory=False)
    routes = pd.read_csv(f"{d}/routes.txt", dtype=str, low_memory=False)
    stops = pd.read_csv(f"{d}/stops.txt", dtype=str, low_memory=False)
    print(f"trips         : {len(trips):,}")
    print(f"routes        : {len(routes):,}")
    print(f"stops         : {len(stops):,}  (location_type=0/vide: "
          f"{(stops.get('location_type').isna() | stops.get('location_type').isin(['0',''])).sum():,})")

    if "shape_id" in trips.columns:
        n = trips["shape_id"].notna().sum()
        print(f"trips.shape_id: colonne presente, {n:,} valeurs non vides"
              + ("  <- mais pas de shapes.txt !" if n and not has_shapes else ""))

    # route_type distribution, weighted by trip count
    routes["route_type"] = routes["route_type"].astype(int)
    rt_by_route = routes.set_index("route_id")["route_type"]
    tr = trips["route_id"].map(rt_by_route)
    print("\nroute_type (routes / trips):")
    for rt, c in sorted(collections.Counter(routes["route_type"]).items()):
        print(f"  {rt:>4} {RT.get(rt,'?'):<20} {c:>5} routes  {int((tr==rt).sum()):>7} trips")

    dates = collections.Counter()
    cal_path = f"{d}/calendar.txt"
    svc_days = {}
    if os.path.exists(cal_path):
        cal = pd.read_csv(cal_path, dtype=str)
        print(f"\ncalendar.txt  : {len(cal):,} services, "
              f"{cal.start_date.min()} -> {cal.end_date.max()}")
    else:
        print("\ncalendar.txt  : ABSENT (services definis uniquement par calendar_dates)")
    cd_path = f"{d}/calendar_dates.txt"
    if os.path.exists(cd_path):
        cd = pd.read_csv(cd_path, dtype=str)
        add = cd[cd.exception_type == "1"]
        print(f"calendar_dates: {len(cd):,} lignes ({len(add):,} ajouts, "
              f"{len(cd)-len(add):,} suppressions), {cd.date.min()} -> {cd.date.max()}")

    active = active_services(d, TARGET_DATE)
    n_act = trips["service_id"].isin(active).sum()
    print(f"\nDATE CIBLE {TARGET_DATE}: {len(active):,} services actifs -> {n_act:,} trips")
    if n_act:
        rt_act = tr[trips["service_id"].isin(active)]
        for rt, c in sorted(collections.Counter(rt_act.dropna().astype(int)).items()):
            print(f"    {rt:>4} {RT.get(rt,'?'):<20} {c:>7} trips")
    return {"trips": len(trips), "active": int(n_act), "shapes": has_shapes}


def active_services(d, date):
    """Services active on `date` (YYYYMMDD): calendar (weekday + range) +/- calendar_dates."""
    dt = datetime.datetime.strptime(date, "%Y%m%d").date()
    dow = ["monday","tuesday","wednesday","thursday","friday","saturday","sunday"][dt.weekday()]
    active = set()
    p = f"{d}/calendar.txt"
    if os.path.exists(p):
        cal = pd.read_csv(p, dtype=str)
        m = (cal[dow] == "1") & (cal.start_date <= date) & (cal.end_date >= date)
        active |= set(cal.loc[m, "service_id"])
    p = f"{d}/calendar_dates.txt"
    if os.path.exists(p):
        cd = pd.read_csv(p, dtype=str)
        day = cd[cd.date == date]
        active |= set(day.loc[day.exception_type == "1", "service_id"])
        active -= set(day.loc[day.exception_type == "2", "service_id"])
    return active


if __name__ == "__main__":
    print(f"### RAPPORT D'INSPECTION GTFS — date cible {TARGET_DATE} "
          f"({datetime.datetime.strptime(TARGET_DATE,'%Y%m%d').strftime('%A %d %B %Y')})")
    for n, d in [("SNCF NATIONAL", f"{RAW}/sncf-national"), ("TRANSILIEN", f"{RAW}/transilien")]:
        report(n, d)
