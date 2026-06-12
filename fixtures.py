"""TheSportsDB fetcher. Free public key, no signup.
NOTE: querying all of Soccer truncates on the free key — always filter by league id.
FIFA World Cup league id on TheSportsDB: 4429 (override in secrets if needed)."""
import requests
import streamlit as st
from datetime import datetime, timezone

API_KEY = "123"
BASE = f"https://www.thesportsdb.com/api/v1/json/{API_KEY}"


def _wc_league_id():
    return str(st.secrets.get("TSDB_WC_LEAGUE_ID", "4429"))


def fetch_day(date_str: str):
    """Games for one UTC date (YYYY-MM-DD), World Cup league only.
    Returns list of game dicts ready for db.upsert_game."""
    r = requests.get(f"{BASE}/eventsday.php",
                     params={"d": date_str, "l": _wc_league_id()}, timeout=15)
    r.raise_for_status()
    return [_to_game(e) for e in (r.json().get("events") or [])]


def fetch_result(event_id: str):
    """Refresh one event; returns (home_score, away_score, status) or (None, None, status)."""
    r = requests.get(f"{BASE}/lookupevent.php", params={"id": event_id}, timeout=15)
    r.raise_for_status()
    evs = r.json().get("events") or []
    if not evs:
        return None, None, "not_found"
    e = evs[0]
    hs, as_ = _int(e.get("intHomeScore")), _int(e.get("intAwayScore"))
    return hs, as_, e.get("strStatus") or ""


def _to_game(e):
    ts = e.get("strTimestamp")
    kickoff = datetime.fromisoformat(ts).replace(tzinfo=timezone.utc).isoformat() if ts else None
    return {
        "id": e["idEvent"],
        "home": e["strHomeTeam"],
        "away": e["strAwayTeam"],
        "kickoff_utc": kickoff,
        "home_score": _int(e.get("intHomeScore")),
        "away_score": _int(e.get("intAwayScore")),
    }


def _int(x):
    try:
        return int(x)
    except (TypeError, ValueError):
        return None
