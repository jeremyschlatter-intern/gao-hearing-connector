"""
GAO Reports <-> Committee Hearings Connector

Fetches upcoming committee hearings from the Congress.gov API and
recent GAO reports from GAO RSS feeds, then matches them using
TF-IDF text similarity so staffers can quickly find relevant
background material for any hearing.
"""

import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

import feedparser
import requests
from flask import Flask, jsonify, render_template, request
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

app = Flask(__name__)

CONGRESS_API_KEY = "CONGRESS_API_KEY"
CONGRESS_API_BASE = "https://api.congress.gov/v3"
CURRENT_CONGRESS = 119

# Cache for API responses
_cache = {}
CACHE_TTL = 1800  # 30 minutes

# Disk cache directory
CACHE_DIR = Path(__file__).parent / ".cache"
CACHE_DIR.mkdir(exist_ok=True)


def cached_fetch(key, fetch_fn, ttl=CACHE_TTL):
    """In-memory + disk cache wrapper."""
    now = time.time()
    # Check memory cache
    if key in _cache and now - _cache[key]["time"] < ttl:
        return _cache[key]["data"]

    # Check disk cache
    disk_file = CACHE_DIR / f"{key.replace('/', '_').replace(':', '_')}.json"
    if disk_file.exists():
        try:
            disk_data = json.loads(disk_file.read_text())
            if now - disk_data.get("time", 0) < ttl:
                _cache[key] = {"data": disk_data["data"], "time": disk_data["time"]}
                return disk_data["data"]
        except (json.JSONDecodeError, KeyError):
            pass

    data = fetch_fn()
    _cache[key] = {"data": data, "time": now}

    # Write to disk cache (best effort)
    try:
        disk_file.write_text(json.dumps({"data": data, "time": now}))
    except Exception:
        pass

    return data


# ---------------------------------------------------------------------------
# Congress.gov API: Committee meetings (hearings)
# ---------------------------------------------------------------------------

def _congress_get(url, params=None, timeout=60, retries=3):
    """Make a GET request to Congress.gov API with retries."""
    if params is None:
        params = {}
    params.setdefault("api_key", CONGRESS_API_KEY)
    params.setdefault("format", "json")

    for attempt in range(retries):
        try:
            resp = requests.get(url, params=params, timeout=timeout)
            if resp.status_code in (429, 500, 502, 503, 504):
                if attempt < retries - 1:
                    time.sleep(2 ** (attempt + 1))
                    continue
            resp.raise_for_status()
            return resp.json()
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            if attempt < retries - 1:
                time.sleep(2 ** (attempt + 1))
                continue
            raise
    return None


def fetch_committee_meetings(chamber=None, limit=250, from_date=None):
    """Fetch committee meetings from the Congress.gov API."""
    if chamber:
        url = f"{CONGRESS_API_BASE}/committee-meeting/{CURRENT_CONGRESS}/{chamber}"
    else:
        url = f"{CONGRESS_API_BASE}/committee-meeting/{CURRENT_CONGRESS}"

    all_meetings = []
    offset = 0

    while True:
        params = {
            "limit": min(limit - len(all_meetings), 250),
            "offset": offset,
        }
        if from_date:
            params["fromDateTime"] = from_date
        data = _congress_get(url, params=params)
        if not data:
            break

        meetings = data.get("committeeMeetings", [])
        if not meetings:
            break
        all_meetings.extend(meetings)

        if len(all_meetings) >= limit or "next" not in data.get("pagination", {}):
            break
        offset += len(meetings)
        time.sleep(0.15)

    return all_meetings


def fetch_meeting_detail(event_url):
    """Fetch detailed info for a single committee meeting."""
    data = _congress_get(event_url, timeout=45)
    if data:
        return data.get("committeeMeeting", {})
    return {}


