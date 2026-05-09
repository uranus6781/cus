import os
import re
import time
import json
import datetime
import requests

from github import Github
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
from playwright_stealth import Stealth

# =========================================================
# CONFIG ĐA KÊNH
# =========================================================

CHANNELS = [
    {
        "id": "buncha",
        "name": "Bún Chả TV",
        "url": "https://bunchatv4.net/truc-tiep-bong-da-xoilac-tv",
        "base_url": "https://bunchatv4.net"
    },
    {
        "id": "hoiquan",
        "name": "Hội Quán TV",
        "url": "https://sv2.hoiquan3.live/lich-thi-dau/bong-da",
        "base_url": "https://sv2.hoiquan3.live"
    }
]

JSON_FILE = "bongda.json"
M3U_FILE = "bongda.m3u"
WAITING_VIDEO_URL = "https://raw.githubusercontent.com/Eternal161/dausoco/main/waiting.mp4"
LIMIT_MATCHES = 15  

VN_TZ = datetime.timezone(datetime.timedelta(hours=7))
GITHUB_TOKEN = os.getenv("GH_TOKEN")
REPO_NAME = os.getenv("GH_REPO", "Eternal161/dausoco")

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
}

LOGO_CACHE = {}

# =========================================================
# UTILS & LOGO
# =========================================================

def normalize_team_name(name):
    name = re.sub(r"\bFc\b$", "FC", name)
    return name.strip()

def get_team_logo(team_name):
    if not team_name or team_name == "Unknown": return ""
    team_name = normalize_team_name(team_name)
    if team_name in LOGO_CACHE: return LOGO_CACHE[team_name]

    try:
        slug = team_name.lower().replace(" ", "-")
        url = f"https://football-logos.cc/{slug}/"
        r = requests.get(url, headers=_HEADERS, timeout=3)
        match = re.search(r'https://football-logos.cc/logos/[^"]+\.png', r.text)
        if match:
            logo = match.group(0)
            LOGO_CACHE[team_name] = logo
            return logo
    except: pass
    return f"https://ui-avatars.com/api/?name={requests.utils.quote(team_name[:2])}&size=200&background=1565C0&color=ffffff&bold=true"

def parse_url_to_info(url):
    try:
        parts = url.rstrip('/').split('/')
        slug = next((p.split('?')[0] for p in reversed(parts) if "-vs-" in p), "")
        if not slug: return "Unknown", "Unknown", "Chưa có lịch"

        slug = re.sub(r'-\d{6,}$', '', slug)
        time_match = re.search(r"-(\d{4}-\d{2}-\d{2}-\d{4})$", slug)

        if time_match:
            t = time_match.group(1)
            thoi_gian = f"{t[0:2]}:{t[2:4]} {t[5:7]}/{t[8:10]}/{t[11:15]}"
            teams_slug = slug[:slug.rfind("-" + t)]
        else:
            thoi_gian = "Unknown"; teams_slug = slug

        teams = teams_slug.split("-vs-", 1)
        doi_nha = teams[0].replace("-", " ").title().strip()
        doi_khach = teams[1].replace("-", " ").title().strip() if len(teams) > 1 else "Unknown"
        return doi_nha, doi_khach, thoi_gian
    except: return "Unknown", "Unknown", "Unknown"

# =========================================================
# CAPTURE STREAM
# =========================================================

def capture_stream(context, match_url):
    page = context.new_page()
    Stealth().apply_stealth_sync(page)
    streams = set()

    def process_url(url):
        u = url.lower()
        if any(bad in u for bad in [".mp4", ".jpg", ".png", "waiting", "saba.m3u8", "/ads/"]): return
        if any(k in u for k in [".m3u8", "taoxanh.biz", "rapidlive.shop", "edgemaxcdn.org", "100ycdn.com", "hqtv"]):
            streams.add(url)

    page.on("request", lambda req: process_url(req.url))
    page.on("response", lambda res: process_url(res.url))

    try:
        page.goto(match_url, wait_until="load", timeout=45000)
        page.wait_for_timeout(5000)
        
        # Click giả lập để kích hoạt player
        try:
            page.mouse.click(500, 500)
        except: pass

        deadline = time.time() + 10
        while time.time() < deadline:
            if any("wssession=" in s.lower() or "100ycdn" in s.lower() for s in streams): break
            time.sleep(1)
    except: pass
    finally: page.close()

    if streams:
        scored = []
        for s in streams:
            score = 0
            low = s.lower()
            if "100ycdn.com" in low: score += 5000
            if "wssession=" in low: score += 2000
            if "playlist.m3u8" in low: score += 500
            scored.append((score, s))
        return sorted(scored, key=lambda x: x[0], reverse=True)[0][1]
    return None

