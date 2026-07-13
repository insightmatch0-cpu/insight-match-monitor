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
| `predict.py` | Daily engine: resolves prior predictions against real results (≤3 API calls via `MAX_RESOLVE_CALLS`), computes accuracy stats, predicts next-24h matches (up to `MAX_PREDICTIONS_24H=60`) in Claude batches of `BATCH_SIZE=12`, injects recent news headlines from `news.json` as context, sends Telegram digest (top leagues only — `DIGEST_TOP_ONLY=True` — with a dashboard link for the rest) |
| `dashboard_update.py` | Builds `data.json` for the dashboard from `state.json` + `predictions.json`; refreshes `news.json` from free RSS feeds (BBC Arabic, BBC Sport, Sky, Guardian) at most every 3h. Runs after monitor and predict; costs zero API-Football budget |
| `index.html` | The dashboard (GitHub Pages). Arabic RTL default, EN toggle. Broadcast-scoreboard design with an **Engine 1 / Engine 2 tab switcher**: Engine 1 = the current daily predictions; Engine 2 ("الجيل الذكي") = a placeholder panel for a next-gen prediction engine to run side-by-side for comparison (under construction). Reads `data.json` with 90s auto-refresh |
| `Index.html` | ⚠️ Stray near-empty file (capital I, whitespace only). NOT the dashboard — don't confuse it with `index.html`. Safe to delete if the user agrees |
| `state.json` | Live-match memory between monitor runs (auto-committed) |
| `predictions.json` | **The learning memory**: `pending` (awaiting results), `resolved` (graded history, capped at 1000), `meta.stats`. Pending entries are dropped without grading if postponed/cancelled or older than 3 days |
| `news.json` | Cached RSS headlines (max 15, ≤3h old); shown on the dashboard AND injected into prediction prompts |
| `data.json` | Generated dashboard payload: `live`, `upcoming`, `recent_results` (last 20), `accuracy`, `news` (auto-committed) |
| `.github/workflows/monitor.yml` | Cron `7,27,47 * * * *` (every 20 min) + manual button; runs monitor then dashboard_update, commits state |
| `.github/workflows/predict.yml` | Cron `15 3 * * *` (06:15 AM KSA daily) + manual button; runs predict then dashboard_update, commits data |
| `.github/workflows/scan.yml` | Manual button only; commits nothing |

## The self-learning loop (core logic — preserve it)

1. Every prediction is stored with pick (`home|draw|away`) + confidence (prompt asks for 40–80; parser clamps to 30–85).
2. Next morning, `predict.py` fetches real results and grades each prediction.
3. Accuracy is computed: overall, last 30 days, top vs other leagues, daily series (last 30 days), and **by confidence bucket** (70+, 60-69, 50-59, <50).
4. That track record is injected into every new Claude prediction prompt with the instruction: *if your real accuracy is below your stated confidence, lower your confidence — and vice versa.* This calibration IS the learning mechanism. Never remove it.

## Analysis methodology (the user's expert framework — apply in all analysis)

**Pre-match factors:** league position; motivation (title race / qualification / relegation); first or second leg + first-leg result; home/away records; current streaks; head-to-head; key injuries & suspensions; goals scored/conceded (attack vs defense); form trajectory last 5–6 games; manager situation (new appointment bounce / under pressure); fixture congestion & rotation risk before bigger matches; derby/clásico factor (these defy logic).

**Live-match additions:** xG; shots on/off target; possession *tied to real danger* (possession alone deceives); key passes; dangerous attacks (note surges in final 10–15 min); corners; red cards (who, when, position); current minute; score-line behavior (is the leader defending or pushing); substitutions (extra striker vs closing the game).

**Output format for any prediction:** short focused analysis of relevant factors only → clear final verdict: winner or draw + confidence % → for live matches, likely next scenario. Occasionally remind (briefly, not every reply): predictions are analytical opinions, never guarantees.

## Hard rules (never violate)

1. **Coverage exclusions:** friendlies, African competitions (CAF/AFCON/keyword "africa"), and all leagues from India, Pakistan, Bangladesh. The `EXCLUDED_COUNTRIES` / `EXCLUDED_LEAGUE_KEYWORDS` lists (and `TOP_LEAGUE_IDS`) are duplicated in `monitor.py`, `scan.py`, and `predict.py` — any change must be applied to all three in sync.
2. **Language convention:** ALL Telegram and dashboard-facing output in Arabic — team/league/country names in standard sports-media Arabic — but numerals always Latin digits (0-9), never Arabic-Indic (٠-٩). Arabic names come from Claude calls and are cached (in `state.json` / `predictions.json`) to avoid repeat calls.
3. **Secrets discipline:** keys live ONLY in GitHub Secrets. Never in code, files, chats, screenshots, or logs. **Never print a key or embed it in an error message** (this caused a real leak once — see history). All scripts `.strip()` env values to survive pasted whitespace.
4. **API budget awareness:** monitor ≈72 calls/day (3 runs/hour × 1 call), predict ≤5/day (2 fixture-date calls + up to 3 resolve calls). Pro plan allows 7,500/day so there is huge headroom now, but keep calls efficient and batched.
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
- `monitor.yml` and `predict.yml` share concurrency group `football-monitor` and use `git pull --rebase` before push to avoid commit races; both commit with `[skip ci]`. `scan.yml` commits nothing so it has no concurrency group.

## Roadmap (user's stated ambitions)

- **Engine 2**: a next-gen prediction engine to run alongside Engine 1 for direct comparison — the dashboard already has its tab and "under construction" panel; the backend does not exist yet
- Expand news/insight sources feeding prediction context (RSS headlines are already injected into `predict.py` prompts via `news.json`; more feeds, injuries, team news are wanted)
- Deeper pre-match data per fixture (standings, H2H via API-Football — budget now allows it)
- Keep improving calibration; the user's dream is maximum realistic accuracy — be honest that world-class models hit ~55-60% on 1X2 and never promise more
- Geopolitics was discussed and deprioritized: near-zero effect on match outcomes; keep at most as a side news feed
- Always design so new APIs/sources can be plugged in easily

## How to work in this repo

- Test Python changes locally before committing: `python -m py_compile <file>.py` at minimum; mock-data runs where possible. Scripts exit early without secrets, so full end-to-end runs happen in Actions.
- Manual runs: Actions tab → workflow → Run workflow. Verify results via `data.json` / `predictions.json` in the repo, not assumptions.
- `state.json`, `data.json`, `news.json`, `predictions.json` are bot-written and auto-committed by workflows — expect them to change under you; `git pull --rebase` before pushing.
- Any new alert type must follow the Arabic output convention and respect exclusions.
- When something fails silently, make it fail loudly first (raise with a clear Arabic message, as `predict.py` does for API failures), diagnose, then fix — but never let error text include secret values.
- Code comments and docstrings are in Arabic — keep that style when editing.
