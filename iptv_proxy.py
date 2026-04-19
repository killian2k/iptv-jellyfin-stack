from flask import Flask, Response, stream_with_context, request, redirect
import requests
import logging
import time
import threading
import re
import json
import base64
import os
import random
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# DYNAMIC CONFIGURATION
BASE_URL = os.getenv("IPTV_PROVIDER_URL", "http://your-provider.com:8000/server/load.php")
MAC = os.getenv("IPTV_MAC_ADDRESS", "00:00:00:00:00:00")
UA = os.getenv("IPTV_USER_AGENT", "Mozilla/5.0 (QtEmbedded; U; Linux; C) AppleWebKit/531.2+ (KHTML, like Gecko) Version/4.0 Safari/531.2+ STB/MAG256")
PROXY_BASE = os.getenv("IPTV_PROXY_BASE", "http://192.168.1.100/iptv")
XMLTV_PATH = os.getenv("IPTV_XMLTV_PATH", "/app/data/xmltv.xml")
USER_TIMEZONE = os.getenv("IPTV_TIMEZONE", "Europe/Zurich")

STB_HEADERS = {
    "User-Agent": UA,
    "X-User-Agent": "Model: MAG256; Link: WiFi",
    "Accept": "*/*",
    "Connection": "Keep-Alive"
}

cache = {"channels": [], "playlist": None, "xmltv": None, "token": None, "token_time": 0}
cache_lock = threading.Lock()

def handshake():
    with cache_lock:
        now = time.time()
        if cache["token"] and (now - cache["token_time"] < 3600): return cache["token"]
        h_headers = STB_HEADERS.copy()
        h_headers["Cookie"] = f"mac={MAC}"
        try:
            resp = requests.get(BASE_URL, params={"type": "stb", "action": "handshake"}, headers=h_headers, timeout=15)
            token = resp.json().get('js', {}).get('token')
            if token:
                cache["token"], cache["token_time"] = token, now
                return token
        except: pass
        return cache["token"]

def get_stream_link(stream_type, cmd_val, ch_id):
    """Force HLS/M3U8 mode to bypass connection limits."""
    headers = STB_HEADERS.copy()
    headers["Cookie"] = f"mac={MAC}; stb_lang=en; timezone={USER_TIMEZONE};"
    # We add the HLS container hint to the provider
    try:
        resp = requests.get(BASE_URL, params={"type": stream_type, "action": "create_link", "cmd": cmd_val, "container": "hls"}, headers=headers, timeout=20).json()
        link = resp.get('js', {}).get('cmd', '')
        if link: return link, headers
    except: pass
    
    token = handshake()
    headers["Authorization"] = f"Bearer {token}"
    try:
        resp = requests.get(BASE_URL, params={"type": stream_type, "action": "create_link", "cmd": cmd_val, "container": "hls"}, headers=headers, timeout=20).json()
        return resp.get('js', {}).get('cmd', ''), headers
    except: return None, headers

def clean_name(name):
    clean = re.sub(r'\|[^\|]+\|', '', name)
    clean = re.sub(r'USA:', '', clean)
    clean = re.sub(r'[▼●★■□▲▶▷\-\s]+', ' ', clean).strip()
    return clean

