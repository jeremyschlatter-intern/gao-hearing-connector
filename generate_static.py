"""
Generate a self-contained static HTML file with all data pre-baked.
This is useful when the server can't be accessed directly.
"""
import json
import time
from app import get_upcoming_hearings, fetch_gao_reports, match_hearings_to_reports
from datetime import datetime, timedelta, timezone

print("Fetching GAO reports...")
t0 = time.time()
reports = fetch_gao_reports()
print(f"  {len(reports)} reports in {time.time()-t0:.1f}s")

print("Fetching hearings...")
t0 = time.time()
hearings = get_upcoming_hearings(days_ahead=14, days_back=7)
print(f"  {len(hearings)} hearings in {time.time()-t0:.1f}s")

print("Matching...")
matches = match_hearings_to_reports(hearings, reports, top_n=5)

# Build the data structure
now = datetime.now(timezone.utc)
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

api_data = {
    "generated": now.isoformat(),
    "hearingCount": len(result),
    "reportCount": len(reports),
    "dateRange": {
        "start": (now - timedelta(days=7)).strftime("%Y-%m-%d"),
        "end": (now + timedelta(days=14)).strftime("%Y-%m-%d"),
    },
    "hearings": result,
}

# Read the template
with open("templates/index.html") as f:
    template = f.read()

# Replace the loadData function to use embedded data instead of fetch
data_json = json.dumps(api_data)

static_html = template.replace(
    """async function loadData() {
            const content = document.getElementById('content');
            content.innerHTML = `
                <div class="loading">
                    <div class="spinner"></div>
                    <p style="margin-top: 1rem;">Fetching hearings and matching GAO reports&hellip;</p>
                    <p style="margin-top: 0.5rem; font-size: 0.85rem; color: #999;">This may take a moment on first load as we fetch data from Congress.gov and GAO.</p>
                </div>`;

            const daysAhead = document.getElementById('days-ahead').value;
            const daysBack = document.getElementById('days-back').value;

            try {
                const resp = await fetch(`/api/data?days_ahead=${daysAhead}&days_back=${daysBack}`);
                if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
                allData = await resp.json();

                if (allData.error) throw new Error(allData.error);

                renderData(allData);
            } catch (e) {
                content.innerHTML = `<div class="error">Error loading data: ${e.message}. Please try refreshing.</div>`;
            }
        }""",
    f"""async function loadData() {{
            allData = {data_json};
            renderData(allData);
        }}"""
)

# Remove the refresh button (not needed for static site) and look-ahead/look-back selectors
static_html = static_html.replace(
    '<button id="refresh-btn" onclick="loadData()">Refresh</button>',
    ''
)

# Convert generated time to Eastern for display
from zoneinfo import ZoneInfo
now_et = now.astimezone(ZoneInfo("America/New_York"))
snapshot_str = now_et.strftime("%B %d, %Y at %I:%M %p ET")

# Update subtitle with snapshot timestamp
static_html = static_html.replace(
    'Connecting Government Accountability Office research to Congressional committee proceedings',
    f'Connecting Government Accountability Office research to Congressional committee proceedings<br><span style="font-size:0.8rem;opacity:0.7">Data as of {snapshot_str} &mdash; refreshed daily at 6:00 AM ET</span>'
)

output_path = "docs/index.html"
import os
os.makedirs("docs", exist_ok=True)
with open(output_path, "w") as f:
    f.write(static_html)

print(f"\nStatic file written to {output_path}")
print(f"  {len(result)} hearings, {len(reports)} reports, {sum(len(m) for m in matches.values())} matches")
