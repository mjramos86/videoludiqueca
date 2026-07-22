#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Static-site generator for videoludique.ca (WordPress -> GitHub Pages).

Reads content dumped from the WordPress REST/MCP API in _source/ and emits a
fully static site at the repository root, preserving the original WordPress
URL structure:

    /                              home (paginated: /page/2/ ...)
    /YYYY/MM/DD/slug/              single post
    /a-propos/                     single page
    /category/<slug>/              category archive (paginated)
    /tag/<slug>/                   tag archive (paginated)
    /YYYY/  /YYYY/MM/              date archives
    /feed/index.xml                RSS feed
    /sitemap.xml  /robots.txt      SEO
    /404.html                      not found

All internal links and media are rewritten to root-absolute paths
(e.g. /2026/06/06/slug/ and /wp-content/uploads/...), so the site is fully
portable to the videoludique.ca custom domain on GitHub Pages.
"""
import html as htmllib
import json
import os
import re
import shutil
import unicodedata
from datetime import datetime

import cms  # dependency-free front-matter + Markdown loader for Pages CMS content

ROOT = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(ROOT, "_source")
CONTENT = os.path.join(ROOT, "content")  # articles/authors authored via Pages CMS
OUT = ROOT  # emit at repo root (GitHub Pages serves from here)

# Byline used for the ~181 posts imported from WordPress (a single-author blog).
# New CMS articles carry their own `author` (a slug into content/authors/).
DEFAULT_AUTHOR_SLUG = "mario-j-ramos"

SITE_TITLE = "VIDÉOLUDIQUE.CA"
SITE_TAGLINE = "Un blogue sur l’industrie du jeu vidéo québécoise par Mario J. Ramos"
SITE_URL = "https://videoludique.ca"
SITE_DESC = ("Vidéoludique.ca est un blogue indépendant qui couvre l’industrie "
             "du jeu vidéo au Québec, 5e pôle mondial.")
AUTHOR = "Mario J. Ramos"
PER_PAGE = 12

MONTHS_FR = ["", "janvier", "février", "mars", "avril", "mai", "juin", "juillet",
             "août", "septembre", "octobre", "novembre", "décembre"]

# collected during build: set of "/wp-content/uploads/..." paths to fetch
MEDIA_PATHS = set()

# ---------------------------------------------------------------------------
# data loading
# ---------------------------------------------------------------------------
def load(name):
    with open(os.path.join(SRC, name), encoding="utf-8") as f:
        return json.load(f)

posts_meta = load("posts_meta.json")["posts"]
tags = load("tags.json")
cats = load("categories.json")
media = load("media_all.json")

MEDIA = {m["id"]: m for m in media}
TAG = {t["id"]: t for t in tags}
CAT = {c["id"]: c for c in cats}

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def strip_tags(s):
    return re.sub(r"<[^>]+>", "", s or "").strip()

def decode(s):
    """HTML entities -> unicode text (for <title>, meta, alt)."""
    return htmllib.unescape(strip_tags(s)) if s else ""

def attr(s):
    """Escape for use inside a double-quoted HTML attribute."""
    return (s or "").replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")

def fr_date(iso):
    d = datetime.fromisoformat(iso)
    return f"{d.day} {MONTHS_FR[d.month]} {d.year}"

def ymd(iso):
    d = datetime.fromisoformat(iso)
    return f"{d.year:04d}", f"{d.month:02d}", f"{d.day:02d}"

def post_path(p):
    y, m, d = ymd(p["date"])
    return f"/{y}/{m}/{d}/{p['slug']}/"

DOMAIN_RE = re.compile(r"https?://(?:i[0-2]\.wp\.com/)?videoludique\.ca")
SRCSET_RE = re.compile(r'\s+(?:srcset|sizes)="[^"]*"')
UPLOAD_QS_RE = re.compile(r'(/wp-content/uploads/[^"\')\s]+?\.(?:jpe?g|png|gif|webp|avif|svg))(?:\?[^"\')\s]*)?', re.I)
IMG_SRC_RE = re.compile(r'(/wp-content/uploads/[^"\')\s]+?\.(?:jpe?g|png|gif|webp|avif|svg))', re.I)
WP_COMMENT_RE = re.compile(r'<!--\s*/?wp:.*?-->', re.S)
EMBED_WRAP_RE = re.compile(
    r'<div class="wp-block-embed__wrapper">\s*(https?://[^\s<]+)\s*</div>', re.S)

def _embed_iframe(m):
    url = m.group(1).strip()
    yt = re.search(r'(?:youtu\.be/|youtube\.com/(?:watch\?v=|embed/|shorts/|v/))([\w-]+)', url)
    if yt:
        return (f'<div class="wp-block-embed__wrapper"><iframe loading="lazy" '
                f'src="https://www.youtube.com/embed/{yt.group(1)}" title="YouTube" '
                f'frameborder="0" allow="accelerometer; autoplay; clipboard-write; '
                f'encrypted-media; gyroscope; picture-in-picture; web-share" '
                f'allowfullscreen></iframe></div>')
    vp = re.search(r'videopress\.com/v/([\w-]+)', url)
    if vp:
        return (f'<div class="wp-block-embed__wrapper"><iframe loading="lazy" '
                f'src="https://videopress.com/embed/{vp.group(1)}" frameborder="0" '
                f'allowfullscreen allow="clipboard-write"></iframe></div>')
    vm = re.search(r'vimeo\.com/(\d+)', url)
    if vm:
        return (f'<div class="wp-block-embed__wrapper"><iframe loading="lazy" '
                f'src="https://player.vimeo.com/video/{vm.group(1)}" frameborder="0" '
                f'allowfullscreen></iframe></div>')
    return f'<div class="wp-block-embed__wrapper"><a href="{url}" rel="noopener" target="_blank">{url}</a></div>'

def rewrite_html(content):
    """Make a post/page body portable: strip domain, localize media, collect paths.

    Handles both rendered WP HTML and raw Gutenberg block markup (the latter has
    <!-- wp:* --> comments and bare-URL embed wrappers)."""
    if not content:
        return ""
    s = content
    # 0. convert bare-URL embeds (raw markup) to iframes, then drop wp block comments
    s = EMBED_WRAP_RE.sub(_embed_iframe, s)
    s = WP_COMMENT_RE.sub("", s)
    # 1. domain -> root-absolute
    s = DOMAIN_RE.sub("", s)
    s = s.replace('href="//videoludique.ca', 'href="').replace('src="//videoludique.ca', 'src="')
    # 2. drop responsive srcset/sizes so the single (localized) src is used
    s = SRCSET_RE.sub("", s)
    # 3. strip query strings on upload URLs (Photon ?resize=, ?w=, etc.)
    s = UPLOAD_QS_RE.sub(r"\1", s)
    # 4. collect upload paths for the media manifest
    for m in IMG_SRC_RE.findall(s):
        MEDIA_PATHS.add(m)
    # 5. drop Jetpack/Photon bloat attributes for lean output
    s = re.sub(r'\s+data-[\w-]+="[^"]*"', "", s)
    s = re.sub(r'\s+(?:decoding|loading|fetchpriority)="[^"]*"', "", s)
    # 6. tidy repeated blank lines left by comment removal
    s = re.sub(r'\n{3,}', '\n\n', s)
    return s

def media_url(mid):
    m = MEDIA.get(mid)
    if not m:
        return None
    url = DOMAIN_RE.sub("", m["source_url"])
    url = re.sub(r"\?.*$", "", url)
    if url.startswith("/wp-content/uploads/"):
        MEDIA_PATHS.add(url)
    return url

def write(path_rel, content):
    """path_rel is a URL path like /2026/06/06/slug/ -> writes index.html."""
    if path_rel.endswith("/"):
        fp = os.path.join(OUT, path_rel.strip("/"), "index.html")
    else:
        fp = os.path.join(OUT, path_rel.strip("/"))
    os.makedirs(os.path.dirname(fp), exist_ok=True)
    with open(fp, "w", encoding="utf-8") as f:
        f.write(content)

# ---------------------------------------------------------------------------
# navigation / chrome
# ---------------------------------------------------------------------------
NAV_CATS = [1519500, 26167, 64088, 1080]  # Aperçu, Actualité, Chronique, Critique

def nav_html():
    items = []
    for cid in NAV_CATS:
        c = CAT.get(cid)
        if c:
            items.append(f'<a href="/category/{c["slug"]}/">{decode(c["name"])}</a>')
    items.append('<a href="/a-propos/">À propos</a>')
    return "\n".join(items)

SOCIAL = [
    ("Facebook", "https://www.facebook.com/mjramos/"),
    ("YouTube", "https://www.youtube.com/channel/UCFnn-dqTDyxCleyWcXxkS9w"),
    ("Instagram", "https://www.instagram.com/mariojorge.ramos/"),
    ("TikTok", "https://www.tiktok.com/@marioj.ramos"),
    ("Threads", "https://www.threads.net/@mariojorge.ramos"),
]

def page_shell(title, body, description=None, canonical=None, og_image=None,
               og_type="website", extra_head=""):
    desc = description or SITE_DESC
    can = canonical or "/"
    title_full = title if title == SITE_TITLE else f"{title} – {SITE_TITLE}"
    og_img_tag = ""
    if og_image:
        og_img_tag = f'<meta property="og:image" content="{SITE_URL}{attr(og_image)}">'
    social_links = "\n".join(
        f'<a href="{u}" rel="noopener" target="_blank">{n}</a>' for n, u in SOCIAL)
    return f"""<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{attr(decode(title_full))}</title>
