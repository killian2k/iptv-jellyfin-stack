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
PROXY_BASE = os.getenv("IPTV_PROXY_BASE", "http://localhost/iptv")
XMLTV_PATH = os.getenv("IPTV_XMLTV_PATH", "/app/data/xmltv.xml")
USER_TIMEZONE = os.getenv("IPTV_TIMEZONE", "Europe/Zurich")
TS_PACKET_SIZE = 188 

STB_HEADERS = {
    "User-Agent": UA,
    "X-User-Agent": "Model: MAG256; Link: WiFi",
    "Accept": "*/*",
    "Connection": "Keep-Alive"
}

cache = {
    "token": None,
    "token_time": 0,
    "channels": [],
    "epg_data": {},
    "playlist": None,
    "xmltv": None,
    "last_update": 0
}
cache_lock = threading.Lock()

def handshake():
    with cache_lock:
        now = time.time()
        if cache["token"] and (now - cache["token_time"] < 3600):
            return cache["token"]
        h_headers = STB_HEADERS.copy()
        h_headers["Cookie"] = f"mac={MAC}"
        try:
            logging.info(f"Handshaking with {BASE_URL} for MAC {MAC}")
            resp = requests.get(BASE_URL, params={"type": "stb", "action": "handshake"}, headers=h_headers, timeout=15)
            token = resp.json().get('js', {}).get('token')
            if token:
                cache["token"], cache["token_time"] = token, now
                logging.info("Handshake Success")
                return token
        except Exception as e:
            logging.error(f"Handshake error: {e}")
        return cache["token"]

def get_stream_link(stream_type, cmd_val, ch_id):
    headers = STB_HEADERS.copy()
    headers["Cookie"] = f"mac={MAC}; stb_lang=en; timezone={USER_TIMEZONE};"
    try:
        resp = requests.get(BASE_URL, params={"type": stream_type, "action": "create_link", "cmd": cmd_val}, headers=headers, timeout=20).json()
        link = resp.get('js', {}).get('cmd', '')
        if link: return link, headers
    except: pass
    token = handshake()
    headers["Authorization"] = f"Bearer {token}"
    try:
        resp = requests.get(BASE_URL, params={"type": stream_type, "action": "create_link", "cmd": cmd_val}, headers=headers, timeout=20).json()
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
    tags = [country]
    xml_cat = "Entertainment"
    if is_sport: tags.append("Sports"); xml_cat = "Sports"
    elif is_cinema: tags.append("Movies"); xml_cat = "Movies"
    elif is_news: tags.append("News"); xml_cat = "News"
    elif is_kids: tags.append("Kids"); xml_cat = "Kids"
    else: tags.append("Entertainment")
    return ";".join(tags), xml_cat

def indent(elem, level=0):
    i = "\n" + level*"  "
    if len(elem):
        if not elem.text or not elem.text.strip(): elem.text = i + "  "
        if not elem.tail or not elem.tail.strip(): elem.tail = i
        for elem in elem: indent(elem, level+1)
        if not elem.tail or not elem.tail.strip(): elem.tail = i
    else:
        if level and (not elem.tail or not elem.tail.strip()): elem.tail = i

