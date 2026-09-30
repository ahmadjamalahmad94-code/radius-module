"""F01-F2 sweep engine — «no visible submit leads to a 403 endpoint».

For a logged-in manager, render a page and collect every control that would
send a request: POST forms (their action, and every ``formaction`` button),
and internal links (GET). Map each target to its Flask endpoint and ask the
SAME decision the server guard takes (routes/blueprint.rbac_denial_status).
A target the guard would refuse is a violation — unless the control cannot be
used (no enabled submit-capable control, e.g. a read-only ``<fieldset
disabled>`` rendering).

Used by tests/test_fix3_gating_sweep.py; kept importable for ad-hoc audits.
"""
from __future__ import annotations

from html.parser import HTMLParser
from urllib.parse import urlsplit

_SKIP_LINK_PREFIXES = ("/admin/radius/static", "/static/", "/admin/radius/docs")


class _Collector(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.forms: list[dict] = []
        self.links: list[str] = []
        self._stack: list[dict] = []        # open forms
        self._fieldset_disabled = 0
        self._template = 0

    # ---------------------------------------------------------------- tags
    def handle_starttag(self, tag, attrs):
        a = {k: (v if v is not None else "") for k, v in attrs}
        if tag == "template":
            self._template += 1
            return
        if self._template:
            return
        if tag == "fieldset" and "disabled" in a:
            self._fieldset_disabled += 1
        if tag == "form":
            self._stack.append({
                "method": (a.get("method") or "get").lower(),
                "action": a.get("action"),
                "usable": False,
                "formactions": [],
                "attrs": a,
            })
            return
        if tag == "a" and a.get("href"):
            self.links.append(a["href"])
        if not self._stack:
            return
        f = self._stack[-1]
        disabled = "disabled" in a or self._fieldset_disabled > 0
        if tag in ("button", "input", "select", "textarea"):
            t = (a.get("type") or ("submit" if tag == "button" else "text")).lower()
            if tag == "input" and t == "hidden":
                return
            if not disabled:
                f["usable"] = True
                if a.get("formaction") and t in ("submit", "image"):
                    f["formactions"].append(((a.get("formmethod") or f["method"]).lower(),
                                             a["formaction"]))

    def handle_endtag(self, tag):
        if tag == "template":
            self._template = max(0, self._template - 1)
            return
        if self._template:
            return
        if tag == "fieldset" and self._fieldset_disabled:
            self._fieldset_disabled -= 1
        if tag == "form" and self._stack:
            self.forms.append(self._stack.pop())


def targets(html: str, page_path: str):
    """[(method, path, kind)] for every usable POST form / formaction and every
    internal link on the page."""
    c = _Collector()
    c.feed(html)
    out = []
    for f in c.forms:
        if not f["usable"]:
            continue
        if f["method"] == "post":
            a = f["attrs"]
            act = f["action"]
            if act in (None, ""):
                # JS-targeted forms declare their real target
                act = (a.get("data-action-template") or "").replace("__USERNAME__", "x") \
                    .replace("__ID__", "1") or None
                if not act and a.get("data-action-endpoint"):
                    out.append(("POST", "endpoint:" + a["data-action-endpoint"], "form"))
                    continue
            act = act or page_path
            out.append(("POST", act, "form"))
        for m, act in f["formactions"]:
            if m == "post":
                out.append(("POST", act, "formaction"))
    for href in c.links:
        out.append(("GET", href, "link"))
    return out


def resolve(app, method: str, url: str):
    """URL → endpoint name (without «radius.») or None (external/unknown)."""
    if url.startswith("endpoint:"):
        ep = url.split(":", 1)[1]
        return ep.split(".", 1)[1] if ep.startswith("radius.") else ep
    parts = urlsplit(url)
    if parts.scheme and parts.scheme not in ("http", "https"):
        return None
    if parts.netloc and parts.netloc not in ("localhost", "hr.test"):
        return None
    path = parts.path
    if not path.startswith("/admin/radius/") or path.startswith(_SKIP_LINK_PREFIXES):
        return None
    if "{" in path or "__" in path:            # JS templates
        return None
    adapter = app.url_map.bind("localhost")
    try:
        ep, _args = adapter.match(path, method=method)
    except Exception:  # noqa: BLE001 — 404/405: not our concern here
        return None
    if not ep.startswith("radius."):
        return None
    return ep.split(".", 1)[1]


def violations(app, html: str, page_path: str, *, admin, perms):
    """Targets on the page the server guard would refuse for this manager."""
    from app.radius.routes.blueprint import (
        _GUARD_ALLOWLIST, _PUBLIC_ENDPOINTS, rbac_denial_status)
    bad = []
    seen = set()
    for method, url, kind in targets(html, page_path):
        name = resolve(app, method, url)
        if not name or ("radius." + name) in _PUBLIC_ENDPOINTS or name in _GUARD_ALLOWLIST:
            continue
        key = (method, name, kind)
        if key in seen:
            continue
        seen.add(key)
        code = rbac_denial_status(name, method, is_super=False, perms=list(perms),
                                  admin_id=admin.id, tenant_id=1,
                                  record_activity=False)
        if code is None:
            need = getattr(app.view_functions.get("radius." + name), "_hr_required_perms", None)
            if need:                      # mt_permissions.requires_perm decorator
                from app.radius.services.mt_permissions import admin_permissions
                held = set(admin_permissions(admin))
                if not all(p in held for p in need):
                    code = 403
        if code is not None:
            bad.append(f"{page_path}: {kind} {method} {url} → {name} ({code})")
            continue
        # a «new/edit» link opens a form — its SAVE must be allowed too
        if method == "GET":
            pair = (name[:-4] + "_create" if name.endswith("_new")
                    else name[:-5] + "_update" if name.endswith("_edit") else "")
            if pair and ("radius." + pair) in app.view_functions:
                code = rbac_denial_status(pair, "POST", is_super=False, perms=list(perms),
                                          admin_id=admin.id, tenant_id=1,
                                          record_activity=False)
                if code is not None:
                    bad.append(f"{page_path}: {kind} GET {url} → {name} opens a form whose "
                               f"save {pair} is refused ({code})")
    return bad
