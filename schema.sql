-- Run this once in Supabase: SQL Editor -> New query -> paste -> Run

create table if not exists games (
  id            text primary key,            -- TheSportsDB idEvent, or 'manual-<n>'
  home          text not null,
  away          text not null,
  kickoff_utc   timestamptz not null,
  status        text not null default 'upcoming',  -- upcoming | result_in | settled | void
  home_score    int,
  away_score    int,
  ref_odds      jsonb default '{}'::jsonb,   -- {"home":1.15,"draw":8.0,"away":18.0,"over":1.9,"under":1.9}
  created_at    timestamptz default now()
);

create table if not exists bets (
  id                 bigint generated always as identity primary key,
  game_id            text not null references games(id) on delete cascade,
  splitwise_user_id  bigint not null,
  user_name          text not null,
  market             text not null check (market in ('result','ou25')),
  pick               text not null check (pick in ('home','draw','away','over','under')),
  amount             numeric not null check (amount > 0 and amount <= 2000),
  updated_at         timestamptz default now(),
  unique (game_id, splitwise_user_id, market)   -- one bet per person per market; re-bet = overwrite
);

create table if not exists settlements (
  id           bigint generated always as identity primary key,
  night_label  text not null,
  game_ids     text[] not null,
  nets         jsonb not null,                -- {"Akshat": 194, "Riya": -50, ...}
  splitwise_expense_id text,
  created_at   timestamptz default now()
);
