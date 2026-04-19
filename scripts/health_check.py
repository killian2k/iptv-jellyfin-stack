import requests
import json
import re
import sys
from datetime import datetime, timedelta

def test_endpoint(name, url, expected_status=200, headers=None, params=None, method="GET"):
    try:
        r = requests.request(method, url, headers=headers, params=params, timeout=20, allow_redirects=False)
        if r.status_code == expected_status:
            print(f"✅ {name}: PASS ({r.status_code})")
            return r
        else:
            print(f"❌ {name}: FAIL (Expected {expected_status}, got {r.status_code})")
            return None
    except Exception as e:
        print(f"❌ {name}: ERROR ({e})")
        return None

print("--- STARTING UPDATED SYSTEM HEALTH CHECK ---")

# 1. Test Proxy Endpoints
test_endpoint("M3U Playlist", "http://localhost/iptv/playlist.m3u")
xml = test_endpoint("XMLTV Guide", "http://localhost/iptv/xmltv.xml")

if xml:
    # A. Check XML Formatting (Line breaks)
    if "\n" in xml.text and "  <channel" in xml.text:
        print("✅ XML Formatting: PASS (Indented with newlines)")
    else:
        print("❌ XML Formatting: FAIL (Missing newlines or indentation)")

    # B. Check Special Character Escaping
    if "&amp;" in xml.text:
        print("✅ XML Special Characters: PASS (Escaped)")
    else:
        print("❌ XML Special Characters: FAIL (Not Escaped)")

    # C. Check 48h Duration & Timezone Overlap
    # Look for the last programme start time
    times = re.findall(r'start="(\d{14})', xml.text)
    if times:
        first_time = datetime.strptime(times[0], "%Y%m%d%H%M%S")
        last_time = datetime.strptime(times[-1], "%Y%m%d%H%M%S")
        now = datetime.utcnow()
        
        # Verify it starts at least 1h in the past
        if first_time < (now - timedelta(hours=1)):
            print(f"✅ Guide Timezone Overlap: PASS (Starts at {first_time} UTC)")
        else:
            print(f"❌ Guide Timezone Overlap: FAIL (Starts too late: {first_time} UTC)")
            
        # Verify duration is roughly 48h
        duration = last_time - first_time
        if duration.total_seconds() >= 40 * 3600: # Allow some wiggle room
            print(f"✅ Guide Duration: PASS ({duration.total_seconds()/3600:.1f} hours)")
        else:
            print(f"❌ Guide Duration: FAIL (Only {duration.total_seconds()/3600:.1f} hours)")

# 2. Test VOD Seeking & Range Support
headers = {"Range": "bytes=0-100"}
r_vod = test_endpoint("VOD Range Request", "http://localhost/iptv/vod/714148.mkv", expected_status=206, headers=headers)
if r_vod and "Content-Range" in r_vod.headers:
    print("✅ VOD Seeking: PASS")
else:
    print("❌ VOD Seeking: FAIL")

# 3. Test Jellyseerr Mobile Login
r_js = test_endpoint("Jellyseerr Mobile Login", "http://localhost/login", expected_status=301)
if r_js and "/jellyseerr/login" in r_js.headers.get("Location", ""):
    print("✅ Jellyseerr Redirect: PASS")
else:
    print("❌ Jellyseerr Redirect: FAIL")

# 4. Test Jellyfin API & Libraries
auth_headers = {
    'X-Emby-Authorization': 'MediaBrowser Client="Jellyfin Web", Device="Chrome", DeviceId="12345", Version="10.9.11"',
    'Content-Type': 'application/json'
}
try:
    res = requests.post('http://192.168.1.100:8096/jellyfin/Users/AuthenticateByName', 
                       json={'Username': 'fedosha', 'Pw': 'bulls12'}, 
                       headers=auth_headers).json()
    tok = res['AccessToken']
    libs = requests.get('http://192.168.1.100:8096/jellyfin/Library/VirtualFolders', 
                       headers={'X-Emby-Token': tok}).json()
    iptv_libs = [l for l in libs if l['Name'].startswith('IPTV ')]
    if len(iptv_libs) >= 14:
        print(f"✅ Library Population: PASS ({len(iptv_libs)} libraries found)")
    else:
        print(f"❌ Library Population: FAIL (Only {len(iptv_libs)} found)")
except Exception as e:
    print(f"❌ Jellyfin API: ERROR ({e})")

print("--- HEALTH CHECK COMPLETE ---")
