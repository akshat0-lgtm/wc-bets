"""World Cup betting pool — Streamlit app.
User flow: join code -> pick your name (Splitwise group) -> bet on tonight's games.
Admin flow (separate code): manage games, odds, results, settle night -> Splitwise.
"""
import smtplib
import hashlib, hmac, os, binascii
from email.mime.text import MIMEText
from datetime import datetime, timezone, timedelta, time
from zoneinfo import ZoneInfo

import streamlit as st

import db
import fixtures
import splitwise_client as sw
from betting_core import (betting_open, result_outcome, ou25_outcome,
                          settle_night)

IST = ZoneInfo("Asia/Kolkata")
CLOSE_MIN = 5          # bets close T-5
MAX_BET = 2000
WINDOW_HOURS = 36      # how far ahead games are shown

st.set_page_config(page_title="WC Betting Pool", page_icon="⚽", layout="centered")


# ---------------- helpers ----------------

def now_utc():
    return datetime.now(timezone.utc)

def kick(g):
    return datetime.fromisoformat(g["kickoff_utc"])

def ist(dt):
    return dt.astimezone(IST).strftime("%a %d %b, %I:%M %p IST")

def pool_summary(bets, market, options):
    rows = [b for b in bets if b["market"] == market]
    total = sum(float(b["amount"]) for b in rows)
    out = {}
    for o in options:
        side = sum(float(b["amount"]) for b in rows if b["pick"] == o)
        out[o] = (side, (side / total * 100) if total else 0.0)
    return out, total

def label(g, o):
    return {"home": g["home"], "draw": "Draw", "away": g["away"],
            "over": "Over 2.5", "under": "Under 2.5"}[o]


TEAM_ISO = {
    "qatar": "QA", "switzerland": "CH", "canada": "CA", "usa": "US",
    "united states": "US", "paraguay": "PY", "brazil": "BR", "morocco": "MA",
    "south korea": "KR", "korea republic": "KR", "czech republic": "CZ",
    "czechia": "CZ", "bosnia-herzegovina": "BA", "bosnia and herzegovina": "BA",
    "argentina": "AR", "france": "FR", "england": "GB", "spain": "ES",
    "portugal": "PT", "germany": "DE", "netherlands": "NL", "belgium": "BE",
    "croatia": "HR", "italy": "IT", "uruguay": "UY", "colombia": "CO",
    "mexico": "MX", "japan": "JP", "senegal": "SN", "ghana": "GH",
    "nigeria": "NG", "cameroon": "CM", "egypt": "EG", "tunisia": "TN",
    "algeria": "DZ", "ivory coast": "CI", "denmark": "DK", "sweden": "SE",
    "poland": "PL", "serbia": "RS", "wales": "GB", "scotland": "GB",
    "ecuador": "EC", "peru": "PE", "chile": "CL", "australia": "AU",
    "iran": "IR", "saudi arabia": "SA", "qatar": "QA", "costa rica": "CR",
    "panama": "PA", "jamaica": "JM", "honduras": "HN", "new zealand": "NZ",
    "norway": "NO", "austria": "AT", "turkey": "TR", "ukraine": "UA",
    "greece": "GR", "russia": "RU", "south africa": "ZA", "cape verde": "CV",
    "curacao": "CW", "haiti": "HT", "jordan": "JO", "uzbekistan": "UZ",
}


def flag(team):
    iso = TEAM_ISO.get((team or "").strip().lower())
    if not iso:
        return ""
    return "".join(chr(0x1F1E6 + ord(c) - ord("A")) for c in iso.upper())


def bet_label(g, o):
    if o == "home":
        return f"{flag(g['home'])} {g['home']}".strip()
    if o == "away":
        return f"{flag(g['away'])} {g['away']}".strip()
    return {"draw": "🤝 Draw", "over": "Over 2.5", "under": "Under 2.5"}[o]


def closes_in_text(kickoff_dt):
    secs = int(((kickoff_dt - timedelta(minutes=CLOSE_MIN)) - now_utc()).total_seconds())
    if secs <= 0:
        return None
    h, m = secs // 3600, (secs % 3600) // 60
    return f"{h}h {m}m" if h else f"{m}m"




