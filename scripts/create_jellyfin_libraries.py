import os
import requests
import argparse
from urllib.parse import quote

def create_libraries(jellyfin_url, username, password, movies_dir, docker_movies_dir):
    print(f"Authenticating with Jellyfin at {jellyfin_url}...")
    headers = {
        "X-Emby-Authorization": 'MediaBrowser Client="CLI", Device="AutomatedSetup", DeviceId="12345", Version="1.0.0"',
        "Content-Type": "application/json"
    }

    try:
        res = requests.post(f"{jellyfin_url}/Users/AuthenticateByName", json={"Username": username, "Pw": password}, headers=headers).json()
        token = res["AccessToken"]
        headers["X-Emby-Token"] = token
    except Exception as e:
        print(f"Authentication failed: {e}")
        return

    print("Fetching existing libraries...")
    existing_res = requests.get(f"{jellyfin_url}/Library/VirtualFolders", headers=headers).json()
    existing_names = [lib["Name"] for lib in existing_res]

    if not os.path.exists(movies_dir):
        print(f"Directory not found: {movies_dir}")
        return

    categories = [d for d in os.listdir(movies_dir) if os.path.isdir(os.path.join(movies_dir, d))]

    created_count = 0
    for cat in categories:
        cat_path = os.path.join(movies_dir, cat)
        
        # Check if category has any .strm files (or .nfo files)
        has_files = any(f.endswith(".strm") or f.endswith(".nfo") for f in os.listdir(cat_path))
        if not has_files: 
            continue
        
        lib_name = f"IPTV {cat}"
        if lib_name in existing_names: 
            continue
        
        docker_path = f"{docker_movies_dir}/{cat}"
        
        url = f"{jellyfin_url}/Library/VirtualFolders?name={quote(lib_name)}&collectionType=movies&refreshLibrary=false&paths={quote(docker_path)}"
        print(f"Creating library: {lib_name} -> {docker_path}")
        
        res = requests.post(url, headers=headers)
        
        if res.status_code in [200, 204]:
            print(f"Successfully created library: {lib_name}")
            created_count += 1
        else:
            print(f"Failed to create {lib_name}: {res.status_code} {res.text}")

    if created_count > 0:
        print("Triggering library refresh...")
        requests.post(f"{jellyfin_url}/Library/Refresh", headers=headers)
        
    print(f"Finished adding {created_count} libraries.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Create Jellyfin libraries for IPTV VODs")
    parser.add_argument("--jellyfin-url", required=True, help="Jellyfin base URL (e.g. http://localhost:8096/jellyfin)")
    parser.add_argument("--username", required=True, help="Jellyfin username")
    parser.add_argument("--password", required=True, help="Jellyfin password")
    parser.add_argument("--movies-dir", required=True, help="Host path to the IPTV Movies directory")
    parser.add_argument("--docker-movies-dir", required=True, help="Docker internal path to the IPTV Movies directory")
    
    args = parser.parse_args()
    
    create_libraries(args.jellyfin_url, args.username, args.password, args.movies_dir, args.docker_movies_dir)