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
# CONFIG
# =========================================================
CHANNELS = [
    {"id": "buncha", "name": "Bún Chả TV", "url": "https://bunchatv4.net/truc-tiep-bong-da-xoilac-tv", "base_url": "https://bunchatv4.net"},
    {"id": "hoiquan", "name": "Hội Quán TV", "url": "https://sv2.hoiquan3.live/lich-thi-dau/bong-da", "base_url": "https://sv2.hoiquan3.live"}
]

JSON_FILE = "bongda.json"
M3U_FILE = "bongda.m3u"
WAITING_VIDEO_URL = "https://raw.githubusercontent.com/Eternal161/dausoco/main/waiting.mp4"
VN_TZ = datetime.timezone(datetime.timedelta(hours=7))

# Lấy Token từ môi trường GitHub
GITHUB_TOKEN = os.getenv("GH_TOKEN")
REPO_NAME = os.getenv("GH_REPO")

_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"}

# =========================================================
# HELPERS
# =========================================================
def get_team_logo(name):
    if not name or name == "Unknown": return ""
    return f"https://ui-avatars.com/api/?name={requests.utils.quote(name[:2])}&background=1565C0&color=fff"

def parse_url_to_info(url):
    try:
        slug = url.split('/')[-1].split('?')[0]
        if "-vs-" not in slug: return "Trận đấu", "Chưa rõ", "Unknown"
        slug = re.sub(r'-\d{6,}$', '', slug)
        time_match = re.search(r"-(\d{4}-\d{2}-\d{2}-\d{4})$", slug)
        t_gian = "Unknown"
        if time_match:
            t = time_match.group(1)
            t_gian = f"{t[0:2]}:{t[2:4]} {t[5:7]}/{t[8:10]}/{t[11:15]}"
            slug = slug[:slug.rfind("-" + t)]
        teams = slug.split("-vs-")
        return teams[0].replace("-"," ").title(), teams[1].replace("-"," ").title(), t_gian
    except: return "Unknown", "Unknown", "Unknown"

# =========================================================
# CORE SCRAPER
# =========================================================
def capture_stream(context, url):
    page = context.new_page()
    Stealth().apply_stealth_sync(page)
    found_streams = []

    def handle_request(req):
        u = req.url.lower()
        if any(k in u for k in [".m3u8", "100ycdn.com", "edgemaxcdn.org", "wssession="]):
            if not any(bad in u for bad in ["/ads/", "saba.m3u8", "waiting"]):
                found_streams.append(req.url)

    page.on("request", handle_request)
    try:
        # Giảm timeout xuống 30s để tránh treo bot
        page.goto(url, wait_until="commit", timeout=30000)
        page.wait_for_timeout(5000)
        page.mouse.click(500, 500) # Kích hoạt player
        page.wait_for_timeout(2000)
    except: pass
    finally: page.close()

    if found_streams:
        # Ưu tiên link có wssession hoặc 100ycdn
        found_streams.sort(key=lambda x: ("wssession" in x or "100ycdn" in x), reverse=True)
        return found_streams[0]
    return None

def scrape():
    all_data = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent=_HEADERS["User-Agent"])
        
        for ch in CHANNELS:
            print(f"--- Quét kênh: {ch['name']} ---")
            page = context.new_page()
            try:
                page.goto(ch["url"], timeout=45000)
                links = page.locator("a[href*='-vs-']").all_attribute_contents("href")
                links = list(set([l if l.startswith("http") else f"{ch['base_url'].rstrip('/')}/{l.lstrip('/')}" for l in links]))
                
                for link in links[:10]:
                    nha, khach, tg = parse_url_to_info(link)
                    is_live = False
                    try:
                        dt = datetime.datetime.strptime(tg, "%H:%M %d/%m/%Y").replace(tzinfo=VN_TZ)
                        if -15 < (datetime.datetime.now(VN_TZ) - dt).total_seconds()/60 < 130:
                            is_live = True
                    except: pass

                    item = {
                        "title": f"{nha} vs {khach}",
                        "is_live": is_live,
                        "logo": get_team_logo(nha),
                        "url": WAITING_VIDEO_URL,
                        "link_xem": link,
                        "time": tg
                    }
                    if is_live:
                        print(f"Đang bắt luồng: {item['title']}")
                        stream = capture_stream(context, link)
                        if stream: item["url"] = stream
                    
                    all_data.append(item)
            except Exception as e: print(f"Lỗi: {e}")
            finally: page.close()
        browser.close()
    return all_data

# =========================================================
# EXPORT & PUSH
# =========================================================
def push_to_github(items):
    # Tạo nội dung M3U
    m3u_lines = ["#EXTM3U"]
    for it in items:
        status = "🔴 LIVE" if it["is_live"] else "⏳"
        m3u_lines.append(f'#EXTINF:-1 tvg-logo="{it["logo"]}" group-title="Bóng Đá", {status} {it["title"]}')
        m3u_lines.append(it["url"])
    
    m3u_content = "\n".join(m3u_lines)
    json_content = json.dumps(items, indent=2, ensure_ascii=False)

    if not GITHUB_TOKEN or not REPO_NAME:
        with open(M3U_FILE, "w") as f: f.write(m3u_content)
        return

    g = Github(GITHUB_TOKEN)
    repo = g.get_repo(REPO_NAME)
    now = datetime.datetime.now(VN_TZ).strftime("%H:%M %d/%m/%Y")

    for fname, content in [(M3U_FILE, m3u_content), (JSON_FILE, json_content)]:
        try:
            f = repo.get_contents(fname)
            repo.update_file(f.path, f"Update {now}", content, f.sha)
        except:
            repo.create_file(fname, f"Init {now}", content)
    print("✅ Đã cập nhật GitHub thành công!")

if __name__ == "__main__":
    data = scrape()
    push_to_github(data)
