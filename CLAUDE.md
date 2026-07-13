# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

This is the complete context for the InsightMatch football monitoring & prediction system. Any AI agent working in this repo must read this first and follow it exactly.

## Who the user is

Nayef — the owner and sole operator. **No coding experience.** Always explain in plain language, provide step-by-step instructions for anything manual, and never assume he can debug code himself. He works mostly from an iPhone, sometimes a Mac. Communication may be voice-transcribed (tolerate imprecise wording; confirm intent when ambiguous).

## What this system is

A fully automated football match monitoring, alerting, and self-learning prediction system running entirely on free/low-cost infrastructure:

- **GitHub Actions** (public repo = unlimited minutes) — all automation. Python 3.12, sole dependency: `requests` (installed per-run in workflows; there is no requirements.txt)
- **API-Football** (api-sports.io, **Pro plan: 7,500 requests/day**) — match data. Key in GitHub Secret `API_FOOTBALL_KEY`
- **Anthropic Claude API** (model `claude-haiku-4-5-20251001`, set as `CLAUDE_MODEL` in each script) — all analysis & predictions. Key in Secret `ANTHROPIC_API_KEY`
- **Telegram bot** — alert delivery. `TELEGRAM_TOKEN` + `TELEGRAM_CHAT_ID` in Secrets
- **GitHub Pages dashboard** — https://insightmatch0-cpu.github.io/insight-match-monitor/

## Repo files

