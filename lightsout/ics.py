"""iCalendar feed. Calendar apps poll it; UIDs stay stable so updates replace entries."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from .series import BY_ID
from .watch import watch_for


def _esc(text: str) -> str:
    return (text or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _fold(line: str) -> str:
    """RFC 5545: lines over 75 octets continue on the next line with a leading space."""
    raw = line.encode()
    if len(raw) <= 75:
        return line
    parts, chunk = [], b""
    for ch in line:
        b = ch.encode()
        if len(chunk) + len(b) > (75 if not parts else 74):
            parts.append(chunk.decode())
            chunk = b""
        chunk += b
    parts.append(chunk.decode())
    return "\r\n ".join(parts)


def _utc(value: str) -> str:
    return value.replace("-", "").replace(":", "")


def build(sessions: list[dict], base_url: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Lights Out//EN",
        "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
        "X-WR-CALNAME:Lights Out", "X-WR-CALDESC:Racing sessions from Lights Out",
        "REFRESH-INTERVAL;VALUE=DURATION:PT6H", "X-PUBLISHED-TTL:PT6H",
    ]
    for s in sessions:
        series = BY_ID[s["series"]]
        watch = watch_for(s["series"], s["kind"], s.get("tv"))
        desc = [f"{series.name} - {s['event_name']}"]
        if s["venue"]:
            desc.append(s["venue"])
        if watch:
            desc.append(f"Watch (US): {watch['badge']} - {watch['label']}" + (f" ({watch['note']})" if watch.get("note") else ""))
        if not s["start_utc"] and s["kind"] not in ("rally_day", "event"):
            desc.append("Session time to be announced.")
        desc.append(f"{base_url}/#/series/{s['series']}")
        lines += ["BEGIN:VEVENT", f"UID:{s['series']}-{s['event_key']}-{s['key']}@lightsout", f"DTSTAMP:{stamp}"]
        if s["start_utc"]:
            lines += [f"DTSTART:{_utc(s['start_utc'])}", f"DTEND:{_utc(s['end_utc'])}"]
        else:
            first = date.fromisoformat(s["day"])
            last = date.fromisoformat(s["end_day"]) if s["end_day"] else first
            lines += [f"DTSTART;VALUE=DATE:{first:%Y%m%d}", f"DTEND;VALUE=DATE:{last + timedelta(days=1):%Y%m%d}",
                      "TRANSP:TRANSPARENT"]
        lines += [
            f"SUMMARY:{_esc(f'{series.short}: {s['event_name']} - {s['name']}')}",
            f"DESCRIPTION:{_esc(chr(10).join(desc))}",
            f"LOCATION:{_esc(', '.join(x for x in (s['venue'], s['country']) if x))}",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(line) for line in lines) + "\r\n"