def project_payouts(bets, market, options):
    """Per-person upside once the pool is locked. Each person bet one side;
    returns their payout IF their pick wins (else they lose their stake)."""
    rows = [b for b in bets if b["market"] == market]
    pool = sum(float(b["amount"]) for b in rows)
    by_side = {o: sum(float(b["amount"]) for b in rows if b["pick"] == o) for o in options}
    out = []
    for b in rows:
        a = float(b["amount"])
        side = by_side[b["pick"]]
        payout = (a / side * pool) if side else 0.0
        out.append({"uid": b["splitwise_user_id"], "name": b["user_name"],
                    "pick": b["pick"], "stake": a,
                    "win_payout": payout, "win_net": payout - a})
    return out, pool


def projection_text(g, rows, pool, market_name="Result"):
    lines = [f"🔒 {market_name} — {g['home']} vs {g['away']}",
             f"Total pool: ₹{pool:.0f}", "",
             "If your pick wins:"]
    for r in sorted(rows, key=lambda r: -r["win_payout"]):
        lines.append(f"{r['name']}: ₹{r['stake']:.0f} on {label(g, r['pick'])} "
                     f"→ +₹{r['win_net']:.0f} (gets ₹{r['win_payout']:.0f} back)")
    return "\n".join(lines)


def send_email(subject, body):
    if "SMTP_USER" not in st.secrets:
        return False, "SMTP not configured"
    try:
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = st.secrets["SMTP_USER"]
        msg["To"] = st.secrets.get("SUMMARY_EMAIL_TO", st.secrets["SMTP_USER"])
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
            s.login(st.secrets["SMTP_USER"], st.secrets["SMTP_PASSWORD"])
            s.send_message(msg)
        return True, "sent"
    except Exception as e:
        return False, str(e)


# ---------------- auth ----------------

def _hash_pw(password, salt=None):
    if salt is None:
        salt = binascii.hexlify(os.urandom(16)).decode()
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000)
    return salt, binascii.hexlify(dk).decode()

def _verify_pw(password, salt, expected):
    _, h = _hash_pw(password, salt)
    return hmac.compare_digest(h, expected)



if "who" not in st.session_state:
    st.session_state.who = None
if "admin" not in st.session_state:
    st.session_state.admin = False

if st.session_state.who is None:
    st.title("⚽ World Cup Betting Pool")
    code = st.text_input("League code", type="password")
    if not code:
        st.stop()
    if code != st.secrets["JOIN_CODE"] and code != st.secrets["ADMIN_CODE"]:
        st.error("Wrong code — not allowed.")
        st.stop()
    is_admin_code = (code == st.secrets["ADMIN_CODE"])

    try:
        members = sw.group_members()
    except Exception as e:
        st.error(f"Couldn't load the Splitwise group: {e}")
        st.stop()

    if st.session_state.get("pending") is None:
        st.write("Who are you?")
        cols = st.columns(2)
        for i, m in enumerate(members):
            if cols[i % 2].button(m["name"], key=f"who-{m['id']}", use_container_width=True):
                st.session_state.pending = m
                st.session_state.pending_admin = is_admin_code
                st.rerun()
        st.caption("Name not in the list? You're not in the Splitwise group yet — "
                   "ping Akshat to add you, then refresh.")
        st.stop()

    m = st.session_state.pending
    auth = db.get_auth(m["id"])
    st.write(f"Hi **{m['name']}**")

    if auth is None:
        st.info("First time — set a password. You'll use it to log in from now on.")
        p1 = st.text_input("New password", type="password", key="np1")
        p2 = st.text_input("Confirm password", type="password", key="np2")
        c1, c2 = st.columns([3, 1])
        if c1.button("Set password & enter", type="primary", use_container_width=True):
            if len(p1) < 4:
                st.error("Use at least 4 characters.")
            elif p1 != p2:
                st.error("Passwords don't match.")
            else:
                salt, h = _hash_pw(p1)
                db.set_auth(m["id"], m["name"], salt, h)
                st.session_state.who = m
                st.session_state.admin = st.session_state.pending_admin
                st.session_state.pending = None
                st.rerun()
        if c2.button("Back", use_container_width=True):
            st.session_state.pending = None
            st.rerun()
    else:
        pw = st.text_input("Your password", type="password", key="lp")
        c1, c2 = st.columns([3, 1])
        if c1.button("Enter", type="primary", use_container_width=True):
            if _verify_pw(pw, auth["salt"], auth["pw_hash"]):
                st.session_state.who = m
                st.session_state.admin = st.session_state.pending_admin
                st.session_state.pending = None
                st.rerun()
            else:
                st.error("Wrong password.")
        if c2.button("Back", use_container_width=True):
            st.session_state.pending = None
            st.rerun()
    st.stop()

