#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CMS content layer for videoludique.ca — reads the articles and authors created
through Pages CMS (https://pagescms.org, configured in .pages.yml) and turns
them into plain Python dicts that build.py merges with the WordPress import.

Nothing here depends on anything outside the Python standard library, so the
site keeps building with a bare `python3 build.py` (no `pip install`). Two jobs:

    parse_front_matter()  ->  split YAML front matter from the Markdown body
    md_to_html()          ->  convert the body a rich-text editor produces to HTML

Pages CMS stores each article/author as a Markdown file with YAML front matter;
the rich-text `body` field is the Markdown below the front matter. We only need
to parse the small, well-defined subset of YAML that our .pages.yml fields
produce (scalars, string lists, and lists of {network, url} objects), so a
focused mini-parser is enough — and keeps the zero-dependency promise.
"""
import os
import re


# ---------------------------------------------------------------------------
# tiny YAML-subset parser (front matter only)
# ---------------------------------------------------------------------------
def _indent(s):
    return len(s) - len(s.lstrip(" "))


def _scalar(v):
    """Coerce a single YAML scalar (quotes, booleans, inline [a, b] lists)."""
    v = v.strip()
    if not v:
        return ""
    if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
        return v[1:-1]
    low = v.lower()
    if low in ("true", "false"):
        return low == "true"
    if low in ("null", "~"):
        return None
    if v.startswith("[") and v.endswith("]"):
        inner = v[1:-1].strip()
        return [_scalar(x) for x in inner.split(",")] if inner else []
    return v


def _parse_block(lines):
    """Parse an indented block of `key: value` / `key:` (nested) pairs."""
    out = {}
    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        if not line.strip() or line.lstrip().startswith("#"):
            i += 1
            continue
        ind = _indent(line)
        key, sep, val = line.strip().partition(":")
        if not sep:
            i += 1
            continue
        key, val = key.strip(), val.strip()
        if val:
            out[key] = _scalar(val)
            i += 1
            continue
        # value on following, more-indented lines: a list or a nested map
        child, j = [], i + 1
        while j < n and (not lines[j].strip() or _indent(lines[j]) > ind):
            child.append(lines[j])
            j += 1
        meaningful = [c for c in child if c.strip()]
        if meaningful and meaningful[0].lstrip().startswith("-"):
            out[key] = _parse_list(child)
        else:
            out[key] = _parse_block(child)
        i = j
    return out


def _parse_list(lines):
    """Parse a `- item` block: scalars, or objects (`- key: val` + more keys)."""
    items = []
    dashes = [l for l in lines if l.strip().startswith("-")]
    if not dashes:
        return items
    base = min(_indent(l) for l in dashes)
    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        if _indent(line) == base and line.strip().startswith("-"):
            after = line.strip()[1:].strip()
            block, j = [], i + 1
            while j < n and (not lines[j].strip() or _indent(lines[j]) > base):
                block.append(lines[j])
                j += 1
            if after and ":" in after and not (after[0] in "'\""):
                # inline object start: "- network: Facebook" + following keys
                items.append(_parse_block([" " * (base + 2) + after] + block))
            elif after == "":
                items.append(_parse_block(block))
            else:
                items.append(_scalar(after))
            i = j
        else:
            i += 1
    return items


def parse_front_matter(text):
    """Return (meta_dict, body_str) for a `--- yaml --- body` document."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if not text.startswith("---"):
        return {}, text
    lines = text.split("\n")
    end = next((k for k in range(1, len(lines)) if lines[k].strip() == "---"), None)
    if end is None:
        return {}, text
    meta = _parse_block(lines[1:end])
    body = "\n".join(lines[end + 1:]).lstrip("\n")
    return meta, body


# ---------------------------------------------------------------------------
# Markdown -> HTML (the subset the Pages CMS rich-text editor emits)
# ---------------------------------------------------------------------------
_ESC = {"&": "&amp;", "<": "&lt;", ">": "&gt;"}


def _esc(s):
    return "".join(_ESC.get(c, c) for c in s)


def _inline(t):
    """Inline spans: code, images, links, bold, italic. URL-safe (tokenised)."""
    stash = []

    def keep(html):
        stash.append(html)
        return f"\x00{len(stash) - 1}\x00"

    t = re.sub(r"`([^`]+)`", lambda m: keep(f"<code>{_esc(m.group(1))}</code>"), t)
    t = re.sub(r'!\[([^\]]*)\]\(([^)\s]+)(?:\s+"[^"]*")?\)',
               lambda m: keep(f'<img src="{m.group(2)}" alt="{m.group(1)}">'), t)
    t = re.sub(r'\[([^\]]+)\]\(([^)\s]+)(?:\s+"[^"]*")?\)',
               lambda m: keep(f'<a href="{m.group(2)}">{m.group(1)}</a>'), t)
    t = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", t)
    t = re.sub(r"__([^_]+)__", r"<strong>\1</strong>", t)
    t = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<em>\1</em>", t)
    t = re.sub(r"(?<![\w_])_([^_\n]+)_(?![\w_])", r"<em>\1</em>", t)
    return re.sub(r"\x00(\d+)\x00", lambda m: stash[int(m.group(1))], t)


_HR_RE = re.compile(r"^\s*([-*_])\s*(?:\1\s*){2,}$")
_HEAD_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_UL_RE = re.compile(r"^\s*[-*+]\s+(.*)$")
_OL_RE = re.compile(r"^\s*\d+\.\s+(.*)$")
_IMG_ONLY_RE = re.compile(r"^\s*<img\b[^>]*>\s*$")


def md_to_html(md):
    """Convert Markdown (or already-HTML) body content to HTML.

    Supports headings, paragraphs, bold/italic/code, links, images, blockquotes,
    ordered/unordered lists, fenced code blocks and horizontal rules. Any line
    that starts with a block-level `<tag>` is passed through untouched, so pasted
    embeds (YouTube/VideoPress <iframe>, tables) and HTML bodies survive intact.
    """
    if not md or not md.strip():
        return ""
    lines = md.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out, i, n = [], 0, len(lines)

    while i < n:
        line = lines[i]
        stripped = line.strip()

        if not stripped:
            i += 1
            continue

        # fenced code block
        if stripped.startswith("```"):
            lang = stripped[3:].strip()
            buf, i = [], i + 1
            while i < n and not lines[i].strip().startswith("```"):
                buf.append(lines[i])
                i += 1
            i += 1  # closing fence
            cls = f' class="language-{lang}"' if lang else ""
            out.append(f"<pre><code{cls}>{_esc(chr(10).join(buf))}</code></pre>")
            continue

        # horizontal rule
        if _HR_RE.match(line):
            out.append("<hr>")
            i += 1
            continue

        # raw block-level HTML: pass the whole block through untouched
        if stripped.startswith("<") and not stripped.startswith("<img"):
            buf = []
            while i < n and lines[i].strip():
                buf.append(lines[i])
                i += 1
            out.append("\n".join(buf))
            continue

        # heading
        mh = _HEAD_RE.match(line)
        if mh:
            lvl = len(mh.group(1))
            out.append(f"<h{lvl}>{_inline(mh.group(2).strip())}</h{lvl}>")
            i += 1
            continue

        # blockquote
        if stripped.startswith(">"):
            buf = []
            while i < n and lines[i].strip().startswith(">"):
                buf.append(re.sub(r"^\s*>\s?", "", lines[i]))
                i += 1
            out.append(f"<blockquote>{md_to_html(chr(10).join(buf))}</blockquote>")
            continue

        # unordered / ordered list
        if _UL_RE.match(line) or _OL_RE.match(line):
            ordered = bool(_OL_RE.match(line))
            rx = _OL_RE if ordered else _UL_RE
            items = []
            while i < n and rx.match(lines[i]):
                items.append(f"<li>{_inline(rx.match(lines[i]).group(1).strip())}</li>")
                i += 1
            tag = "ol" if ordered else "ul"
            out.append(f"<{tag}>{''.join(items)}</{tag}>")
            continue

        # standalone image -> figure
        if _IMG_ONLY_RE.match(_inline(stripped)):
            out.append(f"<figure>{_inline(stripped)}</figure>")
            i += 1
            continue

        # paragraph: gather consecutive plain lines
        buf = []
        while i < n and lines[i].strip() and not (
            lines[i].strip().startswith(("```", ">", "#", "<"))
            or _HR_RE.match(lines[i]) or _UL_RE.match(lines[i]) or _OL_RE.match(lines[i])
        ):
            buf.append(lines[i].strip())
            i += 1
        out.append(f"<p>{_inline(' '.join(buf))}</p>")

    return "\n".join(out)


# ---------------------------------------------------------------------------
# discovery
# ---------------------------------------------------------------------------
def discover(directory, ext=".md"):
    """Yield (slug_from_filename, meta, body) for every content file in a dir."""
    if not os.path.isdir(directory):
        return
    for name in sorted(os.listdir(directory)):
        if not name.endswith(ext) or name.startswith((".", "_")):
            continue
        with open(os.path.join(directory, name), encoding="utf-8") as f:
            meta, body = parse_front_matter(f.read())
        yield name[: -len(ext)], meta, body
