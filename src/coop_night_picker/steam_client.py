from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path

try:
    import httpx
except ImportError:
    httpx = None


class SteamClient:
    def __init__(self, hosts=None, cache_dir="data/http-cache", proxy=None, throttle=1.0, retries=1, transport=None):
        self.hosts = hosts or (
            [os.getenv("STEAM_HOST")]
            if os.getenv("STEAM_HOST")
            else ["https://store.steampowered.com", "https://steamstore-a.akamaihd.net"]
        )
        self.cache = Path(os.getenv("STEAM_CACHE_DIR", cache_dir))
        self.cache.mkdir(parents=True, exist_ok=True)
        self.proxy = proxy or os.getenv("STEAM_PROXY")
        self.throttle = throttle
        self.retries = retries
        self.transport = transport
        self._last = 0.0

    def _request(self, path, cookies=None, **params):
        cp = self.cache / (hashlib.sha256((path + json.dumps(params, sort_keys=True)).encode()).hexdigest() + ".json")
        if cp.exists():
            x = json.loads(cp.read_text(encoding="utf8"))
            return x.get("__text__", "") if isinstance(x, dict) and "__text__" in x else x
        if httpx is None:
            return {"ok": False, "unavailable": True}
        for host in self.hosts:
            for attempt in range(self.retries + 1):
                try:
                    time.sleep(max(0.0, self.throttle - (time.time() - self._last)))
                    self._last = time.time()
                    with httpx.Client(
                        proxy=self.proxy,
                        follow_redirects=False,
                        timeout=20,
                        headers={"User-Agent": "Mozilla/5.0"},
                        cookies=cookies,
                        transport=self.transport,
                    ) as c:
                        r = c.get(host.rstrip("/") + "/" + path.lstrip("/"), params=params)
                    if r.status_code in {301, 302, 303, 307, 308}:
                        break
                    if 200 <= r.status_code < 300:
                        try:
                            d = r.json()
                        except Exception:
                            d = r.text
                        cp.write_text(
                            json.dumps(d, ensure_ascii=False)
                            if not isinstance(d, str)
                            else json.dumps({"__text__": d}, ensure_ascii=False),
                            encoding="utf8",
                        )
                        return d
                except Exception:
                    pass
                if attempt < self.retries:
                    time.sleep(min(2**attempt, 8))
        return {"ok": False, "unavailable": True}

    def appdetails(self, appid):
        return self._request("api/appdetails", appids=appid, cc="cn", l="schinese")

    def store_search(self, query, **params):
        return self._request("search/results", term=query, **params)

    search_results = store_search

    def store_page(self, appid):
        age_cookies = {"birthtime": "568022401", "wants_mature_content": "1", "lastagecheckage": "1-0-1988"}
        return self._request(f"app/{appid}/", cookies=age_cookies, l="schinese", cc="cn")

    def appreviews(self, appid, review_type=None, playtime_filter_min=1):
        p = {
            "json": 1,
            "language": "schinese",
            "filter": "all",
            "purchase_type": "all",
            "num_per_page": 50,
            "playtime_filter_min": playtime_filter_min,
        }
        if review_type:
            p["review_type"] = review_type
        return self._request(f"appreviews/{appid}", **p)

    def tagdata(self, appid=None, lang="schinese"):
        return self._request(f"tagdata/populartags/{lang}")

    def curator_recommendations(self, clanid: int, tagids: str = "3843", start: int = 0, count: int = 50):
        params = {"start": start, "count": count, "l": "schinese", "cc": "cn"}
        if tagids:
            params["tagids"] = tagids
        return self._request(f"curator/{int(clanid)}/ajaxgetfilteredrecommendations/", **params)

    def top_curators(self, start: int = 0, count: int = 20):
        return self._request("curators/ajaxgetcurators/", start=start, count=count, l="schinese", cc="cn")

    def _direct_get(
        self,
        url: str,
        params: dict | None = None,
        *,
        cache_key: str | None = None,
        timeout: float = 12.0,
        follow_redirects: bool = False,
    ):
        key_src = cache_key or (url + json.dumps(params or {}, sort_keys=True))
        cp = self.cache / (hashlib.sha256(key_src.encode()).hexdigest() + ".json")
        if cp.exists():
            x = json.loads(cp.read_text(encoding="utf8"))
            return x.get("__text__", "") if isinstance(x, dict) and "__text__" in x else x
        if httpx is None:
            return None
        for attempt in range(self.retries + 1):
            try:
                sleep_s = max(0.0, min(self.throttle, 0.5) - (time.time() - self._last))
                if sleep_s > 0:
                    time.sleep(sleep_s)
                self._last = time.time()
                with httpx.Client(
                    proxy=self.proxy,
                    follow_redirects=follow_redirects,
                    timeout=timeout,
                    headers={"User-Agent": "Mozilla/5.0"},
                    transport=self.transport,
                ) as c:
                    r = c.get(url, params=params)
                if 200 <= r.status_code < 300:
                    try:
                        d = r.json()
                    except Exception:
                        d = r.text
                    cp.write_text(
                        json.dumps(d, ensure_ascii=False)
                        if not isinstance(d, str)
                        else json.dumps({"__text__": d}, ensure_ascii=False),
                        encoding="utf8",
                    )
                    return d
            except Exception:
                pass
            if attempt < self.retries:
                time.sleep(min(2**attempt, 4))
        return None

    def current_players(self, appid: int) -> int | None:
        """Fetch live Steam concurrent players (zero API key required)."""
        data = self._direct_get(
            "https://api.steampowered.com/ISteamUserStats/GetNumberOfCurrentPlayers/v1/",
            params={"appid": int(appid)},
            cache_key=f"ccu:{int(appid)}",
        )
        if isinstance(data, dict):
            resp = data.get("response") or {}
            if resp.get("result") == 1 and resp.get("player_count") is not None:
                try:
                    return int(resp["player_count"])
                except (TypeError, ValueError):
                    return None
        return None

    def community_followers(self, appid: int) -> int | None:
        """Fetch Steam Community Hub member/follower count (industry proxy for wishlists ~7-12x)."""
        raw = self._direct_get(
            f"https://steamcommunity.com/games/{int(appid)}/memberslistxml/",
            params={"xml": 1},
            cache_key=f"followers:{int(appid)}",
            follow_redirects=True,
        )
        if isinstance(raw, str):
            m = re.search(r"<memberCount>(\d+)</memberCount>", raw)
            if m:
                return int(m.group(1))
        return None

    def app_news(self, appid: int, count: int = 5) -> list[dict]:
        """Fetch recent developer announcements / patch notes / devlogs for an app."""
        data = self._direct_get(
            "https://api.steampowered.com/ISteamNews/GetNewsForApp/v2/",
            params={"appid": int(appid), "count": int(count)},
            cache_key=f"news:{int(appid)}:{int(count)}",
        )
        if isinstance(data, dict):
            items = (data.get("appnews") or {}).get("newsitems") or []
            if isinstance(items, list):
                return [x for x in items if isinstance(x, dict)]
        return []
