import requests
import os
import sys

# SECURE CONFIGURATION
PROXY_URL = os.getenv("IPTV_PROXY_URL", "http://localhost:8081")

def test_endpoint(name, url, expected_status=200, headers=None):
    try:
        r = requests.get(url, headers=headers, timeout=20, allow_redirects=False)
        if r.status_code == expected_status:
            print(f"✅ {name}: PASS ({r.status_code})")
            return r
        print(f"❌ {name}: FAIL (Expected {expected_status}, got {r.status_code})")
    except Exception as e:
        print(f"❌ {name}: ERROR ({e})")
    return None

print("--- STARTING SYSTEM INTEGRITY CHECK ---")

# 1. Test Proxy Connectivity
test_endpoint("Playlist Connectivity", f"{PROXY_URL}/playlist.m3u")

# 2. Test Stream Alignment (188-byte MPEG-TS blocks)
try:
    r_stream = requests.get(f"{PROXY_URL}/play/11.ts", stream=True, timeout=30)
    chunk = next(r_stream.iter_content(chunk_size=512*1024))
    r_stream.close()
    if len(chunk) % 188 == 0:
        print(f"✅ Stream Alignment: PASS (Integrity maintained)")
    else:
        print(f"❌ Stream Alignment: FAIL (Broken packets detected: {len(chunk)})")
except Exception as e:
    print(f"❌ Stream Alignment: ERROR ({e})")

# 3. Test Dynamic Timezone (Zurich Offset)
xml_path = "/home/fedosha/server/iptv-proxy/data/xmltv.xml"
if os.path.exists(xml_path):
    with open(xml_path, 'r') as f:
        content = f.read(100000) # Check first 100KB for offsets
        if "+0200" in content or "+0100" in content:
            print("✅ Dynamic Timezone: PASS (Zurich offset verified)")
        else:
            print("❌ Dynamic Timezone: FAIL (Offset missing)")
else:
    print("❌ Dynamic Timezone: XML MISSING")

print("--- INTEGRITY CHECK COMPLETE ---")
