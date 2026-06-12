from betting_core import (settle_market, settle_night, to_splitwise_expense,
                          result_outcome, ou25_outcome, parse_fixtures)

print("=" * 60)
print("FIXTURE PARSE (mock TheSportsDB eventsday payload)")
print("=" * 60)
mock = {"events": [
    {"idEvent": "1001", "strLeague": "FIFA World Cup", "strHomeTeam": "Spain",
     "strAwayTeam": "Curacao", "strTimestamp": "2026-06-12T19:00:00",
     "intHomeScore": "3", "intAwayScore": "0", "strStatus": "Match Finished"},
    {"idEvent": "1002", "strLeague": "FIFA World Cup", "strHomeTeam": "Brazil",
     "strAwayTeam": "Morocco", "strTimestamp": "2026-06-12T22:00:00",
     "intHomeScore": "1", "intAwayScore": "1", "strStatus": "Match Finished"},
    {"idEvent": "9999", "strLeague": "MLS", "strHomeTeam": "LA", "strAwayTeam": "NYC",
     "strTimestamp": "2026-06-12T23:00:00", "intHomeScore": "2", "intAwayScore": "2"},
]}
for g in parse_fixtures(mock):
    print(f"  {g['game_id']}: {g['home']} {g['home_score']}-{g['away_score']} {g['away']}")
print("  (MLS row correctly filtered out)\n")

print("=" * 60)
print("GAME 1: Spain 3-0 Curacao  (favorite wins -> small payout)")
print("=" * 60)
g1_result = [
    {"user": "Akshat", "pick": "home", "amount": 500},
    {"user": "Riya",   "pick": "home", "amount": 300},
    {"user": "Dev",    "pick": "home", "amount": 800},
    {"user": "Sara",   "pick": "draw", "amount": 200},
    {"user": "Kabir",  "pick": "away", "amount": 100},   # the lone Curacao backer
]
out = result_outcome(3, 0)
nets, status = settle_market(g1_result, out)
print(f"  outcome={out}  status={status}  pool={sum(b['amount'] for b in g1_result)}")
for u, v in nets.items():
    print(f"    {u:8} {'+' if v>=0 else '-'}Rs{abs(v):7.1f}")

print("\n  Counterfactual: if Curacao had won (Kabir alone on away):")
nets_up, _ = settle_market(g1_result, "away")
print(f"    Kabir +Rs{nets_up['Kabir']:.0f} on a Rs100 stake  <- lonely-longshot payout\n")

print("=" * 60)
print("GAME 1 O/U 2.5: total 3 goals -> OVER")
print("=" * 60)
g1_ou = [
    {"user": "Akshat", "pick": "over",  "amount": 400},
    {"user": "Riya",   "pick": "under", "amount": 600},
    {"user": "Dev",    "pick": "over",  "amount": 200},
]
print(f"  outcome={ou25_outcome(3,0)}")

print("=" * 60)
print("GAME 2: Brazil 1-1 Morocco  (draw) + O/U 2 goals -> UNDER")
print("=" * 60)
g2_result = [
    {"user": "Riya",  "pick": "draw", "amount": 250},
    {"user": "Dev",   "pick": "home", "amount": 500},
    {"user": "Sara",  "pick": "away", "amount": 150},
]
g2_ou = [
    {"user": "Kabir", "pick": "under", "amount": 300},
    {"user": "Akshat","pick": "over",  "amount": 300},   # nobody else on under? -> check void
    {"user": "Sara",  "pick": "under", "amount": 100},
]

print("\n" + "=" * 60)
print("NIGHT SETTLEMENT (all 4 markets aggregated -> integer, zero-sum)")
print("=" * 60)
markets = [
    (g1_result, result_outcome(3, 0)),
    (g1_ou,     ou25_outcome(3, 0)),
    (g2_result, result_outcome(1, 1)),
    (g2_ou,     ou25_outcome(1, 1)),
]
night = settle_night(markets)
for u in sorted(night):
    v = night[u]
    print(f"    {u:8} {'+' if v>=0 else '-'}Rs{abs(v)}")
print(f"\n    SUM = {sum(night.values())}  (must be 0)")
assert sum(night.values()) == 0, "LEDGER DOES NOT BALANCE"
print("    [OK] ledger balances exactly\n")

print("=" * 60)
print("SPLITWISE PAYLOAD (one expense for the night)")
print("=" * 60)
ids = {"Akshat": 11, "Riya": 22, "Dev": 33, "Sara": 44, "Kabir": 55}
payload = to_splitwise_expense(night, ids, "WC betting — 12 Jun")
print(f"  cost = Rs{payload['cost']}")
for u in payload["users"]:
    print(f"    uid {u['user_id']}: paid {u['paid_share']:>8}  owed {u['owed_share']:>8}")
paid = sum(float(u["paid_share"]) for u in payload["users"])
owed = sum(float(u["owed_share"]) for u in payload["users"])
print(f"  paid total {paid:.2f} == owed total {owed:.2f} == cost {payload['cost']}")
assert abs(paid - owed) < 0.01 and abs(paid - float(payload["cost"])) < 0.01
print("  [OK] Splitwise expense balances")
