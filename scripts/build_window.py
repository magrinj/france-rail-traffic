"""Builds a window of several days, straddling the current date.

The SNCF feed is FORWARD-LOOKING: it runs from its publication date to roughly
150 days later. Today and future days can therefore be produced, but no date
earlier than the feed's publication. The requested window is trimmed to the
actual coverage read from feed_info.txt.

Each day is computed independently, then written to dist/data/<date>/. Nothing
is carried over between runs: everything is recomputed, which rules out any
silent drift from a cache.

  python scripts/build_window.py --back 3 --forward 3
"""
import argparse, datetime, json, os, shutil, subprocess, sys

RAW, PROC, DIST = "data/raw", "data/processed", "dist"
PY = sys.executable


def couverture():
    """Plage de dates reellement couverte par le feed national."""
    p = f"{RAW}/sncf-national/feed_info.txt"
    if os.path.exists(p):
        import csv
        with open(p) as f:
            r = next(csv.DictReader(f))
        if r.get("feed_start_date") and r.get("feed_end_date"):
            return r["feed_start_date"], r["feed_end_date"]
    # fallback: bounds taken from calendar_dates
    import pandas as pd
    cd = pd.read_csv(f"{RAW}/sncf-national/calendar_dates.txt", dtype=str)
    return cd.date.min(), cd.date.max()


