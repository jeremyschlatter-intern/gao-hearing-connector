# After Action Report: GAO Reports & Committee Hearings Connector

**Project:** Connect GAO Reports to Committee Hearings
**Live URL:** https://jeremyschlatter-intern.github.io/gao-hearing-connector/
**Repository:** https://github.com/jeremyschlatter-intern/gao-hearing-connector
**Completed:** March 17, 2026

---

## What I Built

A web tool that automatically connects Government Accountability Office (GAO) reports to upcoming and recent Congressional committee hearings. When a staffer opens the tool before a hearing, they can instantly see which GAO reports are relevant to the topics being discussed—saving the manual search through gao.gov that would otherwise take 15-30 minutes per hearing.

The tool covers both House and Senate committees, indexes ~587 GAO reports across 25 topic areas, and refreshes automatically every morning at 6:00 AM ET.

---

## Process and Key Decisions

### 1. Research Phase

I started by investigating the available data sources in parallel:
- **Congress.gov API** (`/v3/committee-meeting/`): Provides hearing schedules including titles, dates, committees, witnesses, locations, and status. Required fetching individual meeting details for witness information—471 separate API calls for the current Congress.
- **GAO RSS feeds** (`gao.gov/rss/topic/*`): 25 topic-specific feeds covering all major policy areas, each with ~25-30 recent items. No API key required.

**Key decision:** I chose TF-IDF cosine similarity for matching rather than a simpler keyword search. This was important because hearing titles and GAO report titles often use different terminology for the same topic (e.g., "PFAS contamination" vs. "persistent chemical pollutants"). TF-IDF handles partial vocabulary overlap much better than exact matching.

### 2. Initial Prototype (Commit 1)

Built a Flask web application that:
- Fetches hearings from Congress.gov (both chambers, 119th Congress)
- Parses GAO RSS feeds with feedparser
- Matches them using scikit-learn's TF-IDF vectorizer with cosine similarity
- Renders results in a clean HTML interface grouped by date

### 3. Performance and Reliability Obstacles

**Obstacle: Congress.gov API timeouts and 503 errors**

Fetching 471 individual meeting details sequentially took over 10 minutes and frequently hit timeouts or 503 Service Unavailable errors.

*Failed attempt:* Initially tried simple sequential fetching with a single retry.

*Resolution:* Implemented three fixes:
- `ThreadPoolExecutor` with 10 concurrent workers (reduced fetch time from 10+ minutes to ~45 seconds)
- Exponential backoff retry logic for transient errors (429, 500, 502, 503, 504)
- Disk caching so subsequent runs use cached data (reduces to ~0.1 seconds)

**Obstacle: Chrome browser on a different machine**

The development machine runs headless—Chrome is on a separate machine that can't reach localhost.

*Resolution:* Created `generate_static.py` to pre-bake all data into a self-contained HTML file, deployed via GitHub Pages. This also turned out to be the right architecture anyway, since it enables daily automated refreshes without a running server.

### 4. Matching Quality Improvements

**Obstacle: Weak initial matches**

The basic TF-IDF matching produced many false positives. A hearing about "financial privacy" would match NASA reports because both mentioned the word "protection."

*Resolution (iterative):*

**Round 1 - Synonym expansion:** Added 60+ domain-specific synonym groups mapping Congressional terminology to related terms. For example, "opioid" expands to "substance abuse drug addiction fentanyl overdose narcotics." This improved matches from 289 to 305.

**Round 2 - Committee jurisdiction boosting:** This was the most impactful single improvement. I built a lookup table mapping 40+ committee names to GAO topic areas (e.g., House Financial Services → Financial Markets, Banking, Financial Regulation). When a report's topic overlaps with the hearing committee's jurisdiction, its similarity score gets a 50% boost per overlapping topic. This meant a GAO report tagged "Financial Markets" would naturally score higher for a House Financial Services hearing—matching how a human staffer would evaluate relevance.

**Round 3 - Noise reduction:** Raised the minimum score threshold from 0.05 to 0.08, filtering out weak matches. Also cleaned up the "Matched on" term display by deduplicating terms (bigrams subsume their constituent unigrams) and filtering generic words like "policy," "century," "institute."

*Before and after for "Updating America's Financial Privacy Framework" hearing:*
- Before: 5 "Low match" results including VA Health Care (matched on "21st century") and NASA (matched on "privacy")
- After: 4 "High match" + 1 "Medium match" results, all genuinely about financial regulation (AI in Financial Services, Financial Literacy Forum, Federal Reserve recommendations, OCC recommendations, Bank Supervision)

### 5. DC Agent Feedback Loop

Per the project instructions, I created a DC feedback agent playing the role of Daniel Schuman, the project proposer. I ran three feedback rounds:

**Round 1 (Score: not yet rated):** Identified staleness as the fundamental problem. Led to GitHub Actions daily refresh, collapsed past dates, synonym expansion.

