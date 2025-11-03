# -*- coding: utf-8 -*-
"""
main.py — يغرد أحدث مقالات Blogger إلى X:
- يسحب أحدث المقالات المنشورة (LIVE) من Blogger
- يبني تغريدة كسؤال جذّاب + رابط المقال + رابط اليوتيوب + #لودينغ
- يمنع تكرار التغريد لنفس المقال
- كل الأوقات "aware/UTC" لتجنّب TypeError في المقارنات
"""

import os, re, json, time, html
from datetime import datetime, timedelta, timezone

import tweepy
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

# --------- الإعدادات من البيئة ---------
BLOG_URL       = os.environ["BLOG_URL"]  # مثال: https://loadingapk391.blogspot.com/
CLIENT_ID      = os.environ["CLIENT_ID"]
CLIENT_SECRET  = os.environ["CLIENT_SECRET"]
REFRESH_TOKEN  = os.environ["REFRESH_TOKEN"]

TW_API_KEY       = os.environ["TW_API_KEY"]
TW_API_SECRET    = os.environ["TW_API_SECRET"]
TW_ACCESS_TOKEN  = os.environ["TW_ACCESS_TOKEN"]
TW_ACCESS_SECRET = os.environ["TW_ACCESS_SECRET"]

YOUTUBE_URL = os.getenv("YOUTUBE_URL", "https://www.youtube.com/@-Muhamedloading")

# ملف محلي بسيط لمنع تكرار التغريد
TWEET_LOG_FILE = "tweeted_posts.jsonl"


# =============== أدوات مساعدة عامّة ===============
def _now_utc():
    """وقت حالي aware/UTC."""
    return datetime.now(timezone.utc)


def _load_jsonl(path):
    if not os.path.exists(path): return []
    out = []
    with open(path, "r", encoding="utf-8") as f:
        for ln in f:
            try: out.append(json.loads(ln))
            except: pass
    return out


def _append_jsonl(path, obj):
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def _already_tweeted(post_id: str) -> bool:
    for r in _load_jsonl(TWEET_LOG_FILE):
        if r.get("post_id") == post_id:
            return True
    return False


def _mark_tweeted(post_id: str, tweet_id: str | None):
    _append_jsonl(TWEET_LOG_FILE, {
        "post_id": post_id,
        "tweet_id": tweet_id,
        "time": _now_utc().isoformat()
    })


# =============== Blogger ===============
def _blogger_service():
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
    blog = svc.blogs().getByUrl(url=BLOG_URL).execute()
    return blog["id"]


def _to_utc_aware(published: str) -> datetime:
    """
    يحوّل نص تاريخ Blogger إلى datetime aware/UTC.
    Blogger يعيد ISO8601 مع 'Z' في النهاية عادةً.
    """
    if not published:
        return _now_utc()
    # استبدال Z بـ +00:00 ثم التحويل
    try:
        dt = datetime.fromisoformat(published.replace("Z", "+00:00"))
    except Exception:
        return _now_utc()
    # إذا خرج بدون tzinfo (نادرًا)، اجعله UTC
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    # ثم وحّده إلى UTC
    return dt.astimezone(timezone.utc)


def list_recent_posts(limit: int = 12, max_age_days: int = 7):
    """
    يرجع قائمة أحدث المنشورات المنشورة (LIVE) خلال max_age_days:
    [{'id','title','url','published_utc'}, ...]
    """
    svc = _blogger_service()
    bid = _blog_id(svc)

    # مهم: الحالة بحروف كبيرة كما تتوقع واجهة Blogger
    items = svc.posts().list(
        blogId=bid,
        fetchBodies=False,
        maxResults=limit,
        orderBy="PUBLISHED",
        status=["LIVE"],  # كان سبب الخطأ سابقًا استخدام 'live'
    ).execute().get("items", [])

    cutoff = _now_utc() - timedelta(days=max_age_days)

    out = []
    for it in items:
        dt = _to_utc_aware(it.get("published"))
        if dt < cutoff:
            continue
        out.append({
            "id": it["id"],
            "title": (it.get("title") or "").strip(),
            "url": it.get("url") or it.get("selfLink") or "",
            "published_utc": dt,
        })
    return out