<meta name="description" content="{attr(desc)}">
<link rel="canonical" href="{SITE_URL}{attr(can)}">
<meta property="og:site_name" content="{SITE_TITLE}">
<meta property="og:title" content="{attr(decode(title))}">
<meta property="og:description" content="{attr(desc)}">
<meta property="og:type" content="{og_type}">
<meta property="og:url" content="{SITE_URL}{attr(can)}">
<meta property="og:locale" content="fr_CA">
{og_img_tag}
<meta name="twitter:card" content="summary_large_image">
<link rel="alternate" type="application/rss+xml" title="{SITE_TITLE}" href="/feed/index.xml">
<link rel="icon" href="/favicon.ico" sizes="any">
<link rel="icon" type="image/png" href="/wp-content/uploads/2023/07/cropped-cropped-logo-1.png">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Libre+Franklin:ital,wght@0,400;0,600;0,700;0,800;1,400;1,600&display=swap" rel="stylesheet">
<link rel="stylesheet" href="/assets/style.css">
{extra_head}
</head>
<body>
<a class="skip" href="#main">Aller au contenu</a>
<header class="site-header">
  <div class="wrap header-inner">
    <a class="brand" href="/">
      <span class="brand-name">VIDÉO<span class="brand-accent">LUDIQUE</span>.CA</span>
      <span class="brand-tag">{SITE_TAGLINE}</span>
    </a>
    <button class="nav-toggle" aria-label="Menu" aria-expanded="false">☰</button>
    <nav class="site-nav">{nav_html()}</nav>
  </div>
</header>
<main id="main">
{body}
</main>
<footer class="site-footer">
  <div class="wrap footer-inner">
    <div class="footer-brand">
      <strong>VIDÉOLUDIQUE.CA</strong>
      <p>{SITE_DESC}</p>
      <p class="footer-contact">Contact : <a href="mailto:info@mariojramos.com">info@mariojramos.com</a></p>
    </div>
    <div class="footer-social">
      <span>Suivez-nous</span>
      <div class="social-links">{social_links}</div>
    </div>
  </div>
  <div class="wrap footer-legal">
    © {datetime.now().year} Vidéoludique.ca — {AUTHOR}. Tous droits réservés.
  </div>
