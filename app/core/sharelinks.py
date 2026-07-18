"""Parse proxy share links (vless / vmess / trojan / ss) into mihomo proxy dicts.

This lets the panel accept single (or multiple, one-per-line) node links in
addition to airport subscription URLs. Subscriptions remain the primary path;
this only kicks in when the input clearly starts with a proxy scheme, so it can
never interfere with a normal subscription URL.

Covers the common options (tls, reality, ws, grpc). Exotic transports may need a
subscription instead.
"""

import base64
import json
from urllib.parse import parse_qs, unquote, urlparse

SCHEMES = ("vless://", "vmess://", "trojan://", "ss://")


def looks_like_share_links(text: str) -> bool:
    """True if the first non-empty line is a supported share link (not a URL)."""
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        return line.lower().startswith(SCHEMES)
    return False


def _b64(s: str) -> bytes:
    s = s.strip().replace("-", "+").replace("_", "/")
    s += "=" * (-len(s) % 4)
    return base64.b64decode(s)


def parse_share_links(text: str) -> list[dict]:
    """Parse every share link in `text`; skip lines that fail. Names are deduped."""
    proxies, seen = [], set()
    for i, line in enumerate(text.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            p = _parse_one(line, i)
        except Exception:
            p = None
        if not p or not p.get("server"):
            continue
        name = p["name"]
        base, k = name, 1
        while name in seen:
            name = f"{base}-{k}"
            k += 1
        p["name"] = name
        seen.add(name)
        proxies.append(p)
    return proxies


def _parse_one(uri: str, idx: int):
    scheme = uri.split("://", 1)[0].lower()
    return {
        "vless": _vless, "vmess": _vmess, "trojan": _trojan, "ss": _ss,
    }.get(scheme, lambda *_: None)(uri, idx)


def _vless(uri, idx):
    u = urlparse(uri)
    q = parse_qs(u.query)
    g = lambda k, d=None: q.get(k, [d])[0]
    net = g("type", "tcp")
    p = {
        "name": unquote(u.fragment) or f"vless-{idx + 1}",
        "type": "vless", "server": u.hostname, "port": int(u.port or 443),
        "uuid": u.username, "network": net, "udp": True,
    }
    sec = g("security", "none")
    if sec in ("tls", "reality", "xtls"):
        p["tls"] = True
        if g("sni") or g("peer"):
            p["servername"] = g("sni") or g("peer")
        if g("fp"):
            p["client-fingerprint"] = g("fp")
        if g("alpn"):
            p["alpn"] = g("alpn").split(",")
    if sec == "reality":
        ro = {}
        if g("pbk"):
            ro["public-key"] = g("pbk")
        if g("sid"):
            ro["short-id"] = g("sid")
        p["reality-opts"] = ro
    if g("flow"):
        p["flow"] = g("flow")
    if net == "ws":
        ws = {"path": g("path", "/"), "headers": {}}
        if g("host"):
            ws["headers"]["Host"] = g("host")
        p["ws-opts"] = ws
    elif net == "grpc":
        p["grpc-opts"] = {"grpc-service-name": g("serviceName", "")}
    return p


def _vmess(uri, idx):
    data = json.loads(_b64(uri[len("vmess://"):]).decode("utf-8", "replace"))
    net = data.get("net") or "tcp"
    p = {
        "name": data.get("ps") or f"vmess-{idx + 1}",
        "type": "vmess", "server": data.get("add"), "port": int(data.get("port") or 443),
        "uuid": data.get("id"), "alterId": int(data.get("aid") or 0),
        "cipher": data.get("scy") or "auto", "network": net, "udp": True,
    }
    if str(data.get("tls")).lower() == "tls":
        p["tls"] = True
        if data.get("sni") or data.get("host"):
            p["servername"] = data.get("sni") or data.get("host")
    if net == "ws":
        ws = {"path": data.get("path") or "/", "headers": {}}
        if data.get("host"):
            ws["headers"]["Host"] = data.get("host")
        p["ws-opts"] = ws
    elif net == "grpc":
        p["grpc-opts"] = {"grpc-service-name": data.get("path") or ""}
    return p


def _trojan(uri, idx):
    u = urlparse(uri)
    q = parse_qs(u.query)
    g = lambda k, d=None: q.get(k, [d])[0]
    p = {
        "name": unquote(u.fragment) or f"trojan-{idx + 1}",
        "type": "trojan", "server": u.hostname, "port": int(u.port or 443),
        "password": unquote(u.username or ""), "udp": True,
    }
    if g("sni") or g("peer"):
        p["sni"] = g("sni") or g("peer")
    net = g("type")
    if net == "ws":
        p["network"] = "ws"
        ws = {"path": g("path", "/"), "headers": {}}
        if g("host"):
            ws["headers"]["Host"] = g("host")
        p["ws-opts"] = ws
    elif net == "grpc":
        p["network"] = "grpc"
        p["grpc-opts"] = {"grpc-service-name": g("serviceName", "")}
    if g("allowInsecure") in ("1", "true"):
        p["skip-cert-verify"] = True
    return p


def _ss(uri, idx):
    body = uri[len("ss://"):]
    frag = ""
    if "#" in body:
        body, frag = body.split("#", 1)
    name = unquote(frag) or f"ss-{idx + 1}"
    if "@" in body:  # SIP002: base64(method:pass)@host:port
        userinfo, hostport = body.rsplit("@", 1)
        try:
            method, password = _b64(userinfo).decode().split(":", 1)
        except Exception:
            method, password = unquote(userinfo).split(":", 1)
    else:  # legacy: base64(method:pass@host:port)
        dec = _b64(body).decode()
        userinfo, hostport = dec.rsplit("@", 1)
        method, password = userinfo.split(":", 1)
    host, port = hostport.split("?", 1)[0].split("/", 1)[0].rsplit(":", 1)
    return {
        "name": name, "type": "ss", "server": host, "port": int(port),
        "cipher": method, "password": password, "udp": True,
    }
