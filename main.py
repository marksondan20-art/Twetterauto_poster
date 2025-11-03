# -*- coding: utf-8 -*-
"""
main.py — Tweet Blogger posts to X (Twitter)

الميزات:
- تغريد أحدث المقالات: سؤال جذّاب من العنوان + رابط المقال + رابط اليوتيوب + #لودينغ
- منع تكرار التغريد لنفس المقال (محليًا بملف JSONL + اختياريًا عبر إضافة Label داخل Blogger)
- إعادة تغريد/نشر مقال قديم كل 72 ساعة
- جدولة تلقائية 12:00 و 19:00 بتوقيت بغداد عبر APScheduler، أو تشغيل مرة واحدة

ENV (ضعها في Replit Secrets / GitHub Secrets):
BLOG_URL, CLIENT_ID, CLIENT_SECRET, REFRESH_TOKEN
TW_API_KEY, TW_API_SECRET, TW_ACCESS_TOKEN, TW_ACCESS_SECRET
YOUTUBE_URL (اختياري، افتراضي قناة لودينغ)
TZ=Asia/Baghdad
SCHEDULE_MODE=1  (1=شغّل الجدولة 12:00 و19:00، 0=تشغيل مرة واحدة ثم خروج)
TWEET_NEW_COUNT=1   (عدد المقالات الجديدة لكل تشغيل)
OLD_TWEET_EVERY_HOURS=72
MARK_TWEET_LABEL=tweeted  (اختياري: اسم ليبل يضاف للمقال داخل Blogger بعد التغريد)
"""

import os, re, json, time, random
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import tweepy
from apscheduler.schedulers.background import BackgroundScheduler

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

# ============ إعدادات عامة ============
TZ = ZoneInfo(os.getenv("TZ", "Asia/Baghdad"))
YOUTUBE_URL = os.getenv("YOUTUBE_URL", "https://www.youtube.com/@-Muhamedloading")
SCHEDULE_MODE = os.getenv("SCHEDULE_MODE", "1") == "1"
TWEET_NEW_COUNT = int(os.getenv("TWEET_NEW_COUNT", "1"))
OLD_TWEET_EVERY_HOURS = int(os.getenv("OLD_TWEET_EVERY_HOURS", "72"))
MARK_TWEET_LABEL = os.getenv("MARK_TWEET_LABEL", "tweeted").strip()  # اتركه فارغًا لتعطيل وسم Blogger

# ملفات محلية لحالة التغريد
TWEETED_FILE = "tweeted_posts.jsonl"
OLD_TWEET_STATE = "last_old_tweet.json"

# ============ مفاتيح Blogger ============
BLOG_URL      = os.environ["BLOG_URL"]
CLIENT_ID     = os.environ["CLIENT_ID"]
CLIENT_SECRET = os.environ["CLIENT_SECRET"]
REFRESH_TOKEN = os.environ["REFRESH_TOKEN"]

# ============ مفاتيح X (Twitter) ============
TW_API_KEY       = os.environ["TW_API_KEY"]
TW_API_SECRET    = os.environ["TW_API_SECRET"]
TW_ACCESS_TOKEN  = os.environ["TW_ACCESS_TOKEN"]
TW_ACCESS_SECRET = os.environ["TW_ACCESS_SECRET"]

# ============ أدوات مساعدة ============
def now():
    return datetime.now(TZ)

def _load_jsonl(path):
    if not os.path.exists(path): return []
    out=[]
    with open(path, "r", encoding="utf-8") as f:
        for ln in f:
            try: out.append(json.loads(ln))
            except: pass
    return out

def _append_jsonl(path, obj):
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False)+"\n")

def _load_json(path, default=None):
    if not os.path.exists(path): return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return default

def _save_json(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)

def _as_question(title: str) -> str:
    t = (title or "").strip()
    if not t: return t
    if not t.endswith("؟") and not t.endswith("?"):
        t = re.sub(r"[.!…]+$", "", t) + "؟"
    return t

