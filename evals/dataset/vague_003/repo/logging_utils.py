import datetime


def stamp(message):
    return f"[{datetime.datetime.now(datetime.timezone.utc).isoformat()}] {message}"