def get_upcoming_hearings(days_ahead=14, days_back=7):
    """Get hearings in a window around today."""
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=days_back)
    end = now + timedelta(days=days_ahead)

    # Use fromDateTime to limit results - meetings updated in last 60 days
    # likely cover all upcoming meetings
    from_date = (now - timedelta(days=60)).strftime("%Y-%m-%dT00:00:00Z")

    def _fetch_all():
        hearings = []
        for chamber in ["house", "senate"]:
            try:
                meetings = fetch_committee_meetings(
                    chamber=chamber, limit=500, from_date=from_date
                )
                hearings.extend(meetings)
                print(f"  Fetched {len(meetings)} {chamber} meetings from listing")
            except Exception as e:
                print(f"  Error fetching {chamber} meetings: {e}")
        return hearings

    cache_key = f"all_meetings_{from_date}"
    all_meetings = cached_fetch(cache_key, _fetch_all)
    print(f"  Total meetings in listing: {len(all_meetings)}")

    # Fetch details in parallel with ThreadPoolExecutor
    def _fetch_detail(m):
        detail_url = m.get("url")
        if not detail_url:
            return None
        cache_key = f"detail_{detail_url}"
        try:
            detail = cached_fetch(cache_key, lambda u=detail_url: fetch_meeting_detail(u), ttl=3600)
            return detail
        except Exception as e:
            print(f"  Skipping meeting (error): {e}")
            return None

    details = []
    with ThreadPoolExecutor(max_workers=10) as executor:
        future_to_meeting = {executor.submit(_fetch_detail, m): m for m in all_meetings}
        for future in as_completed(future_to_meeting):
            try:
                detail = future.result()
                if detail:
                    details.append(detail)
            except Exception:
                pass

    print(f"  Fetched {len(details)} meeting details")

    # Filter by date
    filtered = []
    for detail in details:
        date_str = detail.get("date", "")
        if not date_str:
            continue
        try:
            meeting_date = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            continue
        if start <= meeting_date <= end:
            detail["_parsed_date"] = meeting_date.isoformat()
            detail["_chamber"] = detail.get("chamber", "")
            filtered.append(detail)

    filtered.sort(key=lambda h: h.get("_parsed_date", ""))
    print(f"  Filtered to {len(filtered)} hearings in date range")
    return filtered


# ---------------------------------------------------------------------------
# GAO Reports from RSS feeds
# ---------------------------------------------------------------------------

GAO_RSS_FEEDS = {
    "all": "https://www.gao.gov/rss/reports.xml",
    "Agriculture and Food": "https://www.gao.gov/rss/topic/Agriculture_and_Food",
    "Budget and Spending": "https://www.gao.gov/rss/topic/Budget_and_Spending",
    "Business Regulation": "https://www.gao.gov/rss/topic/Business_Regulation_and_Consumer_Protection",
    "Economic Development": "https://www.gao.gov/rss/topic/Economic_Development",
    "Education": "https://www.gao.gov/rss/topic/Education",
    "Employment": "https://www.gao.gov/rss/topic/Employment",
    "Energy": "https://www.gao.gov/rss/topic/Energy",
    "Financial Markets": "https://www.gao.gov/rss/topic/Financial_Markets_and_Institutions",
    "Government Operations": "https://www.gao.gov/rss/topic/Government_Operations",
    "Health Care": "https://www.gao.gov/rss/topic/Health_Care",
    "Homeland Security": "https://www.gao.gov/rss/topic/Homeland_Security",
    "Housing": "https://www.gao.gov/rss/topic/Housing",
    "Information Technology": "https://www.gao.gov/rss/topic/Information_Technology",
    "Information Security": "https://www.gao.gov/rss/topic/Information_Security",
    "International Affairs": "https://www.gao.gov/rss/topic/International_Affairs",
    "Justice and Law Enforcement": "https://www.gao.gov/rss/topic/Justice_and_Law_Enforcement",
    "National Defense": "https://www.gao.gov/rss/topic/National_Defense",
    "Natural Resources": "https://www.gao.gov/rss/topic/Natural_Resources_and_Environment",
    "Science and Technology": "https://www.gao.gov/rss/topic/Science_and_Technology",
    "Space": "https://www.gao.gov/rss/topic/Space",
    "Tax Policy": "https://www.gao.gov/rss/topic/Tax_Policy_and_Administration",
    "Telecommunications": "https://www.gao.gov/rss/topic/Telecommunications",
    "Transportation": "https://www.gao.gov/rss/topic/Transportation",
    "Veterans": "https://www.gao.gov/rss/topic/Veterans",
}


