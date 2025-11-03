import os, re, json, time, hashlib, pathlib, random, argparse, threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import feedparser
import requests
import tweepy
from tweepy import Client
from flask import Flask

# ========================
# إعدادات عامة
# ========================
BAGHDAD_TZ = ZoneInfo("Asia/Baghdad")
POST_TIMES_LOCAL = ["12:00", "19:00"]  # أوقات النشر اليومية
POLL_EVERY_MIN = 30  # فحص RSS كل X دقيقة
RESURFACE_EVERY_HOURS = 72  # إحياء كل 72 ساعة
MAX_NEW_PER_RUN = 3  # حد أقصى نشر جديد في التشغيل الواحد

SITE_URL = os.environ.get("SITE_URL", "https://loadingapk.online")
YOUTUBE_URL = "https://www.youtube.com/@-Muhamedloading"

STATE_JSON = pathlib.Path("posts.json")
RESURFACE_TS = pathlib.Path("last_resurface.txt")
SEEN_LINKS = pathlib.Path("seen_links.txt")

# هاشتاغات ثابتة (تأكد وجود #لودينغ)
HASHTAGS = "#لودينغ #مقالات #أبحاث #تاريخ #تقنية"

# ========================
# مفاتيح X (تويتر)
# ========================
API_KEY = os.environ["TW_API_KEY"]
API_KEY_SECRET = os.environ["TW_API_KEY_SECRET"]
ACCESS_TOKEN = os.environ["TW_ACCESS_TOKEN"]
ACCESS_TOKEN_SECRET = os.environ["TW_ACCESS_TOKEN_SECRET"]
BEARER_TOKEN = os.environ["TW_BEARER_TOKEN"]

# عميل v2 للتغريد
client = Client(bearer_token=BEARER_TOKEN,
                consumer_key=API_KEY,
                consumer_secret=API_KEY_SECRET,
                access_token=ACCESS_TOKEN,
                access_token_secret=ACCESS_TOKEN_SECRET,
                wait_on_rate_limit=True)

# عميل v1.1 لرفع الوسائط
auth = tweepy.OAuth1UserHandler(API_KEY, API_KEY_SECRET, ACCESS_TOKEN,
                                ACCESS_TOKEN_SECRET)
api_v1 = tweepy.API(auth, wait_on_rate_limit=True)

RSS = os.environ.get("BLOG_RSS_URL",
                     "https://loadingapk.online/feeds/posts/default?alt=rss")

# تهيئة ملفات الحالة
if not STATE_JSON.exists(): STATE_JSON.write_text("[]", encoding="utf-8")
if not SEEN_LINKS.exists(): SEEN_LINKS.write_text("", encoding="utf-8")

# ========================
# أدوات مساعدة
# ========================
BAD_PHRASES = [
    r'المصدر\s*[:\-–]?\s*pexels', r'pexels', r'pixabay', r'unsplash',
    r'Image\s*\(forced.*?\)', r'\bsource\b.*', r'حقوق.*?الصورة', r'صورة\s+من'
]
BAD_RE = re.compile("|".join(BAD_PHRASES), re.IGNORECASE)

IMG_RE = re.compile(r'<img[^>]+src=["\']([^"\']+)["\']', re.IGNORECASE)


def now_local():
    return datetime.now(BAGHDAD_TZ)


def load_json():
    try:
        return json.loads(STATE_JSON.read_text(encoding="utf-8"))
    except:
        return []


def save_json(items):
    STATE_JSON.write_text(json.dumps(items, ensure_ascii=False, indent=2),
                          encoding="utf-8")


def load_seen():
    return set(l.strip()
               for l in SEEN_LINKS.read_text(encoding="utf-8").splitlines()
               if l.strip())


def save_seen(seen: set):
    SEEN_LINKS.write_text("\n".join(sorted(seen)), encoding="utf-8")


def sha(link: str) -> str:
    return hashlib.sha1(link.encode("utf-8")).hexdigest()


def clean_html(s: str) -> str:
    if not s: return ""
    s = re.sub(r"<[^>]+>", " ", s)  # إزالة الوسوم
    s = re.sub(BAD_RE, " ", s)  # حذف أسطر المصادر/المكتبات
    s = re.sub(r"\s+", " ", s).strip()
    return s


def shorten(s: str, n: int) -> str:
    return s if len(s) <= n else s[:max(0, n - 1)].rstrip() + "…"


def to_question(title: str, summary: str) -> str:
    """يصيغ سؤالًا تشويقيًا من العنوان/الملخص."""
    starts = [
        "هل يمكن أن", "إلى أي حد يمكن أن", "ما الذي يجعل", "كيف تغيّر",
        "متى يصبح", "لماذا قد يكون", "هل فعلاً"
    ]
    start = random.choice(starts)
    base = title
    if len(base) < 40 and summary:
        base = f"{title}: {summary}"
    base = re.sub(r"[\.!\u061F]+$", "", base).strip()
    return shorten(f"{start} {base}؟", 140)


