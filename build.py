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
from datetime import datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(ROOT, "_source")
OUT = ROOT  # emit at repo root (GitHub Pages serves from here)

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
    fimg = media_url(p.get("featured_media", 0))
    m = MEDIA.get(p.get("featured_media", 0)) or {}
    alt = attr(decode(m.get("alt") or p["title"]))
    thumb = (f'<a class="card-media" href="{href}">'
             f'<img src="{fimg}" alt="{alt}" loading="lazy" width="{m.get("w",0) or 800}" height="{m.get("h",0) or 450}"></a>'
             ) if fimg else ""
    cat = ""
    if p.get("categories"):
        c = CAT.get(p["categories"][0])
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
# build: sort posts, load bodies
# ---------------------------------------------------------------------------
posts = sorted(posts_meta, key=lambda p: p["date"], reverse=True)

def body_fragment(pid):
    fp = os.path.join(SRC, "posts", f"{pid}.html")
    if os.path.exists(fp):
        with open(fp, encoding="utf-8") as f:
            return f.read()
    return None

missing = [p["id"] for p in posts if body_fragment(p["id"]) is None]

# ---------------------------------------------------------------------------
# single posts
# ---------------------------------------------------------------------------
def render_post(p, idx):
    href = post_path(p)
    raw = body_fragment(p["id"])
    if raw is None:
        return  # body not fetched yet
    content = rewrite_html(raw)
    fimg = media_url(p.get("featured_media", 0))
    m = MEDIA.get(p.get("featured_media", 0)) or {}
    hero = ""
    if fimg:
        hero = (f'<figure class="post-hero"><img src="{fimg}" '
                f'alt="{attr(decode(m.get("alt") or p["title"]))}" '
                f'width="{m.get("w",0) or 1200}" height="{m.get("h",0) or 675}"></figure>')
    # category + tags
    catlinks = " ".join(
        f'<a class="chip cat" href="/category/{CAT[c]["slug"]}/">{decode(CAT[c]["name"])}</a>'
        for c in p.get("categories", []) if c in CAT)
    taglinks = " ".join(
        f'<a class="chip" href="/tag/{TAG[t]["slug"]}/">{decode(TAG[t]["name"])}</a>'
        for t in p.get("tags", []) if t in TAG)
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
    body = f"""<article class="post">
  <div class="wrap">
    <div class="post-head">
      <div class="post-crumbs">{catlinks}</div>
      <h1 class="post-title">{p['title']}</h1>
      <div class="post-meta">
        <span class="by">Par {AUTHOR}</span>
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
        "author": {"@type": "Person", "name": AUTHOR},
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
    items = [p for p in posts if cid in p.get("categories", [])]
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
    items = [p for p in posts if tid in p.get("tags", [])]
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

def build_page_about():
    fp = os.path.join(SRC, "pages", "a-propos.html")
    if not os.path.exists(fp):
        return
    with open(fp, encoding="utf-8") as f:
        raw = f.read()
    content = rewrite_html(raw)
    body = f"""<article class="post page">
  <div class="wrap">
    <div class="post-head"><h1 class="post-title">À propos / contact</h1></div>
    <div class="post-content">{content}</div>
  </div>
</article>"""
    write("/a-propos/", page_shell("À propos / contact", body, canonical="/a-propos/",
                                   description="À propos de Vidéoludique.ca et de Mario J. Ramos."))

def build_feed():
    items = []
    for p in posts[:30]:
        link = SITE_URL + post_path(p)
        d = datetime.fromisoformat(p["date"])
        pub = d.strftime("%a, %d %b %Y %H:%M:%S +0000")
        desc = decode(p.get("excerpt", ""))
        cats = "".join(f"<category>{htmllib.escape(decode(CAT[c]['name']))}</category>"
                       for c in p.get("categories", []) if c in CAT)
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
    urls += [f'/category/{c["slug"]}/' for c in cats if any(c["id"] in p.get("categories",[]) for p in posts)]
    urls += [f'/tag/{t["slug"]}/' for t in tags]
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
    build_date_archives()
    build_feed()
    build_sitemap()
    build_404()
    n = write_media_manifest()
    print(f"posts={len(posts)} bodies_missing={len(missing)} media_paths={n}")
    if missing:
        print("MISSING BODIES:", missing[:20], "..." if len(missing) > 20 else "")

if __name__ == "__main__":
    main()
