import csv
import json
from pathlib import Path

FIELDS = ["id", "category", "status", "minutes"]

def read_records(path):
    path = Path(path)
    suffix = path.suffix.lower()

    if suffix not in (".csv", ".json"):
        raise ValueError("Unsupported input format")

    if suffix == ".csv":
        with path.open("r", newline="", encoding="utf-8") as file:
            reader = csv.DictReader(file, strict=True)

            if reader.fieldnames is None or set(reader.fieldnames) != set(FIELDS):
                raise ValueError("CSV header is wrong")

            records = []
            for row in reader:
                if None in row or None in row.values():
                    raise ValueError("CSV row width does not match the header")
                records.append(row)

            return records

    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)

    if not isinstance(data, list):
        raise ValueError("JSON root")

    return data


def write_reports(result, out_dir):
    out_dir.mkdir(parents=True, exist_ok=True)

    with (out_dir / "accepted.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(result["accepted"])

    with (out_dir / "rejected.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["row", "reason"])
        writer.writeheader()
        writer.writerows(result["rejected"])

    with (out_dir / "summary.json").open("w", encoding="utf-8") as handle:
        json.dump(result["summary"], handle, indent=2)