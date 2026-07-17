"""Convert a Lympik oculus raw HDF5 file into CSVs for easy viewing.

Each channel under data/<name>/ has its own `data` and `timegrid` arrays,
and different sensors (e.g. IMU vs GNSS) run at different sample rates, so
there's no single flat table. This groups channels that share an identical
timegrid (typically all channels from the same sensor) into one wide CSV
with columns [timegrid, channel1, channel2, ...]; a channel with a unique
timegrid gets its own CSV.

Usage:
    python h5_to_csv.py <path_to.h5> [--out-dir DIR]

Requires: h5py, pandas, numpy (pip install h5py pandas numpy)
"""

import argparse
import os

import h5py
import numpy as np
import pandas as pd


def load_channels(h5_path, group_name="data"):
    channels = {}
    with h5py.File(h5_path, "r") as f:
        if group_name not in f:
            raise KeyError(f"No '{group_name}' group in {h5_path}")
        for name, obj in f[group_name].items():
            if isinstance(obj, h5py.Group) and "data" in obj and "timegrid" in obj:
                channels[name] = {
                    "data": obj["data"][()],
                    "timegrid": obj["timegrid"][()],
                }
    return channels


def group_by_timegrid(channels):
    """Buckets channels that share an identical timegrid array together."""
    groups = []  # list of [timegrid_array, {name: data_array}]
    for name, arrs in channels.items():
        tg = arrs["timegrid"]
        for group_tg, group_data in groups:
            if group_tg.shape == tg.shape and np.array_equal(group_tg, tg):
                group_data[name] = arrs["data"]
                break
        else:
            groups.append([tg, {name: arrs["data"]}])
    return groups


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("h5_path")
    parser.add_argument("--out-dir", default=None, help="Output directory (default: alongside the h5 file)")
    args = parser.parse_args()

    out_dir = args.out_dir or os.path.dirname(os.path.abspath(args.h5_path)) or "."
    os.makedirs(out_dir, exist_ok=True)
    stem = os.path.splitext(os.path.basename(args.h5_path))[0]

    channels = load_channels(args.h5_path)
    groups = group_by_timegrid(channels)

    for tg, data_map in groups:
        df = pd.DataFrame({"timegrid": tg, **data_map})
        names = sorted(data_map.keys())
        label = "_".join(names) if len(names) <= 3 else f"{len(names)}channels"
        out_path = os.path.join(out_dir, f"{stem}_n{len(tg)}_{label}.csv")
        df.to_csv(out_path, index=False)
        print(f"Wrote {out_path} ({df.shape[0]} rows x {df.shape[1]} cols)")


if __name__ == "__main__":
    main()