def clean_html(text):
    """Strip HTML tags from text."""
    if not text:
        return ""
    return re.sub(r"<[^>]+>", " ", text).strip()


def fetch_gao_reports():
    """Fetch GAO reports from all RSS feeds and deduplicate."""
    def _fetch():
        seen_ids = set()
        reports = []

        for topic_name, feed_url in GAO_RSS_FEEDS.items():
            try:
                feed = feedparser.parse(feed_url)
                for entry in feed.entries:
                    report_id = entry.get("id", entry.get("link", ""))
                    if report_id in seen_ids:
                        for r in reports:
                            if r["id"] == report_id:
                                if topic_name != "all" and topic_name not in r["topics"]:
                                    r["topics"].append(topic_name)
                                break
                        continue

                    seen_ids.add(report_id)

                    title = entry.get("title", "")
                    summary = clean_html(entry.get("description", entry.get("summary", "")))
                    link = entry.get("link", "")
                    pub_date = entry.get("published", "")

                    gao_number = ""
                    if "/products/" in link:
                        gao_number = link.split("/products/")[-1].upper()

                    topics = []
                    if topic_name != "all":
                        topics.append(topic_name)

                    reports.append({
                        "id": report_id,
                        "gao_number": gao_number,
                        "title": title,
                        "summary": summary,
                        "link": link,
                        "pub_date": pub_date,
                        "topics": topics,
                    })
            except Exception as e:
                print(f"Error fetching {topic_name} feed: {e}")
                continue

        return reports

    return cached_fetch("gao_reports", _fetch)


# ---------------------------------------------------------------------------
# Matching engine: TF-IDF cosine similarity + synonym expansion
# ---------------------------------------------------------------------------

# Domain-specific synonyms and related terms for Congressional/policy context.
# When a term is found in text, its synonyms are appended to improve matching.
POLICY_SYNONYMS = {
    # Health
    "opioid": "substance abuse drug addiction fentanyl overdose narcotics",
    "fentanyl": "opioid substance abuse drug overdose narcotics",
    "medicare": "health care CMS elderly seniors hospital physician",
    "medicaid": "health care CMS low income hospital physician",
    "mental health": "behavioral health substance abuse suicide psychology",
    "pandemic": "public health disease outbreak infectious CDC",
    "vaccine": "immunization public health CDC pandemic",
    "abortion": "reproductive health family planning",
    # Defense
    "military": "defense armed forces DOD pentagon",
    "pentagon": "defense DOD military armed forces",
    "weapons": "defense military arms procurement acquisition",
    "missile": "defense military weapons nuclear ICBM",
    "readiness": "military defense force posture training",
    "veterans": "VA military service members benefits",
    "cybersecurity": "information security cyber attacks hacking data breach",
    "AI": "artificial intelligence machine learning automation technology",
    "artificial intelligence": "AI machine learning automation technology",
    # Economy/Finance
    "inflation": "prices economy cost of living consumer",
    "tariff": "trade import export customs duties",
    "trade": "commerce export import tariff international",
    "small business": "SBA entrepreneurship main street startup",
    "main street": "small business SBA entrepreneurship",
    "cryptocurrency": "digital currency bitcoin blockchain fintech",
    "banking": "financial institutions FDIC federal reserve",
    "student loan": "higher education college debt borrower",
    # Energy/Environment
    "climate": "environment global warming emissions carbon greenhouse",
    "emissions": "climate pollution environment carbon greenhouse",
    "renewable": "solar wind energy clean power",
    "nuclear": "energy power reactor NRC radiation",
    "power grid": "electricity energy reliability infrastructure utility",
    "electric": "power energy grid utility",
    "PFAS": "persistent chemicals contamination drinking water environmental",
    "wildfire": "forest fire natural disaster emergency",
    # Immigration
    "immigration": "border ICE asylum deportation migrant refugee",
    "border": "immigration ICE CBP customs patrol",
    "asylum": "immigration refugee border migrant",
    "deportation": "immigration removal ICE enforcement",
    # Tech/Communications
    "broadband": "internet telecommunications connectivity rural access",
    "5G": "telecommunications wireless broadband spectrum",
    "social media": "technology platform online content moderation",
    "privacy": "data protection consumer information security",
    # Transportation
    "aviation": "FAA airline airport flight safety",
    "railroad": "rail train transportation infrastructure freight",
    "highway": "road transportation infrastructure DOT",
    "shipping": "maritime ports trade cargo vessel",
    # Education
    "K-12": "education school student teacher elementary secondary",
    "higher education": "college university student loan tuition",
    "education": "school student learning teacher",
    # Housing
    "housing": "HUD rent mortgage affordable homeless shelter",
    "homeless": "housing shelter affordable HUD",
    # Justice
    "gun": "firearm weapon violence ATF background check",
    "firearm": "gun weapon violence ATF",
    "prison": "incarceration criminal justice corrections BOP",
    "law enforcement": "police FBI DOJ criminal justice",
    # Agencies (expand abbreviations)
    "EPA": "environmental protection agency environment pollution",
    "FDA": "food drug administration safety pharmaceutical",
    "DOD": "department defense military pentagon",
    "DHS": "department homeland security border immigration TSA",
    "DOE": "department energy nuclear renewable",
    "HHS": "department health human services medicare medicaid",
    "DOT": "department transportation highway aviation railroad",
    "USDA": "department agriculture food farm rural",
    "NASA": "space aeronautics satellite",
    "FEMA": "emergency management disaster relief flood",
    "IRS": "tax revenue internal service",
    "SSA": "social security administration retirement disability benefits",
    "USPS": "postal service mail delivery",
}


