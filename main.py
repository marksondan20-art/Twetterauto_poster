# -*- coding: utf-8 -*-
"""
main.py — نشر آخر مقالات Blogger على X (Twitter) مع:
- منع تكرار التغريد لنفس المقال خلال 72 ساعة
- كسر التطابق (duplicate) بتدوير CTA وإضافة UTM متغيّر للرابط
- تضمين رابط المقال + رابط قناة يوتيوب + #لودينغ

الاستدعاء:
    import main
    main.tweet_new_posts(1)  # غرّد أحدث مقال واحد
أو:
    if __name__ == "__main__": main.tweet_new_posts(1)
"""

import os, re, json, time, random, urllib.parse
from datetime import datetime, timedelta

import tweepy
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

# ===================== إعدادات عامة =====================
YOUTUBE_URL = os.getenv("YOUTUBE_URL", "https://www.youtube.com/@-Muhamedloading")
HISTORY_FILE = "tweeted_posts.jsonl"   # يُنشأ تلقائيًا لمنع إعادة التغريد
NO_RETWEET_HOURS = 72                  # لا نغرّد نفس المقال خلال 72 ساعة

# عبارات CTA متنوعة لكسر التطابق
CTAS = [
    "لو مهتم بالتفاصيل، المقال كامل هنا",
    "الشرح كامل والرابط بالمقال",
    "القصة الكاملة عبر هذا الرابط",
    "التفاصيل مع الأمثلة في المقال",
    "مُلخّص ذكي ومصادر موثوقة بالمقال",
    "كل ما تحتاج معرفته هنا",
    "لو حابب تعرف أكتر… اقرأ المقال",
    "خلاصة دقيقة وروابط مفيدة بالمصدر",
]

# ===================== أدوات صغيرة =====================
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
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")

def _as_question(title: str) -> str:
    t = (title or "").strip()
    if not t: return ""
    if not t.endswith(("؟","?")):
        t = re.sub(r"[.!…]+$","", t) + "؟"
    return t

def _add_utm(url: str, tag: str) -> str:
    """نضيف UTM متغيّر لكسر التطابق دون إفساد الرابط."""
    try:
        u = urllib.parse.urlparse(url)
        q = urllib.parse.parse_qsl(u.query, keep_blank_values=True)
        q.append(("utm_source","twitter"))
        q.append(("utm_campaign", tag))
        new_q = urllib.parse.urlencode(q)
        return urllib.parse.urlunparse((u.scheme, u.netloc, u.path, u.params, new_q, u.fragment))
    except Exception:
        sep = "&" if "?" in url else "?"
        return f"{url}{sep}utm_source=twitter&utm_campaign={tag}"

def _build_unique_tweet(title: str, url: str, salt: str) -> str:
    q = _as_question(title) or "تفاصيل أكثر في المقال:"
    cta = random.choice(CTAS)
    url_u = _add_utm(url, f"auto_{salt}")
    text = f"{q}\n{url_u}\n{cta}\nقناتنا على يوتيوب: {YOUTUBE_URL}\n#لودينغ"
    if len(text) > 280:
        room = 280 - (len(text) - len(q))
        q2 = (q[:max(0, room-1)] + "…") if room > 10 else q[:max(0, room)]
        text = f"{q2}\n{url_u}\n{cta}\nقناتنا على يوتيوب: {YOUTUBE_URL}\n#لودينغ"
    return text

# ===================== Blogger API =====================
def _blogger_service():
    creds = Credentials(
        None,
        refresh_token=os.environ["REFRESH_TOKEN"],
        client_id=os.environ["CLIENT_ID"],
        client_secret=os.environ["CLIENT_SECRET"],
        token_uri="https://oauth2.googleapis.com/token",
        scopes=["https://www.googleapis.com/auth/blogger"],
    )
    return build("blogger", "v3", credentials=creds, cache_discovery=False)

def _blog_id(svc):
    blog = svc.blogs().getByUrl(url=os.environ["BLOG_URL"]).execute()
    return blog["id"]

def list_recent_posts(limit=12, max_age_days=7):
    """يجلب أحدث مقالات (عنوان+رابط+id) خلال max_age_days."""
    svc = _blogger_service()
    bid = _blog_id(svc)
    items = svc.posts().list(
        blogId=bid, fetchBodies=False, maxResults=limit,
        orderBy="PUBLISHED", status=["live"]
    ).execute().get("items", [])
    cutoff = datetime.utcnow() - timedelta(days=max_age_days)
    out=[]
    for it in items:
        published = it.get("published","")
        try:
            dt = datetime.fromisoformat(published.replace("Z","+00:00"))
        except Exception:
            dt = datetime.utcnow()
        if dt < cutoff:
            continue
        out.append({
            "id": it["id"],
            "title": (it.get("title") or "").strip(),
            "url": it.get("url") or it.get("selfLink") or "",
            "published": dt.isoformat()
        })
    return out