def compose_tweet(title: str, summary: str, url: str) -> str:
    """
    يبني تغريدة متعددة الأسطر (≥ 3 أسطر):
    1) سؤال تشويقي
    2) هاشتاغات تشمل #لودينغ
    3) رابط الموقع لقراءة المنشور
    4) رابط اليوتيوب (قابل للنقر) — يُحذف فقط إذا تعذّر الطول
    """
    q = to_question(title, summary)

    line1 = q
    line2 = HASHTAGS
    line3 = f"🔗 اقرأ من الموقع: {url}"
    line4 = f"🎬 يوتيوب: {YOUTUBE_URL}"

    # حاول تضمين 4 أسطر، ثم قلّم تدريجيًا مع الحفاظ على ≥ 3 أسطر
    body4 = "\n".join([line1, line2, line3, line4])
    if len(body4) <= 280: return body4

    body3 = "\n".join([line1, line2, line3])
    if len(body3) <= 280: return body3

    for qlen in (120, 110, 100, 90, 80, 70, 60):
        body_try = "\n".join([shorten(line1, qlen), line2, line3])
        if len(body_try) <= 280:
            return body_try

    mini_tags = "#لودينغ #مقالات"
    body_mini = "\n".join([shorten(line1, 60), mini_tags, line3])
    if len(body_mini) <= 280: return body_mini

    return f"{shorten(q, 60)}\n#لودينغ\n{line3}"


def find_image_url(entry) -> str | None:
    """يحاول استخراج أوّل صورة من RSS (media_content/thumbnail أو content HTML)."""
    # 1) media:content / media:thumbnail
    for key in ("media_content", "media_thumbnail"):
        if entry.get(key):
            try:
                url = entry[key][0].get("url")
                if url and url.startswith(("http://", "https://")):
                    return url
            except Exception:
                pass
    # 2) من content/summary بـ <img src="...">
    html_blob = entry.get("content", [{}])[0].get("value") if entry.get(
        "content") else entry.get("summary", "")
    if html_blob:
        m = IMG_RE.search(html_blob)
        if m:
            url = m.group(1)
            if url.startswith("//"): url = "https:" + url
            if url.startswith(
                ("http://", "https://")) and not url.startswith("data:"):
                return url
    return None


def download_image(url: str,
                   timeout=10,
                   max_bytes=5 * 1024 * 1024) -> str | None:
    """يحمّل الصورة إلى ملف مؤقت ويرجع المسار؛ وإلا None."""
    try:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; LoadingAPKBot/1.0)"}
        with requests.get(url, headers=headers, stream=True,
                          timeout=timeout) as r:
            r.raise_for_status()
            ctype = r.headers.get("Content-Type", "").lower()
            if not any(x in ctype for x in
                       ["image/jpeg", "image/png", "image/webp", "image/jpg"]):
                # نجرب رغم ذلك إن لم يُعلن النوع
                pass
            # حفظ إلى /tmp
            ext = ".jpg"
            if "png" in ctype: ext = ".png"
            elif "webp" in ctype: ext = ".webp"
            path = f"/tmp/ldg_{int(time.time())}{ext}"
            size = 0
            with open(path, "wb") as f:
                for chunk in r.iter_content(8192):
                    if not chunk: continue
                    size += len(chunk)
                    if size > max_bytes:
                        f.close()
                        try:
                            os.remove(path)
                        except:
                            pass
                        return None
                    f.write(chunk)
            return path
    except Exception as e:
        print("[IMG] فشل تنزيل الصورة:", e)
        return None


def upload_media_get_id(img_path: str) -> int | None:
    """يرفع الصورة عبر v1.1 ويعيد media_id؛ أو None."""
    try:
        media = api_v1.media_upload(filename=img_path)
        return media.media_id
    except Exception as e:
        print("[IMG] فشل الرفع:", e)
        return None


def fetch_entries():
    feed = feedparser.parse(RSS)
    entries = []
    for e in feed.entries:
        title = (e.get("title") or "").strip()
        link = (e.get("link") or "").strip()
        summary = clean_html(
            e.get("summary", "") or (e.get("content", [{}])[0].get("value")
                                     if e.get("content") else ""))
        entries.append({
            "title": title,
            "link": link,
            "summary": summary,
            "raw": e
        })
    return entries


