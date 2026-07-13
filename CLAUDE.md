# CLAUDE.md — InsightMatch Project Handoff

This file is the complete context for the InsightMatch football monitoring & prediction system. Any AI agent (Claude Code or other) working in this repo must read this first and follow it exactly.

## Who the user is

Nayef — the owner and sole operator. **No coding experience.** Always explain in plain language, provide step-by-step instructions for anything manual, and never assume he can debug code himself. He works mostly from an iPhone, sometimes a Mac. Communication may be voice-transcribed (tolerate imprecise wording; confirm intent when ambiguous).

## What this system is

A fully automated football match monitoring, alerting, and self-learning prediction system running entirely on free/low-cost infrastructure:

- **GitHub Actions** (public repo = unlimited minutes) — all automation
- **API-Football** (api-sports.io, **Pro plan: 7,500 requests/day**) — match data. Key in GitHub Secret `API_FOOTBALL_KEY`
- **Anthropic Claude API** (model `claude-haiku-4-5-20251001`) — all analysis & predictions. Key in Secret `ANTHROPIC_API_KEY`
- **Telegram bot** — alert delivery. `TELEGRAM_TOKEN` + `TELEGRAM_CHAT_ID` in Secrets
- **GitHub Pages dashboard** — https://insightmatch0-cpu.github.io/insight-match-monitor/

## Repo files

| File | Purpose |
|---|---|
| `monitor.py` | Polls live matches every 20 min; Telegram alerts for match start / goals / full-time, each with Claude analysis. Stores display fields in `state.json` |
| `scan.py` | On-demand worldwide live scan ("مسح حي" command) — one Actions button, one API call, one batched Claude call |
| `predict.py` | Daily engine: resolves yesterday's predictions against real results, computes accuracy stats, predicts next-24h matches in Claude batches (12/call), sends Telegram digest |
| `dashboard_update.py` | Builds `data.json` for the dashboard from state + predictions; refreshes news from free RSS feeds (BBC, BBC Arabic, Sky, Guardian) every 3h |
| `index.html` | The dashboard (GitHub Pages). Arabic RTL default, EN toggle. Broadcast-scoreboard design. Reads `data.json` with 90s auto-refresh |
| `state.json` | Live-match memory between monitor runs |
| `predictions.json` | **The learning memory**: `pending` (awaiting results), `resolved` (graded history, max 1000), `meta.stats` |
| `.github/workflows/monitor.yml` | Cron `7,27,47 * * * *` (every 20 min) |
| `.github/workflows/predict.yml` | Cron `15 3 * * *` (06:15 AM KSA daily) + manual button |
| `.github/workflows/scan.yml` | Manual button only |

## The self-learning loop (core logic — preserve it)

1. Every prediction is stored with pick (`home|draw|away`) + confidence (clamped 30–85).
2. Next morning, `predict.py` fetches real results and grades each prediction.
3. Accuracy is computed: overall, last 30 days, top vs other leagues, and **by confidence bucket** (70+, 60-69, 50-59, <50).
4. That track record is injected into every new Claude prediction prompt with the instruction: *if your real accuracy is below your stated confidence, lower your confidence — and vice versa.* This calibration IS the learning mechanism. Never remove it.

## Analysis methodology (the user's expert framework — apply in all analysis)

**Pre-match factors:** league position; motivation (title race / qualification / relegation); first or second leg + first-leg result; home/away records; current streaks; head-to-head; key injuries & suspensions; goals scored/conceded (attack vs defense); form trajectory last 5–6 games; manager situation (new appointment bounce / under pressure); fixture congestion & rotation risk before bigger matches; derby/clásico factor (these defy logic).

**Live-match additions:** xG; shots on/off target; possession *tied to real danger* (possession alone deceives); key passes; dangerous attacks (note surges in final 10–15 min); corners; red cards (who, when, position); current minute; score-line behavior (is the leader defending or pushing); substitutions (extra striker vs closing the game).

**Output format for any prediction:** short focused analysis of relevant factors only → clear final verdict: winner or draw + confidence % → for live matches, likely next scenario. Occasionally remind (briefly, not every reply): predictions are analytical opinions, never guarantees.

## Hard rules (never violate)

1. **Coverage exclusions:** friendlies, African competitions (CAF/AFCON/keyword "africa"), and all leagues from India, Pakistan, Bangladesh. Exclusion lists exist in `monitor.py`, `scan.py`, `predict.py` — keep them in sync.
2. **Language convention:** ALL Telegram and dashboard-facing output in Arabic — team/league/country names in standard sports-media Arabic — but numerals always Latin digits (0-9), never Arabic-Indic (٠-٩). Arabic names come from Claude calls and are cached to avoid repeat calls.
3. **Secrets discipline:** keys live ONLY in GitHub Secrets. Never in code, files, chats, screenshots, or logs. **Never print a key or embed it in an error message** (this caused a real leak once — see history). All scripts `.strip()` env values to survive pasted whitespace.
4. **API budget awareness:** monitor ≈72 calls/day, predict ≤5/day. Pro plan allows 7,500/day so there is huge headroom now, but keep calls efficient and batched.
5. **Empty data ≠ error:** no live matches can simply mean rest day / off-season. Interpret correctly and pivot to upcoming fixtures instead of reporting failure.
6. Claude batch predictions must return **strict JSON** (no fences, no prose); parsers are tolerant but don't rely on it.

## The "مسح حي" command

When the user says "مسح حي" (or "مسح" / "شنو الشغال الحين"): run the Live Scan workflow → all live matches worldwide (1200+ leagues), exclusions applied, quick one-line prediction + confidence per match, sorted by competitiveness, delivered to Telegram, ending with "which match do you want in full detail?"

## Project history & lessons learned

- `scan.yml` was once nested at `.github/workflows/.github/workflows/` — invisible to Actions. Fixed. Watch for path mistakes.
- Secrets once contained: (a) a trailing newline that broke HTTP headers, (b) the football key pasted into the Anthropic secret. Both diagnosed via a temporary debug workflow. Scripts now strip whitespace.
- A debug script once leaked a key into a committed file via an exception message; history was force-push scrubbed and the key rotated. Hence rule 3 above.
- API keys were exposed in a screenshot early on and rotated. Assume any key that ever appeared in plain text is dead.
- Local standalone HTML was tried and abandoned: iOS Files preview doesn't execute JavaScript. GitHub Pages is the chosen architecture.
- Workflows share concurrency group `football-monitor` and use `git pull --rebase` before push to avoid commit races.

## Roadmap (user's stated ambitions)

- Expand news/insight sources (more RSS, injuries, team news; possibly forums) feeding prediction context
- Deeper pre-match data per fixture (standings, H2H via API-Football — budget now allows it)
- Keep improving calibration; the user's dream is maximum realistic accuracy — be honest that world-class models hit ~55-60% on 1X2 and never promise more
- Geopolitics was discussed and deprioritized: near-zero effect on match outcomes; keep at most as a side news feed
- Always design so new APIs/sources can be plugged in easily

## How to work in this repo

- Test Python changes locally before committing (`python -m py_compile`, mock-data runs).
- Manual runs: Actions tab → workflow → Run workflow. Verify results via `data.json` / `predictions.json` in the repo, not assumptions.
- Any new alert type must follow the Arabic output convention and respect exclusions.
- When something fails silently, make it fail loudly first (raise with a clear Arabic message), diagnose, then fix — but never let error text include secret values.