def get_category_info(name):
    name_upper = name.upper()
    if any(x in name_upper for x in ["---", "▼", "▲", "●", "★"]): return None, None
    country = "Global"
    if "|FR|" in name_upper: country = "FR"
    elif "|US|" in name_upper or "USA:" in name_upper: country = "US"
    elif "|UK|" in name_upper: country = "UK"
    elif "|CA|" in name_upper or "|QC|" in name_upper: country = "CA"
    is_sport = any(x in name_upper for x in ["SPORT", "BEIN", "RMC", "EUROSPORT", "DAZN", "EQUIPE", "GOLF", "FOOT", "ELEVEN", "NBA", "ESPN", "TNT US", "FS1", "FS2", "NBCS", "GOLTV"])
    is_cinema = any(x in name_upper for x in ["CANAL+", "CINE+", "OCS", "MOVIE", "FILM", "CINEMA"])
    is_news = any(x in name_upper for x in ["NEWS", "INFO", "BFM", "CNEWS", "LCI", "CNN", "BBC", "FOX NEWS", "MSNBC"])
    is_kids = any(x in name_upper for x in ["KIDS", "DISNEY", "NICKELODEON", "CARTOON", "GULLI", "PIWI", "BOOMERANG"])
    tags = [country]; xml_cat = "Entertainment"
    if is_sport: tags.append("Sports"); xml_cat = "Sports"
    elif is_cinema: tags.append("Movies"); xml_cat = "Movies"
    elif is_news: tags.append("News"); xml_cat = "News"
    elif is_kids: tags.append("Kids"); xml_cat = "Kids"
    else: tags.append("Entertainment")
    return ";".join(tags), xml_cat

def update_cache():
    token = handshake()
    headers = STB_HEADERS.copy()
    headers["Cookie"] = f"mac={MAC}; stb_lang=en; timezone={USER_TIMEZONE};"
    headers["Authorization"] = f"Bearer {token}"
    try:
        resp = requests.get(BASE_URL, params={"type": "itv", "action": "get_all_channels"}, headers=headers, timeout=40)
        channels = resp.json().get('js', {}).get('data', [])
        processed = []
        m3u = "#EXTM3U\n"
        tv = ET.Element("tv", {"generator-info-name": "IPTV Proxy"})
        for ch in channels:
            group, xml_cat = get_category_info(ch.get('name', ''))
            if not group: continue
            ch_id = str(ch.get('id'))
            display_name = clean_name(ch.get('name', ''))
            processed.append({'id': ch_id, 'display_name': display_name, 'group': group, 'xml_cat': xml_cat, 'cmd': ch.get('cmd')})
        with cache_lock:
            cache["channels"], cache["playlist"], cache["xmltv"] = processed, m3u, ET.tostring(tv).decode()
        logging.info("Cache update complete.")
    except Exception as e: logging.error(f"Update error: {e}")

def background_worker():
    while True:
        try: update_cache()
        except: pass
        time.sleep(4 * 3600)

@app.route('/playlist.m3u')
def playlist():
    return Response(cache["playlist"], mimetype='audio/x-mpegurl')

@app.route('/play/<ch_id>.ts')
def play(ch_id):
    cid = ch_id.replace('.ts', '')
    ch_data = next((c for c in cache["channels"] if str(c.get('id')) == cid), None)
    if not ch_data: return "Not found", 404
    
    def generate():
        last_url, last_headers, fail_count = None, None, 0
        leftover = b'' 
        while fail_count < 20:
            if not last_url:
                cmd_val, last_headers = get_stream_link("itv", ch_data.get('cmd'), cid)
                if not cmd_val:
                    fail_count += 1; time.sleep(1); continue
                if cmd_val.startswith('ffmpeg '): cmd_val = cmd_val[7:]
                last_url = cmd_val.replace('ffrt ', '')

            try:
                logging.info(f"HLS-Stateless Stream for {cid}")
                with requests.get(last_url, headers=last_headers, stream=True, timeout=15) as upstream:
                    fail_count = 0
                    for chunk in upstream.iter_content(chunk_size=512*1024):
                        if chunk:
                            data = leftover + chunk
                            align_idx = (len(data) // 188) * 188
                            to_send = data[:align_idx]
                            leftover = data[align_idx:]
                            if to_send: yield to_send
                    last_url = None # Force re-link on connection end
            except GeneratorExit: break
            except Exception:
                last_url, fail_count = None, fail_count + 1
                time.sleep(random.uniform(0.1, 0.5))

    return Response(stream_with_context(generate()), content_type='video/mp2t', headers={'Connection': 'keep-alive'}, direct_passthrough=True)

if __name__ == '__main__':
    update_cache()
    threading.Thread(target=background_worker, daemon=True).start()
    app.run(host='0.0.0.0', port=8081, threaded=True)
