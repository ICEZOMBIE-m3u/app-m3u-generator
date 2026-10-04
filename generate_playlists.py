import requests
import gzip
import json
import os
import logging
import uuid
import time
import shutil
import random
import re
import xml.etree.ElementTree as ET
import urllib3
from io import BytesIO
from datetime import datetime
from urllib.parse import unquote, urlparse, urlunparse
from bs4 import BeautifulSoup

# Disable the InsecureRequestWarning
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# --- Configuration ---
OUTPUT_DIR = "playlists"
USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36'
REQUEST_TIMEOUT = 30

REGION_MAP = {
    'us': 'United States', 'gb': 'United Kingdom', 'ca': 'Canada',
    'de': 'Germany', 'at': 'Austria', 'ch': 'Switzerland',
    'es': 'Spain', 'fr': 'France', 'it': 'Italy', 'br': 'Brazil',
    'mx': 'Mexico', 'ar': 'Argentina', 'cl': 'Chile', 'co': 'Colombia',
    'pe': 'Peru', 'se': 'Sweden', 'no': 'Norway', 'dk': 'Denmark',
    'in': 'India', 'jp': 'Japan', 'kr': 'South Korea', 'au': 'Australia'
}

TOP_REGIONS = ['United States', 'Canada', 'United Kingdom']

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Helper Functions ---

def cleanup_output_dir():
    if os.path.exists(OUTPUT_DIR):
        logger.info(f"Cleaning up old playlists in {OUTPUT_DIR}...")
        for filename in os.listdir(OUTPUT_DIR):
            file_path = os.path.join(OUTPUT_DIR, filename)
            try:
                if os.path.isfile(file_path) or os.path.islink(file_path):
                    os.unlink(file_path)
                elif os.path.isdir(file_path):
                    shutil.rmtree(file_path)
            except Exception as e:
                logger.error(f"Failed to delete {file_path}: {e}")
    else:
        os.makedirs(OUTPUT_DIR)

def fetch_url(url, is_json=True, is_gzipped=False, headers=None, stream=False, retries=3):
    headers = headers or {'User-Agent': USER_AGENT}
    for i in range(retries):
        try:
            response = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT, stream=stream)
            if response.status_code == 429:
                time.sleep((i + 1) * 10 + random.uniform(0, 5))
                continue
            response.raise_for_status()
            content = response.content
            if is_gzipped:
                try:
                    with gzip.GzipFile(fileobj=BytesIO(content), mode='rb') as f:
                        content = f.read()
                    content = content.decode('utf-8')
                except:
                    content = content.decode('utf-8')
            else:
                content = content.decode('utf-8')
            return json.loads(content) if is_json else content
        except Exception as e:
            logger.warning(f"Fetch failed (attempt {i+1}): {e}")
            if i < retries - 1:
                time.sleep(5)
    return None

def write_m3u_file(filename, content):
    filepath = os.path.join(OUTPUT_DIR, filename)
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)

def format_extinf(channel_id, tvg_id, tvg_chno, tvg_name, tvg_logo, group_title, display_name):
    chno_str = str(tvg_chno) if tvg_chno and str(tvg_chno).isdigit() else ""
    return (f'#EXTINF:-1 channel-id="{channel_id}" tvg-id="{tvg_id}" tvg-chno="{chno_str}" '
            f'tvg-name="{tvg_name.replace(chr(34), chr(39))}" tvg-logo="{tvg_logo}" '
            f'group-title="{group_title.replace(chr(34), chr(39))}",{display_name.replace(",", "")}\n')

# --- Standard Services ---

def get_anonymous_token(region: str = 'us') -> str | None:
    headers = {
        'Accept': 'application/json',
        'User-Agent': USER_AGENT,
        'X-Plex-Product': 'Plex Web',
        'X-Plex-Version': '4.150.0',
        'X-Plex-Client-Identifier': str(uuid.uuid4()).replace('-', ''),
        'X-Plex-Platform': 'Web',
    }
    x_forward_ips = {'us': '76.81.9.69'}
    if region in x_forward_ips:
        headers['X-Forwarded-For'] = x_forward_ips[region]
    params = {'X-Plex-Product': 'Plex Web', 'X-Plex-Client-Identifier': headers['X-Plex-Client-Identifier']}
    try:
        resp = requests.post('https://clients.plex.tv/api/v2/users/anonymous', headers=headers, params=params, timeout=15)
        resp.raise_for_status()
        return resp.json().get('authToken')
    except:
        return None