# ===================== X (Twitter) =====================
def _make_x_client():
    return tweepy.Client(
        consumer_key=os.environ["TW_API_KEY"],
        consumer_secret=os.environ["TW_API_SECRET"],
        access_token=os.environ["TW_ACCESS_TOKEN"],
        access_token_secret=os.environ["TW_ACCESS_SECRET"],
        wait_on_rate_limit=True,
    )

def _already_tweeted(post_id: str, hours=NO_RETWEET_HOURS) -> bool:
    """عدم تغريد نفس المقال خلال نافذة زمنية."""
    cutoff = datetime.utcnow() - timedelta(hours=hours)
    for r in reversed(_load_jsonl(HISTORY_FILE)):
        if r.get("post_id") == post_id:
            try:
                t = datetime.fromisoformat(r.get("time"))
            except Exception:
                return True
            return t >= cutoff
    return False

def _mark_tweeted(post_id: str, tweet_id: str):
    _append_jsonl(HISTORY_FILE, {
        "post_id": post_id,
        "tweet_id": tweet_id,
        "time": datetime.utcnow().isoformat(timespec="seconds")
    })

def tweet_new_posts(count: int = 1):
    """
    ينشر حتى count تغريدات من أحدث مقالات Blogger:
    - يمنع تكرار التغريد لنفس المقال خلال 72 ساعة
    - يكسر duplicate عبر CTA وUTM متغيّر
    """
    # تحقّق من المتغيرات
    required = [
        "BLOG_URL","CLIENT_ID","CLIENT_SECRET","REFRESH_TOKEN",
        "TW_API_KEY","TW_API_SECRET","TW_ACCESS_TOKEN","TW_ACCESS_SECRET"
    ]
    missing = [k for k in required if not os.getenv(k)]
    if missing:
        raise RuntimeError(f"Missing env vars: {', '.join(missing)}")

    posts = list_recent_posts(limit=max(12, count*4), max_age_days=7)
    if not posts:
        print("لا توجد منشورات حديثة.")
        return

    client = _make_x_client()
    me = client.get_me()
    print("AUTH_OK for:", "@"+me.data.username if me and me.data else "UNKNOWN")

    tweeted = 0
    for p in posts:
        if tweeted >= count:
            break
        if _already_tweeted(p["id"], hours=NO_RETWEET_HOURS):
            continue

        salt = datetime.utcnow().strftime("%Y%m%d%H") + f"_{random.randint(100,999)}"
        text = _build_unique_tweet(p["title"], p["url"], salt)

        try:
            r = client.create_tweet(text=text)
            tid = (r.data or {}).get("id")
            print("TWEET_OK:", p["title"], p["url"], "->", f"https://x.com/i/web/status/{tid}")
            _mark_tweeted(p["id"], str(tid))
            tweeted += 1
            time.sleep(2)
            continue

        except tweepy.Forbidden as e:
            msg = str(e)
            # لو ظهر تكرار، جرّب نص/UTM مختلفين فورًا
            if "duplicate" in msg.lower():
                try:
                    salt2 = datetime.utcnow().strftime("%Y%m%d%H") + f"_{random.randint(1000,9999)}"
                    text2 = _build_unique_tweet(p["title"], p["url"], salt2)
                    r2 = client.create_tweet(text=text2)
                    tid2 = (r2.data or {}).get("id")
                    print("TWEET_OK_after_retry:", p["title"], p["url"], "->", f"https://x.com/i/web/status/{tid2}")
                    _mark_tweeted(p["id"], str(tid2))
                    tweeted += 1
                    time.sleep(2)
                    continue
                except Exception as e2:
                    print("TWEET_DUPLICATE_RETRY_FAIL:", p["title"], e2)
                    continue
            else:
                print("403 Forbidden عند create_tweet:", e)
                continue

        except Exception as e:
            print("TWEET_ERR:", p["title"], e)
            continue

    if tweeted == 0:
        print("لا يوجد ما يُغرد الآن أو كل الأحدث مُغرَّد عنها مؤخرًا.")

# تشغيل يدوي محلي
if __name__ == "__main__":
    tweet_new_posts(1)