| File | Purpose |
|---|---|
| `monitor.py` | Polls live matches every 20 min; Telegram alerts for match start / goals / full-time, each with Claude analysis (max 20 analyses/run via `MAX_ANALYSES_PER_RUN`; `ANALYZE_ALL=True` analyzes every league, `ALERTS_TOP_ONLY=False` is the emergency flood switch). Stores display fields incl. team/league logo URLs in `state.json` |
| `scan.py` | On-demand worldwide live scan ("مسح حي" command) — one Actions button, one API call, one batched Claude call covering up to `MAX_PREDICTIONS=50` matches, numbered-line reply format |
| `predict.py` | Daily engine (Engine 1): resolves prior predictions against real results (≤3 API calls via `MAX_RESOLVE_CALLS`), computes accuracy stats, predicts next-24h matches (up to `MAX_PREDICTIONS_24H=60`) in Claude batches of `BATCH_SIZE=12`, injects recent news headlines from `news.json` as context, sends Telegram digest (top leagues only — `DIGEST_TOP_ONLY=True` — with a dashboard link for the rest) |
| `predict_v2.py` | Daily Engine 2 (V2) — see the "Engine 2 (V2)" section below. Same fixture selection/exclusions as V1, model `claude-fable-5`, probability output, enriched context for top leagues, own memory `predictions_v2.json` + `lessons_v2.json` |
| `dashboard_update.py` | Builds `data.json` for the dashboard from `state.json` + `predictions.json`, then `data_v2.json` from `predictions_v2.json` (same schema; `live`/`news` empty — dashboard takes those from `data.json`). `data_v2.json` is only created once V2 has real data, and only rewritten when content changes (so monitor runs don't dirty the tree). Also refreshes `news.json` from free RSS feeds (BBC Arabic, BBC Sport, Sky, Guardian) at most every 3h. Runs after monitor and both predict engines; costs zero API-Football budget |
| `index.html` | The dashboard (GitHub Pages). Arabic RTL default, EN toggle. Broadcast-scoreboard design with an **Engine 1 / Engine 2 tab switcher**. The V2 tab reads `data_v2.json` and shows an "under construction" panel until that file exists. Reads `data.json` with 90s auto-refresh |
| `Index.html` | ⚠️ Stray near-empty file (capital I, whitespace only). NOT the dashboard — don't confuse it with `index.html`. Safe to delete if the user agrees |
| `state.json` | Live-match memory between monitor runs (auto-committed) |
| `predictions.json` | **The learning memory**: `pending` (awaiting results), `resolved` (graded history, capped at 1000), `meta.stats`. Pending entries are dropped without grading if postponed/cancelled or older than 3 days |
| `predictions_v2.json` | Engine 2's learning memory — same structure as `predictions.json`, fully separate. Created on the first V2 run |
| `lessons_v2.json` | `{"lessons": []}` — Phase 3 will fill it with lessons extracted from V2's wrong predictions; `predict_v2.py` already injects the most recent 15 into every prompt under "دروس من أخطائك السابقة" |
| `news.json` | Cached RSS headlines (max 15, ≤3h old); shown on the dashboard AND injected into prediction prompts (both engines) |
| `data.json` | Generated dashboard payload: `live`, `upcoming`, `recent_results` (last 20), `accuracy`, `news` (auto-committed) |
| `data_v2.json` | Generated Engine 2 dashboard payload (same schema, `live`/`news` empty). Does not exist until V2's first successful run |
| `.github/workflows/monitor.yml` | Cron `7,27,47 * * * *` (every 20 min) + manual button; runs monitor then dashboard_update, commits state |
| `.github/workflows/predict.yml` | Cron `15 3 * * *` (06:15 AM KSA daily) + manual button; runs predict then dashboard_update, commits data |
| `.github/workflows/predict_v2.yml` | Cron `30 3 * * *` (06:30 AM KSA daily, 15 min after V1) + manual button; runs predict_v2 then dashboard_update, commits V2 data |
| `.github/workflows/scan.yml` | Manual button only; commits nothing |

## The self-learning loop (core logic — preserve it)

1. Every prediction is stored with pick (`home|draw|away`) + confidence (prompt asks for 40–80; parser clamps to 30–85).
2. Next morning, `predict.py` fetches real results and grades each prediction.
3. Accuracy is computed: overall, last 30 days, top vs other leagues, daily series (last 30 days), and **by confidence bucket** (70+, 60-69, 50-59, <50).
4. That track record is injected into every new Claude prediction prompt with the instruction: *if your real accuracy is below your stated confidence, lower your confidence — and vice versa.* This calibration IS the learning mechanism. Never remove it.

## Engine 2 (V2)

The second-generation prediction engine (`predict_v2.py`), built to run side-by-side with Engine 1 for a fair, direct comparison. Design decisions — preserve them:

- **Same fixtures as V1**: identical next-24h selection, exclusion lists, `TOP_LEAGUE_IDS`, and `MAX_PREDICTIONS_24H=60`, so accuracy numbers are comparable engine-to-engine.
- **Model**: `claude-fable-5` (V1 stays on Haiku).
- **Enriched context for top leagues**: for up to `MAX_ENRICHED_FIXTURES=15` top-league fixtures, extra API-Football data is fetched before predicting — standings of both teams (1 call per league, cached per run), head-to-head last 5, each team's last 5 results, and injuries for the fixture. Capped by `ENRICH_CALL_BUDGET=120` calls/run as a safety net; enrichment failures degrade gracefully to a basic prediction (never kill the run). Non-top fixtures are predicted with basic data only.
- **Probability output**: each prediction returns `prob_home`/`prob_draw`/`prob_away` as integers summing to 100 (parser normalizes if they don't). `pick` = highest probability; `confidence` = that probability clamped 30–85. Probabilities are stored in pending AND resolved entries.
- **Batching**: enriched fixtures in batches of `ENRICHED_BATCH_SIZE=4` (context is bulky), basic fixtures in batches of `BASIC_BATCH_SIZE=12` like V1. Strict JSON output, tolerant parser — same rule 6.
- **Own memory**: `predictions_v2.json` (`pending`/`resolved`/`meta.stats`), fully separate from V1, with the exact same grading, resolution, and calibration-stats logic — the calibration record is injected into every V2 prompt just like V1.
- **Lessons loop (Phase 3 hook)**: `lessons_v2.json` holds `{"lessons": []}`; when non-empty, the most recent `MAX_LESSONS_IN_PROMPT=15` are injected into every prompt under the header "دروس من أخطائك السابقة". Phase 3 will generate these from graded mistakes.
- **Telegram digest** is labeled "🤖 المحرك 2" so V1 and V2 messages are distinguishable; it also shows the three probabilities per match.
- **Budget**: 2 fixture-date calls + ≤3 resolve calls + enrichment (typically ~60-75, hard-capped at 120) ≈ well under 130/day on top of V1's 5.

## Analysis methodology (the user's expert framework — apply in all analysis)

**Pre-match factors:** league position; motivation (title race / qualification / relegation); first or second leg + first-leg result; home/away records; current streaks; head-to-head; key injuries & suspensions; goals scored/conceded (attack vs defense); form trajectory last 5–6 games; manager situation (new appointment bounce / under pressure); fixture congestion & rotation risk before bigger matches; derby/clásico factor (these defy logic).

**Live-match additions:** xG; shots on/off target; possession *tied to real danger* (possession alone deceives); key passes; dangerous attacks (note surges in final 10–15 min); corners; red cards (who, when, position); current minute; score-line behavior (is the leader defending or pushing); substitutions (extra striker vs closing the game).

**Output format for any prediction:** short focused analysis of relevant factors only → clear final verdict: winner or draw + confidence % → for live matches, likely next scenario. Occasionally remind (briefly, not every reply): predictions are analytical opinions, never guarantees.

## Hard rules (never violate)

1. **Coverage exclusions:** friendlies, African competitions (CAF/AFCON/keyword "africa"), and all leagues from India, Pakistan, Bangladesh. The `EXCLUDED_COUNTRIES` / `EXCLUDED_LEAGUE_KEYWORDS` lists (and `TOP_LEAGUE_IDS`) are duplicated in `monitor.py`, `scan.py`, `predict.py`, and `predict_v2.py` — any change must be applied to all four in sync.
2. **Language convention:** ALL Telegram and dashboard-facing output in Arabic — team/league/country names in standard sports-media Arabic — but numerals always Latin digits (0-9), never Arabic-Indic (٠-٩). Arabic names come from Claude calls and are cached (in `state.json` / `predictions.json`) to avoid repeat calls.
3. **Secrets discipline:** keys live ONLY in GitHub Secrets. Never in code, files, chats, screenshots, or logs. **Never print a key or embed it in an error message** (this caused a real leak once — see history). All scripts `.strip()` env values to survive pasted whitespace.
4. **API budget awareness:** monitor ≈72 calls/day (3 runs/hour × 1 call), predict ≤5/day (2 fixture-date calls + up to 3 resolve calls), predict_v2 ≤130/day (same 5 + enrichment capped at 120). Pro plan allows 7,500/day so there is huge headroom now, but keep calls efficient and batched.
5. **Empty data ≠ error:** no live matches can simply mean rest day / off-season. Interpret correctly and pivot to upcoming fixtures instead of reporting failure. (`scan.py` already sends a friendly "no live matches" message; `monitor.py` exits cleanly.)
6. Claude batch predictions must return **strict JSON** (no fences, no prose); parsers are tolerant (fence-stripping, bracket extraction) but don't rely on it. `scan.py` uses a numbered `N| ... | ...` line format instead — equally strict.

## The "مسح حي" command

When the user says "مسح حي" (or "مسح" / "شنو الشغال الحين"): run the Live Scan workflow → all live matches worldwide (1200+ leagues), exclusions applied, quick one-line prediction + confidence per match, top leagues first, delivered to Telegram, ending with a prompt for which match to analyze in full detail.

## Project history & lessons learned

- `scan.yml` was once nested at `.github/workflows/.github/workflows/` — invisible to Actions. Fixed. Watch for path mistakes.
- Secrets once contained: (a) a trailing newline that broke HTTP headers, (b) the football key pasted into the Anthropic secret. Both diagnosed via a temporary debug workflow. Scripts now strip whitespace.
- A debug script once leaked a key into a committed file via an exception message; history was force-push scrubbed and the key rotated. Hence rule 3 above.
- API keys were exposed in a screenshot early on and rotated. Assume any key that ever appeared in plain text is dead.
- Local standalone HTML was tried and abandoned: iOS Files preview doesn't execute JavaScript. GitHub Pages is the chosen architecture.
- `monitor.yml`, `predict.yml`, and `predict_v2.yml` share concurrency group `football-monitor` and use `git pull --rebase` before push to avoid commit races; all commit with `[skip ci]`. `scan.yml` commits nothing so it has no concurrency group.

## Roadmap (user's stated ambitions)

- **Engine 2**: Phase 2 (the engine itself) is DONE — see the "Engine 2 (V2)" section. Phase 3 remains: generate lessons from V2's graded mistakes into `lessons_v2.json`
- Expand news/insight sources feeding prediction context (RSS headlines are already injected into `predict.py` prompts via `news.json`; more feeds, injuries, team news are wanted)
- Deeper pre-match data per fixture (standings, H2H via API-Football — budget now allows it)
- Keep improving calibration; the user's dream is maximum realistic accuracy — be honest that world-class models hit ~55-60% on 1X2 and never promise more
- Geopolitics was discussed and deprioritized: near-zero effect on match outcomes; keep at most as a side news feed
- Always design so new APIs/sources can be plugged in easily

## How to work in this repo

- Test Python changes locally before committing: `python -m py_compile <file>.py` at minimum; mock-data runs where possible. Scripts exit early without secrets, so full end-to-end runs happen in Actions.
- Manual runs: Actions tab → workflow → Run workflow. Verify results via `data.json` / `predictions.json` in the repo, not assumptions.
- `state.json`, `data.json`, `data_v2.json`, `news.json`, `predictions.json`, `predictions_v2.json` are bot-written and auto-committed by workflows — expect them to change under you; `git pull --rebase` before pushing.
- Any new alert type must follow the Arabic output convention and respect exclusions.
- When something fails silently, make it fail loudly first (raise with a clear Arabic message, as `predict.py` does for API failures), diagnose, then fix — but never let error text include secret values.
- Code comments and docstrings are in Arabic — keep that style when editing.
