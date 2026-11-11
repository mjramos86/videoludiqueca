#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Convert an old WordPress post (JSON from the WordPress.com REST API / MCP,
context=edit) into a Pages CMS article: content/articles/<slug>.md

    python3 scripts/wp_to_cms.py post.json [post2.json ...]

- Gutenberg markup -> Markdown (headings, lists, bold/italic, links, quotes)
- YouTube / Vimeo / VideoPress embeds -> the bare URL on its own line
  (build.py turns it back into a responsive player; survives the CMS editor)
- Images -> /assets/uploads/wp/<yyyy>/<mm>/<file>; the list of files to fetch
  is appended to scripts/wp_media_todo.txt (one "<source url> <local path>")
- Links to videoludique.ca become site-relative
- Categories / tags are mapped to the names the CMS uses
Requires: markdownify (pip install markdownify)
"""
import html
import json
import os
import re
import sys

from markdownify import markdownify as md

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "_source")
OUT = os.path.join(ROOT, "content", "articles")
TODO = os.path.join(ROOT, "scripts", "wp_media_todo.txt")

CATS = {c["id"]: c for c in json.load(open(os.path.join(SRC, "categories.json"), encoding="utf-8"))}
TAGS = {t["id"]: t for t in json.load(open(os.path.join(SRC, "tags.json"), encoding="utf-8"))}
CMS_CATS = {"apercu", "actualite", "chronique", "critique", "non-quebec", "concours"}

WP_HOSTS = r"(?:https?:)?//(?:i[0-2]\.wp\.com/)?(?:videoludique\.ca|mariojramos8\.wordpress\.com|mariojramos8\.files\.wordpress\.com)"
UPLOAD_RE = re.compile(WP_HOSTS + r"(?:/wp-content/uploads)?/(\d{4})/(\d{2})/([^\"'?\s)]+?\.(?:jpe?g|png|gif|webp|avif))(?:\?[^\"'\s)]*)?", re.I)
EMBED_RE = re.compile(r'<figure[^>]*wp-block-embed[^>]*>\s*<div class="wp-block-embed__wrapper">\s*(https?://\S+?)\s*</div>.*?</figure>', re.S)


def local_media(m, todo):
    y, mo, f = m.group(1), m.group(2), m.group(3)
    local = f"/assets/uploads/wp/{y}/{mo}/{f}"
    todo.add(f"https://mariojramos8.wordpress.com/wp-content/uploads/{y}/{mo}/{f} {local}")
    return local


def convert_body(raw, todo):
    s = raw
    # embeds -> bare URL paragraph
    s = EMBED_RE.sub(lambda m: f"<p>{m.group(1)}</p>", s)
    s = re.sub(r"<!--\s*/?wp:.*?-->", "", s, flags=re.S)
    s = re.sub(r"<p[^>]*>\s*(?:&nbsp;|\s)*</p>", "", s)          # empty paragraphs
    s = UPLOAD_RE.sub(lambda m: local_media(m, todo), s)         # images -> local
    s = re.sub(r"\s(?:srcset|sizes)=\"[^\"]*\"", "", s)
    s = re.sub(r"https?://(?:www\.)?videoludique\.ca(/[^\"'\s<]*)", r"\1", s)  # internal links
    text = md(s, heading_style="ATX", bullets="-", strong_em_symbol="*",
              escape_underscores=False, escape_asterisks=False)
    text = text.replace(" ", " ")
    # bare video URLs stay alone on their line; markdownify may wrap them in <>
    text = re.sub(r"^<(https?://\S+)>$", r"\1", text, flags=re.M)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"
    return text


def yaml_str(v):
    return json.dumps(v, ensure_ascii=False)


def convert(post, featured=None):
    todo = set()
    title = html.unescape(post["title"] if isinstance(post["title"], str) else post["title"].get("raw", ""))
    excerpt = post.get("excerpt") or ""
    if isinstance(excerpt, dict):
        excerpt = excerpt.get("raw") or excerpt.get("rendered") or ""
    excerpt = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", excerpt)).split())
    content = post["content"] if isinstance(post["content"], str) else post["content"].get("raw", "")
    cat = next((CATS[c]["slug"] for c in post.get("categories", []) if c in CATS and CATS[c]["slug"] in CMS_CATS), "")
    tags = [html.unescape(TAGS[t]["name"]) for t in post.get("tags", []) if t in TAGS]
    image = ""
    if featured:
        m = UPLOAD_RE.search(featured)
        if m:
            image = local_media(m, todo)
    body = convert_body(content, todo)
    fm = ["---", f"title: {yaml_str(title)}", f"slug: {post['slug']}",
          f"date: {post['date'][:19]}", "author: mario-j-ramos"]
    if cat:
        fm.append(f"category: {cat}")
    if tags:
        fm.append("tags:")
        fm += [f"  - {yaml_str(t)}" for t in tags]
    if image:
        fm.append(f"image: {image}")
    if excerpt:
        fm.append(f"excerpt: {yaml_str(excerpt)}")
    fm += ["draft: false", "---", ""]
    path = os.path.join(OUT, f"{post['slug']}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(fm) + body)
    if todo:
        old = set(open(TODO, encoding="utf-8").read().split("\n")) if os.path.exists(TODO) else set()
        with open(TODO, "w", encoding="utf-8") as f:
            f.write("\n".join(sorted((old | todo) - {""})) + "\n")
    return path


if __name__ == "__main__":
    for fp in sys.argv[1:]:
        d = json.load(open(fp, encoding="utf-8"))
        d = d.get("data", d)
        print(convert(d, d.get("_featured_url")))