# =========================================================
# EXPORT DATA
# =========================================================

def generate_contents(all_channel_data):
    flat_list = []
    m3u_lines = ["#EXTM3U"]
    
    for c_id in all_channel_data:
        for m in all_channel_data[c_id]:
            # Chỉ lấy các trận Live hoặc có stream để danh sách sạch
            if not m["is_live"] and m["stream_url"] == WAITING_VIDEO_URL:
                continue
                
            flat_list.append(m)
            
            # M3U Format
            logo = m["logo_nha"] or ""
            group = "TRỰC TIẾP" if m["is_live"] else "SẮP ĐÁ"
            m3u_lines.append(f'#EXTINF:-1 tvg-logo="{logo}" group-title="{group}",{m["title"]} ({m["thoi_gian"]})')
            m3u_lines.append(m["stream_url"])

    json_data = {
        "name": "Sáng TV Playlist",
        "date": datetime.datetime.now(VN_TZ).strftime("%H:%M %d/%m/%Y"),
        "channels": flat_list
    }
    
    return json.dumps(json_data, indent=2, ensure_ascii=False), "\n".join(m3u_lines)

def push_to_github(json_str, m3u_str):
    if not GITHUB_TOKEN:
        print("⚠️ GH_TOKEN missing - writing local files")
        with open(JSON_FILE, "w", encoding="utf-8") as f: f.write(json_str)
        with open(M3U_FILE, "w", encoding="utf-8") as f: f.write(m3u_str)
        return

    g = Github(GITHUB_TOKEN)
    repo = g.get_repo(REPO_NAME)
    now_str = datetime.datetime.now(VN_TZ).strftime("%H:%M %d/%m/%Y")

    for fname, content in [(JSON_FILE, json_str), (M3U_FILE, m3u_str)]:
        try:
            target = repo.get_contents(fname)
            repo.update_file(target.path, f"Update {now_str}", content, target.sha)
        except:
            repo.create_file(fname, f"Initial {now_str}", content)
    print("✅ GitHub Updated: JSON & M3U")

# =========================================================
# MAIN
# =========================================================

def scrape_and_push():
    all_channel_data = {"buncha": [], "hoiquan": []}
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-gpu"])
        context = browser.new_context(user_agent=_HEADERS["User-Agent"])

        for channel in CHANNELS:
            print(f"Scraping: {channel['name']}")
            page = context.new_page()
            Stealth().apply_stealth_sync(page)
            
            try:
                page.goto(channel["url"], wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(3000)
                
                links = []
                seen = set()
                for el in page.locator("a[href*='-vs-']").all():
                    href = el.get_attribute("href")
                    if href and "-vs-" in href and href not in seen:
                        seen.add(href)
                        full_url = href if href.startswith("http") else f"{channel['base_url'].rstrip('/')}/{href.lstrip('/')}"
                        links.append(full_url)
                
                for idx, href in enumerate(links[:LIMIT_MATCHES]):
                    nha, khach, t_gian = parse_url_to_info(href)
                    is_live = False
                    status = "Sắp đá"
                    
                    try:
                        m_time = datetime.datetime.strptime(t_gian, "%H:%M %d/%m/%Y").replace(tzinfo=VN_TZ)
                        diff = (datetime.datetime.now(VN_TZ) - m_time).total_seconds() / 60
                        if -10 <= diff <= 130:
                            is_live = True
                            status = "LIVE"
                    except: pass

                    all_channel_data[channel["id"]].append({
                        "title": f"{nha} vs {khach}",
                        "thoi_gian": t_gian,
                        "is_live": is_live,
                        "logo_nha": get_team_logo(nha),
                        "logo_khach": get_team_logo(khach),
                        "stream_url": WAITING_VIDEO_URL,
                        "link_xem": href
                    })
            except Exception as e: print(f"Error {channel['id']}: {e}")
            finally: page.close()

        # Bắt luồng cho các trận Live
        for c_id in all_channel_data:
            lives = [m for m in all_channel_data[c_id] if m["is_live"]]
            for m in lives:
                print(f"Capturing: {m['title']}")
                stream = capture_stream(context, m["link_xem"])
                if stream: m["stream_url"] = stream

        browser.close()

    json_out, m3u_out = generate_contents(all_channel_data)
    push_to_github(json_out, m3u_out)

if __name__ == "__main__":
    scrape_and_push()