# ========================
# نشر مقالات جديدة (مع صورة لو أمكن)
# ========================
def post_new_articles(limit=MAX_NEW_PER_RUN):
    entries = fetch_entries()
    if not entries:
        print("[RSS] لا توجد عناصر.")
        return 0

    state = load_json()
    posted_pids = {x["pid"] for x in state}
    seen = load_seen()

    published = 0
    for item in entries[:10]:  # الأحدث أولاً
        pid = sha(item["link"])
        if pid in posted_pids or item["link"] in seen:
            continue

        tweet = compose_tweet(item["title"], item["summary"], item["link"])

        media_ids = None
        try:
            img_url = find_image_url(item["raw"])
            if img_url:
                img_path = download_image(img_url)
                if img_path:
                    mid = upload_media_get_id(img_path)
                    if mid:
                        media_ids = [mid]
                        print("[IMG] أُرفقت صورة:", img_url)
        except Exception as e:
            print("[IMG] تخطّي الصورة بسبب خطأ:", e)

        if media_ids:
            resp = client.create_tweet(text=tweet, media_ids=media_ids)
        else:
            resp = client.create_tweet(text=tweet)

        tid = resp.data["id"]
        print("[NEW] تم النشر:", tid, "→", item["link"])

        state.append({
            "pid": pid,
            "title": item["title"],
            "link": item["link"],
            "tweet_id": tid,
            "posted_at": int(time.time())
        })
        save_json(state)

        seen.add(item["link"])
        save_seen(seen)
        published += 1
        if published >= limit: break

    if published == 0: print("[NEW] لا جديد للنشر.")
    return published


# ========================
# إحياء تلقائي كل 72 ساعة
# ========================
def maybe_resurface():
    now = int(time.time())
    last = 0
    if RESURFACE_TS.exists():
        try:
            last = int(RESURFACE_TS.read_text().strip() or "0")
        except:
            last = 0

    if now - last < RESURFACE_EVERY_HOURS * 3600:
        print("[RESURFACE] لم يحن الوقت بعد.")
        return None

    state = load_json()
    if len(state) < 2:
        print("[RESURFACE] الأرشيف صغير.")
        RESURFACE_TS.write_text(str(now))
        return None

    cand = random.choice(state[:-1])  # استبعد الأحدث
    quote_text = random.choice([
        "تذكير مهم من أرشيفنا 📚", "عودة لواحدة من قراءاتنا المفضلة 🔁",
        "هل فاتتك هذه؟ 👇"
    ])
    resp = client.create_tweet(text=quote_text,
                               quote_tweet_id=cand["tweet_id"])
    print("[RESURFACE] اقتباس:", resp.data["id"], "←", cand["tweet_id"])
    RESURFACE_TS.write_text(str(now))
    return resp.data["id"]


# ========================
# جدولة داخلية (ديمون)
# ========================
def parse_times_local(times_list):
    return [(int(t.split(":")[0]), int(t.split(":")[1])) for t in times_list]


def next_fire_after(now_dt, hh, mm):
    fire = now_dt.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if fire <= now_dt: fire += timedelta(days=1)
    return fire


def run_daemon():
    post_slots = parse_times_local(POST_TIMES_LOCAL)
    next_runs = {
        (h, m): next_fire_after(now_local(), h, m)
        for (h, m) in post_slots
    }
    print("[DAEMON] بدأ العمل. فحص كل", POLL_EVERY_MIN, "دقيقة. أوقات:",
          POST_TIMES_LOCAL)

    # تشغيل أولي
    post_new_articles()
    maybe_resurface()

    last_poll = datetime.min.replace(tzinfo=BAGHDAD_TZ)
    while True:
        now = now_local()

        # فحص RSS دوري
        if (now - last_poll) >= timedelta(minutes=POLL_EVERY_MIN):
            print("[POLL]", now.strftime("%Y-%m-%d %H:%M"))
            post_new_articles()
            maybe_resurface()
            last_poll = now

        # تنفيذ عند الأوقات المحددة
        for (h, m), fire in list(next_runs.items()):
            if now >= fire:
                print(
                    f"[SLOT {h:02d}:{m:02d}] وقت النشر المحدد — محاولة نشر جديد."
                )
                post_new_articles(limit=MAX_NEW_PER_RUN)
                next_runs[(h, m)] = next_fire_after(now, h, m)

        time.sleep(20)


# ========================
# خادم Flask للإبقاء على Replit نشطًا
# ========================
app = Flask("keep_alive")


@app.get("/")
def home():
    return "Bot is running ✅", 200


def start_web():
    app.run(host="0.0.0.0", port=8080)


# ========================
# التشغيل
# ========================
def main():
    parser = argparse.ArgumentParser(
        description="Twitter auto poster for LoadingAPK")
    parser.add_argument("--daemon",
                        action="store_true",
                        help="تشغيل دائم مع جدولة داخلية")
    args = parser.parse_args()

    if args.daemon:
        threading.Thread(target=start_web, daemon=True).start()
        run_daemon()
    else:
        posted = post_new_articles(limit=MAX_NEW_PER_RUN)
        maybe_resurface()
        print("[DONE] تمت العملية. نُشر:", posted)


if __name__ == "__main__":
    main()
