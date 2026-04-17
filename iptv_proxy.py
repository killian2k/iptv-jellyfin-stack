from flask import Flask, Response, stream_with_context, request
import requests
import logging
import time
import threading
import re
import json
import base64
from datetime import datetime, timedelta

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

BASE_URL = "http://your-provider.com:8000/server/load.php"
MAC = "00:00:00:00:00:00"
UA = "Mozilla/5.0 (QtEmbedded; U; Linux; C) AppleWebKit/531.2+ (KHTML, like Gecko) Version/4.0 Safari/531.2+ STB/MAG256"
PROXY_BASE = "http://192.168.1.100/iptv"

cache = {
    "token": None,
    "token_time": 0,
    "channels": [],
    "playlist": None,
    "xmltv": None,
    "last_update": 0
}
cache_lock = threading.Lock()

def handshake():
    with cache_lock:
        now = time.time()
        if cache["token"] and (now - cache["token_time"] < 1800):
            return cache["token"]
        h_headers = {"Cookie": f"mac={MAC}", "User-Agent": UA}
        try:
            resp = requests.get(BASE_URL, params={"type": "stb", "action": "handshake"}, headers=h_headers, timeout=15)
            token = resp.json().get('js', {}).get('token')
            if token:
                cache["token"] = token
                cache["token_time"] = now
                return token
        except: pass
        return None

def clean_name(name):
    clean = re.sub(r'\|[^\|]+\|', '', name)
    clean = re.sub(r'USA:', '', clean)
    clean = re.sub(r'[▼●★■□▲▶▷\-\s]+', ' ', clean).strip()
    return clean

def get_category_info(name):
    name_upper = name.upper()
    if any(x in name_upper for x in ["---", "▼", "▲", "●", "★"]): return None, None
    country = "Other"
    if "|FR|" in name_upper: country = "FR"
    elif "|US|" in name_upper or "USA:" in name_upper: country = "US"
    elif "|UK|" in name_upper: country = "UK"
    elif "|CA|" in name_upper: country = "CA"
    is_sport = any(x in name_upper for x in ["SPORT", "BEIN", "RMC", "EUROSPORT", "DAZN", "EQUIPE", "GOLF", "FOOT", "ELEVEN", "NBA", "ESPN", "TNT US", "FS1", "FS2", "NBCS", "GOLTV"])
    if country == "FR":
        if is_sport: return "FR - Sports", "Sports"
        if any(x in name_upper for x in ["CANAL+", "CINE+", "OCS"]): return "FR - Cinema", "Movies"
        return "FR - Entertainment", "Entertainment"
    if country == "US":
        if is_sport: return "US - Sports", "Sports"
        return "US - Entertainment", "Entertainment"
    if is_sport: return f"{country} - Sports", "Sports"
    return f"{country} - General", "Entertainment"

