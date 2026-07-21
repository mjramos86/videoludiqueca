#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Fetch ALL content from videoludique.ca via the public WordPress REST API and
write it into _source/ in the exact shape build.py expects. Run this where the
network is open (your laptop, or the included GitHub Action) — the generation
container used to bootstrap this repo had no outbound network, so only a subset
of post bodies were pre-fetched.

    python3 fetch_content.py      # populate _source/ from the live site
    python3 build.py              # generate the static site
    bash scripts/download_media.sh  # download images into wp-content/uploads/

Only stdlib is used, so no pip install is required.
"""
import json
import os
import time
import urllib.request
import urllib.error

ROOT = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(ROOT, "_source")
UA = "Mozilla/5.0 (compatible; videoludique-migration/1.0)"

# The WordPress.com public API proxy avoids the origin's bot filtering.
# Falls back to the site's own /wp-json endpoint if needed.
API_BASES = [
    "https://public-api.wordpress.com/wp/v2/sites/videoludique.ca",
    "https://videoludique.ca/wp-json/wp/v2",
]

def _get(path, params):
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    last_err = None
    for base in API_BASES:
        url = f"{base}/{path}?{qs}"
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                total_pages = int(r.headers.get("X-WP-TotalPages", "1") or "1")
                return json.loads(r.read().decode("utf-8")), total_pages
        except urllib.error.HTTPError as e:
            if e.code == 400:  # page out of range
                return [], 0
            last_err = e
        except Exception as e:  # noqa
            last_err = e
    raise SystemExit(f"Failed to fetch {path}: {last_err}")

def paged(path, params):
    out, page = [], 1
    while True:
        params = dict(params, per_page=100, page=page)
        data, total = _get(path, params)
        if not data:
            break
        out.extend(data)
        if page >= total:
            break
        page += 1
        time.sleep(0.2)
    return out

def rendered(v):
    if isinstance(v, dict):
        return v.get("rendered", "")
    return v or ""

def main():
    os.makedirs(os.path.join(SRC, "posts"), exist_ok=True)
    os.makedirs(os.path.join(SRC, "pages"), exist_ok=True)

    print("Fetching categories…")
    cats = paged("categories", {"hide_empty": "false",
                                "_fields": "id,name,slug,description,count"})
    json.dump(cats, open(os.path.join(SRC, "categories.json"), "w"),
              ensure_ascii=False)

    print("Fetching tags…")
    tags = paged("tags", {"hide_empty": "false",
                          "_fields": "id,name,slug,count"})
    json.dump(tags, open(os.path.join(SRC, "tags.json"), "w"), ensure_ascii=False)
    print(f"  {len(cats)} categories, {len(tags)} tags")

    print("Fetching media…")
    media_raw = paged("media", {"media_type": "image",
                                "_fields": "id,source_url,alt_text,media_details"})
    media = [{"id": m["id"], "source_url": m.get("source_url", ""),
              "alt": m.get("alt_text", ""),
              "w": (m.get("media_details") or {}).get("width", 0) or 0,
              "h": (m.get("media_details") or {}).get("height", 0) or 0}
             for m in media_raw if m.get("source_url")]
    json.dump(media, open(os.path.join(SRC, "media_all.json"), "w"),
              ensure_ascii=False)
    print(f"  {len(media)} media items")

    print("Fetching posts (this is the big one)…")
    posts = paged("posts", {"status": "publish", "_fields":
        "id,slug,date,link,title,excerpt,content,categories,tags,featured_media"})
    meta = []
    for p in posts:
        pid = p["id"]
        with open(os.path.join(SRC, "posts", f"{pid}.html"), "w",
                  encoding="utf-8") as f:
            f.write(rendered(p.get("content")))
        meta.append({
            "id": pid, "slug": p["slug"], "date": p["date"], "link": p["link"],
            "title": rendered(p.get("title")),
            "excerpt": rendered(p.get("excerpt")),
            "categories": p.get("categories", []), "tags": p.get("tags", []),
            "featured_media": p.get("featured_media", 0),
        })
    json.dump({"posts": meta}, open(os.path.join(SRC, "posts_meta.json"), "w"),
              ensure_ascii=False)
    print(f"  {len(posts)} posts written")

    print("Fetching pages…")
    pages = paged("pages", {"status": "publish",
                            "_fields": "id,slug,date,link,title,content"})
    for pg in pages:
        with open(os.path.join(SRC, "pages", f"{pg['slug']}.html"), "w",
                  encoding="utf-8") as f:
            f.write(rendered(pg.get("content")))
    print(f"  {len(pages)} pages written")
    print("Done. Now run: python3 build.py")

if __name__ == "__main__":
    main()
