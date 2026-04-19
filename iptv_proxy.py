from flask import Flask, Response, stream_with_context, request, redirect
import requests
import logging
import time
import threading
import re
import json
import base64
import os
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# CONFIGURATION
BASE_URL = "http://your-provider.com:8000/server/load.php"
MAC = "00:00:00:00:00:00"
UA = "Mozilla/5.0 (QtEmbedded; U; Linux; C) AppleWebKit/531.2+ (KHTML, like Gecko) Version/4.0 Safari/531.2+ STB/MAG256"
PROXY_BASE = "http://192.168.1.100/iptv"
XMLTV_PATH = "/app/data/xmltv.xml"
USER_TIMEZONE = "Europe/Zurich"

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
        return cache["token"]

def get_stream_link(stream_type, cmd_val, ch_id):
    """Smart Fallback: Try Passive (MAC only) first, then Active (Token)."""
    # 1. Try PASSIVE (No token, uses MAC cookie)
    headers = {"Cookie": f"mac={MAC}; stb_lang=en; timezone={USER_TIMEZONE};", "User-Agent": UA, "X-User-Agent": "Model: MAG256; Link: WiFi"}
    try:
        logging.info(f"Attempting PASSIVE link generation for {stream_type} {ch_id}...")
        resp = requests.get(BASE_URL, params={"type": stream_type, "action": "create_link", "cmd": cmd_val}, headers=headers, timeout=20).json()
        link = resp.get('js', {}).get('cmd', '')
        if link:
            logging.info(f"PASSIVE Success for {ch_id}")
            return link, headers
    except: pass

    # 2. Fallback to ACTIVE (Requires Token)
    logging.warning(f"Passive failed for {ch_id}, falling back to ACTIVE mode.")
    token = handshake()
    headers["Authorization"] = f"Bearer {token}"
    try:
        resp = requests.get(BASE_URL, params={"type": stream_type, "action": "create_link", "cmd": cmd_val}, headers=headers, timeout=20).json()
        return resp.get('js', {}).get('cmd', ''), headers
    except Exception as e:
        logging.error(f"Active fallback failed: {e}")
        return None, headers

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
    headers = {"Cookie": f"mac={MAC}; stb_lang=en; timezone={USER_TIMEZONE};", "User-Agent": UA, "Authorization": f"Bearer {token}", "X-User-Agent": "Model: MAG256; Link: WiFi"}
    try:
        resp = requests.get(BASE_URL, params={"type": "itv", "action": "get_all_channels"}, headers=headers, timeout=40)
        channels = resp.json().get('js', {}).get('data', [])
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
        for ch in channels:
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
            cache["channels"], cache["playlist"], cache["xmltv"], cache["last_update"] = channels, m3u, xml_str, time.time()
        logging.info(f"Cache updated: {len(processed)} channels.")
    except Exception as e: logging.error(f"Update error: {e}")

def background_worker():
    while True:
        try: update_cache()
        except: pass
        time.sleep(4 * 3600)

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
    ch_data = next((c for c in cache["channels"] if str(c.get('id')) == cid), None)
    if not ch_data: return "Not found", 404
    
    cmd_val, active_headers = get_stream_link("itv", ch_data.get('cmd'), cid)
    if not cmd_val: return "Link generation failed", 500
    
    try:
        if cmd_val.startswith('ffmpeg '): cmd_val = cmd_val[7:]
        parts = cmd_val.split('?')[0].split('/')
        token_part = cmd_val.split('play_token=')[-1]
        base_url = '/'.join(parts[:3])
        final_url = f"{base_url}/{parts[3]}/{parts[4]}/{parts[-1]}?play_token={token_part}"
        
        logging.info(f"Proxying CID {cid} via {final_url}")
        upstream = requests.get(final_url, headers=active_headers, stream=True, timeout=30)
        
        if request.method == 'HEAD':
            proxy_resp = Response(status=upstream.status_code)
            for key in ['Content-Type', 'Content-Length', 'Content-Range', 'Accept-Ranges']:
                if key in upstream.headers: proxy_resp.headers[key] = upstream.headers[key]
            upstream.close()
            return proxy_resp
            
        def generate():
            try:
                for chunk in upstream.iter_content(chunk_size=128*1024):
                    if chunk: yield chunk
            except Exception as e: logging.error(f"Stream error: {e}")
        return Response(stream_with_context(generate()), content_type='video/mp2t', headers={'Connection': 'keep-alive'}, direct_passthrough=True)
    except Exception as e:
        logging.error(f"Play error: {e}")
        return f"Error: {e}", 500

@app.route('/vod/<vod_id>.<ext>', methods=['GET', 'HEAD', 'OPTIONS'])
def play_vod(vod_id, ext):
    if request.method == 'OPTIONS': return Response()
    cmd_data = {"type": "movie", "stream_id": str(vod_id), "stream_source": None, "target_container": f'["{ext}"]'}
    cmd_b64 = base64.b64encode(json.dumps(cmd_data).encode()).decode()
    
    cmd_val, active_headers = get_stream_link("vod", cmd_b64, vod_id)
    if not cmd_val: return "VOD Link generation failed", 500

    try:
        if cmd_val.startswith('ffmpeg '): cmd_val = cmd_val[7:]
        url = cmd_val.replace(f"[{ext}]", ext)
        try:
            r = requests.get(url, headers=active_headers, allow_redirects=False, stream=True, timeout=10)
            final_url = r.headers.get('Location', url)
        except: final_url = url
        
        logging.info(f"Proxying VOD {vod_id} from final URL: {final_url}")
        req_headers = active_headers.copy()
        if request.headers.get('Range'): req_headers['Range'] = request.headers.get('Range')
        upstream = requests.get(final_url, headers=req_headers, stream=True, timeout=30)
        
        if request.method == 'HEAD':
            proxy_resp = Response(status=upstream.status_code)
            for key in ['Content-Type', 'Content-Length', 'Content-Range', 'Accept-Ranges']:
                if key in upstream.headers: proxy_resp.headers[key] = upstream.headers[key]
            upstream.close()
            return proxy_resp
            
        def generate():
            bytes_yielded = 0
            max_retries = 10
            current_response = upstream
            while max_retries > 0:
                try:
                    for chunk in current_response.iter_content(chunk_size=128*1024):
                        if chunk:
                            yield chunk
                            bytes_yielded += len(chunk)
                    break
                except GeneratorExit: break
                except Exception as e:
                    logging.error(f"VOD Stream interrupted at {bytes_yielded} bytes: {e}. Reconnecting...")
                    max_retries -= 1
                    time.sleep(1)
                    try:
                        current_headers = req_headers.copy()
                        orig_start = 0
                        if 'Range' in req_headers:
                            match = re.search(r'bytes=(\d+)-', req_headers['Range'])
                            if match: orig_start = int(match.group(1))
                        new_start = orig_start + bytes_yielded
                        current_headers['Range'] = f'bytes={new_start}-'
                        current_response = requests.get(final_url, headers=current_headers, stream=True, timeout=15)
                        current_response.raise_for_status()
                    except: pass
        
        proxy_resp = Response(stream_with_context(generate()), status=upstream.status_code)
        for key in ['Content-Type', 'Content-Length', 'Content-Range', 'Accept-Ranges']:
            if key in upstream.headers: proxy_resp.headers[key] = upstream.headers[key]
        return proxy_resp
    except Exception as e:
        logging.error(f"VOD Play error: {e}")
        return f"Error: {e}", 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8081, threaded=True)
