"""
Splitwise World Cup betting — core logic (vendor-agnostic, no UI).
Tested standalone. The Streamlit/Supabase wiring sits on top of this later.
"""
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime, timezone, timedelta


# ---------- 1. FIXTURES (TheSportsDB eventsday shape) ----------

WC_LEAGUE_NAMES = ("FIFA World Cup", "World Cup")  # filter; pin idLeague once confirmed

def parse_fixtures(payload, league_names=WC_LEAGUE_NAMES):
    """Turn TheSportsDB eventsday JSON into clean game dicts (World Cup only)."""
    games = []
    for e in (payload.get("events") or []):
        if not any(n in (e.get("strLeague") or "") for n in league_names):
            continue
        ts = e.get("strTimestamp")
        kickoff = datetime.fromisoformat(ts).replace(tzinfo=timezone.utc) if ts else None
        games.append({
            "game_id": e["idEvent"],
            "home": e["strHomeTeam"],
            "away": e["strAwayTeam"],
            "kickoff_utc": kickoff,
            "home_score": _int_or_none(e.get("intHomeScore")),
            "away_score": _int_or_none(e.get("intAwayScore")),
            "status": e.get("strStatus"),
        })
    return games

def _int_or_none(x):
    try:
        return int(x)
    except (TypeError, ValueError):
        return None


# ---------- 2. BETTING WINDOW ----------

def betting_open(kickoff_utc, now=None, close_buffer_min=5):
    """Bets close T-5. Computed on every request (Streamlit has no scheduler)."""
    now = now or datetime.now(timezone.utc)
    return now < kickoff_utc - timedelta(minutes=close_buffer_min)

def upcoming_games(games, now=None, window_hours=30):
    """Show only matches that haven't kicked off and start within the window.
    Avoids a date-picker and the IST-vs-North-America matchday split."""
    now = now or datetime.now(timezone.utc)
    return [g for g in games if g["kickoff_utc"] and
            now < g["kickoff_utc"] < now + timedelta(hours=window_hours)]


# ---------- 3. RESOLVING MARKETS ----------

def result_outcome(home_score, away_score):
    if home_score > away_score: return "home"
    if home_score < away_score: return "away"
    return "draw"

def ou25_outcome(home_score, away_score):
    return "over" if (home_score + away_score) > 2.5 else "under"


# ---------- 4. PARI-MUTUEL SETTLEMENT (one market) ----------

def settle_market(bets, outcome):
    """bets: [{'user','pick','amount'}]. Returns ({user: net_float}, status).
    Pari-mutuel: winners split the whole pool pro-rata; zero-sum by construction."""
    pool = sum(b["amount"] for b in bets)
    if pool == 0:
        return {}, "no_bets"
    win_stake = sum(b["amount"] for b in bets if b["pick"] == outcome)
    if win_stake == 0:
        # Nobody picked the winner -> void, refund everyone (net 0).
        return {b["user"]: 0.0 for b in bets}, "void_no_winner"
    nets = {}
    for b in bets:
        if b["pick"] == outcome:
            payout = b["amount"] / win_stake * pool
            nets[b["user"]] = payout - b["amount"]
        else:
            nets[b["user"]] = -b["amount"]
    status = "all_winners" if win_stake == pool else "settled"
    return nets, status


# ---------- 5. NIGHT AGGREGATION + INTEGER ROUNDING ----------

def settle_night(markets):
    """markets: [(bets, outcome), ...] across all games + both market types.
    Returns integer per-user nets that sum to exactly zero (one Splitwise expense)."""
    raw = {}
    for bets, outcome in markets:
        nets, _ = settle_market(bets, outcome)
        for u, v in nets.items():
            raw[u] = raw.get(u, 0.0) + v
    return _round_zero_sum(raw)

def _round_zero_sum(raw):
    """Round each net to whole rupees, then push the leftover rupee(s) onto the
    biggest winner so the ledger still closes at zero."""
    rounded = {u: int(Decimal(v).quantize(Decimal("1"), ROUND_HALF_UP)) for u, v in raw.items()}
    residual = -sum(rounded.values())
    if residual and rounded:
        # deterministic: largest net, tie-break alphabetically
        target = sorted(rounded, key=lambda u: (-rounded[u], u))[0]
        rounded[target] += residual
    return rounded


# ---------- 6. SPLITWISE PAYLOAD (one expense for the night) ----------

def to_splitwise_expense(net_by_user, user_ids, description):
    """Map zero-sum nets to a Splitwise expense.
    Winners 'paid' their winnings; losers 'owe' their losses; balances == nets."""
    cost = sum(v for v in net_by_user.values() if v > 0)
    users = []
    for name, net in net_by_user.items():
        users.append({
            "user_id": user_ids[name],
            "paid_share":  f"{net:.2f}" if net > 0 else "0.00",
            "owed_share":  f"{-net:.2f}" if net < 0 else "0.00",
        })
    return {"cost": f"{cost:.2f}", "description": description, "users": users}
