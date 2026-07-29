#!/usr/bin/env python3
"""
Generate structured dataset export files from datasets_export_raw.json.

Reads the raw JSON exported by `export_js_datasets.js` and produces:
  - datasets/persons.json + persons.csv
  - datasets/locations.json + locations.csv
  - datasets/parties.json + parties.csv
  - datasets/mps.json + mps.csv

Usage:
    python services/postprocess/scripts/generate_dataset_exports.py
    python services/postprocess/scripts/generate_dataset_exports.py --from-node
"""

import argparse
import csv
import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
SERVICE_ROOT = SCRIPT_DIR.parent
REPO_ROOT = SERVICE_ROOT.parent.parent
RAW_JSON_PATH = SERVICE_ROOT / "datasets_export_raw.json"
EXPORTER = SCRIPT_DIR / "export_js_datasets.js"
OUTPUT_DIR = SERVICE_ROOT / "datasets"


def load_raw_data_from_node() -> dict:
    """Invoke export_js_datasets.js via subprocess and parse its stdout JSON."""
    try:
        result = subprocess.run(
            ["node", str(EXPORTER)],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        print(f"ERROR: export_js_datasets.js failed (exit {exc.returncode})", file=sys.stderr)
        if exc.stderr:
            print(exc.stderr, file=sys.stderr)
        sys.exit(1)
    except FileNotFoundError:
        print("ERROR: node not found — is Node.js installed?", file=sys.stderr)
        sys.exit(1)

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        print(f"ERROR: could not parse exporter output as JSON: {exc}", file=sys.stderr)
        sys.exit(1)


def load_raw_data() -> dict:
    """Load the raw exported JSON from the file on disk."""
    if not RAW_JSON_PATH.exists():
        print(f"ERROR: {RAW_JSON_PATH} not found", file=sys.stderr)
        print("Hint: run with --from-node to invoke the exporter directly.", file=sys.stderr)
        sys.exit(1)
    with open(RAW_JSON_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def extract_persons(sources: dict) -> list[dict]:
    """Extract all person records (presidents, VPs, speakers, ministers, other notables, cultural figures)."""
    person_keys = [
        "persons_presidents",
        "persons_vice_presidents",
        "persons_speakers",
        "ministers",
        "persons_other_notables",
        "persons_cultural_figures",
    ]
    persons = []
    for key in person_keys:
        if key not in sources:
            continue
        for rec in sources[key]["records"]:
            persons.append({
                "canonical": rec["canonical"],
                "entity_type": rec["entity_type"],
                "role": rec.get("role") or "",
                "aliases": rec.get("aliases", []),
                "source": rec["source"],
            })
    return persons


def extract_locations(sources: dict) -> list[dict]:
    """Extract all location records (regions, cities, supplementary)."""
    location_keys = ["regions", "cities", "supplementary"]
    locations = []
    for key in location_keys:
        if key not in sources:
            continue
        for rec in sources[key]["records"]:
            locations.append({
                "canonical": rec["canonical"],
                "entity_type": rec["entity_type"],
                "region": rec.get("region") or "",
                "aliases": rec.get("aliases", []),
                "source": rec["source"],
            })
    return locations


def extract_parties(sources: dict) -> list[dict]:
    """Extract all party records."""
    parties = []
    if "parties" not in sources:
        return parties
    for rec in sources["parties"]["records"]:
        # The abbreviation is stored in the `party` field by the export script
        aliases = rec.get("aliases", [])
        abbreviation = rec.get("party") or ""
        parties.append({
            "canonical": rec["canonical"],
            "abbreviation": abbreviation,
            "aliases": aliases,
        })
    return parties


def extract_mps(sources: dict) -> list[dict]:
    """Extract all MP records."""
    mps = []
    if "mps" not in sources:
        return mps
    for rec in sources["mps"]["records"]:
        mps.append({
            "canonical": rec["canonical"],
            "constituency": rec.get("constituency") or "",
            "party": rec.get("party") or "",
            "role": rec.get("role") or "",
            "aliases": rec.get("aliases", []),
        })
    return mps


def write_json(data: list[dict], path: Path) -> None:
    """Write data as pretty-printed JSON."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"  Written: {path.relative_to(SERVICE_ROOT)} ({len(data)} records)")


def write_persons_csv(persons: list[dict], path: Path) -> None:
    """Write persons CSV: canonical, entity_type, role, alias_count, source."""
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["canonical", "entity_type", "role", "alias_count", "source"])
        for p in persons:
            writer.writerow([
                p["canonical"],
                p["entity_type"],
                p["role"],
                len(p["aliases"]),
                p["source"],
            ])
    print(f"  Written: {path.relative_to(SERVICE_ROOT)} ({len(persons)} rows)")


def write_locations_csv(locations: list[dict], path: Path) -> None:
    """Write locations CSV: canonical, entity_type, region, alias_count, source."""
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["canonical", "entity_type", "region", "alias_count", "source"])
        for loc in locations:
            writer.writerow([
                loc["canonical"],
                loc["entity_type"],
                loc["region"],
                len(loc["aliases"]),
                loc["source"],
            ])
    print(f"  Written: {path.relative_to(SERVICE_ROOT)} ({len(locations)} rows)")


def write_parties_csv(parties: list[dict], path: Path) -> None:
    """Write parties CSV: canonical, abbreviation, alias_count."""
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["canonical", "abbreviation", "alias_count"])
        for p in parties:
            writer.writerow([
                p["canonical"],
                p["abbreviation"],
                len(p["aliases"]),
            ])
    print(f"  Written: {path.relative_to(SERVICE_ROOT)} ({len(parties)} rows)")


def write_mps_csv(mps: list[dict], path: Path) -> None:
    """Write MPs CSV: canonical, constituency, party, role, alias_count."""
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["canonical", "constituency", "party", "role", "alias_count"])
        for mp in mps:
            writer.writerow([
                mp["canonical"],
                mp["constituency"],
                mp["party"],
                mp["role"],
                len(mp["aliases"]),
            ])
    print(f"  Written: {path.relative_to(SERVICE_ROOT)} ({len(mps)} rows)")


def main():
    parser = argparse.ArgumentParser(
        description="Generate structured dataset export files from the JS exporter output."
    )
    parser.add_argument(
        "--from-node",
        action="store_true",
        help="Invoke export_js_datasets.js via subprocess instead of reading datasets_export_raw.json",
    )
    args = parser.parse_args()

    print("Loading raw dataset export...")
    if args.from_node:
        data = load_raw_data_from_node()
    else:
        data = load_raw_data()
    sources = data["sources"]

    # Create output directory
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print("\nExtracting and writing datasets:\n")

    # Persons
    persons = extract_persons(sources)
    write_json(persons, OUTPUT_DIR / "persons.json")
    write_persons_csv(persons, OUTPUT_DIR / "persons.csv")

    # Locations
    locations = extract_locations(sources)
    write_json(locations, OUTPUT_DIR / "locations.json")
    write_locations_csv(locations, OUTPUT_DIR / "locations.csv")

    # Parties
    parties = extract_parties(sources)
    write_json(parties, OUTPUT_DIR / "parties.json")
    write_parties_csv(parties, OUTPUT_DIR / "parties.csv")

    # MPs
    mps = extract_mps(sources)
    write_json(mps, OUTPUT_DIR / "mps.json")
    write_mps_csv(mps, OUTPUT_DIR / "mps.csv")

    print(f"\nDone! All dataset files written to {OUTPUT_DIR.relative_to(SERVICE_ROOT)}/")
    print(f"  Total persons:   {len(persons)}")
    print(f"  Total locations: {len(locations)}")
    print(f"  Total parties:   {len(parties)}")
    print(f"  Total MPs:       {len(mps)}")


if __name__ == "__main__":
    main()
