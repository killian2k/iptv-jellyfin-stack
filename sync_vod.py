import os
import re
import requests
import json
import time

BASE_URL = "http://your-provider.com:8000/server/load.php"
MAC = "00:00:00:00:00:00"
UA = "Mozilla/5.0 (QtEmbedded; U; Linux; C) AppleWebKit/531.2+ (KHTML, like Gecko) Version/4.0 Safari/531.2+ STB/MAG256"

PROXY_BASE = "http://192.168.1.100/iptv/vod"
MOVIES_DIR = os.getenv("IPTV_MOVIES_DIR", "/mnt/nvme/MediaLibrary/IPTV_Movies")

def handshake():
    h_headers = {"Cookie": f"mac={MAC}", "User-Agent": UA}
    try:
        resp = requests.get(BASE_URL, params={"type": "stb", "action": "handshake"}, headers=h_headers, timeout=5)
        return resp.json()["js"]["token"]
    except Exception as e:
        print(f"Handshake error: {e}")
        return None

def sanitize_filename(name):
    return re.sub(r'[\\/*?:"<>|]', "", name).strip()

QUALITY_RANK = {
    '4K': 4,
    'UHD': 4,
    'FHD': 3,
    '1080P': 3,
    'HD': 2,
    '720P': 2,
    'SD': 1
}

def parse_vod(name):
    # Strip all leading tags like |VO|, |STFR|, |FR|, etc.
    clean = re.sub(r'^(\|[^\|]+\|\s*)+', '', name).strip()
    
    # Extract quality
    quality = ''
    rank = 1
    upper = clean.upper()
    for q, r in QUALITY_RANK.items():
        if f" {q}" in upper or upper.endswith(f" {q}"):
            if r > rank:
                quality = q
                rank = r
                
    # Base name for deduplication
    base_name = clean
    for q in QUALITY_RANK.keys():
        base_name = re.sub(rf'\s+{q}(\s+|$)', ' ', base_name, flags=re.IGNORECASE).strip()
        
    return base_name, quality, rank

def sanitize_folder(name):
    # Remove |FR|, |QC|, etc. and trim
    clean = re.sub(r'^\|[^\|]+\|\s*', '', name).strip()
    return re.sub(r'[\\/*?:"<>|]', "", clean).strip()

GENERIC_CATEGORIES = ["NOUVEAUTÉS", "TOP 100", "FILMS DE NOËL", "4K HDR | SDR", "4K DOLBY VISION", "4K HDR10+", "HEVC | X265", "FILMS SD | WEBRIP | HDCAM", "V.O SOUS TITRÉS"]

def sync_vods():
    token = handshake()
    if not token:
        print("Handshake failed")
        return

    headers = {
        "Cookie": f"mac={MAC}; stb_lang=en; timezone=Europe/Paris;",
        "User-Agent": UA,
        "Authorization": f"Bearer {token}",
        "X-User-Agent": "Model: MAG256; Link: WiFi"
    }

    resp = requests.get(BASE_URL, params={"type": "vod", "action": "get_categories"}, headers=headers, timeout=10)
    categories = resp.json().get("js", [])
    
    os.makedirs(MOVIES_DIR, exist_ok=True)
    
    best_vods = {} # base_name -> vod_data
    
    for cat in categories:
        cat_id = cat.get("id")
        cat_title = cat.get("title", "Other")
        if cat_id == "*" or not cat_id: continue
        if "|FR|" not in cat_title and "|QC|" not in cat_title: continue # Focus on FR/QC
        
        folder_name = sanitize_folder(cat_title)
        is_generic = any(g in folder_name.upper() for g in GENERIC_CATEGORIES)
        
        print(f"Fetching VODs for category: {cat_title}")
        try:
            vod_resp = requests.get(BASE_URL, params={"type": "vod", "action": "get_ordered_list", "category": cat_id}, headers=headers, timeout=20)
            vods = vod_resp.json().get("js", {}).get("data", [])
            # SMALL DELAY TO AVOID BLOCKING
            time.sleep(0.5)
        except Exception as e:
            print(f"Failed to fetch {cat_title}: {e}")
            continue
            
        for vod in vods:
            name = vod.get("name", "")
            vod_id = vod.get("id")
            if not name or not vod_id: continue
            
            base_name, quality, rank = parse_vod(name)
            
            if base_name not in best_vods:
                best_vods[base_name] = {
                    'id': vod_id,
                    'rank': rank,
                    'quality': quality,
                    'category': folder_name,
                    'is_generic_cat': is_generic
                }
            else:
                # Update if higher quality found
                if rank > best_vods[base_name]['rank']:
                    best_vods[base_name].update({
                        'id': vod_id,
                        'rank': rank,
                        'quality': quality
                    })
                    # If current best cat is generic and new one is not, swap cat
                    if best_vods[base_name]['is_generic_cat'] and not is_generic:
                        best_vods[base_name]['category'] = folder_name
                        best_vods[base_name]['is_generic_cat'] = False
                # If same quality but current cat is generic and new one is not, swap cat
                elif rank == best_vods[base_name]['rank'] and best_vods[base_name]['is_generic_cat'] and not is_generic:
                    best_vods[base_name]['category'] = folder_name
                    best_vods[base_name]['is_generic_cat'] = False

    active_files = set()
    for base_name, info in best_vods.items():
        cat_dir = os.path.join(MOVIES_DIR, info['category'])
        os.makedirs(cat_dir, exist_ok=True)
        
        filename = f"{sanitize_filename(base_name)} [{info['id']}].strm"
        rel_path = os.path.join(info['category'], filename)
        active_files.add(rel_path)
        
        filepath = os.path.join(MOVIES_DIR, rel_path)
        if not os.path.exists(filepath):
            try:
                with open(filepath, 'w') as f:
                    f.write(f"{PROXY_BASE}/{info['id']}.mkv\n")
            except:
                pass

    # Cleanup old movies and empty folders
    for root, dirs, files in os.walk(MOVIES_DIR, topdown=False):
        for name in files:
            if name.endswith(".strm"):
                full_path = os.path.join(root, name)
                rel_path = os.path.relpath(full_path, MOVIES_DIR)
                if rel_path not in active_files:
                    try:
                        os.remove(full_path)
                        print(f"Removed: {rel_path}")
                    except:
                        pass
        # Remove empty directories
        for name in dirs:
            dir_path = os.path.join(root, name)
            if not os.listdir(dir_path):
                try:
                    os.rmdir(dir_path)
                    print(f"Removed empty folder: {dir_path}")
                except:
                    pass
                
    print(f"VOD Sync Complete! {len(active_files)} unique movies synced across {len(set(v['category'] for v in best_vods.values()))} categories.")

if __name__ == "__main__":
    sync_vods()