def update_cache():
    token = handshake()
    headers = STB_HEADERS.copy()
    headers["Cookie"] = f"mac={MAC}; stb_lang=en; timezone={USER_TIMEZONE};"
    headers["Authorization"] = f"Bearer {token}"
    try:
        logging.info("Updating Channel Cache...")
        resp = requests.get(BASE_URL, params={"type": "itv", "action": "get_all_channels"}, headers=headers, timeout=40)
        all_channels = resp.json().get('js', {}).get('data', [])
        
        epg_map = {}
        genre_ids = ['3', '511', '1469', '1468', '1467', '1433', '1499', '4', '1404', '951', '62', '19', '1350']
        for g_id in genre_ids:
            try:
                e_resp = requests.get(BASE_URL, params={"type": "itv", "action": "get_epg_info", "genre_id": g_id}, headers=headers, timeout=30).json()
                if 'js' in e_resp and 'data' in e_resp['js']: epg_map.update(e_resp['js']['data'])
            except: pass
        
        processed = []
        m3u = "#EXTM3U\n"
        tv = ET.Element("tv", {"generator-info-name": "IPTV Proxy"})
        for ch in all_channels:
            group, xml_cat = get_category_info(ch.get('name', ''))
            if not group: continue
            ch_id = str(ch.get('id'))
            display_name = clean_name(ch.get('name', ''))
            processed.append({'id': ch_id, 'display_name': display_name, 'group': group, 'xml_cat': xml_cat, 'logo': ch.get('logo', ''), 'cmd': ch.get('cmd')})
            if ch_id in epg_map:
                chan_tag = ET.SubElement(tv, "channel", {"id": ch_id})
                ET.SubElement(chan_tag, "display-name").text = display_name
                if ch.get('logo'): ET.SubElement(chan_tag, "icon", {"src": ch.get('logo')})
        
        tz = ZoneInfo(USER_TIMEZONE)
        now = datetime.now(tz)
        for item in processed:
            cid = item["id"]
            m3u += f'#EXTINF:-1 tvg-id="{cid}" tvg-name="{item["display_name"]}" tvg-logo="{item["logo"]}" group-title="{item["group"]}",{item["display_name"]}\n'
            m3u += f'{PROXY_BASE}/play/{cid}.ts\n'
            real_progs = epg_map.get(cid, [])
            if real_progs:
                real_progs.sort(key=lambda x: int(x['start_timestamp']))
                first_start = datetime.fromtimestamp(int(real_progs[0]['start_timestamp']), tz=tz)
                bridge_start = (now - timedelta(hours=6)).replace(minute=0, second=0, microsecond=0)
                if bridge_start < first_start:
                    prog = ET.SubElement(tv, "programme", {"start": bridge_start.strftime("%Y%m%d%H%M%S %z"), "stop": first_start.strftime("%Y%m%d%H%M%S %z"), "channel": cid})
                    ET.SubElement(prog, "title", {"lang": "en"}).text = f"Live: {item['display_name']}"
                    ET.SubElement(prog, "desc", {"lang": "en"}).text = f"Currently airing on {item['display_name']}."
                    ET.SubElement(prog, "category", {"lang": "en"}).text = item["xml_cat"]
                for p in real_progs:
                    try:
                        p_start = datetime.fromtimestamp(int(p['start_timestamp']), tz=tz).strftime("%Y%m%d%H%M%S %z")
                        p_end = datetime.fromtimestamp(int(p['stop_timestamp']), tz=tz).strftime("%Y%m%d%H%M%S %z")
                        prog = ET.SubElement(tv, "programme", {"start": p_start, "stop": p_end, "channel": cid})
                        ET.SubElement(prog, "title", {"lang": "en"}).text = p.get('name', 'Live')
                        ET.SubElement(prog, "desc", {"lang": "en"}).text = p.get('descr', 'No description available.')
                        ET.SubElement(prog, "category", {"lang": "en"}).text = item["xml_cat"]
                    except: pass
            else:
                start_fallback = (now - timedelta(hours=6)).replace(minute=0, second=0, microsecond=0)
                for i in range(24):
                    p_start = (start_fallback + timedelta(hours=i)).strftime("%Y%m%d%H%M%S %z")
                    p_end = (start_fallback + timedelta(hours=i+1)).strftime("%Y%m%d%H%M%S %z")
                    prog = ET.SubElement(tv, "programme", {"start": p_start, "stop": p_end, "channel": cid})
                    ET.SubElement(prog, "title", {"lang": "en"}).text = f"Live: {item['display_name']}"
                    ET.SubElement(prog, "desc", {"lang": "en"}).text = f"Live stream for {item['display_name']}."
                    ET.SubElement(prog, "category", {"lang": "en"}).text = item["xml_cat"]
        indent(tv)
        xml_str = ET.tostring(tv, encoding='utf-8', xml_declaration=True).decode('utf-8')
        if os.path.exists(os.path.dirname(XMLTV_PATH)):
            with open(XMLTV_PATH, "w", encoding="utf-8") as f: f.write(xml_str)
        with cache_lock:
            cache["channels"], cache["playlist"], cache["xmltv"], cache["last_update"] = processed, m3u, xml_str, time.time()
        logging.info(f"Cache update complete: {len(processed)} channels.")
    except Exception as e: logging.error(f"Update error: {e}")

def background_worker():
    while True:
        try: update_cache()
        except: pass
        time.sleep(4 * 3600)

@app.route('/playlist.m3u')
def playlist():
    if not cache["playlist"]: update_cache()
    return Response(cache["playlist"], mimetype='audio/x-mpegurl')

@app.route('/xmltv.xml')
def xmltv():
    if not cache["xmltv"]: update_cache()
    return Response(cache["xmltv"], mimetype='text/xml')

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
                parts = cmd_val.split('?')[0].split('/')
                token_part = cmd_val.split('play_token=')[-1]
                last_url = f"{'/'.join(parts[:3])}/{parts[3]}/{parts[4]}/{parts[-1]}?play_token={token_part}"
            try:
                with requests.get(last_url, headers=last_headers, stream=True, timeout=15) as upstream:
                    if upstream.status_code == 403:
                        last_url, fail_count = None, fail_count + 1; continue
                    fail_count = 0
                    for chunk in upstream.iter_content(chunk_size=512*1024):
                        if chunk:
                            data = leftover + chunk
                            align_idx = (len(data) // TS_PACKET_SIZE) * TS_PACKET_SIZE
                            to_send = data[:align_idx]
                            leftover = data[align_idx:]
                            if to_send: yield to_send
            except GeneratorExit: break
            except Exception:
                last_url, fail_count = None, fail_count + 1
                time.sleep(random.uniform(0.3, 1.0))
    return Response(stream_with_context(generate()), content_type='video/mp2t', headers={'Connection': 'keep-alive'}, direct_passthrough=True)

if __name__ == '__main__':
    # Initialize cache immediately
    threading.Thread(target=update_cache).start()
    threading.Thread(target=background_worker, daemon=True).start()
    app.run(host='0.0.0.0', port=8081, threaded=True)
