import os, re, requests, json, time, unicodedata

# CREDENTIALS
BASE_URL = "http://pure-ott.com:8000/server/load.php"
MAC = "00:1A:79:3F:6E:5a"
UA = "Mozilla/5.0 (QtEmbedded; U; Linux; C) AppleWebKit/531.2+ (KHTML, like Gecko) Version/4.0 Safari/531.2+ STB/MAG256"
PROXY_BASE = "http://192.168.1.100/iptv/vod"
MOVIES_DIR = "/mnt/nvme/MediaLibrary/IPTV_Movies"

def unaccent(text):
    return "".join(c for c in unicodedata.normalize('NFD', text) if unicodedata.category(c) != 'Mn')

def clean_title(name):
    # 1. Remove bracketed/piped tags at the start (e.g. |FR|, [FR], (FR))
    clean = re.sub(r'^(\[[^\]]+\]|\|[^\|]+\||\([^\)]+\))\s*', '', name).strip()
    # 2. Remove specific prefixes like FR, QC, VOSTFR, STFR if followed by space
    clean = re.sub(r'^(FR|QC|VOSTFR|STFR|MULTIAUDIO|MULTI)\s+', '', clean, flags=re.IGNORECASE).strip()
    # 3. Deduplicate years: (1979) (1979) -> (1979)
    clean = re.sub(r'(\(\d{4}\))\s+\1', r'\1', clean)
    # 4. Clean up multiple spaces
    clean = re.sub(r'\s+', ' ', clean).strip()
    return clean

def sanitize_filename(name):
    # Escape for XML and remove illegal chars
    clean = name.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    return re.sub(r'[\\/*?:"<>|]', "", clean).strip()

def handshake():
    try:
        r = requests.get(BASE_URL, params={"type": "stb", "action": "handshake"}, headers={"Cookie": f"mac={MAC}", "User-Agent": UA}, timeout=10)
        return r.json()["js"]["token"]
    except: return None

TARGET_CATS = {
    "21": "IPTV NOUVEAUTE FR",
    "1398": "TOP 100",
    "1397": "FILMS DE NOEL",
    "36": "DOCUMENTAIRES",
    "30": "THRILLER CRIME",
    "25": "ACTION AVENTURE",
    "32": "DRAME",
    "27": "COMEDIE",
    "35": "HORREUR",
    "1429": "TELEFILM",
    "288": "ANIMATIONS",
    "1402": "QC FILMS",
    "1434": "4K DOLBY VISION",
    "1345": "4K HDR"
}

def sync():
    token = handshake()
    if not token: return
    headers = {"Cookie": f"mac={MAC}; stb_lang=en; timezone=Europe/Paris;", "User-Agent": UA, "Authorization": f"Bearer {token}", "X-User-Agent": "Model: MAG256; Link: WiFi"}
    
    active_files = set()
    for cid, folder in TARGET_CATS.items():
        print(f"Syncing {folder}...")
        path = os.path.join(MOVIES_DIR, folder)
        os.makedirs(path, exist_ok=True)
        
        page = 1
        while page < 300:
            try:
                r = requests.get(BASE_URL, params={"type": "vod", "action": "get_ordered_list", "category": cid, "p": page}, headers=headers, timeout=20).json()
                data = r.get("js", {}).get("data", [])
                if not data: break
                
                for v in data:
                    raw_name = v.get("name", "")
                    vid = v.get("id")
                    if not raw_name or not vid: continue
                    
                    # CLEAN TITLE Logic
                    cleaned_name = clean_title(raw_name)
                    safe_filename = sanitize_filename(cleaned_name)
                    
                    filename_base = f"{safe_filename} [{vid}]"
                    strm, nfo = f"{folder}/{filename_base}.strm", f"{folder}/{filename_base}.nfo"
                    active_files.add(strm); active_files.add(nfo)
                    
                    strm_path = os.path.join(MOVIES_DIR, strm)
                    nfo_path = os.path.join(MOVIES_DIR, nfo)
                    
                    if not os.path.exists(strm_path):
                        with open(strm_path, 'w') as f: f.write(f"{PROXY_BASE}/{vid}.mkv\n")
                    # Always overwrite NFO to apply name fixes
                    collection_name = f"IPTV {folder}" if not folder.startswith("IPTV") else folder
                    with open(nfo_path, 'w', encoding='utf-8') as f:
                        f.write(f'<?xml version="1.0" encoding="utf-8" standalone="yes"?><movie><title>{cleaned_name}</title><set><name>{collection_name}</name></set></movie>')
                
                if len(data) < 10: break
                page += 1
            except: break
    
    print("Cleanup...")
    for root, dirs, files in os.walk(MOVIES_DIR, topdown=False):
        for name in files:
            if name.endswith(".strm") or name.endswith(".nfo"):
                rel_path = os.path.relpath(os.path.join(root, name), MOVIES_DIR)
                if rel_path not in active_files:
                    try: os.remove(os.path.join(root, name))
                    except: pass
        for name in dirs:
            p = os.path.join(root, name)
            if not os.listdir(p):
                try: os.rmdir(p)
                except: pass
    print("Done!")

if __name__ == "__main__":
    sync()
