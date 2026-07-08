"""A tiny, dependency-free, XSS-safe Markdown → HTML renderer.

The project ships no markdown library (kept lean — see deploy/Dockerfile
allowlist), and the self-update changelog arrives as ``changelog_md`` from
the central panel. We render a SAFE subset here rather than pulling a
dependency or trusting remote HTML.

Security model: the ENTIRE input is HTML-escaped first, then a whitelist of
inline/block transforms is applied to the escaped text. No raw HTML from the
source ever reaches the output, so a hostile changelog cannot inject script.
Only ``http(s)://`` links are linkified. RTL-friendly: emits plain semantic
tags, no ``dir`` assumptions, so the page's ``dir=rtl`` context governs.

Supported: headings (#..######), unordered lists (-,*,+), ordered lists,
bold (**x**), italic (*x*/_x_), inline code (`x`), fenced code (```),
links [text](http…), horizontal rule (---), blank-line paragraphs.
"""
from __future__ import annotations

import re
from markupsafe import Markup, escape

_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)\)")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_ITALIC_RE = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)")
_ITALIC_US_RE = re.compile(r"(?<!_)_(?!_)(.+?)(?<!_)_(?!_)")
_CODE_RE = re.compile(r"`([^`]+)`")


def _inline(text: str) -> str:
    """Apply inline transforms to an ALREADY-escaped line."""
    # Links first (before italic/bold eat the punctuation).
    text = _LINK_RE.sub(
        r'<a href="\2" target="_blank" rel="noopener noreferrer">\1</a>', text
    )
    text = _CODE_RE.sub(r"<code>\1</code>", text)
    text = _BOLD_RE.sub(r"<strong>\1</strong>", text)
    text = _ITALIC_RE.sub(r"<em>\1</em>", text)
    text = _ITALIC_US_RE.sub(r"<em>\1</em>", text)
    return text


def render(md: str) -> Markup:
    """Render a markdown string to a safe :class:`markupsafe.Markup`.

    Never raises: any parsing surprise degrades to escaped plain text.
    """
    if not md:
        return Markup("")
    try:
        return Markup(_render(str(md)))
    except Exception:  # noqa: BLE001 — rendering must never break the page
        return Markup("<p>" + str(escape(md)).replace("\n", "<br>") + "</p>")


def _render(md: str) -> str:
    lines = md.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out: list[str] = []
    list_stack: list[str] = []  # 'ul' | 'ol'
    in_code = False
    code_buf: list[str] = []
    para_buf: list[str] = []

    def close_lists() -> None:
        while list_stack:
            out.append(f"</{list_stack.pop()}>")

    def flush_para() -> None:
        if para_buf:
            joined = "<br>".join(_inline(str(escape(x))) for x in para_buf)
            out.append(f"<p>{joined}</p>")
            para_buf.clear()

    for raw in lines:
        line = raw.rstrip()

        # Fenced code blocks — content is escaped, never interpreted.
        if line.strip().startswith("```"):
            if in_code:
                out.append(
                    "<pre><code>"
                    + "\n".join(str(escape(c)) for c in code_buf)
                    + "</code></pre>"
                )
                code_buf = []
                in_code = False
            else:
                flush_para()
                close_lists()
                in_code = True
            continue
        if in_code:
            code_buf.append(raw)
            continue

        stripped = line.strip()

        if not stripped:
            flush_para()
            close_lists()
            continue

        # Horizontal rule.
        if re.match(r"^(-{3,}|\*{3,}|_{3,})$", stripped):
            flush_para()
            close_lists()
            out.append("<hr>")
            continue

        # Headings.
        h = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if h:
            flush_para()
            close_lists()
            level = len(h.group(1))
            out.append(f"<h{level}>{_inline(str(escape(h.group(2))))}</h{level}>")
            continue

        # Unordered list item.
        ul = re.match(r"^[-*+]\s+(.*)$", stripped)
        if ul:
            flush_para()
            if not list_stack or list_stack[-1] != "ul":
                close_lists()
                list_stack.append("ul")
                out.append("<ul>")
            out.append(f"<li>{_inline(str(escape(ul.group(1))))}</li>")
            continue

        # Ordered list item.
        ol = re.match(r"^\d+[.)]\s+(.*)$", stripped)
        if ol:
            flush_para()
            if not list_stack or list_stack[-1] != "ol":
                close_lists()
                list_stack.append("ol")
                out.append("<ol>")
            out.append(f"<li>{_inline(str(escape(ol.group(1))))}</li>")
            continue

        # Plain paragraph text (accumulate; blank line flushes).
        close_lists()
        para_buf.append(stripped)

    if in_code:  # unterminated fence
        out.append(
            "<pre><code>" + "\n".join(str(escape(c)) for c in code_buf) + "</code></pre>"
        )
    flush_para()
    close_lists()
    return "\n".join(out)
