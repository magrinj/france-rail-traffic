<a href="https://www.linkedin.com/in/jeremy-magrin/">
  <img src=".github/assets/france-rail-traffic-banner.jpg" alt="France rail traffic" width="100%" />
</a>

[![en](https://img.shields.io/badge/lang-english-informational.svg)](README.md)
[![fr](https://img.shields.io/badge/lang-fran%C3%A7ais-blue.svg)](README.fr.md)

# Trafic ferroviaire français — visualisation animée sur 24 h

[![site](https://img.shields.io/badge/en%20ligne-france--rail--traffic.pages.dev-3b82f6.svg)](https://france-rail-traffic.pages.dev)
[![licence](https://img.shields.io/badge/licence-MIT-blue.svg)](LICENSE)
[![données](https://img.shields.io/badge/donn%C3%A9es-ODbL-orange.svg)](#licence)
[![Carte quotidienne](https://github.com/magrinj/france-rail-traffic/actions/workflows/carte-quotidienne.yml/badge.svg)](https://github.com/magrinj/france-rail-traffic/actions/workflows/carte-quotidienne.yml)
![mise à jour](https://img.shields.io/badge/reconstruite-chaque%20nuit-brightgreen)

Carte web animée des circulations ferroviaires françaises, de 00:00 à 24:00. Chaque train
est un point mobile qui suit la **vraie géométrie des voies** (map-matching
OpenStreetMap), avec une traînée dont la longueur est exprimée en unités de temps — un
TGV laisse donc mécaniquement une traînée plus longue qu'un TER, sans aucun calcul de
vitesse.

**La carte en ligne : [france-rail-traffic.pages.dev](https://france-rail-traffic.pages.dev)**, reconstruite chaque nuit.

Le pipeline part des horaires GTFS ouverts de SNCF Voyageurs et de Transilien, les
apparie au réseau ferré d'OpenStreetMap, et publie sept journées consultables. Aucune clé
d'API n'est nécessaire, à aucune étape.

<video src="https://github.com/user-attachments/assets/4fbe3f7e-87a2-4f77-960f-a57872ff52e9" width="100%"></video>

## Résultat

Chiffres du **mercredi 26 août 2026**, journée prise pour illustrer. Le pipeline étant
rejoué chaque nuit, les valeurs publiées changent d'un jour à l'autre : un dimanche compte
environ un tiers de circulations en moins qu'un jour ouvré.

| | |
|---|---|
| Circulations | **13 602** |
| Pic simultané | **1 481 trains à 18:10** |
| Creux nocturne | **16 trains à 02:26** |
| Taux de map-matching | **100 %** — 0 repli en ligne droite |
| Écart médian arrêt ↔ tracé | **23,2 m** (p90 81,1 m · p99 104,3 m · max 385,8 m) |
| Données servies au navigateur | **27,9 Mo** binaire, 2 318 435 sommets |
| Performance mesurée | **plus de 110 fps** au pic (Chrome, Apple Silicon, écran 120 Hz) |

| Catégorie | Trains | Part | Marques |
|---|---:|---:|---|
| 🔵 Grande vitesse | 642 | 4,7 % | TGV INOUI 503, OUIGO 74, Lyria 33, ICE 26, Paris–Bruxelles 6 |
| 🟠 Longue distance | 79 | 0,6 % | Intercités |
| 🟡 Nuit | 32 | 0,2 % | Intercités de Nuit |
| 🟢 Régional | 12 849 | 94,5 % | TER 8 066, RER A–E, Transilien H/J/K/L/N/P/R/U/V, tram-train 217 |

Trains simultanés, moyenne par heure :

```
00h    60  █          08h  1262  █████████████████████    16h   993  █████████████████
01h    26  ▏          09h   925  ███████████████          17h  1351  ███████████████████████
02h    18  ▏          10h   742  ████████████             18h  1440  ████████████████████████
03h    18  ▏          11h   756  █████████████            19h  1296  ██████████████████████
04h    36  █          12h   837  ██████████████           20h   938  ████████████████
05h   292  █████      13h   874  ███████████████          21h   598  ██████████
06h   964  ████████   14h   765  █████████████            22h   320  █████
07h  1367  ████████   15h   754  █████████████            23h   122  ██
```

## Interface

Les commandes sont réparties sur les bords, la carte occupe le centre.

| Emplacement | Contenu |
|---|---|
| Haut, gauche | sélecteur **France / Corse**, bascules **Trains** et **Réseau** |
| Haut, droite | horloge, date, nombre de trains, bande des sept journées, **Chiffres du jour**, **À propos** |
| Bas, gauche | les quatre catégories, cliquables pour filtrer, et deux compteurs |
| Bas, centre | la courbe d'activité, qui sert aussi de barre de défilement |

Un clic sur une catégorie la masque ; recliquer sur la seule encore active les remet
toutes. Les bascules *Trains* et *Réseau* sont indépendantes : masquer les trains laisse
le réseau seul. La courbe se clique et se glisse directement, cinq boutons règlent la
vitesse, la barre d'espace met en pause. Arrivée à minuit, la lecture passe à la journée
suivante de la collection.

## Fenêtre de journées

Le site publie **sept journées** et laisse le visiteur choisir la date, via les pastilles
de l'en-tête ou les flèches gauche et droite. La fenêtre est à cheval sur le jour
courant : trois jours passés, aujourd'hui, trois jours à venir.

```bash
python scripts/build_window.py                     # la fenêtre par défaut
python scripts/build_window.py --back 3 --forward 3
python scripts/build_window.py --today 20260901    # forcer le jour de référence
```

Chaque journée est calculée indépendamment — fusion, map-matching, précalcul, puis les dix
contrôles — et déposée dans `dist/data/<AAAAMMJJ>/`. `dist/data/days.json` recense ce qui
est disponible et désigne la date par défaut.

**Rien n'est conservé d'une exécution à l'autre.** Les sept jours sont intégralement
recalculés chaque nuit, ce qui coûte environ six minutes et supprime toute possibilité de
dérive silencieuse d'un cache. **Une journée en échec n'entraîne pas les autres** : elle
est écartée, les six restantes sont publiées, et le workflow sort en erreur pour le
signaler.

Deux limites tiennent à la nature du feed, qui est **prospectif** : aucune date antérieure
à sa publication n'est reconstituable, et la fenêtre demandée est automatiquement rabotée
sur la couverture réelle lue dans `feed_info.txt`. Le feed national couvre environ
150 jours à venir.

## Le réseau corse, en collection séparée

Les Chemins de fer de Corse ne font pas partie du feed SNCF. Ils publient leur propre GTFS
sur data.gouv.fr, et celui-ci **contient déjà son `shapes.txt`** : aucun map-matching
n'est nécessaire. L'écart médian entre une gare et son tracé y est de **0,9 m**, contre
23 m pour les tracés reconstruits — ce sont les géométries de l'exploitant lui-même.

> **Les dates ne sont pas récentes, et ne peuvent pas l'être.** Le calendrier de ce feed
> ne couvre que **la semaine du 3 au 9 mars 2026**, et le fichier n'a pas été rafraîchi
> depuis. La collection Corse affiche donc cette semaine-là, telle quelle. Ce n'est pas un
> retard de mise à jour : c'est tout ce que la source contient.

C'est la raison pour laquelle la Corse est une **collection distincte** et non une couche
de la carte France. Superposer une semaine de mars à la fenêtre glissante du réseau SNCF
laisserait croire que les deux décrivent le même jour. Changer de collection recharge la
semaine correspondante, recadre la carte sur l'île et affiche la raison de l'écart de
dates. Les deux réseaux ne sont jamais mélangés, et aucune donnée n'est rejouée à une date
qui n'est pas la sienne.

```bash
python scripts/build_window.py --corse     # les 7 journées corses
python scripts/build_window.py             # la fenêtre SNCF, sans écraser la corse
```

Les journées vont de **55 circulations le dimanche à 102 le mercredi**, sur 6 lignes et
267 arrêts. **Trois contrôles sont déclarés hors périmètre** plutôt que mis en échec : la
LGV Paris–Marseille, la densité francilienne, et la conformité au Réseau Ferré National —
dont les CFC ne font pas partie. L'analyse de desserte par département est également
omise : elle porte sur la métropole et n'aurait aucun sens sur une seule île.

## Couche réseau et chiffres de desserte

**La couche réseau** colore chaque tronçon inter-gares selon son trafic quotidien, en cinq
classes — 1-5, 6-20, 21-60, 61-150 et 151+ circulations. La gamme violet-magenta n'est
utilisée par aucune des quatre catégories de trains : un tronçon chargé ne peut donc pas
se confondre avec un train.

**Les chiffres de desserte** sont recalculés pour chaque journée : le nombre de trains par
département varie fortement entre un mardi et un dimanche, et la part des circulations
touchant l'Île-de-France passe de 43,7 % un lundi à 56,4 % un dimanche. Les sources sont
les 34 957 communes de `geo.api.gouv.fr` avec leur population et les contours des
96 départements. Toutes les distances sont **à vol d'oiseau** — calculer un isochrone
routier demanderait un moteur de routage, et les seuils sont donc annoncés en kilomètres,
jamais en minutes.

## Rejouer le pipeline

```bash
TARGET_DATE=20260826 ./scripts/run_all.sh   # une journée, de zéro (~25 min, ~25 Go)
python scripts/build_window.py              # les sept journées (~6 min si déjà amorcé)
./scripts/serve.sh                          # puis http://localhost:8777/
```

Changer de journée une fois les sources en place ne prend que **50 secondes** : ni les
feeds GTFS ni le réseau ferré filtré ne sont retéléchargés. La date cible se règle par
`TARGET_DATE` (`AAAAMMJJ`) ; sans elle, le pipeline prend la date du jour.

Étape par étape :

```bash
.venv/bin/python scripts/gtfs_inspect.py     # 1. rapport d'inspection des deux feeds
.venv/bin/python scripts/merge_gtfs.py       # 2. fusion + filtrage à la date cible
bash scripts/run_pfaedle.sh                  # 3. filtrage OSM + map-matching (long)
.venv/bin/python scripts/precompute.py       # 4. trajectoires -> binaire
.venv/bin/python scripts/verify.py           # 5. contrôles qualité (sort en 1 si échec)
.venv/bin/python scripts/export_segments.py  # 6. jeu de données inter-gares + couche réseau
.venv/bin/python scripts/analyse_desserte.py # 7. statistiques de desserte du jour
```

Prérequis : `python3.11`, `uv`, `cmake`, `g++`, `curl`, `unzip`. pfaedle est compilé en
natif depuis les sources (~2 min) ; l'image Docker `ghcr.io/ad-freiburg/pfaedle` est un
repli possible, mais elle est amd64 et tourne sous émulation sur Apple Silicon.

Le site est reconstruit et publié chaque nuit par
[`.github/workflows/carte-quotidienne.yml`](.github/workflows/carte-quotidienne.yml).

## Architecture

```
data/raw/                    sources téléchargées (GTFS ×2, PBF France 4,8 Go)
data/processed/
  merged/                    feed GTFS fusionné, filtré à la date, rail uniquement
  france-rail.osm.pbf        réseau ferré extrait du PBF France (4,8 Go -> 14 Mo)
  gtfs-shaped/               sortie pfaedle, avec shapes.txt
  web/<catégorie>/*.bin      trajectoires binaires de la journée courante
  web/meta.json              index des tranches horaires + courbe d'activité
  trip_meta.csv              catégorie commerciale et décalage horaire par trip
dist/data/<AAAAMMJJ>/        une journée publiée
dist/data/days.json          journées disponibles, collections et date par défaut
scripts/                     le pipeline
web/index.html               la page source (MapLibre GL JS + deck.gl, aucun token)
dist/                        le site assemblé, servi et publié (hors dépôt)
```

Format binaire, par catégorie — consommé tel quel par `TripsLayer` :

| Fichier | Type | Contenu |
|---|---|---|
| `pos.bin` | `Float32` | `lon, lat` entrelacés |
| `time.bin` | `Float32` | secondes depuis minuit |
| `idx.bin` | `Uint32` | `startIndices`, en nombre de sommets |

## Trois décisions qui expliquent le résultat

**Les `route_type` étendus n'existent pas dans ces feeds.** Classer les trains par les
codes 101/102/105/106 n'était pas possible : le feed SNCF n'utilise que les codes de base,
`2` (rail), `0` (tram-train) et `3` (autocar). La classification s'appuie donc sur la
**marque commerciale encodée dans les `stop_id`** (`StopPoint:OCETGV INOUI-87686006`),
présente et unique pour 100 % des trips — bien plus fiable qu'une heuristique sur les
numéros de train.

**Les trains de la veille sont chargés.** Une circulation partie à 21:00 le 25 août et
arrivée à 09:00 le 26 est décrite dans le GTFS comme un trip du 25 avec des heures
supérieures à `24:00:00`. Sans elle, la carte serait vide de 00:00 à 05:00 et la moitié
des trains de nuit manqueraient. Le pipeline charge donc J **et** J-1, ne retient de J-1
que les circulations qui débordent après minuit, et les décale de −86 400 s.

**Découpage horaire pour tenir 60 fps.** deck.gl traite *tous* les sommets d'une
`TripsLayer` à chaque frame, même ceux hors de la fenêtre de traînée : dessiner les 2,3 M
de sommets d'un coup plafonne à 16 fps, identiques à 03:00 (18 trains) et à 18:00
(1 400 trains) — le coût est purement géométrique. Chaque trajectoire est donc coupée en
tranches d'une heure, avec 600 s de recouvrement pour que la traînée reste continue au
passage d'une heure. Résultat : 60 fps, pour +16 % de volume de données.

## Contrôles automatiques

`scripts/verify.py` sort en code 1 si l'un de ces dix contrôles échoue. Ils sont rejoués
pour **chaque journée** de la fenêtre, et une journée qui échoue n'est pas publiée : c'est
la porte de sortie du déploiement, pas un rapport indicatif.

| Contrôle | Ce qu'il détecte |
|---|---|
| Aucun saut > 5 km entre deux échantillons à 30 s | un tracé qui téléporte, une trajectoire corrompue |
| TGV Paris → Marseille sur la LGV, 4 jalons | un map-matching qui a pris la ligne classique |
| Ce TGV ne coupe pas par le Massif central | un repli silencieux en ligne droite |
| Immobilité pendant les arrêts en gare | un train qui glisse au lieu de stationner |
| Creux nocturne marqué | un calendrier mal filtré |
| Le trafic culmine en journée, pas la nuit | une courbe d'activité inversée ou cassée |
| Densité Île-de-France | un merge Transilien qui a échoué |
| Taux de map-matching ≥ 85 % | une extraction OSM incomplète |
| Écart médian arrêt ↔ tracé < 100 m | des arrêts mal placés sur leur tracé |
| **Conformité au référentiel SNCF Réseau** | **un tracé qui ne suit pas une voie réelle** |

Le dernier est le plus utile, parce qu'il fait appel à une **source indépendante** qui
n'entre nulle part dans la construction : le
[fichier de formes des lignes du RFN](https://data.sncf.com/explore/dataset/formes-des-lignes-du-rfn/)
de SNCF Réseau. Résultat sur la journée de référence : **écart médian 7,5 m**, 98,1 % des
points à moins de 25 m. Le contrôle exclut l'Île-de-France, parce que les tronçons
centraux des RER appartiennent à la RATP et ne figurent pas dans le Réseau Ferré National :
y comparer les tracés mesurerait un trou du référentiel, pas une erreur d'appariement. Il
n'échoue pas non plus si data.sncf.com est indisponible — un contrôle qui casse le build
quand une source tierce tousse serait pire que pas de contrôle.

## Audit avant publication

Passé avant l'ouverture du dépôt, et refait après chaque lot de modifications.

| Point | État |
|---|---|
| Secrets, jetons, clés | aucun |
| Chemins absolus, identité | aucun |
| Ressources CDN | 3, toutes avec empreinte `integrity` |
| Image pfaedle | épinglée par digest |
| Dépendances Python | versions figées |
| Interpolation HTML de données tierces | échappée |
| Archives tierces | garde-fou zip-slip avant extraction |
| Erreurs console après un cycle complet | aucune |
| Fichiers versionnés | 20, 173 Ko d'objets git |

## Sources

| Source | Contenu | Licence |
|---|---|---|
| [GTFS SNCF Voyageurs](https://eu.ftp.opendatasoft.com/sncf/plandata/Export_OpenData_SNCF_GTFS_NewTripId.zip) | TGV, Intercités, TER — feed prospectif ~150 j | ODbL |
| [GTFS Transilien](https://eu.ftp.opendatasoft.com/sncf/gtfs/transilien-gtfs.zip) | RER A–E, Transilien H/J/K/L/N/P/R/U/V, TER Île-de-France | ODbL |
| [GTFS Chemins de fer de Corse](https://www.data.gouv.fr/datasets/gtfs-transport-horaires-chemins-de-fer-corse-1/) | réseau corse, semaine du 3 au 9 mars 2026 | ODbL |
| [OpenStreetMap / Geofabrik](https://download.geofabrik.de/europe/france-latest.osm.pbf) | géométrie du réseau ferré (checksum md5 vérifié) | ODbL |
| [SNCF Réseau — formes des lignes du RFN](https://data.sncf.com/explore/dataset/formes-des-lignes-du-rfn/) | référentiel de contrôle uniquement | ODbL |
| [pfaedle](https://github.com/ad-freiburg/pfaedle) | map-matching GTFS ↔ OSM | GPL-3.0 |
| [Carto dark matter](https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json) | fond de carte | — |

## Licence

Deux régimes distincts, à ne pas confondre :

- **Le code** de ce dépôt est sous [licence MIT](LICENSE) : reprise, modification et
  hébergement libres, y compris à titre commercial.
- **Les données produites** — trajectoires et segments inter-gares — sont sous **ODbL**,
  par héritage : elles dérivent des GTFS SNCF et d'OpenStreetMap, tous deux en ODbL avec
  partage à l'identique. Toute réutilisation doit conserver cette licence et citer
  « SNCF Voyageurs et les contributeurs OpenStreetMap ».

`pfaedle` est sous GPL-3.0, mais il est appelé comme exécutable en sous-processus : il
n'est pas lié au code de ce dépôt et n'en contraint donc pas la licence.

## Limites connues

- **Horaires théoriques, pas temps réel.** Le feed décrit le plan de transport prévu : ni
  retards, ni suppressions, ni trains supplémentaires. Un feed prospectif peut aussi
  différer de ce qui a réellement circulé ce jour-là.
- **Pas de fret.** Ces feeds ne couvrent que le transport de voyageurs. Le fret représente
  une part significative du trafic réel, totalement absente de cette carte.
- **Opérateurs non-SNCF absents ou partiels.** Eurostar et Thalys ne sont pas publiés comme
  tels ; seules 6 circulations Paris–Bruxelles apparaissent sous la marque générique
  « Train ». Trenitalia France, Renfe et les nouveaux entrants ne figurent pas dans le
  feed. En revanche les RER A et B sont couverts intégralement, sections exploitées par la
  RATP comprises.
- **1 631 autocars TER et 323 bus de remplacement sont exclus** : ce sont des services
  routiers, pas des trains. 219 circulations en doublon entre les deux feeds ont été
  dédupliquées sur (numéro de train, heure de départ).
- **Vitesse constante entre deux arrêts.** L'interpolation est linéaire en distance : ni
  accélération, ni freinage, ni ralentissement en courbe. La position est juste aux arrêts,
  approchée entre eux.
- **Les tram-trains sont traités comme du rail.** Les 217 circulations de tram-train
  (Mulhouse, Nantes–Châteaubriant, Sarreguemines) sont forcées en `route_type=2` pour le
  map-matching ; leurs sections en voirie urbaine peuvent être approchées.
- **L'écart maximal arrêt ↔ tracé, 385,8 m, est à Bâle Saint-Jean** — station suisse en
  bordure de l'extrait OSM France. Un trip a été écarté, faute de deux points exploitables.
- **La journée de référence est un mercredi d'août**, hors période scolaire : le trafic TER
  et Transilien y est sensiblement plus faible qu'un mercredi d'octobre.

## Chiffres du map-matching

pfaedle a traité les 13 604 trips en 10 s sur un graphe de 114 852 nœuds et
275 996 arêtes, pic mémoire 888,91 Mo, produisant 3 879 tracés distincts — les trips
partageant la même séquence d'arrêts partagent leur tracé. Le filtrage préalable du PBF
France ramène 4,8 Go à 14 Mo, ce qui rend cette étape possible en quelques dizaines de
secondes.

## Soutenir le projet

Si ce projet vous est utile, vous pouvez soutenir son développement :

<a href="https://buymeacoffee.com/magrinj" target="_blank">
  <img src="https://cdn.buymeacoffee.com/buttons/v2/default-yellow.png" alt="Buy Me A Coffee" height="50">
</a>

---

<p align="center">
  Vibe-coded with ♥ by <a href="https://www.linkedin.com/in/jeremy-magrin/">Jérémy Magrin</a>
</p>

<p align="center">
  Si ce projet vous plaît, une étoile ⭐ fait toujours plaisir !
</p>
