#!/usr/bin/env python3
"""
Turn the team's RAW data folders into the datasets the API serves. Used by the Docker image at start-up.

In the deployment the `datasets` image fills a read-only volume with the extraction team's files:

    <raw>/precipitation_2000_2026/precipitation_<YYYY>_<MM>.npy      (or precipitation_monthly_<YYYY>.npz)
    <raw>/temperature_anomaly_1880_2025/temperature_anomaly_<YYYY>_<MM>.npy

The API reads `<prefix>_<variable>.npz + .json` (simulation/INTENSITY_MAP_SPEC.md), so this script runs
simulation/build_monthly_dataset.py on the folders it finds and writes the result to a folder the API
also scans (JUKEBOX_BUILT_DIR, by default <cache>/_datasets, because the raw volume is read-only).

It only rebuilds what changed (same input files + same recipe = nothing to do), and it publishes each
dataset in one go at the end, so the API never sees a half-written file.

Usage
  python backend/prepare_data.py                      # uses JUKEBOX_DATA_DIR and JUKEBOX_CACHE_DIR
  python backend/prepare_data.py --raw ../datasets/data --out simulation/output
  python backend/prepare_data.py --force
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONVERTER = ROOT / "simulation" / "build_monthly_dataset.py"
STAMP = "prepared.json"            # what was built, from which inputs
MARKER = ".preparing"              # exists while a build is running (GET /api/health reports it)

# input name -> the file patterns that identify the team's folder for it
MONTH = "[0-9][0-9][0-9][0-9]_[0-9][0-9]"
INPUTS = {
    "precip": [f"precipitation_{MONTH}.npy", "precipitation_monthly_*.npz"],     # one file per month, or per year
    "temperature": [f"temperature_anomaly_{MONTH}.npy"],
}


def input_files(need: str, folder: Path) -> list[Path]:
    return sorted(f for pattern in INPUTS[need] for f in folder.glob(pattern))

# The datasets to build. Change the pipeline here.
#   needs  : the inputs it uses (the recipe is skipped when one is missing)
#   args   : options of build_monthly_dataset.py
#   prefix : the dataset is served as <prefix>_<first year>_<last year>. Each recipe has its own, so two
#            recipes that happen to cover the same years never overwrite each other.
RECIPES = [
    # every month the team has for each variable, one dataset per variable
    # One bar = one month in every dataset. The frontend decides what it asks for (start date, length, and
    # "frames_per_bar" in the request if it wants a faster time scale), so no dataset gets a scale of its own.
    dict(name="temperature history", prefix="real_temperature", needs=["temperature"], args=["--res", "2"]),
    dict(name="precipitation", prefix="real_precipitation", needs=["precip"], args=["--res", "1"]),
    # the months both have, in one dataset, so the two instruments can play together
    dict(name="precipitation + temperature", prefix="real_monthly", needs=["precip", "temperature"],
         args=["--res", "1"]),
]


def log(msg: str):
    print(f"[prepare_data] {msg}", flush=True)


def find_inputs(raw: Path) -> dict:
    """{input name: folder} for the team's folders found in `raw` (itself, or up to two levels below)."""
    found = {}
    if not raw.is_dir():
        return found
    folders = [raw] + sorted(p for p in raw.glob("*") if p.is_dir()) + sorted(p for p in raw.glob("*/*") if p.is_dir())
    for name in INPUTS:
        for folder in folders:
            if input_files(name, folder):
                found[name] = folder
                break
    return found


def months_span(need: str, folder: Path):
    """(first, last) month 'YYYY-MM' an input can cover, read from the file names only (fast)."""
    stamps = []
    for f in input_files(need, folder):
        m = re.search(r"(\d{4})_(\d{2})\.npy$", f.name)
        if m:
            stamps += [f"{m[1]}-{m[2]}"]
        else:
            y = re.search(r"(\d{4})\.npz$", f.name)
            if y:
                stamps += [f"{y[1]}-01", f"{y[1]}-12"]
    return (min(stamps), max(stamps)) if stamps else None


def recipe_args(recipe: dict, inputs: dict) -> list[str]:
    """The converter options of a recipe. With several inputs only their common months are kept, so the
    converter is told that window up front and does not read the files outside it (1752 monthly files of
    temperature for 26 years of overlap would otherwise all be read)."""
    args = list(recipe["args"])
    spans = [months_span(n, inputs[n]) for n in recipe["needs"]]
    if len(spans) > 1 and all(spans) and "--start" not in args and "--end" not in args:
        args += ["--start", max(s[0] for s in spans), "--end", min(s[1] for s in spans)]
    return args