who = st.session_state.who
st.title("⚽ World Cup Betting Pool")
st.caption(f"Betting as **{who['name']}** · bets close {CLOSE_MIN} min before kickoff · "
           f"max ₹{MAX_BET} per bet · payouts are pari-mutuel (pool-based)")

tabs = ["Tonight's games", "My bets", "🏆 Leaderboard"]
if st.session_state.admin:
    tabs.append("🔧 Admin")
tab_objs = st.tabs(tabs)


# ---------------- tab: tonight's games ----------------

with tab_objs[0]:
    games = [g for g in db.list_games(statuses=["upcoming"])
             if now_utc() < kick(g) < now_utc() + timedelta(hours=WINDOW_HOURS)]
    if not games:
        st.info("No upcoming games in the next day or so. Check back later!")
    st.session_state.setdefault("open_game", None)
    all_bets = db.bets_for_games([g["id"] for g in games])
    for g in games:
        is_open = betting_open(kick(g), close_buffer_min=CLOSE_MIN)
        bets = [b for b in all_bets if b["game_id"] == g["id"]]
        cd = closes_in_text(kick(g))
        opened = st.session_state.open_game == g["id"]
        with st.container(border=True):
            head, btn = st.columns([4, 1])
            with head:
                st.markdown(f"**{flag(g['home'])} {g['home']}  vs  {flag(g['away'])} {g['away']}**")
                game_pool = sum(float(b["amount"]) for b in bets)
                n_people = len({b["splitwise_user_id"] for b in bets})
                stake_line = (f" · 💰 ₹{game_pool:.0f} pool · "
                              f"👥 {n_people} in") if bets else ""
                if is_open and cd:
                    st.caption(f"⏰ Closes in {cd}{stake_line}")
                else:
                    st.caption(f"🔒 Betting closed{stake_line}")
            if btn.button("Close ▾" if opened else "Bet ▸",
                          key=f"toggle-{g['id']}", use_container_width=True):
                st.session_state.open_game = None if opened else g["id"]
                st.rerun()

            if opened:
                for market, options in (("result", ["home", "draw", "away"]),
                                        ("ou25", ["over", "under"])):
                    st.markdown("**🏆 Bet 1 · Match result** — who wins?"
                                if market == "result"
                                else "**⚽ Bet 2 · Total goals** — over or under 2.5?")
                    rows = [b for b in bets if b["market"] == market]

                    if is_open:
                        mine = next((b for b in rows if b["splitwise_user_id"] == who["id"]), None)
                        skey = f"{g['id']}-{market}"
                        pick_key = f"pickval-{skey}"
                        st.session_state.setdefault(
                            pick_key, mine["pick"] if mine else options[0])
                        st.markdown("👉 **Pick a side**")
                        pcols = st.columns(len(options), gap="medium")
                        for pci, o in enumerate(options):
                            sel = st.session_state[pick_key] == o
                            cnt = sum(1 for b in rows if b["pick"] == o)
                            lbl = f"{bet_label(g, o)}  ·  👤 {cnt}"
                            if pcols[pci].button(lbl, key=f"pickbtn-{skey}-{o}",
                                                 use_container_width=True,
                                                 type="primary" if sel else "secondary"):
                                st.session_state[pick_key] = o
                                st.rerun()
                        pick = st.session_state[pick_key]
                        amt_key = f"amt-{skey}"
                        st.session_state.setdefault(
                            amt_key, int(float(mine["amount"])) if mine else 100)
                        st.markdown("💰 **Bet amount (₹)**")
                        chips = st.columns(4)
                        for ci, v in enumerate([100, 250, 500, 1000]):
                            if v <= MAX_BET and chips[ci].button(
                                    f"₹{v}", key=f"chip-{skey}-{v}", use_container_width=True):
                                st.session_state[amt_key] = v
                                st.rerun()
                        amt = st.number_input("Amount ₹", min_value=50, max_value=MAX_BET,
                                              step=50, key=amt_key, label_visibility="collapsed")
                        if st.button("Update bet" if mine else "Place bet",
                                     key=f"place-{skey}", type="primary",
                                     use_container_width=True):
                            if not betting_open(kick(g), close_buffer_min=CLOSE_MIN):
                                st.error("Betting just closed for this game.")
                            else:
                                db.upsert_bet(g["id"], who["id"], who["name"],
                                              market, pick, int(amt))
                                st.success(f"Bet saved: ₹{int(amt)} on {label(g, pick)}")
                                st.rerun()
                        n = len(rows)
                        extra = " · ✅ your bet is in" if mine else ""
                        st.caption(f"🔒 Pool unlocks at kickoff · "
                                   f"🔥 {n} bet{'s' if n != 1 else ''} placed{extra}")
                    else:
                        pools, total = pool_summary(bets, market, options)
                        cols = st.columns(len(options))
                        for c, o in zip(cols, options):
                            side, pct = pools[o]
                            cnt = sum(1 for b in rows if b["pick"] == o)
                            c.metric(label(g, o), f"pool ₹{side:.0f}",
                                     f"{pct:.0f}% · {cnt} 👤", delta_color="off")
                    st.divider()


