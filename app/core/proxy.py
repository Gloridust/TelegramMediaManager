"""Control layer over the mihomo (Clash Meta) sidecar.

We do **not** reimplement a proxy engine. mihomo does the heavy lifting —
subscription parsing, protocol support (vless/vmess/trojan/hysteria/…), health
checks. This module just:

* generates a minimal mihomo config from the user's subscription URL, and
* proxies mihomo's REST API (list / select / latency-test nodes) to the panel.

Design note: this app wants *all* of its traffic through the tunnel, so the
generated ruleset is simply ``MATCH,PROXY`` — no Clash-style split routing. The
subscription's only job here is to supply an auto-updating node list.
"""

import asyncio
import os

import httpx
import yaml

from app.config import Keys, MihomoConfig, Paths
from app.core.i18n import tr
from app.core.sharelinks import looks_like_share_links, parse_share_links

PROXY_GROUP = "PROXY"
PROVIDER_NAME = "subscription"
# Path as seen *inside the mihomo container* (its config volume mount point).
MIHOMO_INTERNAL_CONFIG = os.getenv("TMM_MIHOMO_CONFIG_PATH", "/root/.config/mihomo/config.yaml")
HEALTH_URL = "https://www.gstatic.com/generate_204"


class ProxyManager:
    def __init__(self, store):
        self.store = store
        self._base = MihomoConfig.CONTROLLER.rstrip("/")
        self._secret = MihomoConfig.SECRET

    def _headers(self):
        return {"Authorization": f"Bearer {self._secret}"} if self._secret else {}

    # ------------------------------------------------------------------ #
    # Config generation
    # ------------------------------------------------------------------ #
    def _dns(self):
        """DNS for the sidecar. Without this, mihomo resolves proxy node server
        domains through the container's system resolver (Docker → host DNS),
        which under GFW poisoning gives wrong IPs → nodes time out even though
        they work in a desktop client. `proxy-server-nameserver` resolves node
        hostnames with clean DoH, matching what Clash Verge does by default."""
        doh = ["https://223.5.5.5/dns-query", "https://doh.pub/dns-query", "https://1.1.1.1/dns-query"]
        return {
            "enable": True,
            "ipv6": False,
            "default-nameserver": ["223.5.5.5", "119.29.29.29", "1.1.1.1"],
            "nameserver": doh,
            "proxy-server-nameserver": doh,
        }

    def _render_config(self, subscription_url):
        return {
            "mixed-port": MihomoConfig.PROXY_PORT,
            "allow-lan": True,
            "bind-address": "*",
            "mode": "rule",
            "log-level": "warning",
            "external-controller": "0.0.0.0:9090",
            "secret": self._secret,
            "dns": self._dns(),
            "proxy-providers": {
                PROVIDER_NAME: {
                    "type": "http",
                    "url": subscription_url,
                    "interval": 3600,
                    "path": f"./providers/{PROVIDER_NAME}.yaml",
                    "health-check": {
                        "enable": True,
                        "url": HEALTH_URL,
                        "interval": 300,
                    },
                }
            },
            "proxy-groups": [
                {
                    "name": PROXY_GROUP,
                    "type": "select",
                    "use": [PROVIDER_NAME],
                }
            ],
            "rules": [f"MATCH,{PROXY_GROUP}"],
        }

    def _render_default(self):
        """Minimal valid config so the sidecar can start before any subscription
        is configured. Everything goes direct until the user adds nodes."""
        return {
            "mixed-port": MihomoConfig.PROXY_PORT,
            "allow-lan": True,
            "bind-address": "*",
            "mode": "rule",
            "log-level": "warning",
            "external-controller": "0.0.0.0:9090",
            "secret": self._secret,
            "dns": self._dns(),
            "proxies": [],
            "proxy-groups": [{"name": PROXY_GROUP, "type": "select", "proxies": ["DIRECT"]}],
            "rules": [f"MATCH,DIRECT"],
        }

    def _render_config_with_proxies(self, proxies):
        """Config with explicit nodes parsed from share links."""
        cfg = self._render_default()
        cfg["proxies"] = proxies
        cfg["proxy-groups"] = [{"name": PROXY_GROUP, "type": "select",
                                "proxies": [p["name"] for p in proxies]}]
        cfg["rules"] = [f"MATCH,{PROXY_GROUP}"]
        return cfg

    def _render_from_source(self, source):
        """Build a config from either pasted share links or a subscription URL."""
        source = (source or "").strip()
        if source and looks_like_share_links(source):
            proxies = parse_share_links(source)
            if proxies:
                return self._render_config_with_proxies(proxies)
        return self._render_config(source) if source else self._render_default()

    async def ensure_config(self):
        """Write an initial config on first boot if none exists, so the mihomo
        container has something valid to start from. Regenerated from the stored
        subscription / links if one was already saved."""
        Paths.ensure()
        cfg_path = os.path.join(Paths.MIHOMO_DIR, "config.yaml")
        if os.path.exists(cfg_path):
            return
        sub = await self.store.get_setting(Keys.SUBSCRIPTION_URL)
        try:
            with open(cfg_path, "w", encoding="utf-8") as fh:
                yaml.safe_dump(self._render_from_source(sub), fh, allow_unicode=True, sort_keys=False)
        except OSError as e:
            print(f"Could not write initial mihomo config: {e}")

    async def apply_subscription(self, source):
        """Configure mihomo from an airport subscription URL *or* one/more pasted
        share links (vless/vmess/trojan/ss), then reload. Returns (ok, message)."""
        Paths.ensure()
        source = source.strip()
        if looks_like_share_links(source) and not parse_share_links(source):
            return False, await tr(self.store, "no_valid_nodes")
        cfg_path = os.path.join(Paths.MIHOMO_DIR, "config.yaml")
        try:
            with open(cfg_path, "w", encoding="utf-8") as fh:
                yaml.safe_dump(self._render_from_source(source), fh, allow_unicode=True, sort_keys=False)
        except OSError as e:
            return False, await tr(self.store, "mihomo_write_failed", e=e)
        await self.store.set_setting(Keys.SUBSCRIPTION_URL, source)
        return await self.reload()

    # ------------------------------------------------------------------ #
    # mihomo REST API
    # ------------------------------------------------------------------ #
    async def _get(self, path, retries=4, delay=0.5):
        """GET a mihomo endpoint, retrying transient 503s (mihomo returns 503
        while it is reloading a config or fetching a provider) and connection
        blips. Returns (status_code, json|None)."""
        last = (0, None)
        for _ in range(retries):
            try:
                async with httpx.AsyncClient(timeout=10) as c:
                    r = await c.get(f"{self._base}{path}", headers=self._headers())
                if r.status_code == 200:
                    return 200, r.json()
                last = (r.status_code, None)
                if r.status_code != 503:
                    return last
            except Exception:
                last = (0, None)
            await asyncio.sleep(delay)
        return last

    async def reload(self):
        try:
            async with httpx.AsyncClient(timeout=20) as c:
                r = await c.put(f"{self._base}/configs", params={"force": "true"},
                                json={"path": MIHOMO_INTERNAL_CONFIG}, headers=self._headers())
            if r.status_code in (200, 204):
                # Give mihomo a beat to finish applying before callers query it.
                await asyncio.sleep(0.6)
                return True, await tr(self.store, "proxy_reloaded")
            return False, await tr(self.store, "mihomo_reject", code=r.status_code)
        except Exception as e:
            return False, await tr(self.store, "mihomo_unreachable", e=e)

    async def is_reachable(self):
        try:
            async with httpx.AsyncClient(timeout=5) as c:
                r = await c.get(f"{self._base}/version", headers=self._headers())
            return r.status_code == 200
        except Exception:
            return False

    async def list_nodes(self):
        """Return {selected, nodes:[...]} for the PROXY group, or an error.
        Retries transient 503s so a mid-reload query doesn't surface an error."""
        status, data = await self._get("/proxies")
        if status != 200 or data is None:
            if status == 503:
                return {"ok": False, "error": await tr(self.store, "proxy_loading")}
            return {"ok": False, "error": await tr(self.store, "cannot_get_nodes")
                    if status == 0 else f"HTTP {status}"}
        group = data.get("proxies", {}).get(PROXY_GROUP)
        if not group:
            return {"ok": True, "selected": None, "nodes": []}
        nodes = [n for n in group.get("all", []) if n not in ("DIRECT", "REJECT")]
        return {"ok": True, "selected": group.get("now"), "nodes": nodes}

    async def select_node(self, name):
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.put(f"{self._base}/proxies/{PROXY_GROUP}",
                                json={"name": name}, headers=self._headers())
            if r.status_code in (200, 204):
                await self.store.set_setting(Keys.PROXY_SELECTED, name)
                return {"ok": True}
            return {"ok": False, "error": f"HTTP {r.status_code}"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    async def test_node(self, name):
        try:
            # Top-level proxies (from pasted share links) test directly.
            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.get(f"{self._base}/proxies/{name}/delay",
                                params={"timeout": 5000, "url": HEALTH_URL},
                                headers=self._headers())
            if r.status_code == 200:
                return {"ok": True, "delay": r.json().get("delay")}
            # Subscription nodes live inside a provider and 404 here — test the
            # whole group and pick this node's result. (This 404, mislabelled as a
            # timeout, is why subscription nodes looked broken while direct links
            # worked, even though the node itself is fine.)
            async with httpx.AsyncClient(timeout=25) as c:
                r2 = await c.get(f"{self._base}/group/{PROXY_GROUP}/delay",
                                 params={"timeout": 5000, "url": HEALTH_URL},
                                 headers=self._headers())
            if r2.status_code == 200:
                delay = (r2.json() or {}).get(name)
                if isinstance(delay, int) and delay > 0:
                    return {"ok": True, "delay": delay}
            return {"ok": False, "error": await tr(self.store, "node_timeout")}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    async def refresh_subscription(self):
        try:
            async with httpx.AsyncClient(timeout=30) as c:
                r = await c.put(f"{self._base}/providers/proxies/{PROVIDER_NAME}",
                                headers=self._headers())
            if r.status_code in (200, 204):
                return {"ok": True}
            # mihomo reports an upstream fetch failure (e.g. the airport server
            # returning 403 when rate-limiting frequent re-fetches) as a 503 with
            # a {"message": "..."} body. Surface that real reason, not "HTTP 503".
            detail = ""
            try:
                detail = (r.json() or {}).get("message", "")
            except Exception:
                pass
            return {"ok": False,
                    "error": await tr(self.store, "refresh_failed_detail",
                                      detail=detail or f"HTTP {r.status_code}")}
        except Exception as e:
            return {"ok": False, "error": await tr(self.store, "mihomo_unreachable", e=e)}
