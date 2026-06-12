"""Splitwise API client. Needs SPLITWISE_API_KEY (personal access token)
and SPLITWISE_GROUP_ID in Streamlit secrets."""
import requests
import streamlit as st

BASE = "https://secure.splitwise.com/api/v3.0"


def _headers():
    return {"Authorization": f"Bearer {st.secrets['SPLITWISE_API_KEY']}"}


@st.cache_data(ttl=600)
def group_members():
    """[{'id': 123, 'name': 'Akshat N.'}] for the configured group."""
    gid = st.secrets["SPLITWISE_GROUP_ID"]
    r = requests.get(f"{BASE}/get_group/{gid}", headers=_headers(), timeout=15)
    r.raise_for_status()
    members = r.json()["group"]["members"]
    out = []
    for m in members:
        name = m.get("first_name") or ""
        if m.get("last_name"):
            name += f" {m['last_name']}"
        out.append({"id": m["id"], "name": name.strip() or f"user-{m['id']}"})
    return sorted(out, key=lambda x: x["name"].lower())


def post_night_expense(net_by_user: dict, user_ids: dict, description: str):
    """net_by_user: {name: int_net} summing to 0. Winners are payers, losers owers.
    Returns the created expense id. Raises on any Splitwise error."""
    cost = sum(v for v in net_by_user.values() if v > 0)
    if cost <= 0:
        raise ValueError("Nothing to post — all nets are zero.")
    data = {
        "cost": f"{cost:.2f}",
        "description": description,
        "group_id": st.secrets["SPLITWISE_GROUP_ID"],
        "currency_code": "INR",
        "split_equally": "false",
    }
    i = 0
    for name, net in net_by_user.items():
        if net == 0:
            continue  # Splitwise rejects all-zero participants; skip them
        data[f"users__{i}__user_id"] = user_ids[name]
        data[f"users__{i}__paid_share"] = f"{net:.2f}" if net > 0 else "0.00"
        data[f"users__{i}__owed_share"] = f"{-net:.2f}" if net < 0 else "0.00"
        i += 1
    r = requests.post(f"{BASE}/create_expense", headers=_headers(), data=data, timeout=20)
    r.raise_for_status()
    body = r.json()
    if body.get("errors"):
        raise RuntimeError(f"Splitwise error: {body['errors']}")
    return body["expenses"][0]["id"]