def generate_pluto_m3u():
    data = fetch_url('https://github.com/matthuisman/i.mjh.nz/raw/refs/heads/master/PlutoTV/.channels.json.gz', is_json=True, is_gzipped=True)
    if not data or 'regions' not in data:
        return

    for region in list(data['regions'].keys()) + ['all']:
        is_all = region == 'all'
        output_lines = [f'#EXTM3U url-tvg="https://github.com/matthuisman/i.mjh.nz/raw/master/PlutoTV/{region}.xml.gz"\n']
        channels = {}

        if is_all:
            for r_code, r_data in data['regions'].items():
                country_name = REGION_MAP.get(r_code.lower(), r_code.upper())
                for c_id, c_info in r_data.get('channels', {}).items():
                    channels[f"{c_id}-{r_code}"] = {
                        **c_info,
                        'original_id': c_id,
                        'country_group': country_name,
                        'service_group': c_info.get('group', 'Other')
                    }
        else:
            region_data = data['regions'].get(region, {}).get('channels', {})
            country_name = REGION_MAP.get(region.lower(), region.upper())
            for c_id, c_info in region_data.items():
                channels[c_id] = {
                    **c_info,
                    'original_id': c_id,
                    'country_group': country_name,
                    'service_group': c_info.get('group', 'Other')
                }

        sorted_channels = sorted(
            channels.items(),
            key=lambda x: (0 if x[1]['country_group'] in TOP_REGIONS else 1, x[1].get('name', ''))
        )

        for c_id, ch in sorted_channels:
            group_title = ch['country_group'] if is_all else ch['service_group']
            output_lines.extend([
                format_extinf(
                    c_id,
                    ch['original_id'],
                    ch.get('chno'),
                    ch['name'],
                    ch['logo'],
                    group_title,
                    ch['name']
                ),
                f"https://jmp2.uk/plu-{ch['original_id']}.m3u8\n"
            ])

        write_m3u_file(f"plutotv_{region}.m3u", "".join(output_lines))

def generate_plex_m3u():
    data = fetch_url('https://github.com/matthuisman/i.mjh.nz/raw/refs/heads/master/Plex/.channels.json.gz', is_json=True, is_gzipped=True)
    if not data or 'channels' not in data:
        return
    found_regions = set()
    for ch in data['channels'].values():
        found_regions.update(ch.get('regions', []))
    for region in list(found_regions) + ['all']:
        token = get_anonymous_token(region if region != 'all' else 'us')
        if not token:
            continue
        output_lines = [f'#EXTM3U url-tvg="https://github.com/matthuisman/i.mjh.nz/raw/master/Plex/{region}.xml.gz"\n']
        channel_list = []
        for c_id, ch in data['channels'].items():
            if region == 'all' or region in ch.get('regions', []):
                group = REGION_MAP.get(region.lower(), region.upper()) if region != 'all' else REGION_MAP.get(ch.get('regions', [''])[0].lower(), ch.get('regions', ['Other'])[0].upper())
                channel_list.append((group, ch['name'].lower(), format_extinf(c_id, c_id, ch.get('chno'), ch['name'], ch.get('logo', ''), group, ch['name']), f"https://epg.provider.plex.tv/library/parts/{c_id}/?X-Plex-Token={token}\n"))
        if channel_list:
            channel_list.sort(key=lambda x: (0 if x[0] in TOP_REGIONS else 1, x[1]))
            for _, _, extinf, url in channel_list:
                output_lines.extend([extinf, url])
            write_m3u_file(f"plex_{region}.m3u", "".join(output_lines))

