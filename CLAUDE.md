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
| `monitor.py` | Polls live matches every 10 min; Telegram alerts for match start / goals / full-time gated by the Focus List (see that section) — watchlist matches only, or top leagues when the list is empty — each alert with Claude analysis (max 20 analyses/run via `MAX_ANALYSES_PER_RUN`). **Top-league matches get Engine 2 live analysis**: live statistics + match events + lineups fetched (3 API calls, ≤`MAX_LIVE_ENRICHED_PER_RUN=12` matches/run), analyzed by `claude-fable-5` with extended thinking (2048-token reasoning budget) and an all-scenarios prompt (next goal & which side, most dangerous player by name from events, corner/set-piece danger, game-changing card), labeled "🤖 المحرك 2 (مباشر)". Other leagues keep the basic Haiku analysis. Stores display fields incl. team/league logo URLs in `state.json` |
| `scan.py` | On-demand worldwide live scan ("مسح حي" command) — one Actions button, one API call, one batched Claude call covering up to `MAX_PREDICTIONS=50` matches, numbered-line reply format |
| `predict.py` | Daily engine (Engine 1): resolves prior predictions against real results (≤3 API calls via `MAX_RESOLVE_CALLS`), computes accuracy stats, predicts next-24h matches (up to `MAX_PREDICTIONS_24H=60`) in Claude batches of `BATCH_SIZE=12`, injects recent news headlines from `news.json` as context, sends Telegram digest (top leagues only — `DIGEST_TOP_ONLY=True` — with a dashboard link for the rest) |
| `predict_v2.py` | Daily Engine 2 (V2) — see the "Engine 2 (V2)" section below. Same fixture selection/exclusions as V1, model `claude-fable-5`, probability output, enriched context for top leagues, own memory `predictions_v2.json` + `lessons_v2.json` |
| `dashboard_update.py` | Builds `data.json` for the dashboard from `state.json` + `predictions.json`, then `data_v2.json` from `predictions_v2.json` (same schema; `live`/`news` empty — dashboard takes those from `data.json`). `data_v2.json` is only created once V2 has real data, and only rewritten when content changes (so monitor runs don't dirty the tree). Also refreshes `news.json` from free RSS feeds (BBC Arabic, BBC Sport, Sky, Guardian) at most every 3h. Runs after monitor and both predict engines; costs zero API-Football budget |
| `index.html` | The dashboard (GitHub Pages). Arabic RTL default, EN toggle. Broadcast-scoreboard design with an **Engine 1 / Engine 2 tab switcher**. The V2 tab reads `data_v2.json` and shows an "under construction" panel until that file exists. V2 cards show a 3-segment probability bar (home/draw/away); resolved rows show the probabilities the engine gave; the accuracy panel includes a day-by-day trend (last 30 days; the V2 tab reads it from the permanent `history` archive so it survives the engines' 1000-result cap); a "📚 دروس يتعلمها المحرك" section (V2 tab only) lists the latest lessons. Reads `data.json` with 90s auto-refresh |
| `Index.html` | ⚠️ Stray near-empty file (capital I, whitespace only). NOT the dashboard — don't confuse it with `index.html`. Safe to delete if the user agrees |
| `state.json` | Live-match memory between monitor runs (auto-committed) |
| `predictions.json` | **The learning memory**: `pending` (awaiting results), `resolved` (graded history, capped at 1000), `meta.stats`. Pending entries are dropped without grading if postponed/cancelled or older than 3 days |
| `predictions_v2.json` | Engine 2's learning memory — same structure as `predictions.json`, fully separate. Created on the first V2 run |
| `lessons_v2.json` | Phase 3 memory: `{"lessons": [{"date","match","text"}]}` — filled automatically each morning from V2's wrong predictions (max 100 kept); `predict_v2.py` injects the most recent 15 into every prompt under "دروس من أخطائك السابقة"; the newest 10 go to the dashboard via `data_v2.json` |
| `history.json` | **The permanent progression archive — never truncated.** `{"days": {date: {v1/v2/user: {correct,total}}}, "meta": {lessons_stored}}`. Updated every morning by `predict_v2.py` (`update_history()`, idempotent merge of each store's `stats.daily`). Exists because the engines' detailed memories cap at 1000 resolved (~3 weeks) — this file is how the project's long-term progress stays measurable. Exposed to the dashboard via `data_v2.json.history` |
| `news.json` | Cached RSS headlines (max 15, ≤3h old); shown on the dashboard AND injected into prediction prompts (both engines) |
| `data.json` | Generated dashboard payload: `live`, `upcoming`, `recent_results` (last 20), `accuracy`, `news` (auto-committed) |
| `data_v2.json` | Generated Engine 2 dashboard payload (same schema plus `lessons`; `live`/`news` empty; upcoming/resolved entries carry `prob_home/draw/away`). Does not exist until V2's first successful run |
| `watchlist.py` | **Focus List manager** (see "The Focus List" section): reads the owner's Telegram messages each monitor cycle, interprets them with one Haiku call, maintains `watchlist.json`, fires the Live Scan on "مسح", replies with confirmations incl. both engines' picks |
| `watchlist.json` | Focus List state: `last_update_id` (Telegram offset) + `matches` `{fid: {label, home, away, date}}` (auto-committed; entries expire after 2 days) |
| `predictions_user.json` | The owner's personal predictions (texted via Telegram) — same pending/resolved/meta structure as the engines, graded every morning by `predict_v2.py`; feeds the three-way accuracy race in the V2 digest. Created on first use |
| `watchdog.py` | **Scheduler watchdog** (permanent fix for GitHub's unreliable cron): runs in every monitor run; after 04:00 UTC fires `predict.yml` via `gh workflow run` if V1 hasn't run today (per `meta.last_run`), after 04:30 UTC fires `predict_v2.yml` once V1 has run — order preserved for the digest comparison. Sends a Telegram note whenever it intervenes. Zero API-Football calls. Needs `actions: write` + `GH_TOKEN` (both provided in monitor.yml) |
| `.github/workflows/monitor.yml` | Cron `2,12,22,32,42,52 * * * *` (every 10 min) + manual button; runs watchlist (Telegram commands), monitor, dashboard_update, then the scheduler watchdog; commits state incl. `watchlist.json`. Has `actions: write` permission for the watchdog and scan trigger |
| `.github/workflows/predict.yml` | Cron `15 3 * * *` (06:15 AM KSA daily) + backup cron `15 4 * * *` (skipped via a same-day guard if the first succeeded; guard applies to scheduled runs only, manual runs always execute) + manual button; runs predict then dashboard_update, commits data |
| `.github/workflows/predict_v2.yml` | Cron `30 3 * * *` (06:30 AM KSA daily, 15 min after V1) + backup cron `30 4 * * *` (same same-day guard) + manual button; runs predict_v2 then dashboard_update, commits V2 data |
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
- **Enriched context for ALL fixtures** (top leagues get priority since the list is sorted top-first): extra API-Football data is fetched before predicting — standings of both teams (1 call per league, cached per run), head-to-head last 5, each team's last 5 results, injuries, **bookmaker odds** (first bookmaker's Match Winner odds, converted to implied probabilities with the overround removed — the prompt instructs Claude to treat the market as a strong reference but diverge with a stated reason), and **API-Football's own statistical prediction** (percentages + advice). `MAX_ENRICHED_FIXTURES=60` (all of the day's fixtures), capped by `ENRICH_CALL_BUDGET=600` calls/run (~385 typical) as a safety net; enrichment failures degrade gracefully to a basic prediction (never kill the run).
- **Probability output**: each prediction returns `prob_home`/`prob_draw`/`prob_away` as integers summing to 100 (parser normalizes if they don't). `pick` = highest probability; `confidence` = that probability clamped 30–85. Probabilities are stored in pending AND resolved entries.
- **Batching**: enriched fixtures in batches of `ENRICHED_BATCH_SIZE=4` (context is bulky), basic fixtures in batches of `BASIC_BATCH_SIZE=12` like V1. Strict JSON output, tolerant parser — same rule 6.
- **Own memory**: `predictions_v2.json` (`pending`/`resolved`/`meta.stats`), fully separate from V1, with the exact same grading, resolution, and calibration-stats logic — the calibration record is injected into every V2 prompt just like V1.
- **Lessons loop (Phase 3 — LIVE)**: every morning, after grading, `generate_lessons()` sends up to `MAX_MISTAKES_PER_RUN=30` (i.e. effectively ALL) of yesterday's WRONG predictions to Claude in one call, which extracts one short, generalizable Arabic lesson per mistake (a pattern to watch for, not a description of the game). Lessons are stored in `lessons_v2.json` as `{"date","match","text"}` (capped at `MAX_LESSONS_STORED=100`); the most recent `MAX_LESSONS_IN_PROMPT=15` are injected into every prediction prompt under "دروس من أخطائك السابقة". **Consolidation**: when stored lessons exceed `CONSOLIDATE_THRESHOLD=60`, one Claude call merges similar lessons into ≤`CONSOLIDATE_TARGET=30` stronger general principles (entries labeled "خلاصة مُجمّعة"); on any parse failure the original lessons are kept untouched. The newest 10 lessons also appear on the dashboard (V2 tab) and the digest notes how many new lessons were learned. Note: calibration (the accuracy record in every prompt) already learns from ALL graded matches — the lessons loop is the qualitative layer on top.
- **Telegram digest** is labeled "🤖 المحرك 2" so V1 and V2 messages are distinguishable; it shows the three probabilities per match AND, for each match, Engine 1's pick next to Engine 2's pick (read from `predictions.json` pending — V2 runs 15 min after V1 so V1's picks already exist). The side-by-side comparison lives in the V2 digest only; V1's digest is unchanged.
- **Live layer (in `monitor.py`)**: top-league live matches are analyzed by `claude-fable-5` with **extended thinking** (`LIVE_THINKING_BUDGET=2048` tokens of deep reasoning before answering) fed with real live statistics + events + **lineups/formations** (3 API calls per match) and an all-scenarios prompt — next goal, dangerous player by name, corners/set-pieces, cards. Capped at 12 matches per run.
- **Logo self-repair**: both engines backfill missing `home_logo`/`away_logo`/`league_logo` on pending entries from the daily fixtures fetch, and fill them from the results fetch at resolve time — zero extra API calls.
- **Budget**: 2 fixture-date calls + ≤3 resolve calls + enrichment (typically ~385 incl. odds + model predictions, hard-capped at 600) ≈ ~400/day on top of V1's 5.

## Analysis methodology (the user's expert framework — apply in all analysis)

**Pre-match factors:** league position; motivation (title race / qualification / relegation); first or second leg + first-leg result; home/away records; current streaks; head-to-head; key injuries & suspensions; goals scored/conceded (attack vs defense); form trajectory last 5–6 games; manager situation (new appointment bounce / under pressure); fixture congestion & rotation risk before bigger matches; derby/clásico factor (these defy logic).

**Live-match additions:** xG; shots on/off target; possession *tied to real danger* (possession alone deceives); key passes; dangerous attacks (note surges in final 10–15 min); corners; red cards (who, when, position); current minute; score-line behavior (is the leader defending or pushing); substitutions (extra striker vs closing the game).

**Output format for any prediction:** short focused analysis of relevant factors only → clear final verdict: winner or draw + confidence % → for live matches, likely next scenario. Occasionally remind (briefly, not every reply): predictions are analytical opinions, never guarantees.

## Hard rules (never violate)

1. **Coverage exclusions:** friendlies, African competitions (CAF/AFCON/keyword "africa"), and all leagues from India, Pakistan, Bangladesh. The `EXCLUDED_COUNTRIES` / `EXCLUDED_LEAGUE_KEYWORDS` lists (and `TOP_LEAGUE_IDS`) are duplicated in `monitor.py`, `scan.py`, `predict.py`, and `predict_v2.py` — any change must be applied to all four in sync.
2. **Language convention:** ALL Telegram and dashboard-facing output in Arabic — team/league/country names in standard sports-media Arabic — but numerals always Latin digits (0-9), never Arabic-Indic (٠-٩). Arabic names come from Claude calls and are cached (in `state.json` / `predictions.json`) to avoid repeat calls.
3. **Secrets discipline:** keys live ONLY in GitHub Secrets. Never in code, files, chats, screenshots, or logs. **Never print a key or embed it in an error message** (this caused a real leak once — see history). All scripts `.strip()` env values to survive pasted whitespace.
4. **Cost philosophy (user's explicit directive 2026-07-14): spend API-Football freely, spend Claude wisely.** API-Football Pro (7,500/day) is prepaid — more calls cost nothing, so never skimp on data that improves predictions. Current usage: monitor ≈144/day polling (every 10 min) + up to 36/run for Engine 2 live context (3 calls × ≤12 top-league matches on start/goal events), predict ≤5/day, predict_v2 ≈400/day (enrichment incl. odds + statistical predictions for all 60 fixtures, capped 600). Total worst case ≈1,150/day — still lots of headroom. The Anthropic API bills per call, however, so keep Claude calls batched and purposeful — that's the real cost driver, not API-Football.
5. **Empty data ≠ error:** no live matches can simply mean rest day / off-season. Interpret correctly and pivot to upcoming fixtures instead of reporting failure. (`scan.py` already sends a friendly "no live matches" message; `monitor.py` exits cleanly.)
6. Claude batch predictions must return **strict JSON** (no fences, no prose); parsers are tolerant (fence-stripping, bracket extraction) but don't rely on it. `scan.py` uses a numbered `N| ... | ...` line format instead — equally strict.

## The Focus List (قائمة التركيز)

Telegram is a two-way control channel, not just alerts. `watchlist.py` runs at the start of every monitor cycle (every 10 min):

- It reads new Telegram messages via `getUpdates` (**only** from `TELEGRAM_CHAT_ID` — messages from any other chat are ignored entirely; the offset is stored in `watchlist.json`).
- The user texts match names in plain Arabic/English (voice-transcribed, typos tolerated); one Haiku call interprets intent (`set`/`add`/`remove`/`clear`) against the day's pending fixtures and updates `watchlist.json`. The bot replies with a confirmation showing both engines' predictions for the chosen matches.
- Texting "مسح"/"مسح حي" fires the Live Scan workflow directly (no Claude call). "امسح القائمة" clears the list.
- **Alert gating in `monitor.py`**: watchlist non-empty → Telegram alerts (start/goal/FT) for watchlist matches ONLY; watchlist empty → top leagues only (the default — the old alert-everything mode is gone by user request). Claude live analyses are only spent on matches that will actually be alerted. Data collection, dashboard, and daily predictions still cover everything.
- Watchlist matches get **VIP live treatment**: they take priority for Engine 2 deep live analysis regardless of league.
- Entries expire 2 days after their match date so the list never mutes future days.
- `watchlist.json` is bot-written and auto-committed by monitor.yml.
- **The owner's own predictions (سباق الدقة الثلاثي)**: after confirming a Focus List, the bot sends one message per match with **three inline buttons** (فوز المضيف / تعادل / فوز الضيف) — one tap records the pick (button callbacks arrive via `getUpdates` as `callback_query`, processed with NO Claude call; `answerCallbackQuery` is best-effort since polling latency usually exceeds its window). Typing the prediction as text still works ("الريال يفوز وتعادل فرنسا" → the Haiku interpreter returns `action:"predict"` with picks). Picks are stored in `predictions_user.json` (same pending/resolved/meta structure, fixed `confidence: 60`, entries copied from V2 pending for names/logos; picks for already-kicked-off matches are refused; re-predicting before kickoff overwrites). Every morning `predict_v2.py` resolves them with the same `resolve_pending` logic and the V2 digest shows the three-way race line: "🏆 سباق الدقة — أنت | المحرك 1 | المحرك 2". Auto-committed by monitor.yml and predict_v2.yml.
- **Instant end-of-day summary**: `monitor.py` records each Focus-List match's final score into `watchlist.json` (`matches[fid].result`) at its FT event; the moment ALL watchlist matches have results, it sends "🏁 انتهت كل مباريات قائمة التركيز" with per-match ✅/❌ for the owner and both engines plus a day tally — sent once per list (`results_sent` flag, reset whenever the list changes). This is informational only; official grading/lessons stay in the morning run. The unchanged morning digest still goes out as usual.

## The "مسح حي" command

When the user says "مسح حي" (or "مسح" / "شنو الشغال الحين"): run the Live Scan workflow → all live matches worldwide (1200+ leagues), exclusions applied, quick one-line prediction + confidence per match, top leagues first, delivered to Telegram, ending with a prompt for which match to analyze in full detail.

## Project history & lessons learned

- `scan.yml` was once nested at `.github/workflows/.github/workflows/` — invisible to Actions. Fixed. Watch for path mistakes.
- Secrets once contained: (a) a trailing newline that broke HTTP headers, (b) the football key pasted into the Anthropic secret. Both diagnosed via a temporary debug workflow. Scripts now strip whitespace.
- A debug script once leaked a key into a committed file via an exception message; history was force-push scrubbed and the key rotated. Hence rule 3 above.
- API keys were exposed in a screenshot early on and rotated. Assume any key that ever appeared in plain text is dead.
- Local standalone HTML was tried and abandoned: iOS Files preview doesn't execute JavaScript. GitHub Pages is the chosen architecture.
- `monitor.yml`, `predict.yml`, and `predict_v2.yml` share concurrency group `football-monitor` and use `git pull --rebase` before push to avoid commit races; all commit with `[skip ci]`. `scan.yml` commits nothing so it has no concurrency group.
- **GitHub's cron is best-effort, not guaranteed**: on 2026-07-14 GitHub silently skipped both daily prediction runs (03:15/03:30 UTC) and left an 8.5h gap in the 20-min monitor cron. Permanent mitigation (four layers, keep all of them): (1) the scheduler watchdog in every monitor run, (2) backup cron slots one hour after the primary with a same-day guard, (3) duplicate runs are harmless by design — resolve is idempotent, already-pending fixtures are skipped, and no digest is sent when there is nothing new, (4) **external cron pinger — LIVE since 2026-07-14**: a cron-job.org job ("InsightMatch monitor", on the owner's account) POSTs to the workflow-dispatch API for monitor.yml every 10 minutes with a fine-grained PAT named `insightmatch-pinger` (this repo only, Actions: read/write, no expiration). The monitor's in-repo watchdog then guarantees the daily prediction runs, so this one ping keeps the whole system alive regardless of GitHub's scheduler. Note: the pinned API version header `2022-11-28` has a published sunset of 2028-03-10 — bump the `X-GitHub-Api-Version` header in the cron-job.org job before then, (5) **backup heartbeat**: three permanent Claude Routines (staggered :05/:25/:45 hourly, named "InsightMatch pinger A/B/C") in the owner's Claude session infrastructure re-trigger monitor.yml if the newest run is older than 15 min and fire missed daily prediction runs in V1-then-V2 order — normally dormant while the cron-job.org pinger works.

## Roadmap (user's stated ambitions)

- **Engine 2**: Phase 2 (the engine) AND Phase 3 (the lessons loop) are DONE — see the "Engine 2 (V2)" section. Per the user's explicit wish, capability enhancements go to Engine 2 only; Engine 1 stays frozen as the comparison baseline
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