def expand_with_synonyms(text):
    """Expand text with domain-specific synonyms to improve matching."""
    if not text:
        return text
    text_lower = text.lower()
    expansions = []
    for term, synonyms in POLICY_SYNONYMS.items():
        if term.lower() in text_lower:
            expansions.append(synonyms)
    if expansions:
        return text + " " + " ".join(expansions)
    return text


def build_hearing_text(hearing):
    """Build a text representation of a hearing for matching."""
    parts = []

    title = hearing.get("title", "")
    if title:
        parts.extend([title] * 3)

    for c in hearing.get("committees", []):
        name = c.get("name", "")
        if name:
            parts.append(name)

    for w in hearing.get("witnesses", []):
        if w.get("organization"):
            parts.append(w["organization"])
        if w.get("position"):
            parts.append(w["position"])
        if w.get("name"):
            parts.append(w["name"])

    text = " ".join(parts)
    return expand_with_synonyms(text)


def build_report_text(report):
    """Build a text representation of a GAO report for matching."""
    parts = []

    title = report.get("title", "")
    if title:
        parts.extend([title] * 3)

    summary = report.get("summary", "")
    if summary:
        parts.append(summary[:2000])

    for topic in report.get("topics", []):
        parts.append(topic)

    text = " ".join(parts)
    return expand_with_synonyms(text)


def get_shared_terms(hearing_vec, report_vec, feature_names, top_k=5):
    """Find the top shared TF-IDF terms between a hearing and report."""
    # Element-wise minimum gives shared contribution
    import numpy as np
    h = np.asarray(hearing_vec.todense()).flatten()
    r = np.asarray(report_vec.todense()).flatten()
    shared = np.minimum(h, r)
    top_indices = shared.argsort()[::-1][:top_k]
    terms = []
    for idx in top_indices:
        if shared[idx] > 0:
            terms.append(feature_names[idx])
    return terms


