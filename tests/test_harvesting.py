"""Source mapping and visit merging: the rules the energy results depend on."""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from harvesting import source_of, to_visits  # noqa: E402


def _ev(rows):
    df = pd.DataFrame(rows, columns=["ts", "node_id", "event_type", "zone"])
    df["ts"] = pd.to_datetime(df.ts)
    return df


def test_each_event_maps_to_one_of_the_four_sources():
    assert source_of("floor", "motion") == "footstep"
    assert source_of("ambient", "motion") == "footstep"
    assert source_of("furniture", "motion") == "compression"       # lounge chair
    assert source_of("furniture", "sleep_presence") == "compression"
    assert source_of("door", "door") == "door"


def test_node_rule_merges_zones_of_one_node():
    # Kitchen and LivingRoom are both floor-node zones, 10 s apart: one visit.
    ev = _ev([("2011-01-01 10:00:00", "floor", "motion", "Kitchen"),
              ("2011-01-01 10:00:10", "floor", "motion", "LivingRoom")])
    assert len(to_visits(ev, 60.0, by="node")) == 1


def test_zone_rule_keeps_overlapping_zones_separate():
    ev = _ev([("2011-01-01 10:00:00", "floor", "motion", "Kitchen"),
              ("2011-01-01 10:00:10", "floor", "motion", "LivingRoom")])
    assert len(to_visits(ev, 60.0, by="zone")) == 2


def test_door_events_are_never_merged():
    ev = _ev([("2011-01-01 10:00:00", "door", "door", "OutsideDoor"),
              ("2011-01-01 10:00:05", "door", "door", "OutsideDoor")])
    assert len(to_visits(ev, 60.0, by="node")) == 2
    assert len(to_visits(ev, 60.0, by="zone")) == 2


def test_gap_longer_than_debounce_starts_a_new_visit():
    ev = _ev([("2011-01-01 10:00:00", "floor", "motion", "Kitchen"),
              ("2011-01-01 10:02:00", "floor", "motion", "Kitchen")])
    assert len(to_visits(ev, 60.0, by="node")) == 2


def test_bad_visit_rule_is_rejected():
    ev = _ev([("2011-01-01 10:00:00", "floor", "motion", "Kitchen")])
    try:
        to_visits(ev, 60.0, by="room")
    except ValueError:
        return
    raise AssertionError("expected ValueError for an unknown visit rule")
