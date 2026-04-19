import requests
import json
import re
import sys
import os
from datetime import datetime, timedelta

def test_endpoint(name, url, expected_status=200, headers=None, method="GET"):
    try:
        r = requests.request(method, url, headers=headers, timeout=20, allow_redirects=False)
        if r.status_code == expected_status:
            print(f"✅ {name}: PASS ({r.status_code})")
            return r
        else:
            print(f"❌ {name}: FAIL (Expected {expected_status}, got {r.status_code})")
            return None
    except Exception as e:
        print(f"❌ {name}: ERROR ({e})")
        return None

print("--- STARTING SYSTEM HEALTH CHECK ---")

# 1. Test Proxy Endpoints
test_endpoint("M3U Playlist", "http://localhost/iptv/playlist.m3u")

# 2. Test Local XMLTV File (Optimization)
xml_path = "/home/fedosha/server/iptv-proxy/data/xmltv.xml"
if os.path.exists(xml_path):
    size_mb = os.path.getsize(xml_path)/1024/1024
    if size_mb > 10:
        print(f"✅ Local XMLTV File: PASS ({size_mb:.1f} MB)")
    else:
        print(f"❌ Local XMLTV File: FAIL (File too small: {size_mb:.1f} MB)")
else:
    print("❌ Local XMLTV File: FAIL (Not found)")

# 3. Test VOD Seeking
headers = {"Range": "bytes=0-100"}
r_vod = test_endpoint("VOD Range Request", "http://localhost/iptv/vod/714148.mkv", expected_status=206, headers=headers)
if r_vod and "Content-Range" in r_vod.headers:
    print("✅ VOD Seeking: PASS")
else:
    print("❌ VOD Seeking: FAIL")

# 4. Test Jellyfin API & Smart EPG
auth_headers = {
    'X-Emby-Authorization': 'MediaBrowser Client="HealthCheck", Device="Chrome", DeviceId="12345", Version="10.9.11"',
    'Content-Type': 'application/json'
}
try:
    res = requests.post('http://192.168.1.100:8096/jellyfin/Users/AuthenticateByName', 
                       json={'Username': 'fedosha', 'Pw': 'bulls12'}, 
                       headers=auth_headers).json()
    tok = res['AccessToken']
    uid = res['User']['Id']
    
    # Check if programs are populated
    prog = requests.get(f'http://192.168.1.100:8096/jellyfin/LiveTv/Programs?UserId={uid}&HasAired=false&Limit=1', 
                       headers={'X-Emby-Token': tok}).json()
    count = prog.get('TotalRecordCount', 0)
    if count > 5000: # Threshold for optimized guide
        print(f"✅ Smart Guide Data: PASS ({count} programs found)")
    else:
        print(f"❌ Smart Guide Data: FAIL (Only {count} programs found)")

    # Check for REAL provider data (not just our 'Now:' or 'Live:' fallbacks)
    # We search the first 200 items for a title that doesn't start with Now: or Live:
    prog_real = requests.get(f'http://192.168.1.100:8096/jellyfin/LiveTv/Programs?UserId={uid}&HasAired=false&Limit=200', 
                       headers={'X-Emby-Token': tok}).json()
    items = prog_real.get('Items', [])
    provider_progs = [i['Name'] for i in items if not (i['Name'].startswith('Live:') or i['Name'].startswith('Now:'))]
    if len(provider_progs) > 0:
        print(f"✅ Real Provider EPG Content: PASS ('{provider_progs[0]}')")
    else:
        print("❌ Real Provider EPG Content: FAIL (Only fallbacks found in first 200 items)")

except Exception as e:
    print(f"❌ Jellyfin API: ERROR ({e})")

print("--- HEALTH CHECK COMPLETE ---")
