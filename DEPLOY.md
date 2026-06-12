# Deploy guide — World Cup Betting Pool

Stack: Streamlit Community Cloud (app) + Supabase (database) + Splitwise (ledger)
+ TheSportsDB (fixtures/results, free). Total cost: ₹0.

---

## 1. Supabase (~5 min)

1. Go to supabase.com → New project (any name, e.g. `wc-bets`). Pick a region (Mumbai exists).
2. Left sidebar → **SQL Editor** → New query → paste the whole of `schema.sql` → **Run**.
   You should see "Success" and three tables under Table Editor.
3. Left sidebar → **Project Settings → API**. Copy two things:
   - **Project URL** → this is `SUPABASE_URL`
   - **service_role key** (under "Project API keys") → this is `SUPABASE_KEY`
   (service_role is fine here because only the Streamlit server ever holds it —
   users never see it. Don't put it in any client-side code.)

## 2. Splitwise (~5 min)

1. Go to **secure.splitwise.com/apps** → "Register your application" →
   fill anything (name: wc-bets, homepage: your future app URL) → after creating,
   you'll see an **API key / personal access token**. Copy it → `SPLITWISE_API_KEY`.
2. Get the group id: open your friends' group on splitwise.com in a browser —
   the URL looks like `secure.splitwise.com/#/groups/12345678`.
   That number → `SPLITWISE_GROUP_ID`.
3. Make sure all ~20 friends are members of that group (the app's name dropdown
   comes from this member list).

## 3. Push the code to GitHub (~3 min)

```bash
cd worldcup-bets
git init && git add . && git commit -m "wc betting pool v1"
# create an empty repo on github.com (e.g. akshat0-lgtm/wc-bets), then:
git remote add origin https://github.com/akshat0-lgtm/wc-bets.git
git branch -M main && git push -u origin main
```

Note: `.streamlit/secrets.toml.example` is a TEMPLATE and safe to push.
Never create/push a real `secrets.toml`. (Add `.streamlit/secrets.toml` to
`.gitignore` if you run locally.)

## 4. Streamlit Community Cloud (~5 min)

1. share.streamlit.io → sign in with GitHub → **New app** →
   repo `akshat0-lgtm/wc-bets`, branch `main`, file `app.py` → Deploy.
2. While it builds: app menu (⋮) → **Settings → Secrets** → paste the contents
   of `secrets.toml.example` **with real values filled in**:

```toml
JOIN_CODE = "whatever-you-tell-friends"
ADMIN_CODE = "something-only-you-know"
SUPABASE_URL = "https://xxxx.supabase.co"
SUPABASE_KEY = "eyJ...service_role..."
SPLITWISE_API_KEY = "..."
SPLITWISE_GROUP_ID = "12345678"
TSDB_WC_LEAGUE_ID = "4429"
```

3. Save → the app reboots with secrets. Open the URL, enter your ADMIN_CODE,
   pick your name → you should see the **🔧 Admin** tab.

Optional email (Gmail): myaccount.google.com → Security → 2-Step Verification →
**App passwords** → create one for "Mail" → add to secrets:

```toml
SMTP_USER = "you@gmail.com"
SMTP_PASSWORD = "abcd efgh ijkl mnop"
SUMMARY_EMAIL_TO = "you@gmail.com"
```

## 5. First-run sanity checks (do these before sharing the link)

1. **Fixtures**: Admin → "Fetch fixtures from TheSportsDB" → today's date.
   - World Cup games appear → great, vendor confirmed.
   - Nothing appears → the league id may differ; verify by opening
     `https://www.thesportsdb.com/api/v1/json/123/search_all_leagues.php?c=FIFA`
     in a browser and looking for FIFA World Cup's `idLeague`, update
     `TSDB_WC_LEAGUE_ID` in secrets. Or just use "Add a game manually" — the
     whole app works fine on manual games.
2. **Odds**: Admin → Reference odds → enter the bookmaker numbers for tonight
   (grab them from any odds site; they're display-only).
3. **Bet test**: open the app in an incognito window, enter the JOIN_CODE,
   pick a friend's name, place a ₹10 test bet. Check it lands in Supabase
   (Table Editor → bets).
4. **Settlement dry run**: add a manual game with kickoff 10 minutes ago,
   place two opposing ₹10 bets from two names, enter a result, Settle & post.
   Confirm the expense shows up in Splitwise, then delete it in Splitwise.

## 6. Matchday routine (~5 min/day)

| When            | What you do                                                  |
|-----------------|--------------------------------------------------------------|
| Morning         | Admin → fetch fixtures for today (or add manually). Enter reference odds. |
| Anytime         | Friends bet. Nothing for you to do. Bets auto-close T-5.     |
| After full-time | Admin → Results → "Fetch score" (or type it). Then **Settle & post**. |
| Then            | Copy the generated summary into WhatsApp. Done.              |

## Troubleshooting

- **"Couldn't load the Splitwise group"** — API key or group id wrong, or the
  key lost access. Re-check secrets.
- **Splitwise post failed** — nothing is marked settled in that case; fix the
  issue and press Settle again (safe to retry).
- **Friend's name missing from dropdown** — they're not in the Splitwise group.
  Add them there; the dropdown refreshes within ~10 min (cached) or on app reboot.
- **App sleeps** (Streamlit free tier sleeps after inactivity) — first visitor
  wakes it in ~30s. Harmless.
- **Supabase pauses after ~7 days of zero activity** (free tier) — won't happen
  during the tournament; if it does, un-pause from the dashboard.
