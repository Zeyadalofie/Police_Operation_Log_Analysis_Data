"""Cloud storage naming and metadata helpers."""
import json
from pathlib import Path

def s3_report_key(user_id, filename, prefix=""):
    clean_parts = [
        part.strip()
        for part in str(prefix).replace("\\", "/").split("/")
        if part.strip() and part.strip() not in (".", "..")
    ]
    return "/".join(["users", str(user_id), *clean_parts, Path(filename).name])


def drive_file_metadata(filename, parent_id=None, mime_type="application/json"):
    metadata = {"name": Path(filename).name, "mimeType": mime_type}
    if parent_id:
        metadata["parents"] = [parent_id]
    return json.dumps(metadata)