</footer>
<script>
document.querySelector('.nav-toggle').addEventListener('click', function(){{
  var n=document.querySelector('.site-nav'); var o=this.getAttribute('aria-expanded')==='true';
  this.setAttribute('aria-expanded', String(!o)); n.classList.toggle('open');
}});
</script>
</body>
</html>"""

# ---------------------------------------------------------------------------
# cards & pagination
# ---------------------------------------------------------------------------
def card(p):
    href = post_path(p)
    fimg = p["_featured"]
    thumb = (f'<a class="card-media" href="{href}">'
             f'<img src="{fimg}" alt="{p["_falt"]}" loading="lazy" width="{p["_fw"] or 800}" height="{p["_fh"] or 450}"></a>'
             ) if fimg else ""
    cat = ""
    if p["_cats"]:
        c = CAT.get(p["_cats"][0])
        if c:
            cat = f'<a class="card-cat" href="/category/{c["slug"]}/">{decode(c["name"])}</a>'
    excerpt = rewrite_html(p.get("excerpt", ""))
    return f"""<article class="card">
  {thumb}
  <div class="card-body">
    {cat}
    <h2 class="card-title"><a href="{href}">{p['title']}</a></h2>
    <div class="card-excerpt">{excerpt}</div>
    <time class="card-date" datetime="{p['date']}">{fr_date(p['date'])}</time>
  </div>
