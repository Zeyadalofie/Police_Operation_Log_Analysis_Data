"""Helpers for user-specific AI/cloud connection records."""

def ai_config_for_user(user_id, db_factory, open_secret):
    with db_factory() as connection:
        row = connection.execute("SELECT ai FROM connections WHERE user_id=?", (user_id,)).fetchone()
    return open_secret(row["ai"]) if row and row["ai"] else None

def public_connection_details(row, open_secret):
    ai = open_secret(row["ai"]) if row["ai"] else None
    s3 = open_secret(row["s3"]) if row["s3"] else None
    drive = open_secret(row["drive"]) if row["drive"] else None
    return {
        "ai": {"connected": bool(ai), "provider": ai.get("provider") if ai else None, "model": ai.get("model") if ai else None, "base_url": ai.get("base_url") if ai else None},
        "google_drive": {"connected": bool(drive), "email": drive.get("email") if drive else None},
        "s3": {"connected": bool(s3), "bucket": s3.get("bucket") if s3 else None},
        "active_storage": row["active_storage"],
    }
