"""
See the WhatsApp conversations people have had with the assistant.

Every message in and out is saved to the message_log table by the webhook; this
script just prints them in a readable way.

Usage:
    python view_messages.py                 # all recent conversations
    python view_messages.py 27831234567     # only this person's messages
    python view_messages.py --limit 100     # show more (default 50 per person)
    python view_messages.py --watch         # keep printing new messages live
"""

import sys
import time
from datetime import datetime

from dotenv import load_dotenv

load_dotenv()

from db import get_connection

ARROWS = {"in": "→", "out": "←"}  # → user to us, ← us to user


def _fetch(number: str | None, limit: int, after_id: int = 0) -> list[dict]:
    sql = "SELECT id, wa_from, direction, kind, body, created_at FROM message_log WHERE id > %s"
    params: list = [after_id]
    if number:
        sql += " AND wa_from = %s"
        params.append(number)
    sql += " ORDER BY id DESC LIMIT %s"
    params.append(int(limit))

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
    finally:
        conn.close()
    return list(reversed(rows))  # oldest first for reading


def _fmt(row: dict) -> str:
    when = row["created_at"]
    stamp = when.strftime("%d %b %H:%M") if isinstance(when, datetime) else str(when)
    arrow = ARROWS.get(row["direction"], "?")
    who = "User" if row["direction"] == "in" else "Bot "
    body = (row["body"] or "").replace("\n", "\n         ")
    return f"[{stamp}] {arrow} {who}: {body}"


def show(number: str | None, limit: int) -> None:
    rows = _fetch(number, limit)
    if not rows:
        print("No messages yet.")
        return

    # Group by person so each conversation reads as a block.
    people: dict[str, list] = {}
    for r in rows:
        people.setdefault(r["wa_from"], []).append(r)

    for wa, msgs in people.items():
        print("\n" + "=" * 60)
        print(f"Conversation with +{wa}  ({len(msgs)} messages)")
        print("=" * 60)
        for m in msgs:
            print(_fmt(m))


def watch(number: str | None) -> None:
    print("Watching for new messages (Ctrl+C to stop)...\n")
    last = 0
    # Prime with the most recent id so we only show *new* messages from here.
    seen = _fetch(number, 1)
    if seen:
        last = seen[-1]["id"]
    try:
        while True:
            rows = _fetch(number, 50, after_id=last)
            for r in rows:
                print(_fmt(r))
                last = max(last, r["id"])
            time.sleep(3)
    except KeyboardInterrupt:
        print("\nStopped.")


def main() -> None:
    args = sys.argv[1:]
    number = None
    limit = 50
    do_watch = False

    i = 0
    while i < len(args):
        a = args[i]
        if a == "--limit" and i + 1 < len(args):
            limit = int(args[i + 1]); i += 2; continue
        if a == "--watch":
            do_watch = True; i += 1; continue
        number = a.lstrip("+"); i += 1

    if do_watch:
        watch(number)
    else:
        show(number, limit)


if __name__ == "__main__":
    main()
