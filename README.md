# IPTV Jellyfin Media Stack

This repository contains the configuration and custom proxy scripts to securely integrate an IPTV provider (specifically MAG box emulations) into a complete media server stack featuring Jellyfin, Nginx, and various Arr apps.

## Tech Stack
* **Jellyfin**: Media server for local files and Live TV.
* **Nginx**: Reverse proxy to manage endpoints for all services under a single port.
* **Custom Flask IPTV Proxy**: A custom Python proxy that handshakes with MAG portal providers, extracts stream URLs, generates an M3U playlist, constructs a rich XMLTV EPG guide, and tunnels the MPEG-TS video streams back to Jellyfin with the necessary headers to prevent blocking.
* **VOD Sync**: An automated script to fetch VOD movies from the provider and map them into the media library as `.strm` files.
* **Arr Stack**: Sonarr, Radarr, Prowlarr, Jellyseerr, qBittorrent.

## Files
* `docker-compose.yml`: The main compose file defining all the containers and networks.
* `nginx.conf`: Nginx routing rules and specific fixes for Jellyfin Live TV proxying.
* `iptv_proxy.py`: The Python Flask application that proxies the MAG portal IPTV streams.
* `sync_vod.py`: The standalone script to fetch and map VODs to your library.

## Setup Instructions
1. Update `iptv_proxy.py` and `sync_vod.py` with your specific provider's `BASE_URL` and your authorized `MAC` address.
2. Spin up the stack using `docker compose up -d`.
3. In Jellyfin, configure a new M3U Tuner pointing to `http://<your-ip>/iptv/playlist.m3u`.
4. Configure an XMLTV Guide Data Provider pointing to `http://<your-ip>/iptv/xmltv.xml`.
5. Enjoy categorized Live TV (Sports, Movies, Entertainment) directly in Jellyfin.

## Security Note
Make sure your environment variables (e.g., VPN credentials, API keys) are configured securely on your host before deploying the stack. No passwords or MAC addresses are hardcoded in this repo.

## Troubleshooting

### VOD Playback Crashing on Web Browsers
If Jellyfin silently drops playback of `.strm` VODs with an FFmpeg `Function not implemented` or `Invalid argument` error related to `av1_vaapi` hardware encoding, it means your GPU lacks native AV1 encoding support. 

To fix this:
1. Open `/config/encoding.xml` in your Jellyfin data directory.
2. Find the `<AllowAv1Encoding>true</AllowAv1Encoding>` tag.
3. Change it to `<AllowAv1Encoding>false</AllowAv1Encoding>`.
4. Restart the Jellyfin container. This forces Jellyfin to fall back to highly compatible H264 encoding for browsers.
