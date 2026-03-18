"""Quick test to verify data fetching and matching works."""
import json
import time
from app import fetch_gao_reports, get_upcoming_hearings, match_hearings_to_reports

print("=" * 60)
print("Testing GAO Reports fetch...")
t0 = time.time()
reports = fetch_gao_reports()
print(f"  Fetched {len(reports)} GAO reports in {time.time()-t0:.1f}s")
if reports:
    r = reports[0]
    print(f"  Sample: {r['title'][:80]}...")
    print(f"  Topics: {r['topics']}")
    print(f"  Link: {r['link']}")

print()
print("=" * 60)
print("Testing committee meetings fetch...")
t0 = time.time()
hearings = get_upcoming_hearings(days_ahead=14, days_back=7)
print(f"  Fetched {len(hearings)} hearings in {time.time()-t0:.1f}s")
if hearings:
    h = hearings[0]
    print(f"  Sample: {h.get('title', 'N/A')[:80]}")
    print(f"  Date: {h.get('date', 'N/A')}")
    print(f"  Chamber: {h.get('_chamber', 'N/A')}")
    committees = [c.get('name', '') for c in h.get('committees', [])]
    print(f"  Committees: {', '.join(committees)}")

print()
print("=" * 60)
print("Testing matching...")
t0 = time.time()
matches = match_hearings_to_reports(hearings, reports, top_n=3)
print(f"  Matching done in {time.time()-t0:.1f}s")

matched_count = sum(len(v) for v in matches.values())
print(f"  Total matches: {matched_count} across {len(matches)} hearings")

# Show a few examples
for h in hearings[:3]:
    eid = h.get('eventId', '')
    m = matches.get(eid, [])
    print(f"\n  Hearing: {h.get('title', 'N/A')[:70]}")
    if m:
        for r in m[:2]:
            print(f"    -> [{r['relevance_score']:.3f}] {r['title'][:60]}...")
    else:
        print("    -> No matches")

print("\nDone!")