**Round 2 (Score: "ready for beta, not broad rollout"):** Requested match explanations, shareable links, about page. All implemented.

**Round 3 (Score: 6.5/10):** Identified match quality as the dealbreaker. Specific examples: Financial Privacy hearing showing only "Low" matches, VA Health Care false positive. Led to committee jurisdiction boosting, threshold increase, term cleanup.

**Round 4 (Score: 8/10, "Ship it"):** Confirmed critical issues addressed. Remaining must-fix: date range selectors were non-functional in static version. Led to client-side date filtering implementation.

### 6. Visual Verification

I used Claude in Chrome to visually inspect every feature on the deployed site, clicking through hearings, expanding reports, testing selectors, and verifying the about page. This caught several issues that code review alone wouldn't have found, like the raw RSS date format and the UTC/ET timezone confusion.

---

## Architecture

```
Congress.gov API ──→ app.py ──→ generate_static.py ──→ docs/index.html ──→ GitHub Pages
GAO RSS Feeds   ──→   │              │
                       │              └── Embeds JSON data + JS rendering
                       │
                       ├── TF-IDF + synonym expansion + committee boosting
                       ├── Concurrent fetching (ThreadPoolExecutor)
                       └── Disk + memory caching

GitHub Actions (daily 6 AM ET) ──→ generate_static.py ──→ git push ──→ GitHub Pages
```

**Key files:**
- `app.py` — Backend: API fetching, matching engine, synonym expansion, committee jurisdiction mapping
- `templates/index.html` — Frontend: rendering, filtering, deep links, about page
- `generate_static.py` — Static site generator: embeds data into HTML
- `.github/workflows/update-data.yml` — Daily refresh automation

---

## Features

| Feature | Description |
|---------|-------------|
| Hearing schedule | Both chambers, grouped by date, with committee, time, location, witnesses |
| GAO report matching | TF-IDF + 60+ synonym groups + committee jurisdiction boosting |
| Match explanations | "Matched on: financial, banking, fdic" shows why each report was linked |
| Relevance badges | High (≥0.15) and Medium (0.08-0.15) scores |
| Date range filtering | Look ahead (7/14/30 days) and look back (3/7/14 days), functional in static site |
| Chamber filtering | All / Senate / House |
| Shareable deep links | `#hearing-{eventId}` hash navigation with auto-open |
| Past hearings collapsed | Past dates collapsed by default, today highlighted with gold marker |
| Status badges | POSTPONED, RESCHEDULED, CANCELLED badges on affected hearings |
| About page | Methodology, data sources, coverage, limitations |
| Daily auto-refresh | GitHub Actions at 6 AM ET, commits only when data changes |
| Timezone-aware | All dates display in Eastern Time |

---

## What Went Well

1. **Committee jurisdiction boosting** was the right architectural insight. It mirrors how a human expert would evaluate relevance—a financial regulation report is inherently more interesting to a Financial Services committee staffer regardless of keyword overlap.

2. **The feedback loop with the DC agent** was highly productive. Each round surfaced specific, actionable issues that I wouldn't have identified on my own. The agent correctly identified match quality as the dealbreaker before it became a user-facing problem.

3. **Static site deployment** was the right call. It's zero-maintenance (no server to run), fast to load (no API calls on page load), and the daily refresh via GitHub Actions keeps it current.

4. **Visual verification in Chrome** caught real polish issues (raw dates, timezone confusion, non-functional selectors) that automated testing wouldn't have found.

---

## What I Would Do Differently

1. **Start with committee jurisdiction boosting from the beginning.** I underestimated how important domain knowledge is for relevance ranking. Pure keyword matching was never going to work well enough for this use case.

2. **Fetch the wider date range from the start.** I initially fetched 14 days ahead / 7 back, then had to re-fetch with 30/14 when the selectors needed more data. Fetching the maximum range upfront would have avoided this.

3. **Consider semantic embeddings.** TF-IDF with synonym expansion works surprisingly well, but a future version could use sentence embeddings for truly semantic matching. This would handle cases where hearing and report titles use completely different vocabulary for the same concept.

---

## Metrics

- **128 hearings** in the current 30-day/14-day window
- **587 GAO reports** indexed across 25 topic areas
- **373 total matches** (was 305 before quality improvements, 231 in the narrower window)
- **Match precision**: Spot-checked 10 hearings—8 had exclusively relevant matches, 2 had one borderline match each
- **Daily refresh**: ~2-3 minute GitHub Actions run
- **Page load**: Instant (all data pre-baked, ~1.2MB HTML)

---

## Team

This project was completed by a single Claude agent (me), with feedback from a DC agent teammate playing the role of the project proposer. The DC agent provided three rounds of substantive review that directly shaped the matching algorithm, UI design, and feature priorities.
