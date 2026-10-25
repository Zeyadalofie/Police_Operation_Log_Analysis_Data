import csv
import sys
from pathlib import Path
from rules import process_records
from storage import read_records, write_reports

BASE = Path(__file__).resolve().parent


def main(argv):
    if len(argv) != 2:
        print("Usage: python app.py INPUT OUTPUT_FOLDER", file=sys.stderr)
        return 2

    source = (BASE / argv[0]).resolve()
    out_dir = (BASE / argv[1]).resolve()

    try:
        if not source.is_file():
            raise FileNotFoundError(f"Input file not found: {source}")

        report_file = {
            (out_dir / "accepted.csv").resolve(),
            (out_dir / "rejected.csv").resolve(),
            (out_dir / "summary.json").resolve()
        }

        if source in report_file:
            raise ValueError("input/output collision")

        record = read_records(source)
        result = process_records(record)
        write_reports(result, out_dir)

    except (OSError, ValueError, csv.Error) as error:
        print(f"Cannot complete run: {error}", file=sys.stderr)
        return 2

    print(
        f"Run complete: {len(result['accepted'])} accepted, "
        f"{len(result['rejected'])} rejected. Reports saved to {out_dir}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
