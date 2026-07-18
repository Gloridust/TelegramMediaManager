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

import os

import httpx
import yaml

from app.config import Keys, MihomoConfig, Paths
from app.core.i18n import tr

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
    def _render_config(self, subscription_url):
        return {
            "mixed-port": MihomoConfig.PROXY_PORT,
            "allow-lan": True,
            "bind-address": "*",
            "mode": "rule",
            "log-level": "warning",
            "external-controller": "0.0.0.0:9090",
            "secret": self._secret,
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
            "proxies": [],
            "proxy-groups": [{"name": PROXY_GROUP, "type": "select", "proxies": ["DIRECT"]}],
            "rules": [f"MATCH,DIRECT"],
        }

    async def ensure_config(self):
        """Write an initial config on first boot if none exists, so the mihomo
        container has something valid to start from. Regenerated from the stored
        subscription if one was already saved."""
        Paths.ensure()
        cfg_path = os.path.join(Paths.MIHOMO_DIR, "config.yaml")
        if os.path.exists(cfg_path):
            return
        sub = await self.store.get_setting(Keys.SUBSCRIPTION_URL)
        cfg = self._render_config(sub) if sub else self._render_default()
        try:
            with open(cfg_path, "w", encoding="utf-8") as fh:
                yaml.safe_dump(cfg, fh, allow_unicode=True, sort_keys=False)
        except OSError as e:
            print(f"Could not write initial mihomo config: {e}")

    async def apply_subscription(self, subscription_url):
        """Write a fresh mihomo config from the subscription and reload it.
        Returns (ok, message)."""
        Paths.ensure()
        cfg = self._render_config(subscription_url)
        cfg_path = os.path.join(Paths.MIHOMO_DIR, "config.yaml")
        try:
            with open(cfg_path, "w", encoding="utf-8") as fh:
                yaml.safe_dump(cfg, fh, allow_unicode=True, sort_keys=False)
        except OSError as e:
            return False, await tr(self.store, "mihomo_write_failed", e=e)
        await self.store.set_setting(Keys.SUBSCRIPTION_URL, subscription_url)
        ok, msg = await self.reload()
        return ok, msg

    # ------------------------------------------------------------------ #
    # mihomo REST API
    # ------------------------------------------------------------------ #
    async def reload(self):
        try:
            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.put(f"{self._base}/configs", params={"force": "true"},
                                json={"path": MIHOMO_INTERNAL_CONFIG}, headers=self._headers())
            if r.status_code in (200, 204):
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
        """Return {selected, nodes:[...]} for the PROXY group, or an error."""
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.get(f"{self._base}/proxies", headers=self._headers())
            if r.status_code != 200:
                return {"ok": False, "error": f"HTTP {r.status_code}"}
            proxies = r.json().get("proxies", {})
            group = proxies.get(PROXY_GROUP)
            if not group:
                return {"ok": True, "selected": None, "nodes": []}
            nodes = [n for n in group.get("all", []) if n not in ("DIRECT", "REJECT")]
            return {"ok": True, "selected": group.get("now"), "nodes": nodes}
        except Exception as e:
            return {"ok": False, "error": str(e)}

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
            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.get(f"{self._base}/proxies/{name}/delay",
                                params={"timeout": 5000, "url": HEALTH_URL},
                                headers=self._headers())
            if r.status_code == 200:
                return {"ok": True, "delay": r.json().get("delay")}
            return {"ok": False, "error": "超时或不可用"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    async def refresh_subscription(self):
        try:
            async with httpx.AsyncClient(timeout=30) as c:
                r = await c.put(f"{self._base}/providers/proxies/{PROVIDER_NAME}",
                                headers=self._headers())
            if r.status_code in (200, 204):
                return {"ok": True}
            return {"ok": False, "error": f"HTTP {r.status_code}"}
        except Exception as e:
            return {"ok": False, "error": str(e)}