# =============== بناء نص التغريدة ===============
def _as_question(title: str) -> str:
    """تحويل العنوان إلى سؤال جذّاب إن لم ينتهِ بعلامة استفهام."""
    t = (title or "").strip()
    if not t:
        return "تفاصيل أكثر في المقال؟"
    if not (t.endswith("؟") or t.endswith("?")):
        t = re.sub(r"[.!…]+$", "", t) + "؟"
    return t


def build_tweet_text(title: str, url: str) -> str:
    """
    يكوّن التغريدة:
    - سؤال جذاب من العنوان
    - رابط المقال
    - CTA لليوتيوب
    - #لودينغ
    مع تقليم بسيط تحت حد 280 حرفًا (روابط X تُقصر تلقائيًا).
    """
    q = _as_question(title)
    # لا نمرر HTML في النص
    safe_url = (url or "").strip()
    yt = YOUTUBE_URL.strip()

    base = f"{q}\n{safe_url}\nقناتنا على يوتيوب: {yt}\n#لودينغ"

    # تقليم خفيف لو تجاوز 280
    if len(base) > 280:
        room = 280 - (len(base) - len(q))
        q2 = (q[:max(0, room - 1)] + "…") if room > 8 else q[:max(0, room)]
        base = f"{q2}\n{safe_url}\nقناتنا على يوتيوب: {yt}\n#لودينغ"

    return base


# =============== X (Twitter) ===============
def _x_client():
    """
    عميل Tweepy v2. استخدم create_tweet (v2).
    تأكد أن التطبيق لديه Read/Write وأن المفاتيح تخص نفس التطبيق/الحساب.
    """
    client = tweepy.Client(
        consumer_key=TW_API_KEY,
        consumer_secret=TW_API_SECRET,
        access_token=TW_ACCESS_TOKEN,
        access_token_secret=TW_ACCESS_SECRET,
        wait_on_rate_limit=True,
    )
    # تشخيص سريع
    try:
        me = client.get_me()
        print("AUTH_OK for:", "@"+me.data.username)
    except Exception as e:
        print("AUTH_FAIL:", repr(e))
        raise
    return client


def _post_tweet(client, text: str) -> str | None:
    """
    ينشر تغريدة. إذا ظهر خطأ "duplicate content" نحاول إضافة مُلَطِّف صغير.
    يعيد tweet_id أو None.
    """
    try:
        r = client.create_tweet(text=text)
        return (r.data or {}).get("id")
    except tweepy.Forbidden as e:
        msg = str(e)
        # في بعض الحالات ترجع X رسالة مكررات content
        if "duplicate" in msg.lower():
            softened = text
            # أضف مسافة ضئيلة/تنويعة طفيفة لكسر التطابق
            softened += " ‎"  # U+00A0/space-like
            try:
                r = client.create_tweet(text=softened)
                return (r.data or {}).get("id")
            except Exception as ee:
                print("TWEET_ERR (dup retry):", repr(ee))
                return None
        print("403 Forbidden:", msg)
        return None
    except Exception as e:
        print("TWEET_ERR:", repr(e))
        return None


# =============== منطق التنفيذ ===============
def tweet_new_posts(count: int = 1, max_age_days: int = 7):
    """
    ينشر حتى count تغريدة من أحدث مقالات Blogger (LIVE) خلال max_age_days.
    يمنع تكرار التغريد لنفس المقال.
    """
    posts = list_recent_posts(limit=max(12, count * 4), max_age_days=max_age_days)
    if not posts:
        print("لا توجد مقالات حديثة مناسبة.")
        return

    client = _x_client()

    tweeted = 0
    for p in posts:
        if tweeted >= count:
            break
        if _already_tweeted(p["id"]):
            continue
        if not p["url"]:
            continue

        text = build_tweet_text(p["title"], p["url"])
        tid = _post_tweet(client, text)
        if tid:
            print("TWEET_OK:", p["title"], p["url"], "->", f"https://x.com/i/web/status/{tid}")
            _mark_tweeted(p["id"], tid)
            tweeted += 1
            time.sleep(2)

    if tweeted == 0:
        print("لا يوجد ما يُغرد الآن أو جميع الأحدث مُغرَّد عنها من قبل.")


# نقطة دخول بسيطة متوافقة مع GitHub Actions/Replit
def run_once():
    # غرد مقالًا واحدًا افتراضيًا
    tweet_new_posts(count=int(os.getenv("COUNT") or 1))


def publish():
    run_once()


if __name__ == "__main__":
    run_once()