def build_tweet_text(title: str, url: str) -> str:
    """
    نص التغريدة النهائي:
    - سؤال جذّاب من العنوان
    - رابط المقال
    - CTA نحو اليوتيوب
    - #لودينغ
    مع تقليم بسيط لاحترام 280 حرفًا.
    """
    q = _as_question(title) or "تفاصيل أكثر في المقال:"
    base = f"{q}\n{url}\nقناتنا على يوتيوب: {YOUTUBE_URL}\n#لودينغ"
    if len(base) > 280:
        room = 280 - (len(base) - len(q))
        q2 = (q[:max(0, room-1)] + "…") if room > 10 else q[:max(0, room)]
        base = f"{q2}\n{url}\nقناتنا على يوتيوب: {YOUTUBE_URL}\n#لودينغ"
    return base

# ============ Blogger ============

def get_blogger_service():
    creds = Credentials(
        None,
        refresh_token=REFRESH_TOKEN,
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        token_uri="https://oauth2.googleapis.com/token",
        scopes=["https://www.googleapis.com/auth/blogger"],
    )
    return build("blogger", "v3", credentials=creds, cache_discovery=False)

def _blog_id(svc):
    return svc.blogs().getByUrl(url=BLOG_URL).execute()["id"]

def list_live_posts(limit=50, days_back=30):
    """يرجع أحدث مقالات حية: [{id,title,url,published_dt}] خلال فترة محددة."""
    svc = get_blogger_service()
    bid = _blog_id(svc)
    items = svc.posts().list(
        blogId=bid, fetchBodies=False, maxResults=min(500, limit),
        orderBy="PUBLISHED", status=["live"]
    ).execute().get("items", [])
    cutoff = now() - timedelta(days=days_back)
    out=[]
    for it in items:
        pub_raw = it.get("published") or ""
        try:
            # Blogger time مثل: 2025-11-03T19:05:08+03:00
            dt = datetime.fromisoformat(pub_raw)
        except Exception:
            # fallback UTC
            dt = datetime.utcnow()
        if dt.replace(tzinfo=None) < cutoff.replace(tzinfo=None):
            continue
        out.append({
            "id": it["id"],
            "title": (it.get("title") or "").strip(),
            "url": it.get("url") or it.get("selfLink") or "",
            "labels": it.get("labels") or [],
            "published_dt": dt,
        })
    return out

def add_label_to_post(post_id: str, label: str):
    if not label: return
    try:
        svc = get_blogger_service()
        bid = _blog_id(svc)
        # نحتاج أولًا جلب الملصقات الحالية:
        cur = svc.posts().get(blogId=bid, postId=post_id).execute()
        labels = (cur.get("labels") or [])[:]
        if label not in labels:
            labels.append(label)
        body = {"labels": labels}
        svc.posts().patch(blogId=bid, postId=post_id, body=body).execute()
    except Exception as e:
        print("WARN: add_label_to_post failed:", e)

# ============ X (Twitter) ============

def make_x_client():
    client = tweepy.Client(
        consumer_key=TW_API_KEY,
        consumer_secret=TW_API_SECRET,
        access_token=TW_ACCESS_TOKEN,
        access_token_secret=TW_ACCESS_SECRET,
        wait_on_rate_limit=True,
    )
    # اختبار سريع
    me = client.get_me()
    print("AUTH_OK for:", "@"+me.data.username)
    return client

def already_tweeted(post_id: str) -> bool:
    for r in _load_jsonl(TWEETED_FILE):
        if r.get("post_id") == post_id:
            return True
    return False

def mark_tweeted(post_id: str, tweet_id: str):
    _append_jsonl(TWEETED_FILE, {
        "post_id": post_id,
        "tweet_id": str(tweet_id),
        "time": datetime.utcnow().isoformat()
    })

