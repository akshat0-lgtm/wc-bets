"""World Cup betting pool — Streamlit app.
User flow: join code -> pick your name (Splitwise group) -> bet on tonight's games.
Admin flow (separate code): manage games, odds, results, settle night -> Splitwise.
"""
import smtplib
import hashlib, hmac, os, binascii
from email.mime.text import MIMEText
from datetime import datetime, timezone, timedelta
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

tabs = ["Tonight's games", "My bets"]
if st.session_state.admin:
    tabs.append("🔧 Admin")
tab_objs = st.tabs(tabs)


# ---------------- tab: tonight's games ----------------

with tab_objs[0]:
    games = [g for g in db.list_games(statuses=["upcoming"])
             if now_utc() < kick(g) < now_utc() + timedelta(hours=WINDOW_HOURS)]
    if not games:
        st.info("No upcoming games in the next day or so. Check back later!")
    for g in games:
        is_open = betting_open(kick(g), close_buffer_min=CLOSE_MIN)
        ref = g.get("ref_odds") or {}
        bets = db.bets_for_game(g["id"])
        with st.container(border=True):
            st.subheader(f"{g['home']} vs {g['away']}")
            st.caption(f"Kickoff {ist(kick(g))} · "
                       + ("🟢 betting open" if is_open else "🔒 betting closed"))

            for market, options in (("result", ["home", "draw", "away"]),):
                st.markdown("**Match result**" if market == "result"
                            else "**Total goals (line: 2.5)**")
                pools, total = pool_summary(bets, market, options)
                cols = st.columns(len(options))
                for c, o in zip(cols, options):
                    side, pct = pools[o]
                    refv = ref.get(o)
                    c.metric(label(g, o),
                             f"{refv}x" if refv else "—",
                             f"pool ₹{side:.0f} · {pct:.0f}%", delta_color="off")
                st.caption(f"Boxes show **reference odds** (bookmaker line) — your real "
                           f"payout is pari-mutuel on the final pool (₹{total:.0f} so far) "
                           f"and is fixed only when betting closes.")

                if is_open:
                    mine = next((b for b in bets if b["splitwise_user_id"] == who["id"]
                                 and b["market"] == market), None)
                    with st.form(f"bet-{g['id']}-{market}", border=False):
                        c1, c2, c3 = st.columns([2, 1, 1])
                        pick = c1.selectbox("Pick", options,
                                            format_func=lambda o, g=g: label(g, o),
                                            index=options.index(mine["pick"]) if mine else 0,
                                            key=f"p-{g['id']}-{market}")
                        amt = c2.number_input("₹", min_value=50, max_value=MAX_BET,
                                              value=int(float(mine["amount"])) if mine else 100,
                                              key=f"a-{g['id']}-{market}")
                        placed = c3.form_submit_button(
                            "Update bet" if mine else "Place bet", use_container_width=True)
                        if placed:
                            # re-check window server-side at write time (stale-tab guard)
                            if not betting_open(kick(g), close_buffer_min=CLOSE_MIN):
                                st.error("Betting just closed for this game.")
                            else:
                                db.upsert_bet(g["id"], who["id"], who["name"],
                                              market, pick, int(amt))
                                st.success(f"Bet saved: ₹{int(amt)} on {label(g, pick)}")
                                st.rerun()
                    if mine:
                        st.caption(f"Your current bet: ₹{float(mine['amount']):.0f} on "
                                   f"{label(g, mine['pick'])} — submit again to change it, "
                                   f"any time before close.")
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


# ---------------- tab: admin ----------------

if st.session_state.admin:
    with tab_objs[-1]:
        st.markdown("### Games")

        with st.expander("Fetch fixtures from TheSportsDB"):
            d = st.date_input("Date (UTC)", value=now_utc().date())
            if st.button("Fetch World Cup games for this date"):
                try:
                    found = fixtures.fetch_day(d.isoformat())
                except Exception as e:
                    st.error(f"Fetch failed: {e}")
                    found = []
                if not found:
                    st.warning("No World Cup games returned — check TSDB_WC_LEAGUE_ID "
                               "in secrets, or add the game manually below.")
                for f in found:
                    f["status"] = "upcoming"
                    f.pop("home_score", None); f.pop("away_score", None)
                    db.upsert_game(f)
                    st.success(f"Added: {f['home']} vs {f['away']} ({f['kickoff_utc']})")

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

                    if "SMTP_USER" in st.secrets:
                        try:
                            msg = MIMEText(text)
                            msg["Subject"] = night_label
                            msg["From"] = st.secrets["SMTP_USER"]
                            msg["To"] = st.secrets.get("SUMMARY_EMAIL_TO",
                                                       st.secrets["SMTP_USER"])
                            with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
                                s.login(st.secrets["SMTP_USER"],
                                        st.secrets["SMTP_PASSWORD"])
                                s.send_message(msg)
                            st.success("Summary emailed ✉️")
                        except Exception as e:
                            st.warning(f"Email failed (Splitwise already posted fine): {e}")