def run(cmd, env=None, titre=""):
    e = dict(os.environ); e.update(env or {})
    r = subprocess.run(cmd, env=e, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"      ECHEC: {titre}\n{r.stdout[-2000:]}\n{r.stderr[-2000:]}")
        return False, r.stdout + r.stderr
    return True, r.stdout


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--back", type=int, default=3)
    ap.add_argument("--forward", type=int, default=3)
    ap.add_argument("--today", default=None, help="forcer le jour de reference (YYYYMMDD)")
    ap.add_argument("--corse", action="store_true",
                    help="produire la semaine des Chemins de fer de Corse au lieu du reseau SNCF")
    a = ap.parse_args()

    if a.corse:
        # The CFC feed covers a single week, 3-9 March 2026, and has not been
        # refreshed since. It is produced as-is, as a separate collection:
        # overlaying it on the rolling SNCF window would make no sense.
        dates = [f"2026030{k}" for k in range(3, 10)]
        ref = datetime.date(2026, 3, 4)
        print(f"perimetre corse : {dates[0]} -> {dates[-1]} ({len(dates)} jours)\n")
        hors = []
    else:
        ref = (datetime.datetime.strptime(a.today, "%Y%m%d").date() if a.today
               else datetime.date.today())
        lo, hi = couverture()
        print(f"feed couvre {lo} -> {hi}")
        voulues = [(ref + datetime.timedelta(days=k)).strftime("%Y%m%d")
                   for k in range(-a.back, a.forward + 1)]
        dates = [d for d in voulues if lo <= d <= hi]
        hors = [d for d in voulues if d not in dates]
    if hors:
        print(f"hors couverture, ignorees : {', '.join(hors)}")
    if not dates:
        sys.exit("ERREUR: aucune date de la fenetre n'est couverte par le feed")
    if not a.corse:
        print(f"fenetre retenue : {dates[0]} -> {dates[-1]} ({len(dates)} jours)\n")

    os.makedirs(f"{DIST}/data", exist_ok=True)
    faits, echecs = [], []
    # The reference day is processed LAST, so that at the end of the loop
    # data/processed/ holds its data and scripts/export_segments.py produces the
    # dataset for that day rather than an arbitrary one from the window. Each day
    # is written to its own directory anyway.
    ref_str = ref.strftime("%Y%m%d")
    ordre = ([d for d in dates if d != ref_str] + [ref_str]) if ref_str in dates else dates
    for i, d in enumerate(ordre, 1):
        print(f"[{i}/{len(dates)}] {d}", flush=True)
        shutil.rmtree(f"{PROC}/gtfs-shaped", ignore_errors=True)
        shutil.rmtree(f"{PROC}/merged", ignore_errors=True)
        for p in (f"{PROC}/manifest.json", f"{PROC}/web"):
            shutil.rmtree(p, ignore_errors=True) if os.path.isdir(p) else (
                os.path.exists(p) and os.remove(p))

        script = "scripts/merge_corse.py" if a.corse else "scripts/merge_gtfs.py"
        ok, out = run([PY, script], {"TARGET_DATE": d}, "fusion")
        if not ok:
            echecs.append((d, "fusion")); continue
        if not a.corse:            # the Corsican feed already ships its traces
            ok, _ = run(["bash", "scripts/run_pfaedle.sh"], None, "map-matching")
            if not ok:
                echecs.append((d, "map-matching")); continue
        ok, _ = run([PY, "scripts/precompute.py"], None, "precalcul")
        if not ok:
            echecs.append((d, "precalcul")); continue
        ok, vout = run([PY, "scripts/verify.py"], None, "controles")
        if not ok:
            echecs.append((d, "controles")); print(vout[-1500:]); continue

        cible = f"{DIST}/data/{d}"
        shutil.rmtree(cible, ignore_errors=True)
        shutil.copytree(f"{PROC}/web", cible)

        # Network layer and service analysis, PER DAY: each segment's traffic and
        # the number of trains per department vary sharply between a Tuesday and a
        # Sunday. Computing them once would give figures that do not match the day
        # on display.
        ok, _ = run([PY, "scripts/export_segments.py", "--web", f"{cible}/reseau"],
                    None, "couche reseau")
        if not ok:
            echecs.append((d, "couche reseau")); continue
        if not a.corse:      # the analysis covers mainland departments
            ok, _ = run([PY, "scripts/analyse_desserte.py", f"{cible}/desserte.json"],
                        None, "analyse desserte")
            if not ok:
                echecs.append((d, "analyse desserte")); continue
        m = json.load(open(f"{cible}/meta.json"))
        faits.append({"date": d, "trips": m["total_trips"],
                      "peak": m["peak"], "night_min": m["night_min"]})
        print(f"      OK — {m['total_trips']:,} circulations, "
              f"pic {m['peak']['trains']} a {m['peak']['time']}".replace(",", " "))

    if not faits:
        sys.exit("ERREUR: aucune journee n'a pu etre produite")

    # A failing day must not bring the whole window down: what is valid gets
    # published, the rest is reported.
    defaut = ref.strftime("%Y%m%d")
    if defaut not in [f["date"] for f in faits]:
        defaut = min(faits, key=lambda f: abs(
            (datetime.datetime.strptime(f["date"], "%Y%m%d").date() - ref).days))["date"]
    # Chronological sort: the loop handles the reference day last, so production
    # order is not date order. The site's selector derives its min/max bounds from
    # this.
    faits.sort(key=lambda f: f["date"])
    # days.json carries several collections; only this one's entry is replaced.
    chemin = f"{DIST}/data/days.json"
    doc = {"collections": [], "defaut": "france"}
    if os.path.exists(chemin):
        try: doc = json.load(open(chemin))
        except Exception: pass
    doc.setdefault("collections", [])
    ident = "corse" if a.corse else "france"
    col = {"id": ident,
           "libelle": "Corse" if a.corse else "France",
           "days": faits, "default": defaut,
           "note": ("Chemins de fer de Corse — le seul feed publié couvre la semaine "
                    "du 3 au 9 mars 2026 et n'a pas été rafraîchi depuis.")
                   if a.corse else None}
    doc["collections"] = [c for c in doc["collections"] if c.get("id") != ident] + [col]
    doc["collections"].sort(key=lambda c: c["id"] != "france")
    doc["defaut"] = "france"
    doc["generated_at"] = datetime.datetime.now().isoformat(timespec="seconds")
    with open(chemin, "w") as f:
        json.dump(doc, f, ensure_ascii=False)

    print(f"\n=== {len(faits)}/{len(dates)} journees publiees, defaut {defaut} ===")
    for f in faits:
        print(f"   {f['date']}  {f['trips']:>6,} circulations".replace(",", " "))
    if echecs:
        print("\nechecs :")
        for d, e in echecs:
            print(f"   {d} : {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
