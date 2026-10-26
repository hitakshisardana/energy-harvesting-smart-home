# Dataset notes

## Source

CASAS Smart Home datasets, WSU. The classic single-file "Aruba" download is no
longer served by casas.wsu.edu; the current authoritative release is on Zenodo:

- Record **15708568** – "CASAS Smart Home dataset – free living, motion, door,
  activity labels", file `labeled_data.zip` (236 MB, 82 testbeds).
- We pull individual testbed CSVs out of that zip with HTTP range requests
  (`src/fetch_casas.py`) instead of downloading the whole archive.
- Floorplans: same record, `floorplans.zip`. Not needed – zone names are already
  human-readable.

## Files fetched (`data/raw/`)

| file | testbed | span | rows | door events | notes |
|---|---|---|---|---|---|
| `hh104.csv` | HH104 apartment, 2011 | 61 days, no gaps | 344,701 | 6,388 (OutsideDoor) | **primary** – long, clean, strong Sleep labels, has `LoungeChair` zone |
| `hh101.csv` | HH101 apartment, 2012 | 60 days, no gaps | 222,858 | 7,429 | secondary home |
| `mn50.csv` | Milan, 2019 | 15 days | 99,928 | 1,768 | 2019 data, `Office`+`Hall` zones |
| `mn51.csv` | Milan, 2019 | 30 days (1 dead day, 36 h gap) | 175,521 | 2,583 | use with care |

To get more: `python src/fetch_casas.py --list` then e.g.
`python src/fetch_casas.py hh113` (90 MB uncompressed – long trace).

## Row format (no header)

```
date, time(HH:MM:SS.ffffff), zone, state, [activity label]
2011-06-16,07:12:03.441,OutsideDoor,ON,Enter_Home="begin"
```

- `state` is `ON`/`OFF` for motion + door; a few analog rows (temperature).
- Activity labels appear only on the rows that bound an activity
  (`..."begin"` / `..."end"`).

## Why HH/MN and not Aruba

Aruba isn't in the labeled Zenodo release and its old direct URL is dead. The
HH-series is newer (2011–2012, refreshed 2021), same sensor model, cleaner files,
and richer activity labels. Nothing in the project design depended on Aruba
specifically – it only needs per-zone occupancy + a door stream + activity
labels, which HH104 has.

## What the data does NOT contain

No exercise/gym and no chair/mattress-compression sensing. Those two harvesting
sources stay fully synthetic (allowed by the plan: four physical sources, fifth
node = second floor instance). `LoungeChair` motion in HH104 is used as a rough
furniture-presence proxy only.

**Measured correction (was wrong in the first draft of this file):** furniture
was assumed to be an anti-correlated night source. It is not. It covers only
13.7 % of the windows where floor/door/ambient are all silent, with identical
45 min night and day silence medians (`src/silence_analysis.py`). Every real
source in hh104 is occupancy-coupled, so none of them harvest during the
silence the others face.

## Proposed mapping: CASAS zones -> our 5 virtual nodes (using HH104)

| Virtual node | Signal |
|---|---|
| Floor node | `Kitchen` + `LivingRoom` motion ON (high-traffic footstep proxy) |
| Door node | `OutsideDoor` ON events |
| Furniture node | `LoungeChair` motion + `Bedroom` motion during `Sleep` labels. Harvests from the compression source (chair and bed), not footsteps |
| Gym node | synthetic (no CASAS equivalent) |
| Ambient node | `WorkArea` + `Bathroom` motion (secondary zone) |

This mapping is applied in `src/build_events.py` and frozen in
`config/nodes.yaml`.

## Data coverage (current)

| Source | Data | Enough? |
|---|---|---|
| Footsteps (floor, ambient) | 38,000 to 127,000 motion events per home, 15 to 61 days | Yes |
| Doors | 884 to 3,714 door events per home | Yes for counts; the energy is tiny |
| Furniture compression | Chair motion in hh104 only (1,942 events); sleep-labelled intervals 732 (hh104), 326 (hh101), 150 (mn50), 118 (mn51), 12 (hh107) | No: thin outside hh104 |
| Exercise | None. The gym node is synthetic | No real data |
| Pets | Not recorded. A pet and a person look the same to the sensors | Cannot be separated |
| Walkers at once | Zones active within 30 s of each other: 16 % to 28 % of motion events. CASAS does not say who is moving | Bracketed by the visit rules, not counted |

Five homes in total, all older-adult or community settings, at most two months each.
