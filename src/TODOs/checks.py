import csv
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from app import main
from generate_data import make_records
from rules import normalize_record, process_records
from storage import read_records, write_reports

BASE = Path(__file__).resolve().parent
EXPECTED = {
    "dataset": "synthetic_training_only",
    "accepted_count": 5, "rejected_count": 3,
    "total_minutes": 120, "mean_minutes": 24.0,
    "by_status": {"open": 2, "closed": 3},
    "by_category": {"assistance": 2, "traffic": 2,
                    "property": 1},
}


def expect_error(action, message):
    try:
        action()
    except ValueError as error:
        assert str(error) == message
    else:
        raise AssertionError(f"Expected {message}")


result = process_records(make_records())
assert result["summary"] == EXPECTED
assert result["rejected"] == [
    {"row": 6, "reason": "status value"},
    {"row": 7, "reason": "minutes format"},
    {"row": 8, "reason": "category value"},
]
for value, message in [(True, "minutes type"),
                       (-5, "minutes range"),
                       ("0000", "minutes format")]:
    row = dict(make_records()[0], minutes=value)
    expect_error(lambda: normalize_record(row), message)
assert process_records([])["summary"]["mean_minutes"] is None

csv_path = BASE / "data/raw/shift.csv"
json_path = BASE / "data/raw/shift.json"
raw_before = [p.read_bytes() for p in (csv_path, json_path)]

csv_result = process_records(read_records(csv_path))
json_result = process_records(read_records(json_path))
assert csv_result == json_result

with TemporaryDirectory(dir=BASE) as name:
    folder = Path(name)
    write_reports(result, folder)

    with (folder / "summary.json").open(encoding="utf-8") as f:
        assert json.load(f) == EXPECTED

    for filename, key in [("accepted.csv", "accepted"),
                          ("rejected.csv", "rejected")]:
        with (folder / filename).open(
            encoding="utf-8", newline=""
        ) as f:
            saved = list(csv.DictReader(f))

        wanted = [{k: str(v) for k, v in row.items()}
                  for row in result[key]]
        assert saved == wanted

    bad = folder / "bad.json"
    bad.write_text("{}", encoding="utf-8")
    expect_error(lambda: read_records(bad), "JSON root")

    reports = [folder / n for n in
               ("accepted.csv", "rejected.csv", "summary.json")]
    before = [p.read_bytes() for p in reports]
    assert main([str(bad), str(folder)]) == 2
    assert [p.read_bytes() for p in reports] == before

assert [p.read_bytes() for p in (csv_path, json_path)] == raw_before
print("Baseline checks passed")