# ---------------- tab: my bets ----------------

with tab_objs[1]:
    mine = db.bets_for_user(who["id"])
    if not mine:
        st.info("No bets yet.")
    else:
        games_by_id = {g["id"]: g for g in db.list_games()}
        for b in sorted(mine, key=lambda b: games_by_id[b["game_id"]]["kickoff_utc"],
                        reverse=True):
            g = games_by_id[b["game_id"]]
            st.write(f"• **{g['home']} vs {g['away']}** — "
                     f"{'Result' if b['market']=='result' else 'O/U 2.5'}: "
                     f"₹{float(b['amount']):.0f} on {label(g, b['pick'])} "
                     f"({g['status']})")
    st.divider()
    st.markdown("**Past settlements**")
    for s in db.list_settlements():
        nets = s["nets"]
        you = nets.get(who["name"])
        if you is not None:
            st.write(f"• {s['night_label']}: you "
                     f"{'won ₹' + str(you) if you >= 0 else 'lost ₹' + str(-you)}")


# ---------------- tab: leaderboard ----------------

with tab_objs[2]:
    st.markdown("### 🏆 Tournament standings")
    settlements = db.list_settlements()
    if not settlements:
        st.info("No settled games yet — standings appear after the first payout.")
    else:
        totals, played = {}, {}
        for s in settlements:
            for name, net in (s["nets"] or {}).items():
                totals[name] = totals.get(name, 0) + int(net)
                played[name] = played.get(name, 0) + 1
        ranked = sorted(totals.items(), key=lambda kv: (-kv[1], kv[0]))
        medals = {0: "🥇", 1: "🥈", 2: "🥉"}
        for idx, (name, net) in enumerate(ranked):
            tag = medals.get(idx, f"{idx+1}.")
            mark = "  ← you" if name == who["name"] else ""
            sign = "+" if net >= 0 else "−"
            dot = "🟢" if net >= 0 else "🔴"
            st.write(f"{tag}  **{name}**{mark} — {dot} {sign}₹{abs(net)} "
                     f"`({played[name]} night{'s' if played[name] != 1 else ''})`")
        st.caption(f"{len(settlements)} night(s) settled so far.")


# ---------------- tab: admin ----------------

