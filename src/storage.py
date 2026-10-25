"""Cloud storage naming and metadata helpers."""
import json
from pathlib import Path

def s3_report_key(user_id, filename):
    return f"users/{user_id}/{Path(filename).name}"

def drive_file_metadata(filename):
    return json.dumps({"name": Path(filename).name, "mimeType": "application/json"})