</article>"""

def paginate(items, base, render_intro, per_page=PER_PAGE):
    """base like '/' or '/category/foo/'. Emits base, base/page/2/, ..."""
    total = max(1, (len(items) + per_page - 1) // per_page)
    for pg in range(1, total + 1):
        chunk = items[(pg - 1) * per_page: pg * per_page]
        cards = "\n".join(card(p) for p in chunk)
        nav = pagination_nav(base, pg, total)
        intro = render_intro(pg) if pg == 1 else render_intro(pg)
        body = f'{intro}<div class="card-grid">{cards}</div>{nav}'
        path = base if pg == 1 else f"{base}page/{pg}/"
        yield path, body, pg, total

def pagination_nav(base, pg, total):
    if total <= 1:
        return ""
    def url(n):
        return base if n == 1 else f"{base}page/{n}/"
    parts = ['<nav class="pagination">']
    if pg > 1:
        parts.append(f'<a class="pg prev" href="{url(pg-1)}">← Précédent</a>')
    parts.append(f'<span class="pg-info">Page {pg} / {total}</span>')
    if pg < total:
        parts.append(f'<a class="pg next" href="{url(pg+1)}">Suivant →</a>')
    parts.append("</nav>")
    return "".join(parts)

# ---------------------------------------------------------------------------
# authors + CMS content (Pages CMS -> content/) merged with the WordPress import
#
# WordPress posts key their categories/tags by numeric id; CMS articles pick a
# category slug and free-text tags. To let one set of builders serve both, every
# post gets normalized `_cats` / `_tags` (numeric ids into CAT/TAG) plus a
# resolved `_author`, `_featured` image and `_body` HTML.
# ---------------------------------------------------------------------------
def slugify(s):
    s = decode(s).lower().replace("æ", "ae").replace("œ", "oe")
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "n-a"

def norm_date(v):
    v = (str(v) if v is not None else "").strip().replace("Z", "")
    if " " in v and "T" not in v:
        v = v.replace(" ", "T", 1)
    try:
        datetime.fromisoformat(v)
        return v
    except ValueError:
        return datetime.now().isoformat(timespec="seconds")

# author profiles ------------------------------------------------------------
AUTHORS = {}
for _fslug, _meta, _bio in cms.discover(os.path.join(CONTENT, "authors")):
    _slug = (_meta.get("slug") or _fslug).strip()
    AUTHORS[_slug] = {
        "slug": _slug,
        "name": _meta.get("name") or _slug,
        "role": _meta.get("role") or "",
        "email": _meta.get("email") or "",
        "website": _meta.get("website") or "",
        "photo": (_meta.get("photo") or "").strip(),
        "social": [s for s in (_meta.get("social") or [])
                   if isinstance(s, dict) and s.get("url")],
        "bio": cms.md_to_html(_bio),
    }
# The primary byline must always resolve, even before its profile is authored.
AUTHORS.setdefault(DEFAULT_AUTHOR_SLUG, {
    "slug": DEFAULT_AUTHOR_SLUG, "name": AUTHOR, "role": "", "social": [],
    "email": "info@mariojramos.com", "website": "https://mariojramos.com",
    "photo": "/assets/mario-j-ramos.jpg", "bio": "",
})

# taxonomy lookups + on-the-fly creation for CMS-only categories/tags ---------
CAT_BY_SLUG = {c["slug"]: c["id"] for c in cats}
TAG_BY_SLUG = {t["slug"]: t["id"] for t in tags}
_synth_id = [-1]  # negative ids never collide with WordPress' positive ones

def ensure_cat(label):
    slug = slugify(label)
    if slug in CAT_BY_SLUG:
        return CAT_BY_SLUG[slug]
    cid = _synth_id[0]; _synth_id[0] -= 1
    c = {"id": cid, "name": label, "slug": slug, "description": "", "count": 0}
    cats.append(c); CAT[cid] = c; CAT_BY_SLUG[slug] = cid
    return cid

def ensure_tag(label):
    slug = slugify(label)
    if slug in TAG_BY_SLUG:
        return TAG_BY_SLUG[slug]
    tid = _synth_id[0]; _synth_id[0] -= 1
    t = {"id": tid, "name": label, "slug": slug, "count": 0}
    tags.append(t); TAG[tid] = t; TAG_BY_SLUG[slug] = tid
    return tid

def _excerpt_from(html_body):
    words = strip_tags(html_body).split()
    text = " ".join(words[:55])
    if not text:
        return ""
    return f"<p>{htmllib.escape(text)}{'…' if len(words) > 55 else ''}</p>"

def _norm_imported(p):
    m = MEDIA.get(p.get("featured_media", 0)) or {}
    p["_cms"] = False
    p["_draft"] = False
    p["_author"] = DEFAULT_AUTHOR_SLUG
    p["_cats"] = [c for c in p.get("categories", []) if c in CAT]
    p["_tags"] = [t for t in p.get("tags", []) if t in TAG]
    p["_featured"] = media_url(p.get("featured_media", 0))
    p["_falt"] = attr(decode(m.get("alt") or p["title"]))
    p["_fw"], p["_fh"] = m.get("w", 0) or 0, m.get("h", 0) or 0
    p["_body"] = None  # loaded lazily from _source/posts/<id>.html
    return p

def _load_cms_articles():
    out = []
    for fslug, meta, body in cms.discover(os.path.join(CONTENT, "articles")):
        if not meta.get("title"):
            continue
        slug = (meta.get("slug") or fslug).strip()
        title = htmllib.escape(str(meta.get("title")), quote=False)
        html_body = rewrite_html(cms.md_to_html(body))
        cat_ids = [ensure_cat(meta["category"])] if meta.get("category") else []
        tag_ids = [ensure_tag(t) for t in (meta.get("tags") or []) if str(t).strip()]
        excerpt = (meta.get("excerpt") or "").strip()
        excerpt_html = (f"<p>{htmllib.escape(excerpt)}</p>" if excerpt
                        else _excerpt_from(html_body))
        author = (meta.get("author") or DEFAULT_AUTHOR_SLUG).strip()
        img = (meta.get("image") or "").strip()
        out.append({
            "id": f"cms-{slug}", "slug": slug, "date": norm_date(meta.get("date")),
            "title": title, "excerpt": excerpt_html,
            "categories": cat_ids, "tags": tag_ids, "featured_media": 0,
            "_cms": True, "_draft": bool(meta.get("draft")),
            "_author": author if author in AUTHORS else DEFAULT_AUTHOR_SLUG,
            "_cats": cat_ids, "_tags": tag_ids,
            "_featured": img or None, "_falt": attr(decode(title)),
            "_fw": 1200, "_fh": 675, "_body": html_body,
        })
    return out

# ---------------------------------------------------------------------------
# build: normalize, merge, drop drafts, sort newest-first, load bodies
# ---------------------------------------------------------------------------
_all = [_norm_imported(dict(p)) for p in posts_meta] + _load_cms_articles()
posts = sorted((p for p in _all if not p["_draft"]),
               key=lambda p: p["date"], reverse=True)

def body_fragment(pid):
    fp = os.path.join(SRC, "posts", f"{pid}.html")
    if os.path.exists(fp):
        with open(fp, encoding="utf-8") as f:
            return f.read()
    return None

missing = [p["id"] for p in posts if not p["_cms"] and body_fragment(p["id"]) is None]

# ---------------------------------------------------------------------------
# single posts
# ---------------------------------------------------------------------------
def render_post(p, idx):
    href = post_path(p)
    if p["_cms"]:
        content = p["_body"]
    else:
        raw = body_fragment(p["id"])
        if raw is None:
            return  # body not fetched yet
        content = rewrite_html(raw)
    fimg = p["_featured"]
    hero = ""
    if fimg:
        hero = (f'<figure class="post-hero"><img src="{fimg}" '
                f'alt="{p["_falt"]}" '
                f'width="{p["_fw"] or 1200}" height="{p["_fh"] or 675}"></figure>')
    # category + tags
    catlinks = " ".join(
        f'<a class="chip cat" href="/category/{CAT[c]["slug"]}/">{decode(CAT[c]["name"])}</a>'
        for c in p["_cats"] if c in CAT)
    taglinks = " ".join(
        f'<a class="chip" href="/tag/{TAG[t]["slug"]}/">{decode(TAG[t]["name"])}</a>'
        for t in p["_tags"] if t in TAG)
    tags_block = f'<div class="post-tags"><span class="tags-label">Étiquettes :</span> {taglinks}</div>' if taglinks else ""
    # prev (older) / next (newer)
    nav = []
    if idx + 1 < len(posts):
        older = posts[idx + 1]
        nav.append(f'<a class="pn prev" href="{post_path(older)}"><span>← Article précédent</span><strong>{older["title"]}</strong></a>')
    if idx > 0:
        newer = posts[idx - 1]
        nav.append(f'<a class="pn next" href="{post_path(newer)}"><span>Article suivant →</span><strong>{newer["title"]}</strong></a>')
    postnav = f'<nav class="post-nav">{"".join(nav)}</nav>' if nav else ""
    desc = decode(p.get("excerpt", "")) or SITE_DESC
    y, mo, d = ymd(p["date"])
    author = AUTHORS.get(p["_author"]) or AUTHORS[DEFAULT_AUTHOR_SLUG]
    byline = (f'Par <a class="author-link" href="/auteur/{author["slug"]}/" '
              f'rel="author">{decode(author["name"])}</a>')
    body = f"""<article class="post">
  <div class="wrap">
    <div class="post-head">
      <div class="post-crumbs">{catlinks}</div>
      <h1 class="post-title">{p['title']}</h1>
      <div class="post-meta">
        <span class="by">{byline}</span>
        <time datetime="{p['date']}"><a href="/{y}/{mo}/{d}/">{fr_date(p['date'])}</a></time>
      </div>
    </div>
    {hero}
    <div class="post-content">{content}</div>
    {tags_block}
    {postnav}
  </div>