def match_hearings_to_reports(hearings, reports, top_n=5, min_score=0.05):
    """Match hearings to relevant GAO reports using TF-IDF similarity."""
    if not hearings or not reports:
        return {}

    hearing_texts = [build_hearing_text(h) for h in hearings]
    report_texts = [build_report_text(r) for r in reports]

    all_texts = hearing_texts + report_texts

    vectorizer = TfidfVectorizer(
        stop_words="english",
        max_features=10000,
        ngram_range=(1, 2),
        min_df=1,
        sublinear_tf=True,
    )

    tfidf_matrix = vectorizer.fit_transform(all_texts)
    feature_names = vectorizer.get_feature_names_out()

    n_hearings = len(hearings)
    hearing_vectors = tfidf_matrix[:n_hearings]
    report_vectors = tfidf_matrix[n_hearings:]

    similarity = cosine_similarity(hearing_vectors, report_vectors)

    results = {}
    for i, hearing in enumerate(hearings):
        scores = similarity[i]
        top_indices = scores.argsort()[::-1][:top_n]

        matched = []
        for idx in top_indices:
            score = float(scores[idx])
            if score >= min_score:
                report = reports[idx].copy()
                report["relevance_score"] = round(score, 4)
                report["match_terms"] = get_shared_terms(
                    hearing_vectors[i], report_vectors[idx], feature_names
                )
                matched.append(report)

        event_id = hearing.get("eventId", str(i))
        results[event_id] = matched

    return results


# ---------------------------------------------------------------------------
# Flask routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/data")
def api_data():
    """Main API endpoint: returns hearings with matched GAO reports."""
    days_ahead = int(request.args.get("days_ahead", 14))
    days_back = int(request.args.get("days_back", 7))
    top_n = int(request.args.get("top_n", 5))

    try:
        hearings = get_upcoming_hearings(days_ahead=days_ahead, days_back=days_back)
        reports = fetch_gao_reports()
        matches = match_hearings_to_reports(hearings, reports, top_n=top_n)

        result = []
        for h in hearings:
            event_id = h.get("eventId", "")
            hearing_data = {
                "eventId": event_id,
                "title": h.get("title", ""),
                "date": h.get("_parsed_date", h.get("date", "")),
                "chamber": h.get("_chamber", h.get("chamber", "")),
                "type": h.get("type", ""),
                "meetingStatus": h.get("meetingStatus", ""),
                "location": h.get("location", {}),
                "committees": [
                    {"name": c.get("name", ""), "systemCode": c.get("systemCode", "")}
                    for c in h.get("committees", [])
                ],
                "witnesses": [
                    {
                        "name": w.get("name", ""),
                        "position": w.get("position", ""),
                        "organization": w.get("organization", ""),
                    }
                    for w in h.get("witnesses", [])
                ],
                "congressGovUrl": f"https://www.congress.gov/event/119th-Congress/{h.get('_chamber', '').lower()}-event/{event_id}" if event_id else "",
                "matchedReports": matches.get(event_id, []),
            }
            result.append(hearing_data)

        return jsonify({
            "generated": datetime.now(timezone.utc).isoformat(),
            "hearingCount": len(result),
            "reportCount": len(reports),
            "dateRange": {
                "start": (datetime.now(timezone.utc) - timedelta(days=days_back)).strftime("%Y-%m-%d"),
                "end": (datetime.now(timezone.utc) + timedelta(days=days_ahead)).strftime("%Y-%m-%d"),
            },
            "hearings": result,
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/api/reports")
def api_reports():
    """Return all cached GAO reports."""
    reports = fetch_gao_reports()
    return jsonify({
        "count": len(reports),
        "reports": reports,
    })


@app.route("/api/hearing/<event_id>")
def api_hearing_detail(event_id):
    """Return details for a single hearing with matched reports."""
    url = f"{CONGRESS_API_BASE}/committee-meeting/{CURRENT_CONGRESS}/house/{event_id}"
    try:
        detail = fetch_meeting_detail(url)
    except Exception:
        url = f"{CONGRESS_API_BASE}/committee-meeting/{CURRENT_CONGRESS}/senate/{event_id}"
        detail = fetch_meeting_detail(url)

    reports = fetch_gao_reports()
    matches = match_hearings_to_reports([detail], reports, top_n=10)

    detail["matchedReports"] = matches.get(detail.get("eventId", event_id), [])
    return jsonify(detail)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8347))
    print(f"Starting GAO-Hearings Connector on http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=True)
