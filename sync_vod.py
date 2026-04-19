import os, re, requests, json, time, unicodedata

BASE_URL = "http://your-provider.com:8000/server/load.php"
MAC = "00:00:00:00:00:00"
UA = "Mozilla/5.0 (QtEmbedded; U; Linux; C) AppleWebKit/531.2+ (KHTML, like Gecko) Version/4.0 Safari/531.2+ STB/MAG256"
PROXY_BASE = "http://192.168.1.100/iptv/vod"
MOVIES_DIR = "/mnt/nvme/MediaLibrary/IPTV_Movies"

def unaccent(text):
    return "".join(c for c in unicodedata.normalize('NFD', text) if unicodedata.category(c) != 'Mn')

def clean_title(name):
    clean = re.sub(r'^(\[[^\]]+\]|\|[^\|]+\||\([^\)]+\))\s*', '', name).strip()
    clean = re.sub(r'^(FR|QC|VOSTFR|STFR|MULTIAUDIO|MULTI)\s+', '', clean, flags=re.IGNORECASE).strip()
    clean = re.sub(r'(\(\d{4}\))\s+\1', r'\1', clean)
    clean = re.sub(r'\s+', ' ', clean).strip()
    return clean

def sanitize_filename(name):
    clean = name.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    return re.sub(r'[\\/*?:"<>|]', "", clean).strip()

def sanitize_folder(name):
    clean = re.sub(r'^(\|[^\|]+\|\s*)+', '', name).strip()
    clean = re.sub(r'[\\/*?:"<>|]', "", clean).strip()
    return unaccent(clean).upper()

QUALITY_RANK = {'4K': 4, 'UHD': 4, 'FHD': 3, '1080P': 3, 'HD': 2, '720P': 2, 'SD': 1}

def parse_vod(name):
    clean = re.sub(r'^(\|[^\|]+\|\s*)+', '', name).strip()
    quality, rank = '', 1
    upper = clean.upper()
    for q, r in QUALITY_RANK.items():
        if f" {q}" in upper or upper.endswith(f" {q}"):
            if r > rank: quality, rank = q, r
    base_name = clean
    for q in QUALITY_RANK.keys():
        base_name = re.sub(rf'\s+{q}(\s+|$)', ' ', base_name, flags=re.IGNORECASE).strip()
    return base_name, quality, rank

def handshake():
    try:
        resp = requests.get(BASE_URL, params={"type": "stb", "action": "handshake"}, headers={"Cookie": f"mac={MAC}", "User-Agent": UA}, timeout=10)
        return resp.json()["js"]["token"]
    except: return None

def sync_vods():
    token = handshake()
    if not token: return
    headers = {"Cookie": f"mac={MAC}; stb_lang=en; timezone=Europe/Zurich;", "User-Agent": UA, "Authorization": f"Bearer {token}", "X-User-Agent": "Model: MAG256; Link: WiFi"}
    
    print("Fetching categories...")
    categories = requests.get(BASE_URL, params={"type": "vod", "action": "get_categories"}, headers=headers, timeout=15).json().get("js", [])
    os.makedirs(MOVIES_DIR, exist_ok=True)
    
    active_files = set()
    active_folders = set()
    SPECIAL_FR_NOUVEAUTE = "IPTV NOUVEAUTE FR"

    for cat in categories:
        cat_id, cat_title = cat.get("id"), cat.get("title", "Other")
        if cat_id == "*" or not cat_id: continue
        
        # Only process FR, QC, VO, ENG, MULTI categories
        if not any(tag in cat_title for tag in ["|FR|", "|QC|", "|VO|", "|ENG|", "|MULTI|"]): continue
        
        folder_name = sanitize_folder(cat_title)
        is_fr = "|FR|" in cat_title or "|QC|" in cat_title
        
        # Special naming for Nouveautés
        if "NOUVEAUTE" in folder_name and is_fr:
            target_folder = SPECIAL_FR_NOUVEAUTE
        else:
            target_folder = folder_name
        
        print(f"Syncing: {cat_title} (ID: {cat_id}) -> {target_folder}")
        cat_dir = os.path.join(MOVIES_DIR, target_folder)
        os.makedirs(cat_dir, exist_ok=True)
        active_folders.add(target_folder)

        cat_best_vods = {}
        page = 1
        while page < 500:
            try:
                vod_resp = requests.get(BASE_URL, params={"type": "vod", "action": "get_ordered_list", "category": cat_id, "p": page}, headers=headers, timeout=20).json()
                data = vod_resp.get("js", {}).get("data", [])
                if not data: break
                
                for vod in data:
                    name, vod_id = vod.get("name", ""), vod.get("id")
                    if not name or not vod_id: continue
                    
                    cleaned_display_name = clean_title(name)
                    base_name, quality, rank = parse_vod(name)
                    
                    if cleaned_display_name not in cat_best_vods or rank > cat_best_vods[cleaned_display_name]['rank']:
                        cat_best_vods[cleaned_display_name] = {'id': vod_id, 'name': cleaned_display_name, 'rank': rank}

                if len(data) < 10: break
                page += 1
            except: break

        for info in cat_best_vods.values():
            safe_filename = sanitize_filename(info['name'])
            filename_base = f"{safe_filename} [{info['id']}]"
            strm_rel, nfo_rel = os.path.join(target_folder, f"{filename_base}.strm"), os.path.join(target_folder, f"{filename_base}.nfo")
            active_files.add(strm_rel); active_files.add(nfo_rel)
            
            strm_path, nfo_path = os.path.join(MOVIES_DIR, strm_rel), os.path.join(MOVIES_DIR, nfo_rel)
            if not os.path.exists(strm_path):
                with open(strm_path, 'w') as f: f.write(f"{PROXY_BASE}/{info['id']}.mkv\n")
            
            collection_name = f"IPTV {target_folder}" if not target_folder.startswith("IPTV") else target_folder
            with open(nfo_path, 'w', encoding='utf-8') as f:
                f.write('<?xml version="1.0" encoding="utf-8" standalone="yes"?>\n<movie>\n  <title>' + info['name'] + '</title>\n  <set>\n    <name>' + collection_name + '</name>\n  </set>\n</movie>\n')

    print("Cleaning up orphans...")
    for root, dirs, files in os.walk(MOVIES_DIR, topdown=False):
        for name in files:
            if name.endswith(".strm") or name.endswith(".nfo"):
                rel_path = os.path.relpath(os.path.join(root, name), MOVIES_DIR)
                if rel_path not in active_files:
                    try: os.remove(os.path.join(root, name))
                    except: pass
        for name in dirs:
            dir_path = os.path.join(root, name)
            if not os.listdir(dir_path):
                try: os.rmdir(dir_path)
                except: pass
    print(f"VOD Sync Complete! Total files tracked: {len(active_files)}")

if __name__ == "__main__":
    sync_vods()