</article>"""
    schema = json.dumps({
        "@context": "https://schema.org", "@type": "NewsArticle",
        "headline": decode(p["title"]), "datePublished": p["date"],
        "author": {"@type": "Person", "name": decode(author["name"]),
                   "url": f'{SITE_URL}/auteur/{author["slug"]}/'},
        "publisher": {"@type": "Organization", "name": SITE_TITLE},
        "mainEntityOfPage": SITE_URL + href,
        **({"image": SITE_URL + fimg} if fimg else {}),
    }, ensure_ascii=False)
    head = f'<script type="application/ld+json">{schema}</script>'
    write(href, page_shell(decode(p["title"]), body, description=desc,
                           canonical=href, og_image=fimg, og_type="article",
                           extra_head=head))

# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------
def build_home():
    def intro(pg):
        if pg == 1:
            return ('<section class="hero-band"><div class="wrap">'
                    '<h1>L’actualité du jeu vidéo québécois</h1>'
                    f'<p>{SITE_TAGLINE}</p></div></section>')
        return f'<div class="wrap archive-head"><h1>Articles — page {pg}</h1></div>'
    for path, body, pg, total in paginate(posts, "/", intro):
        wrapped = body if pg == 1 else f'<div class="wrap">{body}</div>'
        if pg == 1:
            # hero band is full-width; grid inside wrap
            html_body = body.replace('<div class="card-grid">',
                                     '<div class="wrap"><div class="card-grid">', 1)
            html_body = html_body.replace('</div><nav class="pagination">',
                                          '</div></div><nav class="pagination">', 1)
            # ensure pagination inside wrap
            html_body = re.sub(r'(<nav class="pagination">.*?</nav>)$',
                               r'<div class="wrap">\1</div>', html_body, flags=re.S)
            write(path, page_shell(SITE_TITLE, html_body, canonical="/"))
        else:
            write(path, page_shell(SITE_TITLE, f'<div class="wrap">{body}</div>',
                                   canonical=path))

def build_category(c):
    cid = c["id"]
    items = [p for p in posts if cid in p["_cats"]]
    if not items:
        return
    base = f'/category/{c["slug"]}/'
    def intro(pg):
        return (f'<div class="wrap archive-head"><span class="kicker">Catégorie</span>'
                f'<h1>{decode(c["name"])}</h1>'
                + (f'<p>{decode(c["description"])}</p>' if c.get("description") else "")
                + f'<p class="count">{len(items)} article{"s" if len(items)>1 else ""}</p></div>')
    for path, body, pg, total in paginate(items, base, intro):
        write(path, page_shell(f'{decode(c["name"])} — Catégorie',
                               f'<div class="wrap">{body}</div>', canonical=path,
                               description=f'Articles dans la catégorie {decode(c["name"])} sur Vidéoludique.ca.'))

def build_tag(t):
    tid = t["id"]
    items = [p for p in posts if tid in p["_tags"]]
    if not items:
        return
    base = f'/tag/{t["slug"]}/'
    def intro(pg):
        return (f'<div class="wrap archive-head"><span class="kicker">Étiquette</span>'
                f'<h1>{decode(t["name"])}</h1>'
                f'<p class="count">{len(items)} article{"s" if len(items)>1 else ""}</p></div>')
    for path, body, pg, total in paginate(items, base, intro):
        write(path, page_shell(f'{decode(t["name"])} — Étiquette',
                               f'<div class="wrap">{body}</div>', canonical=path,
                               description=f'Articles avec l’étiquette « {decode(t["name"])} » sur Vidéoludique.ca.'))

# ---------------------------------------------------------------------------
# author archives  (/auteur/<slug>/)  — WordPress-style author pages
# ---------------------------------------------------------------------------
def author_profile_html(a):
    photo = (f'<figure class="author-photo"><img src="{a["photo"]}" '
             f'alt="Portrait de {attr(decode(a["name"]))}" width="480" height="480"></figure>'
             ) if a["photo"] else ""
    role = f'<p class="author-role">{decode(a["role"])}</p>' if a["role"] else ""
    bio = f'<div class="author-bio">{a["bio"]}</div>' if a["bio"] else ""
    social = ""
    if a["social"]:
        links = "".join(
            f'<a href="{s["url"]}" rel="noopener" target="_blank">{decode(s.get("network","Lien"))}</a>'
            for s in a["social"])
        social = f'<div class="author-social">{links}</div>'
    btns = []
    if a["email"]:
        btns.append(f'<a class="btn" href="mailto:{a["email"]}">Me contacter</a>')
    if a["website"]:
        btns.append(f'<a class="btn btn-ghost" href="{a["website"]}" rel="noopener" target="_blank">Site web</a>')
    actions = f'<div class="author-actions">{"".join(btns)}</div>' if btns else ""
    return (f'<header class="author-hero">{photo}'
            f'<div class="author-heading"><span class="kicker">Auteur</span>'
            f'<h1 class="author-name">{decode(a["name"])}</h1>'
            f'{role}{bio}{social}{actions}</div></header>')

def build_author(a):
    items = [p for p in posts if p["_author"] == a["slug"]]
    base = f'/auteur/{a["slug"]}/'
    profile = author_profile_html(a)
    desc = f'Articles signés {decode(a["name"])} sur Vidéoludique.ca.'
    og = a["photo"] or None
    if not items:
        body = f'{profile}<p class="count">Aucun article pour le moment.</p>'
        write(base, page_shell(f'{decode(a["name"])} — Auteur',
                               f'<div class="wrap">{body}</div>', canonical=base,
                               description=desc, og_image=og, og_type="profile"))
        return
    def intro(pg):
        head = profile if pg == 1 else (
            f'<div class="archive-head"><span class="kicker">Auteur</span>'
            f'<h1>{decode(a["name"])} — page {pg}</h1></div>')
        return (f'{head}<p class="count">{len(items)} '
                f'article{"s" if len(items) > 1 else ""}</p>')
    for path, body, pg, total in paginate(items, base, intro):
        write(path, page_shell(f'{decode(a["name"])} — Auteur',
                               f'<div class="wrap">{body}</div>', canonical=path,
                               description=desc, og_image=og, og_type="profile"))

def build_date_archives():
    years = {}
    months = {}
    for p in posts:
        y, m, d = ymd(p["date"])
        years.setdefault(y, []).append(p)
        months.setdefault((y, m), []).append(p)
    for y, items in years.items():
        base = f'/{y}/'
        def intro(pg, y=y, items=items):
            return f'<div class="wrap archive-head"><span class="kicker">Archives</span><h1>{y}</h1><p class="count">{len(items)} articles</p></div>'
        for path, body, pg, total in paginate(items, base, intro):
            write(path, page_shell(f'Archives {y}', f'<div class="wrap">{body}</div>', canonical=path))
    for (y, m), items in months.items():
        base = f'/{y}/{m}/'
        label = f'{MONTHS_FR[int(m)].capitalize()} {y}'
        def intro(pg, label=label, items=items):
            return f'<div class="wrap archive-head"><span class="kicker">Archives</span><h1>{label}</h1><p class="count">{len(items)} articles</p></div>'
        for path, body, pg, total in paginate(items, base, intro):
            write(path, page_shell(f'Archives — {label}', f'<div class="wrap">{body}</div>', canonical=path))

# about-page profile data (curated) -----------------------------------------
ABOUT_PHOTO = "/assets/mario-j-ramos.jpg"
ABOUT_LEDE = ("Scénariste et réalisateur primé devenu journaliste vidéoludique, "
              "je couvre depuis dix ans l’industrie du jeu vidéo au Québec — "
              "le 5<sup>e</sup> pôle mondial du secteur.")

# social links with inline SVG glyphs (name, url, viewBox, path)
ABOUT_SOCIAL = [
    ("Facebook", "https://www.facebook.com/mjramos/", "0 0 24 24",
     '<path d="M12 2C6.5 2 2 6.5 2 12c0 5 3.7 9.1 8.4 9.9v-7H7.9V12h2.5V9.8c0-2.5 1.5-3.9 3.8-3.9 1.1 0 2.2.2 2.2.2v2.5h-1.3c-1.2 0-1.6.8-1.6 1.6V12h2.8l-.4 2.9h-2.3v7C18.3 21.1 22 17 22 12c0-5.5-4.5-10-10-10z"></path>'),
    ("YouTube", "https://www.youtube.com/channel/UCFnn-dqTDyxCleyWcXxkS9w", "0 0 24 24",
     '<path d="M21.8,8.001c0,0-0.195-1.378-0.795-1.985c-0.76-0.797-1.613-0.801-2.004-0.847c-2.799-0.202-6.997-0.202-6.997-0.202 h-0.009c0,0-4.198,0-6.997,0.202C4.608,5.216,3.756,5.22,2.995,6.016C2.395,6.623,2.2,8.001,2.2,8.001S2,9.62,2,11.238v1.517 c0,1.618,0.2,3.237,0.2,3.237s0.195,1.378,0.795,1.985c0.761,0.797,1.76,0.771,2.205,0.855c1.6,0.153,6.8,0.201,6.8,0.201 s4.203-0.006,7.001-0.209c0.391-0.047,1.243-0.051,2.004-0.847c0.6-0.607,0.795-1.985,0.795-1.985s0.2-1.618,0.2-3.237v-1.517 C22,9.62,21.8,8.001,21.8,8.001z M9.935,14.594l-0.001-5.62l5.404,2.82L9.935,14.594z"></path>'),
    ("Instagram", "https://www.instagram.com/mariojorge.ramos/", "0 0 24 24",
     '<path d="M12,4.622c2.403,0,2.688,0.009,3.637,0.052c0.877,0.04,1.354,0.187,1.671,0.31c0.42,0.163,0.72,0.358,1.035,0.673 c0.315,0.315,0.51,0.615,0.673,1.035c0.123,0.317,0.27,0.794,0.31,1.671c0.043,0.949,0.052,1.234,0.052,3.637 s-0.009,2.688-0.052,3.637c-0.04,0.877-0.187,1.354-0.31,1.671c-0.163,0.42-0.358,0.72-0.673,1.035 c-0.315,0.315-0.615,0.51-1.035,0.673c-0.317,0.123-0.794,0.27-1.671,0.31c-0.949,0.043-1.233,0.052-3.637,0.052 s-2.688-0.009-3.637-0.052c-0.877-0.04-1.354-0.187-1.671-0.31c-0.42-0.163-0.72-0.358-1.035-0.673 c-0.315-0.315-0.51-0.615-0.673-1.035c-0.123-0.317-0.27-0.794-0.31-1.671C4.631,14.688,4.622,14.403,4.622,12 s0.009-2.688,0.052-3.637c0.04-0.877,0.187-1.354,0.31-1.671c0.163-0.42,0.358-0.72,0.673-1.035 c0.315-0.315,0.615-0.51,1.035-0.673c0.317-0.123,0.794-0.27,1.671-0.31C9.312,4.631,9.597,4.622,12,4.622 M12,3 C9.556,3,9.249,3.01,8.289,3.054C7.331,3.098,6.677,3.25,6.105,3.472C5.513,3.702,5.011,4.01,4.511,4.511 c-0.5,0.5-0.808,1.002-1.038,1.594C3.25,6.677,3.098,7.331,3.054,8.289C3.01,9.249,3,9.556,3,12c0,2.444,0.01,2.751,0.054,3.711 c0.044,0.958,0.196,1.612,0.418,2.185c0.23,0.592,0.538,1.094,1.038,1.594c0.5,0.5,1.002,0.808,1.594,1.038 c0.572,0.222,1.227,0.375,2.185,0.418C9.249,20.99,9.556,21,12,21s2.751-0.01,3.711-0.054c0.958-0.044,1.612-0.196,2.185-0.418 c0.592-0.23,1.094-0.538,1.594-1.038c0.5-0.5,0.808-1.002,1.038-1.594c0.222-0.572,0.375-1.227,0.418-2.185 C20.99,14.751,21,14.444,21,12s-0.01-2.751-0.054-3.711c-0.044-0.958-0.196-1.612-0.418-2.185c-0.23-0.592-0.538-1.094-1.038-1.594 c-0.5-0.5-1.002-0.808-1.594-1.038c-0.572-0.222-1.227-0.375-2.185-0.418C14.751,3.01,14.444,3,12,3L12,3z M12,7.378 c-2.552,0-4.622,2.069-4.622,4.622S9.448,16.622,12,16.622s4.622-2.069,4.622-4.622S14.552,7.378,12,7.378z M12,15 c-1.657,0-3-1.343-3-3s1.343-3,3-3s3,1.343,3,3S13.657,15,12,15z M16.804,6.116c-0.596,0-1.08,0.484-1.08,1.08 s0.484,1.08,1.08,1.08c0.596,0,1.08-0.484,1.08-1.08S17.401,6.116,16.804,6.116z"></path>'),
    ("TikTok", "https://www.tiktok.com/@marioj.ramos", "0 0 32 32",
     '<path d="M16.708 0.027c1.745-0.027 3.48-0.011 5.213-0.027 0.105 2.041 0.839 4.12 2.333 5.563 1.491 1.479 3.6 2.156 5.652 2.385v5.369c-1.923-0.063-3.855-0.463-5.6-1.291-0.76-0.344-1.468-0.787-2.161-1.24-0.009 3.896 0.016 7.787-0.025 11.667-0.104 1.864-0.719 3.719-1.803 5.255-1.744 2.557-4.771 4.224-7.88 4.276-1.907 0.109-3.812-0.411-5.437-1.369-2.693-1.588-4.588-4.495-4.864-7.615-0.032-0.667-0.043-1.333-0.016-1.984 0.24-2.537 1.495-4.964 3.443-6.615 2.208-1.923 5.301-2.839 8.197-2.297 0.027 1.975-0.052 3.948-0.052 5.923-1.323-0.428-2.869-0.308-4.025 0.495-0.844 0.547-1.485 1.385-1.819 2.333-0.276 0.676-0.197 1.427-0.181 2.145 0.317 2.188 2.421 4.027 4.667 3.828 1.489-0.016 2.916-0.88 3.692-2.145 0.251-0.443 0.532-0.896 0.547-1.417 0.131-2.385 0.079-4.76 0.095-7.145 0.011-5.375-0.016-10.735 0.025-16.093z" />'),
    ("Threads", "https://www.threads.net/@mariojorge.ramos", "0 0 24 24",
     '<path d="M16.3 11.3c-.1 0-.2-.1-.2-.1-.1-2.6-1.5-4-3.9-4-1.4 0-2.6.6-3.3 1.7l1.3.9c.5-.8 1.4-1 2-1 .8 0 1.4.2 1.7.7.3.3.5.8.5 1.3-.7-.1-1.4-.2-2.2-.1-2.2.1-3.7 1.4-3.6 3.2 0 .9.5 1.7 1.3 2.2.7.4 1.5.6 2.4.6 1.2-.1 2.1-.5 2.7-1.3.5-.6.8-1.4.9-2.4.6.3 1 .8 1.2 1.3.4.9.4 2.4-.8 3.6-1.1 1.1-2.3 1.5-4.3 1.5-2.1 0-3.8-.7-4.8-2S5.7 14.3 5.7 12c0-2.3.5-4.1 1.5-5.4 1.1-1.3 2.7-2 4.8-2 2.2 0 3.8.7 4.9 2 .5.7.9 1.5 1.2 2.5l1.5-.4c-.3-1.2-.8-2.2-1.5-3.1-1.3-1.7-3.3-2.6-6-2.6-2.6 0-4.7.9-6 2.6C4.9 7.2 4.3 9.3 4.3 12s.6 4.8 1.9 6.4c1.4 1.7 3.4 2.6 6 2.6 2.3 0 4-.6 5.3-2 1.8-1.8 1.7-4 1.1-5.4-.4-.9-1.2-1.7-2.3-2.3z"/>'),
]

# curated directory of Québec video-game media / orgs / podcasts
ABOUT_NETWORK = [
    ("Communauté", [
        ("Discord HUB", "https://discord.gg/MUxn4WY4Qx",
         "Lieu d’échanges et de discussions entre passionnés de jeux vidéo."),
    ]),
    ("Médias québécois — nouvelles & critiques", [
        ("Le Bêta-Testeur", "https://www.lebetatesteur.ca/",
         "Média indépendant fondé par Patrick Tremblay."),
        ("Le Salon de Gaming de Monsieur Smith", "https://www.salongaming.ca/",
         "Steeve Tremblay et ses collaborateurs vous parlent de gaming."),
        ("Geeks and com", "https://www.geeksandcom.com/",
         "Média indépendant tenu par Anthony Gravel."),
        ("M2 Gaming", "https://m2gaming.ca/",
         "Média indépendant fondé par Marc Desgagnés et Martin Grondin."),
        ("Pèse sur Start", "https://www.pesesurstart.com/",
         "Média spécialisé sur les jeux vidéo et la techno de Quebecor."),
        ("Radio-Canada Techno", "https://ici.radio-canada.ca/techno",
         "La section jeux vidéo et technologie de Radio-Canada."),
        ("Blogue de Simon Dor", "https://www.simondor.com/",
         "Simon Dor est professeur en études du jeu vidéo à l’UQAT."),
    ]),
    ("Organismes", [
        ("La Fondation des Gardiens virtuels", "https://gardiensvirtuels.org/",
         "OBNL qui œuvre à promouvoir la saine utilisation des plateformes numériques."),
        ("La Guilde du Jeu vidéo du Québec", "https://www.laguilde.quebec/fr/",
         "Coopérative à but non lucratif regroupant les développeur.euse.s, créateur.rice.s, "
         "établissements d’enseignement et entrepreneur.euse.s du jeu vidéo au Québec."),
    ]),
    ("Balados", [
        ("Chez Papa Cassette", "https://baladoquebec.ca/papa-cassette-podcast",
         "Dominic Bourret et Jean-François Cromp discutent de jeux vidéo rétro."),
        ("Entre deux parties", "https://baladoquebec.ca/entre-deux-parties",
         "Game designer et collectionneur, Fred Gémus parle d’actualité vidéoludique "
         "et de sa passion pour la collection."),
        ("Équilibre Numérique", "https://baladoquebec.ca/equilibre-numerique",
         "Balado dont je suis producteur au contenu. Samuel « Son Off Odin » Gignac et "
         "Éloïse « LaCoiffeuseGeek » Pratte parlent de la relation entre le numérique "
         "et la santé mentale."),
    ]),
]

def about_social_html():
    items = []
    for name, url, vb, path in ABOUT_SOCIAL:
        items.append(
            f'<li class="wp-social-link wp-block-social-link">'
            f'<a href="{url}" class="wp-block-social-link-anchor" aria-label="{name}" '
            f'rel="noopener" target="_blank">'
            f'<svg width="24" height="24" viewBox="{vb}" xmlns="http://www.w3.org/2000/svg" '
            f'aria-hidden="true" focusable="false">{path}</svg>'
            f'<span class="screen-reader-text">{name}</span></a></li>')
    return ('<ul class="wp-block-social-links is-layout-flex wp-block-social-links-is-layout-flex">'
            + "".join(items) + '</ul>')

def about_network_html():
    groups = []
    for title, cards in ABOUT_NETWORK:
        items = "".join(
            f'<a class="net-card" href="{url}" rel="noopener" target="_blank">'
            f'<span class="net-name">{name}</span>'
            f'<span class="net-desc">{desc}</span></a>'
            for name, url, desc in cards)
        groups.append(
            f'<div class="net-group"><h3 class="net-group-title">{title}</h3>'
            f'<div class="net-grid">{items}</div></div>')
    return "".join(groups)

def build_page_about():
    fp = os.path.join(SRC, "pages", "a-propos.html")
    bio = ""
    if os.path.exists(fp):
        with open(fp, encoding="utf-8") as f:
            bio = rewrite_html(f.read())
    body = f"""<article class="about">
  <div class="wrap">
    <header class="about-hero">
      <figure class="about-photo">
        <img src="{ABOUT_PHOTO}" alt="Portrait de {AUTHOR}" width="1365" height="2048">
      </figure>
      <div class="about-heading">
        <span class="kicker">À propos</span>
        <h1 class="about-name">{AUTHOR}</h1>
        <p class="about-role">Fondateur de Vidéoludique.ca · Scénariste et réalisateur</p>
        <p class="about-lede">{ABOUT_LEDE}</p>
        {about_social_html()}
        <div class="about-actions">
          <a class="btn" href="mailto:info@mariojramos.com">Me contacter</a>
          <a class="btn btn-ghost" href="https://mariojramos.com/" rel="noopener" target="_blank">Portfolio</a>
        </div>
      </div>
    </header>

    <section class="about-body post-content">
      <h2 class="about-section-title">Parcours</h2>
      {bio}
    </section>

    <section class="about-network">
      <h2 class="about-section-title">Dans mon réseau</h2>
      <p class="about-network-intro">Quelques médias, organismes et balados québécois à découvrir.</p>
      {about_network_html()}
    </section>
  </div>
