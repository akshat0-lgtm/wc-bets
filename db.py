"""Thin Supabase wrapper. All reads/writes go through here."""
from supabase import create_client
import streamlit as st


@st.cache_resource
def client():
    return create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])


# ---------- games ----------

def upsert_game(game: dict):
    client().table("games").upsert(game).execute()

def list_games(statuses=None):
    q = client().table("games").select("*").order("kickoff_utc")
    if statuses:
        q = q.in_("status", statuses)
    return q.execute().data

def get_game(game_id: str):
    rows = client().table("games").select("*").eq("id", game_id).execute().data
    return rows[0] if rows else None

def set_result(game_id: str, home_score: int, away_score: int):
    client().table("games").update(
        {"home_score": home_score, "away_score": away_score, "status": "result_in"}
    ).eq("id", game_id).execute()

def set_status(game_id: str, status: str):
    client().table("games").update({"status": status}).eq("id", game_id).execute()

def set_ref_odds(game_id: str, ref_odds: dict):
    client().table("games").update({"ref_odds": ref_odds}).eq("id", game_id).execute()


# ---------- bets ----------

def upsert_bet(game_id, sw_user_id, user_name, market, pick, amount):
    client().table("bets").upsert(
        {"game_id": game_id, "splitwise_user_id": sw_user_id, "user_name": user_name,
         "market": market, "pick": pick, "amount": amount},
        on_conflict="game_id,splitwise_user_id,market",
    ).execute()

def delete_bet(game_id, sw_user_id, market):
    client().table("bets").delete().eq("game_id", game_id)\
        .eq("splitwise_user_id", sw_user_id).eq("market", market).execute()

def bets_for_game(game_id: str):
    return client().table("bets").select("*").eq("game_id", game_id).execute().data

def bets_for_user(sw_user_id: int):
    return client().table("bets").select("*").eq("splitwise_user_id", sw_user_id).execute().data


# ---------- settlements ----------

def record_settlement(night_label, game_ids, nets, expense_id):
    client().table("settlements").insert(
        {"night_label": night_label, "game_ids": game_ids,
         "nets": nets, "splitwise_expense_id": str(expense_id)}
    ).execute()

def list_settlements():
    return client().table("settlements").select("*").order("created_at", desc=True).execute().data


# ---------- per-user passwords ----------

def get_auth(sw_user_id: int):
    rows = client().table("user_auth").select("*")\
        .eq("splitwise_user_id", sw_user_id).execute().data
    return rows[0] if rows else None

def set_auth(sw_user_id, user_name, salt, pw_hash):
    client().table("user_auth").upsert(
        {"splitwise_user_id": sw_user_id, "user_name": user_name,
         "salt": salt, "pw_hash": pw_hash}).execute()


def list_auth():
    return client().table("user_auth").select(
        "splitwise_user_id, user_name").order("user_name").execute().data

def delete_auth(sw_user_id):
    client().table("user_auth").delete().eq("splitwise_user_id", sw_user_id).execute()

def bets_for_games(game_ids: list):
    if not game_ids:
        return []
    return client().table("bets").select("*").in_("game_id", game_ids).execute().data