def generate_samsungtvplus_m3u():
    data = fetch_url('https://github.com/matthuisman/i.mjh.nz/raw/refs/heads/master/SamsungTVPlus/.channels.json.gz', is_json=True, is_gzipped=True)
    if not data or 'regions' not in data:
        return
    slug_template = data.get('slug', '{id}.m3u8')

    for region in list(data['regions'].keys()) + ['all']:
        is_all = region == 'all'
        output_lines = [f'#EXTM3U url-tvg="https://github.com/matthuisman/i.mjh.nz/raw/master/SamsungTVPlus/{region}.xml.gz"\n']
        channels = {}

        if is_all:
            for r_code, r_info in data['regions'].items():
                country_name = REGION_MAP.get(r_code.lower(), r_code.upper())
                for c_id, c_info in r_info.get('channels', {}).items():
                    channels[f"{c_id}-{r_code}"] = {
                        **c_info,
                        'original_id': c_id,
                        'country_group': country_name,
                        'service_group': c_info.get('group', 'Other')
                    }
        else:
            region_data = data['regions'].get(region, {}).get('channels', {})
            country_name = REGION_MAP.get(region.lower(), region.upper())
            for c_id, c_info in region_data.items():
                channels[c_id] = {
                    **c_info,
                    'original_id': c_id,
                    'country_group': country_name,
                    'service_group': c_info.get('group', 'Other')
                }

        sorted_channels = sorted(
            channels.items(),
            key=lambda x: (0 if x[1]['country_group'] in TOP_REGIONS else 1, x[1].get('name', '').lower())
        )

        for c_id, ch in sorted_channels:
            group_title = ch['country_group'] if is_all else ch['service_group']
            output_lines.extend([
                format_extinf(
                    c_id,
                    ch['original_id'],
                    ch.get('chno'),
                    ch['name'],
                    ch['logo'],
                    group_title,
                    ch['name']
                ),
                f"https://jmp2.uk/{slug_template.replace('{id}', ch['original_id'])}\n"
            ])

        write_m3u_file(f"samsungtvplus_{region}.m3u", "".join(output_lines))

def generate_roku_m3u():
    data = fetch_url('https://i.mjh.nz/Roku/.channels.json', is_json=True)
    if not data:
        return

    ROKU_GROUP_MAP = {
        'News': 'News', 'Newsmagazine': 'News', 'Special': 'News', 'Politics': 'News',
        'Weather': 'Weather',
        'Sports': 'Sports', 'Sports Talk': 'Sports', 'Olympics': 'Sports',
        'Action Sports': 'Sports', 'Action': 'Sports',
        'Baseball': 'Sports', 'Basketball': 'Sports', 'Football': 'Sports',
        'Soccer': 'Sports', 'Hockey': 'Sports', 'Tennis': 'Sports', 'Golf': 'Sports',
        'Boxing': 'Sports', 'Mixed Martial Arts': 'Sports', 'Martial Arts': 'Sports',
        'Wrestling': 'Sports', 'Rugby': 'Sports', 'Volleyball': 'Sports',
        'Skateboarding': 'Sports', 'Snowboarding': 'Sports', 'Surfing': 'Sports',
        'Cycling': 'Sports', 'Bicycle': 'Sports', 'Bmx Racing': 'Sports',
        'Bullfighting': 'Sports', 'Rodeo': 'Sports', 'Western': 'Sports',
        'Fishing': 'Sports', 'Hunting': 'Sports', 'Outdoors': 'Sports',
        'Boat Racing': 'Sports', 'Drag Racing': 'Sports', 'Motorsports': 'Sports',
        'Motorcycle': 'Sports', 'Motorcycle Racing': 'Sports',
        'Judo': 'Sports', 'Karate': 'Sports', 'Billiards': 'Sports',
        'Auto': 'Auto & Motorsports', 'Auto Racing': 'Auto & Motorsports',
        'Adventure': 'Movies', 'Thriller': 'Movies', 'Suspense': 'Movies',
        'Science Fiction': 'Movies', 'Fantasy': 'Movies', 'Horror': 'Movies',
        'Entertainment': 'TV & Entertainment', 'Sitcom': 'TV & Entertainment',
        'Drama': 'TV & Entertainment', 'Soap': 'TV & Entertainment',
        'Talk': 'TV & Entertainment', 'Reality': 'TV & Entertainment',
        'Comedy Drama': 'TV & Entertainment', 'History': 'TV & Entertainment',
        'Comedy': 'Comedy', 'Romantic Comedy': 'Comedy',
        'Romance': 'Romance',
        'Documentary': 'Documentary', 'Nature': 'Documentary',
        'Music': 'Music',
        'Anime': 'Anime',
        'Gaming': 'Gaming & Tech', 'Computers': 'Gaming & Tech',
        'Esports': 'Gaming & Tech',
        'Faith': 'Faith & Family', 'Religious': 'Faith & Family',
        'Family': 'Faith & Family',
        'Health': 'Health', 'Medical': 'Health',
    }

    channels = data.get('channels', {})
    group_map = {}
    for c_id, ch in channels.items():
        raw_group = ch['groups'][0] if ch.get('groups') else 'Other'
        group = ROKU_GROUP_MAP.get(raw_group, raw_group)
        group_map.setdefault(group, []).append((c_id, ch))

    output_lines = ['#EXTM3U url-tvg="https://github.com/matthuisman/i.mjh.nz/raw/master/Roku/all.xml.gz"\n']
    for group in sorted(group_map.keys()):
        for c_id, ch in sorted(group_map[group], key=lambda x: x[1].get('name', '').lower()):
            output_lines.extend([
                format_extinf(c_id, c_id, ch.get('chno'), ch['name'], ch['logo'], group, ch['name']),
                f"https://jmp2.uk/rok-{c_id}.m3u8\n"
            ])

    write_m3u_file("roku_all.m3u", "".join(output_lines))

