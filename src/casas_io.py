"""Shared reader for CASAS testbed logs.

A CASAS log line has no header and looks like:
    2011-06-16,07:03:11.441,Kitchen,ON,Cook_Breakfast="begin"
    date , time(HH:MM:SS.ffffff) , zone/sensor , state , [activity label]
"""
import pandas as pd

ON_STATES = {"ON", "OPEN", "PRESENT", "OPENED"}
OFF_STATES = {"OFF", "CLOSE", "ABSENT", "CLOSED"}


def load(path) -> pd.DataFrame:
    """Read a CASAS log into a DataFrame[ts, zone, state, label], time-sorted."""
    rows = []
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line:
                continue
            parts = line.split(",", 4)
            if len(parts) < 4:
                continue
            rows.append((
                parts[0] + " " + parts[1],
                parts[2].strip(),
                parts[3].strip(),
                parts[4].strip() if len(parts) == 5 else "",
            ))
    df = pd.DataFrame(rows, columns=["ts", "zone", "state", "label"])
    df["ts"] = pd.to_datetime(df["ts"], errors="coerce", format="mixed")
    return df.dropna(subset=["ts"]).sort_values("ts").reset_index(drop=True)
