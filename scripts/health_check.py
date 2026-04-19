import requests
import json
import re
import sys

def test_endpoint(name, url, expected_status=200, headers=None, params=None, method="GET"):
    try:
        r = requests.request(method, url, headers=headers, params=params, timeout=15, allow_redirects=False)
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
xml = test_endpoint("XMLTV Guide", "http://localhost/iptv/xmltv.xml")
if xml and "&amp;" in xml.text:
    print("✅ XML Special Characters: PASS (Escaped)")
else:
    print("❌ XML Special Characters: FAIL (Not Escaped or Not found)")

# 2. Test VOD Seeking & Range Support
headers = {"Range": "bytes=0-100"}
r_vod = test_endpoint("VOD Range Request", "http://localhost/iptv/vod/714148.mkv", expected_status=206, headers=headers)
if r_vod and "Content-Range" in r_vod.headers:
    print("✅ VOD Seeking: PASS")
else:
    print("❌ VOD Seeking: FAIL (No Content-Range header)")

# 3. Test Jellyseerr Mobile Redirect
r_js = test_endpoint("Jellyseerr Mobile Login", "http://localhost/login", expected_status=301)
if r_js and "/jellyseerr/login" in r_js.headers.get("Location", ""):
    print("✅ Jellyseerr Redirect: PASS")
else:
    print("❌ Jellyseerr Redirect: FAIL")

# 4. Test Jellyfin API & Libraries
auth_headers = {
    'X-Emby-Authorization': 'MediaBrowser Client="HealthCheck", Device="TestDevice", DeviceId="12345", Version="1.0.0"',
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
    if len(iptv_libs) > 10:
        print(f"✅ Library Population: PASS ({len(iptv_libs)} libraries found)")
        # Check if first lib has paths
        if iptv_libs[0].get("Locations"):
            print("✅ Library Paths: PASS")
        else:
            print("❌ Library Paths: FAIL (Locations empty)")
    else:
        print(f"❌ Library Population: FAIL (Only {len(iptv_libs)} found)")
except Exception as e:
    print(f"❌ Jellyfin API: ERROR ({e})")

print("--- HEALTH CHECK COMPLETE ---")