def signature(recipe: dict, inputs: dict) -> str:
    """Changes when an input file, the recipe or the converter changes."""
    h = hashlib.sha1()
    h.update(json.dumps([recipe["prefix"], recipe["needs"], recipe["args"]]).encode())
    h.update(str(CONVERTER.stat().st_size).encode() + CONVERTER.read_bytes())
    for need in recipe["needs"]:
        for f in input_files(need, inputs[need]):
            st = f.stat()
            h.update(f"{f.name}:{st.st_size}:{int(st.st_mtime)}".encode())
    return h.hexdigest()[:16]


def build(recipe: dict, inputs: dict, out: Path) -> list[str]:
    """Run the converter into a scratch folder, then move the files into `out`. Returns the file names."""
    tmp = out / f".building-{recipe['name'].replace(' ', '_').replace('+', 'and')}"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    cmd = [sys.executable, str(CONVERTER), "--out", str(tmp), *recipe_args(recipe, inputs)]
    for need in recipe["needs"]:
        cmd += [f"--{need}", str(inputs[need])]
    try:
        r = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True)
        tail = [line for line in (r.stdout + r.stderr).splitlines() if line.strip() and not line.startswith("  read ")]
        for line in tail[-6:]:
            log(f"   {line.strip()}")
        if r.returncode != 0:
            raise RuntimeError(f"converter failed (exit {r.returncode})")
        produced = sorted(p.name for p in tmp.iterdir() if p.suffix in (".npz", ".json"))
        if not produced:
            raise RuntimeError("converter wrote no file")
        names = []
        for n in sorted(produced, key=lambda n: n.endswith(".json")):   # data first, then its description
            # the converter names its files real_monthly_<first year>_<last year>_<variable>
            final = re.sub(r"^real_monthly(?=_\d{4}_\d{4}_)", recipe["prefix"], n)
            os.replace(tmp / n, out / final)
            names.append(final)
        return sorted(names)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def prepare(raw: Path, out: Path, force: bool = False) -> int:
    """Build what is missing or out of date. Returns the number of datasets built."""
    inputs = find_inputs(raw)
    if not inputs:
        log(f"no raw team data in {raw} (looked for {', '.join(p for ps in INPUTS.values() for p in ps)}); "
            f"nothing to do")
        return 0
    log("found " + ", ".join(f"{k}: {v}" for k, v in inputs.items()))
    out.mkdir(parents=True, exist_ok=True)
    stamp_path = out / STAMP
    try:
        stamp = json.loads(stamp_path.read_text())
    except (OSError, ValueError):
        stamp = {}
    marker = out / MARKER
    built = 0
    try:
        for recipe in RECIPES:
            name = recipe["name"]
            if any(n not in inputs for n in recipe["needs"]):
                log(f"skip '{name}': needs {recipe['needs']}")
                continue
            sig = signature(recipe, inputs)
            old = stamp.get(name, {})
            if not force and old.get("signature") == sig and all((out / f).exists() for f in old.get("files", [])):
                log(f"up to date: '{name}' ({', '.join(f for f in old['files'] if f.endswith('.npz'))})")
                continue
            marker.write_text(name)
            log(f"building '{name}' ...")
            t0 = time.time()
            try:
                files = build(recipe, inputs, out)
            except Exception as e:                     # one failed dataset must not stop the others
                log(f"FAILED '{name}': {e}")
                continue
            for stale in set(old.get("files", [])) - set(files):          # e.g. the year range in the name changed
                (out / stale).unlink(missing_ok=True)
            stamp[name] = {"signature": sig, "files": files, "seconds": round(time.time() - t0, 1)}
            stamp_path.write_text(json.dumps(stamp, indent=2))
            built += 1
            log(f"built '{name}' in {time.time() - t0:.0f} s")
    finally:
        marker.unlink(missing_ok=True)
    return built


def default_dirs():
    raw = Path(os.environ.get("JUKEBOX_RAW_DIR") or os.environ.get("JUKEBOX_DATA_DIR") or ROOT / "simulation" / "output")
    cache = Path(os.environ.get("JUKEBOX_CACHE_DIR", ROOT / "backend" / "cache"))
    out = Path(os.environ.get("JUKEBOX_BUILT_DIR") or cache / "_datasets")
    return raw, out


def main():
    raw, out = default_dirs()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--raw", default=str(raw), help="folder with the team's raw folders (default: JUKEBOX_DATA_DIR)")
    ap.add_argument("--out", default=str(out), help="where the datasets are written (default: JUKEBOX_BUILT_DIR)")
    ap.add_argument("--force", action="store_true", help="rebuild even when nothing changed")
    a = ap.parse_args()
    n = prepare(Path(a.raw), Path(a.out), a.force)
    log(f"done: {n} dataset(s) built, output in {a.out}")


if __name__ == "__main__":
    main()
