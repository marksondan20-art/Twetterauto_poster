# -*- coding: utf-8 -*-
"""
main.py — نشر أحدث مقالات Blogger على X (Twitter) مع:
- رابط المقال + رابط قناة يوتيوب + #لودينغ
- منع التكرار 72 ساعة لكل مقال
- تفادي 403 duplicate عبر: CTA متغيّر + UTM فريد + محارف غير مرئية + محاولات بديلة
"""

import os, re, json, time, random, urllib.parse, hashlib
from datetime import datetime, timedelta

import tweepy
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

# ============ إعدادات ============
YOUTUBE_URL = os.getenv("YOUTUBE_URL", "https://www.youtube.com/@-Muhamedloading")
HISTORY_FILE = "tweeted_posts.jsonl"
NO_RETWEET_HOURS = 72                 # لا نغرّد نفس المقال خلال 72 ساعة
MAX_TWEET_LEN = 280

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

# محارف غير مرئية لكسر التطابق من غير ما تغيّر المعنى
ZW_CHARS = ["\u200b", "\u200c", "\u200d", "\u2060", "\u2063"]

# ============ أدوات مساعدة ============
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

def _inject_zw(s: str) -> str:
    """نحقن محرف غير مرئي في مواضع مختلفة لكسر التطابق بدون ما يبان."""
    if not s: return s
    parts = s.split(" ")
    if len(parts) > 3:
        idx = random.randint(1, len(parts)-2)
        parts[idx] = parts[idx] + random.choice(ZW_CHARS)
        s = " ".join(parts)
    else:
        s = s + random.choice(ZW_CHARS)
    return s

def _hash_text(s: str) -> str:
    return hashlib.sha1((s or "").encode("utf-8")).hexdigest()[:12]

def _build_tweet(title: str, url: str, variant: int) -> str:
    q = _as_question(title) or "تفاصيل أكثر في المقال:"
    cta = random.choice(CTAS)
    salt = datetime.utcnow().strftime("%Y%m%d%H") + f"_{random.randint(10,99)}_{variant}"
    url_u = _add_utm(url, f"auto_{salt}")
    text = f"{q}\n{url_u}\n{cta}\nقناتنا على يوتيوب: {YOUTUBE_URL}\n#لودينغ"
    if len(text) > MAX_TWEET_LEN:
        room = MAX_TWEET_LEN - (len(text) - len(q))
        q2 = (q[:max(0, room-1)] + "…") if room > 10 else q[:max(0, room)]
        text = f"{q2}\n{url_u}\n{cta}\nقناتنا على يوتيوب: {YOUTUBE_URL}\n#لودينغ"
    # حقن محرف غير مرئي لكسر أي تطابق صارم
    return _inject_zw(text)

# ============ Blogger ============
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
    svc = _blogger_service()
    bid = _blog_id(svc)
    items = svc.posts().list(
        blogId=bid, fetchBodies=False, maxResults=limit,
        orderBy="PUBLISHED", status=["LIVE"]
    ).execute().get("items", [])
    cutoff = datetime.utcnow() - timedelta(days=max_age_days)
    out=[]
    for it in items:
        published = it.get("published","")
        try:
            dt = datetime.fromisoformat(published.replace("Z","+00:00"))
        except Exception:
            dt = datetime.utcnow()
        if dt < cutoff:  # الأحدث فقط
            continue
        out.append({
            "id": it["id"],
            "title": (it.get("title") or "").strip(),
            "url": it.get("url") or it.get("selfLink") or "",
            "published": dt.isoformat()
        })
    return out

# ============ X (Twitter) ============
def _make_x_client():
    return tweepy.Client(
        consumer_key=os.environ["TW_API_KEY"],
        consumer_secret=os.environ["TW_API_SECRET"],
        access_token=os.environ["TW_ACCESS_TOKEN"],
        access_token_secret=os.environ["TW_ACCESS_SECRET"],
        wait_on_rate_limit=True,
    )

def _already_tweeted(post_id: str, hours=NO_RETWEET_HOURS) -> bool:
    cutoff = datetime.utcnow() - timedelta(hours=hours)
    for r in reversed(_load_jsonl(HISTORY_FILE)):
        if r.get("post_id") == post_id:
            try:
                t = datetime.fromisoformat(r.get("time"))
            except Exception:
                return True
            return t >= cutoff
    return False

def _mark_tweeted(post_id: str, tweet_id: str, text_hash: str):
    _append_jsonl(HISTORY_FILE, {
        "post_id": post_id,
        "tweet_id": tweet_id,
        "text_hash": text_hash,
        "time": datetime.utcnow().isoformat(timespec="seconds")
    })

def tweet_new_posts(count: int = 1):
    """
    ينشر حتى count تغريدات من أحدث مقالات Blogger:
    - يمنع التكرار 72 ساعة
    - يتجنب 403 duplicate بمحاولات بصيغ مختلفة
    """
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

        # 3 محاولات بصيغ مختلفة قبل الاستسلام
        variants = 3
        success = False
        for v in range(variants):
            text = _build_tweet(p["title"], p["url"], v)
            text_hash = _hash_text(text)
            try:
                r = client.create_tweet(text=text)  # v2 endpoint
                tid = (r.data or {}).get("id")
                print("TWEET_OK:", p["title"], p["url"], "->", f"https://x.com/i/web/status/{tid}")
                _mark_tweeted(p["id"], str(tid), text_hash)
                tweeted += 1
                success = True
                time.sleep(2)
                break
            except tweepy.Forbidden as e:
                msg = str(e).lower()
                if "duplicate" in msg:
                    print("Duplicate caught — retrying with new variant…")
                    continue
                else:
                    print("403 Forbidden:", e)
                    break  # لا فائدة من المزيد
            except Exception as e:
                print("TWEET_ERR:", p["title"], e)
                # جرّب صيغة أخرى
                continue

        if not success:
            print("FAILED_ALL_VARIANTS_FOR:", p["title"])

    if tweeted == 0:
        print("لا يوجد ما يُغرد الآن أو كل الأحدث مُغرَّد عنها مؤخرًا.")

# تشغيل يدوي
if __name__ == "__main__":
    tweet_new_posts(1)