def tweet_new_posts(count: int = 1):
    """
    غرّد حتى count من أحدث المقالات (غير المغرّد عنها سابقًا).
    يحاول إضافة Label داخل Blogger بعد النجاح (اختياريًا).
    """
    posts = list_live_posts(limit=max(30, count*4), days_back=14)
    client = make_x_client()

    tweeted = 0
    for p in posts:
        if tweeted >= count: break
        if already_tweeted(p["id"]) or (MARK_TWEET_LABEL and MARK_TWEET_LABEL in p["labels"]):
            continue
        text = build_tweet_text(p["title"], p["url"])
        try:
            r = client.create_tweet(text=text)
            tid = (r.data or {}).get("id")
            print("TWEET_OK:", p["title"], "->", f"https://x.com/i/web/status/{tid}")
            mark_tweeted(p["id"], tid or "")
            if MARK_TWEET_LABEL:
                add_label_to_post(p["id"], MARK_TWEET_LABEL)
            tweeted += 1
            time.sleep(2)
        except tweepy.Forbidden as e:
            print("403 Forbidden عند create_tweet:", e)
            break
        except Exception as e:
            print("TWEET_ERR:", p["title"], e)
            continue

    if tweeted == 0:
        print("لا يوجد مقال جديد مناسب للتغريد الآن.")

# ============ إعادة تغريد مقال قديم كل 72 ساعة ============

def should_tweet_old() -> bool:
    st = _load_json(OLD_TWEET_STATE, {})
    last = st.get("last_time")
    if not last: return True
    try:
        last_dt = datetime.fromisoformat(last)
    except Exception:
        return True
    return (datetime.utcnow() - last_dt) >= timedelta(hours=OLD_TWEET_EVERY_HOURS)

def mark_old_tweeted():
    _save_json(OLD_TWEET_STATE, {"last_time": datetime.utcnow().isoformat()})

def tweet_one_old_post():
    """
    اختَر مقالًا قديماً (ما زال حديثاً خلال 90 يومًا) سبق تغريده أو لم يُغرّد،
    وغرّدُه مجددًا كسؤال جذّاب.
    """
    if not should_tweet_old():
        print("لم يحن وقت تغريد مقال قديم بعد.")
        return

    posts = list_live_posts(limit=80, days_back=90)
    if not posts:
        print("لا توجد مقالات لاختيار تغريدة قديمة.")
        return

    # فضّل مقالات مُغرَّد عنها سابقًا ثم اختر عشوائيًا
    tweeted_ids = {r.get("post_id") for r in _load_jsonl(TWEETED_FILE)}
    tweeted_posts = [p for p in posts if p["id"] in tweeted_ids]
    pool = tweeted_posts or posts
    p = random.choice(pool)

    text = build_tweet_text(p["title"], p["url"])
    client = make_x_client()
    try:
        r = client.create_tweet(text=text)
        tid = (r.data or {}).get("id")
        print("OLD_TWEET_OK:", p["title"], "->", f"https://x.com/i/web/status/{tid}")
        mark_old_tweeted()
    except Exception as e:
        print("OLD_TWEET_ERR:", e)

# ============ جدولة/تشغيل ============

def job_new_noon():
    print(f"[{now()}] NEW NOON")
    tweet_new_posts(TWEET_NEW_COUNT)

def job_new_evening():
    print(f"[{now()}] NEW EVENING")
    tweet_new_posts(TWEET_NEW_COUNT)

def job_old_every_72h():
    print(f"[{now()}] OLD 72h")
    tweet_one_old_post()

def schedule_jobs():
    sched = BackgroundScheduler(timezone=str(TZ))
    # 12:00 و 19:00 بغداد — مقالات جديدة
    sched.add_job(job_new_noon,   "cron", hour=12, minute=0, id="new_noon")
    sched.add_job(job_new_evening,"cron", hour=19, minute=0, id="new_evening")
    # فحص كل ساعة هل حان وقت تغريدة قديمة
    sched.add_job(job_old_every_72h, "cron", minute=5)  # كل ساعة عند الدقيقة 5
    sched.start()
    print("Scheduler started: 12:00 & 19:00 Baghdad for new posts, plus 72h-old repost check hourly.")

def run_once():
    """تشغيل فوري: غرّد أحدث مقال واحد + جرّب تغريدة قديمة إن حان وقتها."""
    tweet_new_posts(TWEET_NEW_COUNT)
    tweet_one_old_post()

if __name__ == "__main__":
    if SCHEDULE_MODE:
        schedule_jobs()
        try:
            while True:
                time.sleep(30)
        except KeyboardInterrupt:
            pass
    else:
        run_once()
