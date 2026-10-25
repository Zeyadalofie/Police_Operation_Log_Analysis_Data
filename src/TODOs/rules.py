FIELDS = {"id", "category", "status", "minutes"}
CATEGORIES = ("assistance", "traffic", "property")
STATUSES = ("open", "closed")

def normalize_record(record):
    if type(record) is not dict or set(record) != FIELDS:
        raise ValueError("record shape")

    identifier = record["id"]
    if type(identifier) is not str:
        raise ValueError("id format")
    if not identifier or " " in identifier or not any(
        char.isascii() and char.isdigit() for char in identifier
    ):
        raise ValueError("id format")

    category = record["category"]
    if type(category) is not str:
        raise ValueError("category value")
    category = category.strip().lower()
    if category not in CATEGORIES:
        raise ValueError("category value")

    status = record["status"]
    if type(status) is not str:
        raise ValueError("status value")
    status = status.strip().lower()
    if status not in STATUSES:
        raise ValueError("status value")

    minutes = record["minutes"]
    if type(minutes) is int:
        if minutes < 0:
            raise ValueError("minutes range")
    elif type(minutes) is str:
        minutes = minutes.strip()
        if not minutes or not minutes.isascii() or not minutes.isdecimal() or (len(minutes) > 1 and minutes.startswith("0")):
            raise ValueError("minutes format")
        minutes = int(minutes)
        if minutes < 0:
            raise ValueError("minutes range")
    else:
        raise ValueError("minutes type")

    return {
        "id": identifier,
        "category": category,
        "status": status,
        "minutes": minutes,
    }


def build_summary(accepted, rejected_count):
    total_minutes = sum(record["minutes"] for record in accepted)
    mean_minutes = total_minutes / len(accepted) if accepted else None

    by_status = {"open": 0, "closed": 0}
    by_category = {"assistance": 0, "traffic": 0, "property": 0}

    for record in accepted:
        by_status[record["status"]] += 1
        by_category[record["category"]] += 1

    return {
        "dataset": "synthetic_training_only",
        "accepted_count": len(accepted),
        "rejected_count": rejected_count,
        "total_minutes": total_minutes,
        "mean_minutes": mean_minutes,
        "by_status": by_status,
        "by_category": by_category,
    }


def process_records(records):
    accepted, rejected = [], []

    for row_number, record in enumerate(records, start=1):
        try:
            clean = normalize_record(record)
        except ValueError as error:
            rejected.append({
                "row": row_number,
                "reason": str(error),
            })
        else:
            accepted.append(clean)
            
    return {
        "accepted": accepted,
        "rejected": rejected,
        "summary": build_summary(accepted, len(rejected))
    }


    

