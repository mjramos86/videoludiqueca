#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Migrate old WordPress posts into Pages CMS articles (content/articles/<slug>.md)
so they can be edited in the CMS.

    python3 scripts/wp_to_cms.py all_posts.json featured.json [--only slug,...]

all_posts.json : list of posts from the WordPress REST API
                 (/wp/v2/posts?_fields=id,slug,date,title,excerpt,categories,
                 tags,featured_media,content)
featured.json  : {post_id: {"u": featured image URL, "alt": text}}

- Rendered WordPress HTML -> Markdown (headings, lists, quotes, bold/italic,
  links). Tables stay as HTML.
- YouTube / VideoPress embeds -> the bare URL on its own line (build.py turns it
  into a player; it survives the CMS rich-text editor). TikTok / X embeds ->
  a plain link.
- Images (inline, galleries, featured) are downloaded to
  assets/uploads/wp/<yyyy>/<mm>/<file> (wider than 1920 px are downscaled) and
  referenced from there; captions become an italic line under the image.
- Links to videoludique.ca become site-relative.
Requires: markdownify, beautifulsoup4, Pillow.
"""
import html
import json
import os
import re
import sys
import time
import urllib.request

from bs4 import BeautifulSoup, Comment
from markdownify import markdownify as md

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "_source")
OUT = os.path.join(ROOT, "content", "articles")
UP = os.path.join(ROOT, "assets", "uploads", "wp")
UA = "Mozilla/5.0 (compatible; videoludique-migration/2.0)"

CATS = {c["id"]: c for c in json.load(open(os.path.join(SRC, "categories.json"), encoding="utf-8"))}
TAGS = {t["id"]: t for t in json.load(open(os.path.join(SRC, "tags.json"), encoding="utf-8"))}
CMS_CATS = {"apercu", "actualite", "chronique", "critique", "non-quebec", "concours"}

UPLOAD_RE = re.compile(
    r"^(?:https?:)?//(?:i[0-2]\.wp\.com/)?(?:videoludique\.ca|mariojramos8\.wordpress\.com|"
    r"mariojramos8\.files\.wordpress\.com|atomic-temporary-221340425\.wpcomstaging\.com)"
    r"(?:/wp-content/uploads)?/(\d{4})/(\d{2})/([^?#]+)", re.I)
ORIGIN = "https://mariojramos8.wordpress.com/wp-content/uploads"
FAILED = []


def fetch_image(url):
    """Download an upload once; return its site path, or None."""
    m = UPLOAD_RE.match(url.strip())
    if not m:
        return None
    y, mo, f = m.group(1), m.group(2), m.group(3)
    f = os.path.basename(urllib.request.unquote(f))
    local_rel = f"/assets/uploads/wp/{y}/{mo}/{f}"
    dest = os.path.join(UP, y, mo, f)
    if os.path.exists(dest):
        return local_rel
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    src = f"{ORIGIN}/{y}/{mo}/{urllib.request.quote(f)}"
    for attempt in range(3):
        try:
            req = urllib.request.Request(src, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=60) as r:
                data = r.read()
                ctype = r.headers.get("Content-Type", "")
            if not ctype.startswith("image/"):
                raise ValueError(ctype)
            with open(dest, "wb") as fh:
                fh.write(data)
            shrink(dest)
            return local_rel
        except Exception as e:  # noqa: BLE001
            err = e
            time.sleep(1.5)
    FAILED.append(f"{src} ({err})")
    return None


def shrink(path, maxw=1920):
    try:
        from PIL import Image
        im = Image.open(path)
        if im.width <= maxw or getattr(im, "is_animated", False):
            return
        h = round(im.height * maxw / im.width)
        im = im.resize((maxw, h), Image.LANCZOS)
        fmt = (im.format or Image.open(path).format or "").upper()
        kw = {"quality": 86, "optimize": True} if fmt in ("JPEG", "JPG", "WEBP") else {"optimize": True}
        if fmt in ("JPEG", "JPG") and im.mode not in ("RGB", "L"):
            im = im.convert("RGB")
        im.save(path, **kw)
    except Exception:  # noqa: BLE001
        pass


def video_url(fig):
    ifr = fig.find("iframe")
    src = ifr.get("src", "") if ifr else ""
    yt = re.search(r"youtube(?:-nocookie)?\.com/embed/([\w-]+)", src)
    if yt:
        return f"https://www.youtube.com/watch?v={yt.group(1)}"
    vp = re.search(r"(?:video\.wordpress\.com|videopress\.com)/(?:embed|v)/([\w-]+)", src)
    if vp:
        return f"https://videopress.com/v/{vp.group(1)}"
    vm = re.search(r"player\.vimeo\.com/video/(\d+)", src)
    if vm:
        return f"https://vimeo.com/{vm.group(1)}"
    wrap = fig.find(class_="wp-block-embed__wrapper")
    txt = wrap.get_text(" ", strip=True) if wrap else ""
    m = re.search(r"https?://\S+", txt)
    return m.group(0) if m else ""


def convert_html(raw):
    soup = BeautifulSoup(raw, "html.parser")
    for c in soup.find_all(string=lambda t: isinstance(t, Comment)):
        c.extract()
    for t in soup.find_all(["script", "style", "noscript"]):
        t.decompose()
    for t in soup.select(".wp-block-jetpack-subscriptions, .sharedaddy, .jp-relatedposts"):
        t.decompose()

    # embeds
    for fig in soup.select("figure.wp-block-embed"):
        cls = " ".join(fig.get("class", []))
        social = any(k in cls for k in ("tiktok", "twitter", "instagram", "facebook"))
        if not social and ("is-type-video" in cls or fig.find("iframe")):
            url = video_url(fig)
            new = soup.new_tag("p")
            new.string = url
            fig.replace_with(new if url else "")
            continue
        bq = fig.find("blockquote")
        url = (bq.get("cite") if bq and bq.get("cite") else "")
        if not url:
            a = fig.find_all("a")
            url = a[-1].get("href", "") if a else ""
        label = ("TikTok" if "tiktok" in cls else "X (Twitter)" if "twitter" in cls
                 else "Instagram" if "instagram" in cls else "Facebook" if "facebook" in cls else "la publication")
        new = BeautifulSoup(f'<p>Voir sur {label} : <a href="{html.escape(url)}">{html.escape(url)}</a></p>', "html.parser")
        fig.replace_with(new)

    # images (single, galleries)
    for fig in soup.select("figure.wp-block-image, figure.wp-block-gallery"):
        parts = []
        for img in fig.find_all("img"):
            src = img.get("data-orig-file") or img.get("src", "")
            local = fetch_image(src) or fetch_image(img.get("src", ""))
            if not local:
                continue
            alt = (img.get("alt") or "").strip()
            parts.append(f'<p><img src="{local}" alt="{html.escape(alt)}"></p>')
            cap = img.find_parent("figure").find("figcaption") if img.find_parent("figure") else None
            if cap and cap.get_text(strip=True):
                parts.append(f"<p><em>{cap.decode_contents().strip()}</em></p>")
                cap.decompose()
        gcap = fig.find("figcaption", recursive=False)
        if gcap and gcap.get_text(strip=True):
            parts.append(f"<p><em>{gcap.decode_contents().strip()}</em></p>")
        fig.replace_with(BeautifulSoup("".join(parts), "html.parser"))
    for img in soup.find_all("img"):  # stray inline images
        local = fetch_image(img.get("data-orig-file") or img.get("src", ""))
        if local:
            img.attrs = {"src": local, "alt": img.get("alt", "")}

    # tables stay as one-line HTML
    tables = []
    for i, tb in enumerate(soup.find_all("table")):
        for t in tb.find_all(True):
            t.attrs = {k: v for k, v in t.attrs.items() if k in ("colspan", "rowspan")}
        tables.append(re.sub(r"\s*\n\s*", " ", str(tb)))
        holder = tb.find_parent("figure") or tb
        holder.replace_with(f"@@TABLE{i}@@")

    for t in soup.find_all(True):
        t.attrs = {k: v for k, v in t.attrs.items() if k in ("href", "src", "alt", "cite")}
    for p in soup.find_all("p"):
        if not p.get_text(strip=True) and not p.find("img"):
            p.decompose()

    text = md(str(soup), heading_style="ATX", bullets="-", strong_em_symbol="*",
              escape_underscores=False, escape_asterisks=False)
    text = text.replace(" ", " ")
    text = re.sub(r"https?://(?:www\.)?videoludique\.ca(/[^\s)\"']*)", r"\1", text)
    text = re.sub(r"^<(https?://\S+)>$", r"\1", text, flags=re.M)
    text = re.sub(r"<(https?://[^>\s]+)>", r"[\1](\1)", text)
    for i, tb in enumerate(tables):
        text = text.replace(f"@@TABLE{i}@@", "\n" + tb + "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"
    return text


def q(v):
    return json.dumps(v, ensure_ascii=False)


def convert(post, featured):
    title = html.unescape(post["title"]["rendered"] if isinstance(post["title"], dict) else post["title"])
    ex = post.get("excerpt") or ""
    ex = ex.get("rendered", "") if isinstance(ex, dict) else ex
    ex = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", ex)).split())
    ex = re.sub(r"\s*(?:\[…\]|\[\.\.\.\]|…)\s*$", "…", ex)
    body = post["content"]["rendered"] if isinstance(post["content"], dict) else post["content"]
    cat = next((CATS[c]["slug"] for c in post.get("categories", [])
                if c in CATS and CATS[c]["slug"] in CMS_CATS), "")
    tags = [html.unescape(TAGS[t]["name"]) for t in post.get("tags", []) if t in TAGS]
    image = fetch_image(featured["u"]) if featured else None
    text = convert_html(body)
    fm = ["---", f"title: {q(title)}", f"slug: {post['slug']}",
          f"date: {post['date'][:19]}", "author: mario-j-ramos"]
    if cat:
        fm.append(f"category: {cat}")
    if tags:
        fm += ["tags:"] + [f"  - {q(t)}" for t in tags]
    if image:
        fm.append(f"image: {image}")
    if ex:
        fm.append(f"excerpt: {q(ex)}")
    fm += ["draft: false", "---", ""]
    path = os.path.join(OUT, f"{post['slug']}.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(fm) + text)
    return path


if __name__ == "__main__":
    posts = json.load(open(sys.argv[1], encoding="utf-8"))
    feat = {int(k): v for k, v in json.load(open(sys.argv[2], encoding="utf-8")).items()}
    only = None
    if "--only" in sys.argv:
        only = set(sys.argv[sys.argv.index("--only") + 1].split(","))
    skip = set()
    if "--skip" in sys.argv:
        skip = set(sys.argv[sys.argv.index("--skip") + 1].split(","))
    n = 0
    for p in posts:
        if (only and p["slug"] not in only) or p["slug"] in skip:
            continue
        convert(p, feat.get(p["id"]))
        n += 1
        print(f"[{n}] {p['slug']}", flush=True)
    if FAILED:
        print("IMAGES FAILED:", len(FAILED))
        for f in FAILED:
            print("  ", f)
