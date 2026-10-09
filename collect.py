"""Сборщик данных для дашборда Instagram (RU @magnatkav + US @vipmyway) через Apify.

Подписчики — каждый запуск (дёшево, ~$0.003).
Рилсы (15 последних) — раз в REELS_EVERY_DAYS дней или с --force (~$0.04 на аккаунт).

Запуск:
  python collect.py                      # обычный ежедневный запуск
  python collect.py --force --limit 50   # принудительно рилсы, 50 штук (первичная загрузка)
Ключ: переменная окружения APIFY_TOKEN или файл D:\\8. Cursor\\keys\\apify.txt
Результат: data.json рядом со скриптом (его читает index.html).
"""
import argparse
import json
import os
import sys
import io
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

HERE = Path(__file__).parent
DATA = HERE / "data.json"
THUMBS = HERE / "thumbs"
AVATARS = HERE / "avatars"
KEY_FILE = Path(r"D:\8. Cursor\keys\apify.txt")
MSK = timezone(timedelta(hours=3))
REELS_EVERY_DAYS = 3
ACCOUNTS = {
    "ru": {"username": "magnatkav", "title": "Денежная ходьба", "flag": "RU"},
    "us": {"username": "vipmyway", "title": "VipMyWay", "flag": "US"},
}


def token() -> str:
    t = os.environ.get("APIFY_TOKEN", "").strip()
    if not t and KEY_FILE.exists():
        t = KEY_FILE.read_text(encoding="utf-8").strip()
    if not t:
        sys.exit("Нет ключа Apify (APIFY_TOKEN или keys\\apify.txt)")
    return t


def run_actor(actor: str, payload: dict, timeout: int = 280) -> list:
    url = f"https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items?timeout={timeout}"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {token()}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout + 40) as r:
        return json.loads(r.read().decode("utf-8"))


def save_image(url: str, path: Path, width: int) -> bool:
    """Скачать картинку с CDN Instagram и ужать до width px (ссылки Instagram живут недолго)."""
    if not url:
        return False
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        raw = urllib.request.urlopen(req, timeout=30).read()
        path.parent.mkdir(exist_ok=True)
        try:
            from PIL import Image
            im = Image.open(io.BytesIO(raw)).convert("RGB")
            if im.width > width:
                im = im.resize((width, round(im.height * width / im.width)), Image.LANCZOS)
            im.save(path, "JPEG", quality=82, optimize=True)
        except ImportError:
            path.write_bytes(raw)
        return True
    except Exception as e:  # обложка не критична — дашборд покажет заглушку
        print(f"  картинка не скачалась {path.name}: {e}")
        return False


def load() -> dict:
    if DATA.exists():
        return json.loads(DATA.read_text(encoding="utf-8"))
    return {"accounts": {k: {**v, "followers": {}, "posts": None, "reels": {}, "reel_snaps": []}
                         for k, v in ACCOUNTS.items()}}


def save(d: dict) -> None:
    DATA.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")


def collect_profiles(d: dict, today: str) -> None:
    names = [a["username"] for a in ACCOUNTS.values()]
    items = run_actor("apify~instagram-profile-scraper", {"usernames": names})
    by_name = {(i.get("username") or "").lower(): i for i in items}
    for key, acc in d["accounts"].items():
        p = by_name.get(acc["username"].lower())
        if not p or p.get("followersCount") is None:
            print(f"[{key}] профиль не получен: {p.get('error') if p else 'нет в ответе'}")
            continue
        acc["followers"][today] = p["followersCount"]
        acc["posts"] = p.get("postsCount")
        acc["full_name"] = p.get("fullName")
        acc["bio"] = (p.get("biography") or "").strip()
        if save_image(p.get("profilePicUrlHD") or p.get("profilePicUrl"), AVATARS / f"{key}.jpg", 160):
            acc["avatar"] = f"avatars/{key}.jpg"
        print(f"[{key}] подписчиков {p['followersCount']}, публикаций {p.get('postsCount')}")


def reels_due(acc: dict, today: str) -> bool:
    if not acc["reel_snaps"]:
        return True
    last = datetime.fromisoformat(acc["reel_snaps"][-1]).date()
    return (datetime.fromisoformat(today).date() - last).days >= REELS_EVERY_DAYS


def apply_reels(acc: dict, items: list, today: str) -> int:
    n = 0
    for it in items:
        code = it.get("shortCode")
        plays = it.get("videoPlayCount") or it.get("videoViewCount")
        if not code or plays is None:
            continue
        r = acc["reels"].setdefault(code, {"plays": {}})
        r["ts"] = it.get("timestamp")
        r["caption"] = (it.get("caption") or "").strip().replace("\n", " ")[:220]
        r["likes"] = it.get("likesCount") or 0
        r["comments"] = it.get("commentsCount") or 0
        r["plays"][today] = plays
        thumb = THUMBS / f"{code}.jpg"
        if not thumb.exists():
            save_image(it.get("displayUrl"), thumb, 240)
        r["thumb"] = thumb.exists()
        n += 1
    if n and today not in acc["reel_snaps"]:
        acc["reel_snaps"].append(today)
    return n


def collect_reels(d: dict, today: str, force: bool, limit: int) -> None:
    due = [k for k, a in d["accounts"].items() if force or reels_due(a, today)]
    if not due:
        print("рилсы: ещё не пора (раз в", REELS_EVERY_DAYS, "дня)")
        return
    for key in due:
        acc = d["accounts"][key]
        items = run_actor("apify~instagram-reel-scraper", {"username": [acc["username"]], "resultsLimit": limit})
        print(f"[{key}] рилсов обновлено: {apply_reels(acc, items, today)}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="собрать рилсы сейчас")
    ap.add_argument("--limit", type=int, default=15, help="сколько последних рилсов брать")
    args = ap.parse_args()
    now = datetime.now(MSK)
    today = now.date().isoformat()
    d = load()
    for k, v in ACCOUNTS.items():  # новые поля/аккаунты без потери истории
        d["accounts"].setdefault(k, {**v, "followers": {}, "posts": None, "reels": {}, "reel_snaps": []})
    collect_profiles(d, today)
    collect_reels(d, today, args.force, args.limit)
    d["updated"] = now.strftime("%Y-%m-%d %H:%M")
    save(d)
    print("готово:", DATA)


if __name__ == "__main__":
    main()
