"""Download the elevation map and a dated copy of NASA's route files into data/raw/.

Safe to re-run: a finished elevation map is skipped and an interrupted one resumes. NASA's
route files can change without notice, so each run keeps its own dated copy, along with
checksums that show exactly which version a result used.

Run from the project folder: python -m scripts.download_data
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import date
from pathlib import Path

import requests

from mars_rover.sources import DTM_URL, ROUTE_URLS

RAW_DIR = Path("data/raw")
CHUNK_BYTES = 4 * 1024 * 1024
REPORT_EVERY_BYTES = 200 * 1024 * 1024


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def download_large(url: str, destination: Path) -> Path:
    """Download one large file, resuming a partial copy and verifying its final size."""
    head = requests.head(url, timeout=60, allow_redirects=True)
    head.raise_for_status()
    expected = int(head.headers["Content-Length"])

    if destination.exists():
        if destination.stat().st_size == expected:
            print(f"Already complete: {destination}")
            return destination
        raise RuntimeError(
            f"{destination} is {destination.stat().st_size} bytes, expected {expected}. "
            "Check it, then delete it to download again."
        )

    partial = destination.with_name(destination.name + ".part")
    have = partial.stat().st_size if partial.exists() else 0
    if have > expected:
        raise RuntimeError(f"{partial} is larger than the source file. Delete it and retry.")

    if have < expected:
        headers = {"Range": f"bytes={have}-"} if have else {}
        with requests.get(url, headers=headers, stream=True, timeout=(30, 120)) as response:
            response.raise_for_status()
            if have and response.status_code != 206:
                raise RuntimeError("The server ignored the resume request. Delete the .part file.")
            next_report = have + REPORT_EVERY_BYTES
            with partial.open("ab") as handle:
                for chunk in response.iter_content(CHUNK_BYTES):
                    handle.write(chunk)
                    have += len(chunk)
                    if have >= next_report:
                        print(f"  {have / 1e9:.2f} of {expected / 1e9:.2f} GB", flush=True)
                        next_report += REPORT_EVERY_BYTES

    actual = partial.stat().st_size
    if actual != expected:
        raise RuntimeError(f"Download incomplete: {actual} of {expected} bytes. Run again to resume.")
    os.replace(partial, destination)
    print(f"Saved: {destination} ({expected / 1e9:.2f} GB)")
    return destination


def snapshot_routes(day: str) -> list[dict]:
    """Save today's copy of the route files and return a record of each one."""
    folder = RAW_DIR / "routes" / day
    folder.mkdir(parents=True, exist_ok=True)
    records = []
    for name, url in ROUTE_URLS.items():
        response = requests.get(url, timeout=120)
        response.raise_for_status()
        features = len(json.loads(response.text)["features"])
        path = folder / url.rsplit("/", 1)[-1]
        path.write_bytes(response.content)
        print(f"Saved: {path} ({len(response.content) / 1e6:.2f} MB, {features} features)")
        records.append(
            {
                "name": name,
                "url": url,
                "path": path.as_posix(),
                "bytes": len(response.content),
                "features": features,
                "sha256": hashlib.sha256(response.content).hexdigest(),
                "last_modified": response.headers.get("Last-Modified"),
            }
        )
    return records


def main() -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    day = date.today().isoformat()

    routes = snapshot_routes(day)
    dtm = download_large(DTM_URL, RAW_DIR / DTM_URL.rsplit("/", 1)[-1])
    print("Computing checksum for the elevation map...", flush=True)
    manifest = {
        "downloaded": day,
        "elevation_map": {
            "url": DTM_URL,
            "path": dtm.as_posix(),
            "bytes": dtm.stat().st_size,
            "sha256": sha256_of(dtm),
        },
        "routes": routes,
    }
    manifest_path = RAW_DIR / "MANIFEST.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Saved: {manifest_path}")


if __name__ == "__main__":
    main()
