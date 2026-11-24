"""Zero-config cloud persistence for played / disliked / wishlist logs via a dedicated GitHub branch."""
from __future__ import annotations

import base64
import json
import os
import threading
import time
from typing import Any

import httpx

DEFAULT_STATE_REPO = "Wattson-Law/What-To-play-Tonight"
DEFAULT_STATE_BRANCH = "bot-state"
DEFAULT_STATE_PATH = "state/user_log.json"


def _clean_record(rec: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(rec, dict):
        return None
    raw_id = rec.get("appid")
    if raw_id is None or not str(raw_id).strip().isdigit():
        return None
    status = str(rec.get("status") or "").strip()
    if status not in {"played", "avoided", "wishlist"}:
        return None
    return {
        "appid": int(raw_id),
        "status": status,
        "name": str(rec.get("name") or "").strip(),
        "note": str(rec.get("note") or "").strip(),
        "updated_at": float(rec.get("updated_at") or 0.0),
    }


def normalize_state(raw: Any) -> dict[str, Any]:
    """Normalize legacy {'records': [...]} or v2 {'version': 2, 'scopes': {...}} into canonical v2."""
    out: dict[str, Any] = {"version": 2, "scopes": {}}
    if not isinstance(raw, dict):
        return out

    raw_scopes = raw.get("scopes")
    if isinstance(raw_scopes, dict):
        for scope_name, bucket in raw_scopes.items():
            if not isinstance(bucket, dict):
                continue
            by_id: dict[int, dict[str, Any]] = {}
            for item in bucket.get("records") or []:
                cleaned = _clean_record(item)
                if cleaned is not None:
                    prev = by_id.get(cleaned["appid"])
                    if prev is None or cleaned["updated_at"] >= prev["updated_at"]:
                        by_id[cleaned["appid"]] = cleaned
            deleted: dict[str, float] = {}
            for k, v in (bucket.get("deleted") or {}).items():
                if str(k).isdigit():
                    try:
                        deleted[str(int(k))] = float(v)
                    except (TypeError, ValueError):
                        pass
            out["scopes"][str(scope_name)] = {
                "records": sorted(by_id.values(), key=lambda x: (x["updated_at"], x["appid"])),
                "deleted": deleted,
            }

    # Back-compat with legacy top-level "records" when "default" scope is absent
    legacy_records = raw.get("records")
    if isinstance(legacy_records, list) and legacy_records and "default" not in out["scopes"]:
        by_id = {}
        for item in legacy_records:
            cleaned = _clean_record(item)
            if cleaned is not None:
                prev = by_id.get(cleaned["appid"])
                if prev is None or cleaned["updated_at"] >= prev["updated_at"]:
                    by_id[cleaned["appid"]] = cleaned
        if by_id:
            out["scopes"]["default"] = {
                "records": sorted(by_id.values(), key=lambda x: (x["updated_at"], x["appid"])),
                "deleted": {},
            }

    return out


def merge_state_payloads(remote_raw: Any, local_raw: Any) -> dict[str, Any]:
    """Merge two v2 state payloads without losing concurrent updates or resurrecting deletions."""
    remote = normalize_state(remote_raw)
    local = normalize_state(local_raw)
    all_scopes = set(remote["scopes"].keys()) | set(local["scopes"].keys())
    merged_scopes: dict[str, Any] = {}

    for scope in sorted(all_scopes):
        r_bucket = remote["scopes"].get(scope) or {"records": [], "deleted": {}}
        l_bucket = local["scopes"].get(scope) or {"records": [], "deleted": {}}

        deleted: dict[str, float] = {}
        for src_del in (r_bucket.get("deleted") or {}, l_bucket.get("deleted") or {}):
            for k, ts in src_del.items():
                try:
                    ts_f = float(ts)
                except (TypeError, ValueError):
                    continue
                deleted[str(k)] = max(deleted.get(str(k), 0.0), ts_f)

        by_id: dict[int, dict[str, Any]] = {}
        for rec in r_bucket.get("records") or []:
            by_id[int(rec["appid"])] = dict(rec)
        for rec in l_bucket.get("records") or []:
            aid = int(rec["appid"])
            prev = by_id.get(aid)
            if prev is None or float(rec.get("updated_at") or 0.0) >= float(prev.get("updated_at") or 0.0):
                by_id[aid] = dict(rec)

        final_records = [
            rec
            for aid, rec in by_id.items()
            if deleted.get(str(aid), 0.0) <= float(rec.get("updated_at") or 0.0)
        ]
        final_records.sort(key=lambda x: (float(x.get("updated_at") or 0.0), int(x["appid"])))
        merged_scopes[scope] = {"records": final_records, "deleted": deleted}

    return {"version": 2, "scopes": merged_scopes}


class GitHubStateStore:
    """Persists JSON state to a dedicated orphan GitHub branch via REST API (zero external DB)."""

    def __init__(
        self,
        repo: str = DEFAULT_STATE_REPO,
        token: str = "",
        branch: str = DEFAULT_STATE_BRANCH,
        path: str = DEFAULT_STATE_PATH,
        timeout: float = 10.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.repo = repo.strip()
        self.token = token.strip()
        self.branch = branch.strip() or DEFAULT_STATE_BRANCH
        self.path = path.strip().lstrip("/") or DEFAULT_STATE_PATH
        self.timeout = timeout
        self.transport = transport
        self._sha: str | None = None
        self._lock = threading.Lock()

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "coop-night-picker-state-store",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _api(self, suffix: str) -> str:
        return f"https://api.github.com/repos/{self.repo}/{suffix.lstrip('/')}"

    def _client(self) -> httpx.Client:
        return httpx.Client(headers=self._headers(), timeout=self.timeout, transport=self.transport)

    def _pull_unlocked(self, client: httpx.Client) -> dict[str, Any] | None:
        try:
            resp = client.get(self._api(f"contents/{self.path}"), params={"ref": self.branch})
            if resp.status_code != 200:
                if resp.status_code == 404:
                    self._sha = None
                return None
            payload = resp.json()
            self._sha = payload.get("sha")
            raw_b64 = str(payload.get("content") or "")
            decoded = base64.b64decode(raw_b64).decode("utf-8")
            return normalize_state(json.loads(decoded))
        except Exception:
            return None

    def pull(self) -> dict[str, Any] | None:
        if not self.repo:
            return None
        with self._lock:
            with self._client() as client:
                return self._pull_unlocked(client)

    def _ensure_branch_unlocked(self, client: httpx.Client) -> bool:
        try:
            r = client.get(self._api(f"git/ref/heads/{self.branch}"))
            if r.status_code == 200:
                return True
            if r.status_code != 404:
                return False
            # Bootstrap a clean orphan branch containing initial empty state
            init_content = json.dumps({"version": 2, "scopes": {}}, ensure_ascii=False, indent=2) + "\n"
            blob_r = client.post(
                self._api("git/blobs"),
                json={"content": init_content, "encoding": "utf-8"},
            )
            if blob_r.status_code not in {200, 201}:
                return False
            blob_sha = blob_r.json().get("sha")
            tree_r = client.post(
                self._api("git/trees"),
                json={"tree": [{"path": self.path, "mode": "100644", "type": "blob", "sha": blob_sha}]},
            )
            if tree_r.status_code not in {200, 201}:
                return False
            tree_sha = tree_r.json().get("sha")
            commit_r = client.post(
                self._api("git/commits"),
                json={"message": "chore(state): initialize bot-state branch", "tree": tree_sha, "parents": []},
            )
            if commit_r.status_code not in {200, 201}:
                return False
            commit_sha = commit_r.json().get("sha")
            ref_r = client.post(
                self._api("git/refs"),
                json={"ref": f"refs/heads/{self.branch}", "sha": commit_sha},
            )
            if ref_r.status_code in {200, 201}:
                self._sha = blob_sha
                return True
            return ref_r.status_code == 422
        except Exception:
            return False

    def push(self, state: dict[str, Any], message: str = "chore(state): sync play log") -> bool:
        if not self.repo or not self.token:
            return False
        with self._lock:
            with self._client() as client:
                if not self._ensure_branch_unlocked(client):
                    return False

                to_write = normalize_state(state)
                if not self._sha:
                    remote = self._pull_unlocked(client)
                    if remote is not None:
                        to_write = merge_state_payloads(remote, to_write)

                for attempt in range(2):
                    body_text = json.dumps(to_write, ensure_ascii=False, indent=2) + "\n"
                    req_body: dict[str, Any] = {
                        "message": message,
                        "content": base64.b64encode(body_text.encode("utf-8")).decode("ascii"),
                        "branch": self.branch,
                    }
                    if self._sha:
                        req_body["sha"] = self._sha
                    try:
                        resp = client.put(self._api(f"contents/{self.path}"), json=req_body)
                    except Exception:
                        return False
                    if resp.status_code in {200, 201}:
                        new_sha = ((resp.json() or {}).get("content") or {}).get("sha")
                        if new_sha:
                            self._sha = new_sha
                        return True
                    if resp.status_code in {409, 422} and attempt == 0:
                        remote = self._pull_unlocked(client)
                        if remote is not None:
                            to_write = merge_state_payloads(remote, to_write)
                        continue
                    return False
                return False


_DEFAULT_STORE: GitHubStateStore | None = None


def get_default_cloud_store() -> GitHubStateStore | None:
    """Return a configured GitHubStateStore when running in Actions or explicitly opted-in."""
    global _DEFAULT_STORE
    if os.getenv("COOP_NIGHT_PICKER_DISABLE_CLOUD") == "1":
        return None
    enabled = (
        os.getenv("GITHUB_ACTIONS") == "true"
        or os.getenv("COOP_NIGHT_PICKER_ENABLE_CLOUD_STATE") == "1"
        or bool(os.getenv("STATE_SYNC_TOKEN"))
    )
    if not enabled:
        return None
    token = (os.getenv("STATE_SYNC_TOKEN") or os.getenv("GITHUB_TOKEN") or "").strip()
    if not token:
        return None
    repo = (os.getenv("STATE_SYNC_REPO") or os.getenv("GITHUB_REPOSITORY") or DEFAULT_STATE_REPO).strip()
    branch = (os.getenv("STATE_SYNC_BRANCH") or DEFAULT_STATE_BRANCH).strip()
    path = (os.getenv("STATE_SYNC_PATH") or DEFAULT_STATE_PATH).strip()
    if _DEFAULT_STORE is None or _DEFAULT_STORE.repo != repo or _DEFAULT_STORE.token != token or _DEFAULT_STORE.branch != branch:
        _DEFAULT_STORE = GitHubStateStore(repo=repo, token=token, branch=branch, path=path)
    return _DEFAULT_STORE