if st.session_state.admin:
    with tab_objs[-1]:
        st.markdown("### Games")

        with st.expander("Fetch fixtures from TheSportsDB"):
            c1, c2 = st.columns(2)
            rnd = c1.number_input("Matchday / round", min_value=1, max_value=20, value=1)
            d = c2.date_input("Show only this IST day",
                              value=now_utc().astimezone(IST).date())
            st.caption("Pulls the whole round (beats the free-tier per-day cap), "
                       "then keeps games on the IST day you picked.")
            if st.button("Fetch round → add games for this IST day"):
                try:
                    allgames = fixtures.fetch_round(int(rnd))
                except Exception as e:
                    st.error(f"Fetch failed: {e}")
                    allgames = []
                # keep games whose kickoff falls on the chosen IST calendar day
                found = [f for f in allgames if f["kickoff_utc"] and
                         datetime.fromisoformat(f["kickoff_utc"]).astimezone(IST).date() == d]
                if not found:
                    st.warning(f"No round-{int(rnd)} games on {d} (IST). Try another "
                               "round/day, or add manually below.")
                for f in found:
                    f["status"] = "upcoming"
                    f.pop("home_score", None); f.pop("away_score", None)
                    db.upsert_game(f)
                    st.success(f"Added: {f['home']} vs {f['away']} "
                               f"({ist(datetime.fromisoformat(f['kickoff_utc']))})")

        with st.expander("Add a game manually"):
            with st.form("manual-game"):
                h = st.text_input("Home team")
                a = st.text_input("Away team")
                dt = st.date_input("Kickoff date (IST)")
                tm = st.time_input("Kickoff time (IST)")
                if st.form_submit_button("Add game") and h and a:
                    k = datetime.combine(dt, tm, tzinfo=IST).astimezone(timezone.utc)
                    gid = f"manual-{h[:3]}{a[:3]}-{k.strftime('%m%d%H%M')}".lower()
                    db.upsert_game({"id": gid, "home": h, "away": a,
                                    "kickoff_utc": k.isoformat(), "status": "upcoming"})
                    st.success(f"Added {h} vs {a} — {ist(k)}")

        st.markdown("### Reference odds (manual for now)")
        for g in db.list_games(statuses=["upcoming"]):
            ref = g.get("ref_odds") or {}
            with st.expander(f"{g['home']} vs {g['away']} — {ist(kick(g))}"):
                with st.form(f"odds-{g['id']}"):
                    c = st.columns(5)
                    vals = {}
                    for i, o in enumerate(["home", "draw", "away", "over", "under"]):
                        vals[o] = c[i].number_input(label(g, o), min_value=1.01,
                                                    value=float(ref.get(o) or 2.0),
                                                    step=0.05, key=f"o-{g['id']}-{o}")
                    if st.form_submit_button("Save reference odds"):
                        db.set_ref_odds(g["id"], {k: round(v, 2) for k, v in vals.items()})
                        st.success("Saved.")

        st.markdown("### Locked pools — projected payouts")
        any_locked = False
        for g in db.list_games(statuses=["upcoming"]):
            if betting_open(kick(g), close_buffer_min=CLOSE_MIN):
                continue  # betting still open
            bets = db.bets_for_game(g["id"])
            markets_def = [("Match result", "result", ["home", "draw", "away"]),
                           ("Total goals O/U 2.5", "ou25", ["over", "under"])]
            parts, total_pool = [], 0.0
            for mname, mkey, mopts in markets_def:
                rows, pool = project_payouts(bets, mkey, mopts)
                if rows:
                    parts.append(projection_text(g, rows, pool, mname))
                    total_pool += pool
            if not parts:
                continue
            any_locked = True
            with st.expander(f"{g['home']} vs {g['away']} — pool ₹{total_pool:.0f}"):
                txt = "\n\n".join(parts)
                st.code(txt, language=None)
                if st.button("📧 Email these projections", key=f"proj-{g['id']}"):
                    ok, info = send_email(
                        f"Locked pool — {g['home']} vs {g['away']}", txt)
                    if ok:
                        st.success("Emailed ✉️")
                    else:
                        st.warning(f"Email failed: {info}")
        if not any_locked:
            st.caption("Nothing locked yet — projections appear here once a game's betting closes.")

        st.markdown("### Results")
        for g in db.list_games(statuses=["upcoming", "result_in"]):
            if kick(g) > now_utc():
                continue
            with st.expander(f"{g['home']} vs {g['away']} — enter/refresh result"):
                if not str(g["id"]).startswith("manual-"):
                    if st.button("Fetch score from TheSportsDB", key=f"fs-{g['id']}"):
                        hs, as_, status = fixtures.fetch_result(g["id"])
                        if hs is None:
                            st.warning(f"No score yet (status: {status}).")
                        else:
                            db.set_result(g["id"], hs, as_)
                            st.success(f"{g['home']} {hs}-{as_} {g['away']}")
                            st.rerun()
                with st.form(f"res-{g['id']}"):
                    c1, c2 = st.columns(2)
                    hs = c1.number_input(g["home"], min_value=0, step=1,
                                         value=g.get("home_score") or 0)
                    as_ = c2.number_input(g["away"], min_value=0, step=1,
                                          value=g.get("away_score") or 0)
                    if st.form_submit_button("Save result"):
                        db.set_result(g["id"], int(hs), int(as_))
                        st.success("Result saved.")
                        st.rerun()

        st.markdown("### Reset a user's password")
        auths = db.list_auth()
        if not auths:
            st.caption("No passwords set yet.")
        else:
            opts = {f"{a['user_name']}": a["splitwise_user_id"] for a in auths}
            choice = st.selectbox("Whose password to clear?",
                                  ["— select —"] + list(opts.keys()), key="pwreset")
            if choice != "— select —":
                st.warning(f"This clears **{choice}**'s password. They'll set a new one "
                           "next time they log in (first to set it owns it again).")
                if st.button("Delete this password", key="delpw"):
                    db.delete_auth(opts[choice])
                    st.success(f"Password cleared for {choice}.")
                    st.rerun()

        st.markdown("### Settle the night → Splitwise")
        ready = db.list_games(statuses=["result_in"])
        if not ready:
            st.info("No games with results awaiting settlement.")
        else:
            chosen = st.multiselect(
                "Games to settle together (one Splitwise expense)",
                options=[g["id"] for g in ready],
                default=[g["id"] for g in ready],
                format_func=lambda gid: next(
                    f"{g['home']} {g['home_score']}-{g['away_score']} {g['away']}"
                    for g in ready if g["id"] == gid))
            night_label = st.text_input(
                "Night label", value=f"WC betting — {now_utc().astimezone(IST):%d %b}")
            if chosen and st.button("⚖️ Settle & post to Splitwise", type="primary"):
                markets, lines = [], []
                for gid in chosen:
                    g = db.get_game(gid)
                    bets = db.bets_for_game(gid)
                    hs, as_ = g["home_score"], g["away_score"]
                    lines.append(f"{g['home']} {hs}-{as_} {g['away']}")
                    res_bets = [{"user": b["user_name"], "pick": b["pick"],
                                 "amount": float(b["amount"])}
                                for b in bets if b["market"] == "result"]
                    ou_bets = [{"user": b["user_name"], "pick": b["pick"],
                                "amount": float(b["amount"])}
                               for b in bets if b["market"] == "ou25"]
                    if res_bets:
                        markets.append((res_bets, result_outcome(hs, as_)))
                    if ou_bets:
                        markets.append((ou_bets, ou25_outcome(hs, as_)))
                nets = settle_night(markets)
                if not nets or all(v == 0 for v in nets.values()):
                    st.warning("Nothing to settle (no bets, or all markets void/refunded).")
                    for gid in chosen:
                        db.set_status(gid, "settled")
                else:
                    members = sw.group_members()
                    ids = {m["name"]: m["id"] for m in members}
                    missing = [n for n in nets if n not in ids]
                    if missing:
                        st.error(f"Not in Splitwise group anymore: {missing} — fix and retry.")
                        st.stop()
                    try:
                        exp_id = sw.post_night_expense(nets, ids, night_label)
                    except Exception as e:
                        st.error(f"Splitwise post failed (nothing marked settled): {e}")
                        st.stop()
                    for gid in chosen:
                        db.set_status(gid, "settled")
                    db.record_settlement(night_label, chosen, nets, exp_id)

                    summary = [f"🏆 {night_label}", *lines, ""]
                    for n, v in sorted(nets.items(), key=lambda kv: -kv[1]):
                        summary.append(f"{'🟢' if v >= 0 else '🔴'} {n}: "
                                       f"{'+' if v >= 0 else '−'}₹{abs(v)}")
                    summary.append("\nPosted to Splitwise ✅")
                    text = "\n".join(summary)
                    st.success(f"Settled and posted (expense {exp_id}).")
                    st.code(text, language=None)
                    st.caption("Copy the summary above into the WhatsApp group.")

                    ok, info = send_email(night_label, text)
                    if ok:
                        st.success("Summary emailed ✉️")
                    elif info != "SMTP not configured":
                        st.warning(f"Email failed (Splitwise already posted fine): {info}")