</article>"""
    write("/a-propos/", page_shell("À propos / contact", body, canonical="/a-propos/",
                                   description="À propos de Vidéoludique.ca et de Mario J. Ramos.",
                                   og_image=ABOUT_PHOTO))

def build_feed():
    items = []
    for p in posts[:30]:
        link = SITE_URL + post_path(p)
        d = datetime.fromisoformat(p["date"])
        pub = d.strftime("%a, %d %b %Y %H:%M:%S +0000")
        desc = decode(p.get("excerpt", ""))
        cats = "".join(f"<category>{htmllib.escape(decode(CAT[c]['name']))}</category>"
                       for c in p["_cats"] if c in CAT)
        items.append(f"""  <item>
    <title>{htmllib.escape(decode(p['title']))}</title>
    <link>{link}</link>
    <guid isPermaLink="true">{link}</guid>
    <pubDate>{pub}</pubDate>
    <description>{htmllib.escape(desc)}</description>
    {cats}
  </item>""")
    now = datetime.now().strftime("%a, %d %b %Y %H:%M:%S +0000")
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">
<channel>
  <title>{SITE_TITLE}</title>
  <atom:link href="{SITE_URL}/feed/index.xml" rel="self" type="application/rss+xml"/>
  <link>{SITE_URL}/</link>
  <description>{htmllib.escape(SITE_DESC)}</description>
  <language>fr-CA</language>
  <lastBuildDate>{now}</lastBuildDate>
{chr(10).join(items)}
</channel>
</rss>"""
    write("/feed/index.xml", xml)