# --- Tubi Scraping Logic (maximized + robust) ---

TUBI_LIVE_PAGE_URL = "https://tubitv.com/live"
TUBI_EPG_URL       = "https://tubitv.com/oz/epg/programming"

TUBI_CONTAINER_CANDIDATES = [
    "https://tubitv.com/oz/containers/linear",
    "https://tubitv.com/oz/containers/tubitv_us_linear",
    "https://tubitv.com/oz/containers?container_id=tubitv_us_linear",
    "https://tubitv.com/oz/containers?slug=tubitv_us_linear",
    "https://tubitv.com/oz/containers/linear?platform=web",
]

TUBI_HEADERS = {
    'User-Agent': USER_AGENT,
    'Accept-Language': 'en-US,en;q=0.9',
    'Accept': 'application/json, text/html, */*',
}

TUBI_FALLBACK_FILE = os.path.join(OUTPUT_DIR, "tubi_fallback_ids.json")

# Clean unique 174-ID seed list
TUBI_SEED_FALLBACK_IDS = [
    400000008, 400000011, 400000012, 400000024, 400000028, 400000030, 400000031,
    400000033, 400000056, 400000059, 400000062, 400000063, 400000067, 400000069,
    400000070, 400000073, 400000074, 400000083, 400000085, 400000086, 400000087,
    400000088, 400000089, 400000090, 400000092, 400000094, 400000095, 400000096,
    400000098, 400000099, 400000104, 400000105, 400000106, 400000108, 400000117,
    400000119, 400000121, 400000164, 400000169, 400000179, 400000195, 400000196,
    400000247, 400000251, 400000285, 400000287, 400000289, 400000290, 400000291,
    400000294, 400000296, 400000299, 400000303, 400000304,
    555113, 555119, 555121, 555124, 555126, 555127, 555130, 555382,
    556174, 557344, 557345, 559144, 560215, 571664,
    613683, 613695, 613758, 613759, 613761, 618762, 618763, 628893, 629323,
    653199, 653200, 653208, 656575, 670602, 670604, 671073, 671083,
    673411, 673498, 673499, 673500, 677011, 680705, 682057, 682059, 682634,
    684164, 684165, 684167, 684170, 691129, 692051, 692057, 692086, 692087,
    692090, 692114, 694174, 700406, 700407, 700414, 700418, 711410,
    715946, 715947, 715948, 715949, 715950, 715951, 715952, 724209,
]