def update_cache():
    token = handshake()
    if not token: return
    headers = {"Cookie": f"mac={MAC}; stb_lang=en; timezone=Europe/Paris;", "User-Agent": UA, "Authorization": f"Bearer {token}"}
    try:
        resp = requests.get(BASE_URL, params={"type": "itv", "action": "get_all_channels"}, headers=headers, timeout=40)
        channels = resp.json().get('js', {}).get('data', [])
        processed = []
        m3u = "#EXTM3U\n"
        xmltv = '<?xml version="1.0" encoding="UTF-8"?>\n<tv generator-info-name="IPTV Proxy">\n'
        priority_list = ["US - Sports", "FR - Sports", "FR - Entertainment", "US - Entertainment"]
        for ch in channels:
            group, xml_cat = get_category_info(ch.get('name', ''))
            if not group: continue
            ch_id = str(ch.get('id'))
            display_name = clean_name(ch.get('name', ''))
            processed.append({'id': ch_id, 'display_name': display_name, 'group': group, 'xml_cat': xml_cat, 'logo': ch.get('logo', ''), 'cmd': ch.get('cmd')})
        processed.sort(key=lambda x: (priority_list.index(x['group']) if x['group'] in priority_list else 99, x['group'], x['display_name']))
        for item in processed:
            m3u += f'#EXTINF:-1 tvg-id="{item["id"]}" tvg-name="{item["display_name"]}" tvg-logo="{item["logo"]}" group-title="{item["group"]}",{item["display_name"]}\n'
            m3u += f'{PROXY_BASE}/play/{item["id"]}.ts\n'
            xmltv += f'  <channel id="{item["id"]}"><display-name>{item["display_name"]}</display-name>'
            if item["logo"]: xmltv += f'<icon src="{item["logo"]}" />'
            xmltv += '</channel>\n'
        now = datetime.now()
        start = now.replace(minute=0, second=0, microsecond=0)
        for item in processed:
            for i in range(24):
                p_start = (start + timedelta(hours=i)).strftime("%Y%m%d%H%M%S +0000")
                p_end = (start + timedelta(hours=i+1)).strftime("%Y%m%d%H%M%S +0000")
                xmltv += f'  <programme start="{p_start}" stop="{p_end}" channel="{item["id"]}">\n'
                xmltv += f'    <title lang="en">Live: {item["display_name"]}</title>\n'
                xmltv += f'    <category lang="en">{item["xml_cat"]}</category>\n'
                xmltv += f'  </programme>\n'
        xmltv += '</tv>'
        with cache_lock:
            cache["channels"] = channels
            cache["playlist"] = m3u
            cache["xmltv"] = xmltv
            cache["last_update"] = time.time()
        logging.info(f"Cache updated: {len(processed)} channels.")
    except Exception as e: logging.error(f"Update error: {e}")

def background_worker():
    while True:
        try: update_cache()
        except: pass
        time.sleep(12 * 3600)

threading.Thread(target=background_worker, daemon=True).start()

@app.after_request
def add_headers(response):
    response.headers['Access-Control-Allow-Origin'] = '*'
    return response

@app.route('/playlist.m3u')
def playlist():
    if not cache["playlist"]: update_cache()
    return Response(cache["playlist"], mimetype='audio/x-mpegurl')

@app.route('/xmltv.xml')
def xmltv():
    if not cache["xmltv"]: update_cache()
    return Response(cache["xmltv"], mimetype='text/xml')

@app.route('/play/<ch_id>.ts', methods=['GET', 'HEAD', 'OPTIONS'])
def play(ch_id):
    if request.method == 'OPTIONS': return Response()
    cid = ch_id.replace('.ts', '')
    if request.method == 'HEAD': return Response(content_type='video/mp2t')
    token = handshake()
    ch_data = next((c for c in cache["channels"] if str(c.get('id')) == cid), None)
    if not ch_data: return "Not found", 404
    headers = {"Cookie": f"mac={MAC}; stb_lang=en; timezone=Europe/Paris;", "User-Agent": UA, "Authorization": f"Bearer {token}", "X-User-Agent": "Model: MAG256; Link: WiFi"}
    try:
        resp = requests.get(BASE_URL, params={"type": "itv", "action": "create_link", "cmd": ch_data.get('cmd')}, headers=headers, timeout=25)
        js = resp.json().get('js', {})
        cmd_val = js.get('cmd', '')
        if cmd_val.startswith('ffmpeg '): cmd_val = cmd_val[7:]
        
        parts = cmd_val.split('?')[0].split('/')
        token_part = cmd_val.split('play_token=')[-1]
        base_url = '/'.join(parts[:3])
        final_url = f"{base_url}/{parts[3]}/{parts[4]}/{parts[-1]}?play_token={token_part}"
        
        logging.info(f"Proxying CID {cid} via {final_url}")
        upstream = requests.get(final_url, headers=headers, stream=True, timeout=30)
        
        def generate():
            try:
                for chunk in upstream.iter_content(chunk_size=128*1024):
                    if chunk: yield chunk
            except Exception as e: logging.error(f"Stream error: {e}")
        
        # REMOVED Transfer-Encoding: chunked to prevent 502 Bad Gateway via Nginx
        # Added direct_passthrough=True for optimal streaming
        return Response(stream_with_context(generate()), content_type='video/mp2t', headers={'Connection': 'keep-alive'}, direct_passthrough=True)
    except Exception as e:
        logging.error(f"Play error: {e}")
        return f"Error: {e}", 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8081, threaded=True)