def build_sitemap():
    urls = ["/", "/a-propos/"]
    urls += [post_path(p) for p in posts]
    urls += [f'/category/{c["slug"]}/' for c in cats if any(c["id"] in p["_cats"] for p in posts)]
    urls += [f'/tag/{t["slug"]}/' for t in tags if any(t["id"] in p["_tags"] for p in posts)]
    urls += [f'/auteur/{a["slug"]}/' for a in AUTHORS.values()
             if any(p["_author"] == a["slug"] for p in posts)]
    seen = set()
    body = []
    for u in urls:
        if u in seen:
            continue
        seen.add(u)
        body.append(f"  <url><loc>{SITE_URL}{u}</loc></url>")
    xml = ('<?xml version="1.0" encoding="UTF-8"?>\n'
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
           + "\n".join(body) + "\n</urlset>\n")
    write("/sitemap.xml", xml)
    write("/robots.txt", f"User-agent: *\nAllow: /\nSitemap: {SITE_URL}/sitemap.xml\n")

def build_404():
    body = ('<div class="wrap error404"><h1>404</h1>'
            '<p>La page demandée est introuvable.</p>'
            '<p><a class="btn" href="/">Retour à l’accueil</a></p></div>')
    write("/404.html", page_shell("Page introuvable (404)", body, canonical="/404.html"))

def write_media_manifest():
    paths = sorted(MEDIA_PATHS)
    with open(os.path.join(ROOT, "scripts", "media_manifest.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(paths) + "\n")
    return len(paths)

def main():
    os.makedirs(os.path.join(ROOT, "scripts"), exist_ok=True)
    for i, p in enumerate(posts):
        render_post(p, i)
    build_page_about()
    build_home()
    for c in cats:
        build_category(c)
    for t in tags:
        build_tag(t)
    for a in AUTHORS.values():
        build_author(a)
    build_date_archives()
    build_feed()
    build_sitemap()
    build_404()
    n = write_media_manifest()
    print(f"posts={len(posts)} authors={len(AUTHORS)} "
          f"bodies_missing={len(missing)} media_paths={n}")
    if missing:
        print("MISSING BODIES:", missing[:20], "..." if len(missing) > 20 else "")

if __name__ == "__main__":
    main()