_SOCKS_READY = None

def ensure_socks_support():
    global _SOCKS_READY
    if _SOCKS_READY is not None:
        return _SOCKS_READY
    try:
        import socks
        _SOCKS_READY = True
        return True
    except Exception:
        pass
    try:
        import subprocess, sys
        logger.info("Installing PySocks for SOCKS support...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "--quiet", "PySocks"])
        import socks
        _SOCKS_READY = True
    except Exception as e:
        logger.warning(f"Could not enable SOCKS support: {e}")
        _SOCKS_READY = False
    return _SOCKS_READY

def _tubi_request_kwargs(proxy=None, timeout=20):
    kwargs = {"headers": TUBI_HEADERS, "verify": False, "timeout": timeout}
    if proxy:
        kwargs["proxies"] = {"http": proxy, "https": proxy}
    return kwargs

def get_proxies(country_code="US", limit=8):
    url = (f"https://api.proxyscrape.com/v2/?request=displayproxies"
           f"&protocol=socks4&timeout=10000&country={country_code}&ssl=all&anonymity=elite")
    try:
        r = requests.get(url, timeout=15)
        if r.status_code == 200:
            return [f"socks4://{p.strip()}" for p in r.text.splitlines() if p.strip()][:limit]
    except Exception as e:
        logger.warning(f"Proxy fetch error: {e}")
    return []

def load_tubi_fallback_ids():
    if os.path.exists(TUBI_FALLBACK_FILE):
        try:
            with open(TUBI_FALLBACK_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                ids = [int(x) for x in data.get("ids", [])]
                if ids:
                    logger.info(f"Loaded {len(ids)} IDs from {TUBI_FALLBACK_FILE}")
                    return ids
        except Exception as e:
            logger.warning(f"Could not load fallback file: {e}")
    return list(TUBI_SEED_FALLBACK_IDS)

def save_tubi_fallback_ids(ids):
    if len(ids) < 250:
        return
    try:
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        payload = {
            "updated": datetime.utcnow().isoformat() + "Z",
            "count": len(ids),
            "ids": sorted(set(ids))
        }
        with open(TUBI_FALLBACK_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        logger.info(f"Saved {len(ids)} IDs to {TUBI_FALLBACK_FILE}")
    except Exception as e:
        logger.warning(f"Failed to save fallback IDs: {e}")

def _content_id_of(entry):
    if isinstance(entry, bool):
        return None
    if isinstance(entry, int):
        return entry
    if isinstance(entry, dict):
        value = entry.get('content_id') or entry.get('contentId') or entry.get('id')
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None
    if isinstance(entry, str) and entry.strip().isdigit():
        return int(entry)
    return None

def _recursive_extract_ids(obj, found=None):
    if found is None:
        found = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            key = str(k).lower()
            if key in ("content_id", "contentid", "id", "channel_id", "channelid") and isinstance(v, (int, str)):
                try:
                    cid = int(v)
                    if 100000 <= cid <= 999999999:
                        found.add(cid)
                except (ValueError, TypeError):
                    pass
            else:
                _recursive_extract_ids(v, found)
    elif isinstance(obj, list):
        for item in obj:
            _recursive_extract_ids(item, found)
    return found

def fetch_channel_ids_via_api(proxy=None):
    for url in TUBI_CONTAINER_CANDIDATES:
        try:
            r = requests.get(url, **_tubi_request_kwargs(proxy))
            if r.status_code != 200:
                logger.warning(f"  {url} → {r.status_code}")
                continue
            data = r.json()
            ids = []
            for row in data.get("rows", []):
                for item in row.get("contents", []):
                    cid = _content_id_of(item)
                    if cid is not None:
                        ids.append(cid)
            if not ids:
                for item in data.get("contents", []):
                    cid = _content_id_of(item)
                    if cid is not None:
                        ids.append(cid)
            if not ids:
                ids = list(_recursive_extract_ids(data))
            if ids:
                logger.info(f"API strategy ({url}): found {len(ids)} channel IDs")
                return list(dict.fromkeys(ids))
        except Exception as e:
            logger.warning(f"  API error on {url}: {e}")
    return []

def fetch_channel_list_via_html(proxy=None, retries=2):
    for attempt in range(retries):
        try:
            r = requests.get(TUBI_LIVE_PAGE_URL, **_tubi_request_kwargs(proxy))
            if r.status_code != 200:
                logger.warning(f"HTML fetch → {r.status_code} (attempt {attempt+1})")
                continue
            html = r.content.decode('utf-8', errors='replace')
            if "not available in Europe" in html or "gdpr.tubi.tv" in html.lower():
                logger.warning("GDPR / geo-block detected – skipping")
                return None
            soup = BeautifulSoup(html, "html.parser")
            target = None
            for script in soup.find_all("script"):
                text = script.string or ""
                if "window.__data" in text:
                    target = text
                    break
            if not target:
                for script in soup.find_all("script"):
                    text = script.string or ""
                    if text.strip().startswith("{") and '"epg"' in text:
                        target = text
                        break
            if not target:
                continue
            start = target.find("{")
            end = target.rfind("}") + 1
            js = target[start:end].replace("undefined", "null")
            js = re.sub(r'new Date\("([^"]*)"\)', r'"\1"', js)
            data = json.loads(js)
            logger.info("HTML strategy: successfully decoded window.__data")
            return data
        except Exception as e:
            logger.warning(f"HTML strategy error (attempt {attempt+1}): {e}")
    return None

def extract_ids_from_html_data(json_data):
    ids = []
    container = json_data if isinstance(json_data, dict) else {}
    epg = container.get('epg', {}).get('contentIdsByContainer', {})
    for cat_list in epg.values():
        for cat in cat_list:
            for entry in cat.get('contents', []):
                cid = _content_id_of(entry)
                if cid is not None:
                    ids.append(cid)
    ids.extend(_recursive_extract_ids(json_data))
    return list(dict.fromkeys(ids))

def create_group_mapping(json_data):
    mapping = {}
    container = json_data if isinstance(json_data, dict) else {}
    epg = container.get('epg', {}).get('contentIdsByContainer', {})
    for cat_list in epg.values():
        for cat in cat_list:
            name = cat.get('name') or cat.get('title') or 'Other'
            for entry in cat.get('contents', []):
                cid = _content_id_of(entry)
                if cid is not None:
                    mapping[str(cid)] = name
    return mapping

def fetch_epg_data(channel_ids):
    if not channel_ids:
        return []
    epg_data = []
    group_size = 120
    batches = [channel_ids[i:i + group_size] for i in range(0, len(channel_ids), group_size)]
    for i, batch in enumerate(batches, 1):
        params = {"content_id": ",".join(map(str, batch))}
        try:
            r = requests.get(TUBI_EPG_URL, params=params, headers=TUBI_HEADERS, timeout=30, verify=False)
            if r.status_code == 200:
                rows = r.json().get('rows', [])
                epg_data.extend(rows)
                logger.info(f"EPG batch {i}/{len(batches)}: +{len(rows)} rows")
            else:
                logger.warning(f"EPG batch {i} failed: {r.status_code}")
        except Exception as e:
            logger.warning(f"EPG batch error: {e}")
    logger.info(f"EPG total: {len(epg_data)} rows")
    return epg_data

def clean_stream_url(url):
    p = urlparse(unquote(url))
    return urlunparse((p.scheme, p.netloc, p.path, '', '', ''))

def create_m3u_playlist(epg_data, group_mapping):
    lines = ['#EXTM3U url-tvg="tubi_epg.xml"']
    seen = set()
    for ch in sorted(epg_data, key=lambda x: (x.get('title') or '').lower()):
        name = (ch.get('title') or 'Unknown').encode('utf-8', 'ignore').decode('utf-8')
        tvg_id = str(ch.get('content_id', ''))
        logo = (ch.get('images', {}).get('thumbnail') or [None])[0] or ''
        group = group_mapping.get(tvg_id, 'Other').encode('utf-8', 'ignore').decode('utf-8')
        resources = ch.get('video_resources') or []
        if not resources:
            continue
        raw = (resources[0].get('manifest') or {}).get('url', '')
        url = clean_stream_url(raw)
        if not url or url in seen:
            continue
        lines.append(f'#EXTINF:-1 tvg-id="{tvg_id}" tvg-logo="{logo}" group-title="{group}",{name}')
        lines.append(url)
        seen.add(url)
    return "\n".join(lines) + "\n"

def create_epg_xml(epg_data):
    root = ET.Element("tv")
    for ch in epg_data:
        cid = str(ch.get("content_id", ""))
        channel = ET.SubElement(root, "channel", id=cid)
        ET.SubElement(channel, "display-name").text = ch.get("title", "Unknown")
        thumb = (ch.get("images", {}).get("thumbnail") or [None])[0]
        if thumb:
            ET.SubElement(channel, "icon", src=thumb)
        for prog in ch.get("programs", []):
            p = ET.SubElement(root, "programme", channel=cid)
            try:
                start = datetime.strptime(prog.get("start_time", ""), "%Y-%m-%dT%H:%M:%SZ")
                stop  = datetime.strptime(prog.get("end_time", ""), "%Y-%m-%dT%H:%M:%SZ")
                p.set("start", start.strftime("%Y%m%d%H%M%S +0000"))
                p.set("stop",  stop.strftime("%Y%m%d%H%M%S +0000"))
            except Exception:
                p.set("start", prog.get("start_time", ""))
                p.set("stop",  prog.get("end_time", ""))
            ET.SubElement(p, "title").text = prog.get("title", "")
            if prog.get("description"):
                ET.SubElement(p, "desc").text = prog["description"]
    return ET.ElementTree(root)

def generate_tubi_m3u():
    """Maximized + robust version with auto-save of high-quality ID lists."""
    socks_ok = ensure_socks_support()
    proxies = get_proxies("US", limit=8) if socks_ok else []
    if not socks_ok:
        logger.warning("SOCKS support unavailable – using direct connection only")

    connection_options = [None] + proxies

    channel_ids = []
    group_mapping = {}

    # Pass 1: API (preferred)
    for proxy in connection_options:
        logger.info(f"Tubi API attempt via {proxy or 'direct'}")
        ids = fetch_channel_ids_via_api(proxy)
        if ids:
            channel_ids = ids
            html_data = fetch_channel_list_via_html(proxy)
            if html_data:
                group_mapping = create_group_mapping(html_data)
            break

    # Pass 2: HTML
    if not channel_ids:
        for proxy in connection_options:
            logger.info(f"Tubi HTML attempt via {proxy or 'direct'}")
            html_data = fetch_channel_list_via_html(proxy)
            if html_data:
                ids = extract_ids_from_html_data(html_data)
                if ids:
                    channel_ids = ids
                    group_mapping = create_group_mapping(html_data)
                    break

    # Pass 3: Fallback
    if not channel_ids:
        logger.warning("All live strategies failed – using fallback IDs")
        channel_ids = load_tubi_fallback_ids()

    if not channel_ids:
        logger.error("Tubi: no channel IDs available. Skipping.")
        return

    channel_ids = list(dict.fromkeys(channel_ids))
    logger.info(f"Tubi final ID count: {len(channel_ids)}")

    save_tubi_fallback_ids(channel_ids)

    epg_data = fetch_epg_data(channel_ids)
    if not epg_data:
        logger.error("Tubi: EPG endpoint returned no data. Skipping.")
        return

    m3u = create_m3u_playlist(epg_data, group_mapping)
    epg_tree = create_epg_xml(epg_data)

    write_m3u_file("tubi_all.m3u", m3u)
    epg_tree.write(os.path.join(OUTPUT_DIR, "tubi_epg.xml"), encoding='utf-8', xml_declaration=True)
    logger.info(f"Tubi: wrote {len(epg_data)} channels.")

# --- Execution ---

if __name__ == "__main__":
    cleanup_output_dir()
    generate_pluto_m3u()
    generate_plex_m3u()
    generate_samsungtvplus_m3u()
    generate_tubi_m3u()
    generate_roku_m3u()
