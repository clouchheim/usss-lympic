"""Download a raw HDF5 dataset file from the Lympik oculus device API.

Uses the same LYMPIK_PROFILE_ID / LYMPIK_API_KEY from .env as the rest of
the pipeline (HTTP Basic auth, per Lympik's `personalToken` scheme).

Usage:
    python download_oculus_raw.py <device_id> <dataset_id> [--out FILE] [--inspect]

Example (the endpoint given for exploration):
    python download_oculus_raw.py \\
        c20d99b0-9d3f-46d2-9d01-ea3c314fd873 \\
        5897d78c-6af0-4474-a12c-3fd0e7df9a3c \\
        --inspect
"""

import argparse
import os

import requests
from dotenv import load_dotenv

load_dotenv()

DEFAULT_BASE_URL = "https://api.lympik.com/v1"


def download_raw(device_id, dataset_id, out_path, base_url=None):
    base_url = (base_url or os.environ.get("LYMPIK_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
    profile_id = os.environ["LYMPIK_PROFILE_ID"]
    api_key = os.environ["LYMPIK_API_KEY"]

    url = f"{base_url}/device/{device_id}/dataset/{dataset_id}/download-raw"
    with requests.get(url, auth=(profile_id, api_key), stream=True, timeout=120) as response:
        response.raise_for_status()
        with open(out_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=1 << 20):
                f.write(chunk)
    return out_path


def inspect(path):
    """Print the HDF5 group/dataset tree. Requires `pip install h5py`."""
    import h5py

    def visit(name, obj):
        if isinstance(obj, h5py.Dataset):
            print(f"{name}: shape={obj.shape} dtype={obj.dtype}")
        else:
            print(f"{name}/")

    with h5py.File(path, "r") as f:
        f.visititems(visit)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("device_id")
    parser.add_argument("dataset_id")
    parser.add_argument("--out", default=None, help="Output file path (default: <dataset_id>.h5)")
    parser.add_argument("--inspect", action="store_true", help="Print the HDF5 structure after download (requires h5py)")
    args = parser.parse_args()

    out_path = args.out or f"{args.dataset_id}.h5"
    download_raw(args.device_id, args.dataset_id, out_path)
    print(f"Saved to {out_path}")

    if args.inspect:
        inspect(out_path)


if __name__ == "__main__":
    main()
