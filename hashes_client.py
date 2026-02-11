from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable, Iterable

import requests


# Delay between left-list downloads to avoid rate limiting (seconds)
DOWNLOAD_DELAY_BETWEEN_REQUESTS = 0.4


class HashesApiError(Exception):
    pass


class HashesClient:
    BASE_URL = "https://hashes.com"
    BASE_API_URL = "https://hashes.com/en/api"
    # Left-list downloads use HTTP (matches site behavior; HTTPS may 404)
    DOWNLOAD_BASE_URL = "http://hashes.com"

    def __init__(self, api_key: str = "", timeout: int = 20) -> None:
        self.api_key = api_key.strip()
        self.timeout = timeout
        self.session = requests.Session()
        self._conversion_cache: dict[str, Any] | None = None
        self._conversion_cache_at = 0.0

    def set_api_key(self, api_key: str) -> None:
        self.api_key = api_key.strip()

    def get_algorithms(self) -> dict[str, str]:
        payload = self._request_json("/algorithms")
        algorithms: dict[str, str] = {}
        for item in payload.get("list", []):
            algorithms[str(item["id"])] = str(item["algorithmName"])
        return algorithms

    def fetch_and_update_algorithms_file(self, file_path: Path) -> tuple[bool, dict[str, str]]:
        try:
            algorithms = self.get_algorithms()
            if not algorithms:
                return False, {}
            sorted_algs = dict(
                sorted(algorithms.items(), key=lambda x: (int(x[0]) if x[0].isdigit() else 999999, x[0]))
            )
            content = "validalgs = " + json.dumps(sorted_algs, indent=4) + "\n"
            file_path.write_text(content, encoding="utf-8")
            return True, algorithms
        except (HashesApiError, OSError, requests.RequestException):
            return False, {}

    def get_jobs(
        self,
        *,
        sortby: str = "createdAt",
        reverse: bool = True,
        currency_filter: set[str] | None = None,
        algorithm_filter: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        payload = self._request_json("/jobs", requires_api_key=True)
        jobs = payload.get("list", [])
        if currency_filter:
            jobs = [
                job
                for job in jobs
                if str(job.get("currency", "")).upper() in currency_filter
            ]
        if algorithm_filter:
            jobs = [
                job
                for job in jobs
                if str(job.get("algorithmId", "")) in algorithm_filter
            ]
        return sorted(jobs, key=lambda row: self._sort_value(row.get(sortby)), reverse=reverse)

    def get_balance(self) -> dict[str, str]:
        payload = self._request_json("/balance", requires_api_key=True)
        return {k: str(v) for k, v in payload.items() if k != "success"}

    def identify_hash(self, hash_value: str, extended: bool = False) -> list[str]:
        params = {"hash": hash_value, "extended": str(bool(extended)).lower()}
        payload = self._request_json("/identifier", params=params)
        return [str(row) for row in payload.get("algorithms", [])]

    def lookup_hashes(self, hashes: Iterable[str]) -> dict[str, Any]:
        hash_list = [h.strip() for h in hashes if h and h.strip()]
        if not hash_list:
            raise HashesApiError("Please provide at least one hash to search.")
        if len(hash_list) > 250:
            raise HashesApiError("The API allows up to 250 hashes per lookup request.")
        payload = self._request_json(
            "/search",
            method="POST",
            data={"hashes[]": hash_list},
            requires_api_key=True,
        )
        return payload

    def convert_to_usd(self, value: float | str, currency: str) -> str:
        if currency.lower() == "credits":
            return "N/A"
        rates = self.get_conversion_rates()
        if currency.upper() not in rates:
            return "$0.00"
        usd = float(value) * float(rates[currency.upper()])
        return f"${usd:.3f}"

    def get_conversion_rates(self, *, cache_seconds: int = 60) -> dict[str, Any]:
        now = time.time()
        if self._conversion_cache and (now - self._conversion_cache_at) < cache_seconds:
            return self._conversion_cache
        payload = self._request_json("/conversion")
        self._conversion_cache = payload
        self._conversion_cache_at = now
        return payload

    def download_left_lists(
        self,
        jobs: list[dict[str, Any]],
        destination: str | Path,
        *,
        on_progress: Callable[[int, int, int, int, dict[str, Any]], None] | None = None,
    ) -> tuple[int, list[tuple[int, str]]]:
        if not jobs:
            raise HashesApiError("No jobs were selected for download.")
        output = Path(destination)
        bytes_written = 0
        failed: list[tuple[int, str]] = []
        append = False
        for index, job in enumerate(jobs, start=1):
            url_path = str(job.get("leftList", ""))
            if not url_path:
                failed.append((int(job.get("id", 0)), "No left list URL"))
                continue
            if index > 1:
                time.sleep(DOWNLOAD_DELAY_BETWEEN_REQUESTS)
            try:
                n = self._stream_download(
                    url_path,
                    output,
                    append=append,
                    progress_cb=lambda d, t, j=job, i=index: (
                        on_progress(i, len(jobs), d, t, j) if on_progress else None
                    ),
                )
                bytes_written += n
                append = True
            except (HashesApiError, OSError, requests.RequestException) as exc:
                failed.append((int(job.get("id", 0)), str(exc)))
        return bytes_written, failed

    def _stream_download(
        self,
        url_path: str,
        destination: Path,
        *,
        append: bool,
        progress_cb: Callable[[int, int], None] | None = None,
    ) -> int:
        url = f"{self.DOWNLOAD_BASE_URL}{url_path}"
        try:
            with self.session.get(
                url,
                stream=True,
                timeout=self.timeout,
                headers={"Accept-Encoding": None},
            ) as response:
                response.raise_for_status()
                total_size = int(response.headers.get("Content-Length") or 0)
                downloaded = 0
                mode = "ab" if append else "wb"
                with destination.open(mode) as output:
                    for chunk in response.iter_content(chunk_size=8192):
                        if not chunk:
                            continue
                        output.write(chunk)
                        downloaded += len(chunk)
                        if progress_cb:
                            progress_cb(downloaded, total_size)
                return downloaded
        except OSError as exc:
            raise HashesApiError(f"Failed writing file '{destination}': {exc}") from exc
        except requests.RequestException as exc:
            raise HashesApiError(f"Failed downloading '{url_path}': {exc}") from exc

    def _request_json(
        self,
        path: str,
        *,
        method: str = "GET",
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | list[tuple[str, Any]] | None = None,
        requires_api_key: bool = False,
    ) -> dict[str, Any]:
        if requires_api_key and not self.api_key:
            raise HashesApiError("An API key is required for this action.")

        request_params = dict(params or {})
        request_data: dict[str, Any] | list[tuple[str, Any]] | None = data
        if requires_api_key:
            if method.upper() == "GET":
                request_params["key"] = self.api_key
            else:
                if request_data is None:
                    request_data = {}
                if isinstance(request_data, dict):
                    request_data = dict(request_data)
                    request_data["key"] = self.api_key
                else:
                    request_data = list(request_data)
                    request_data.append(("key", self.api_key))

        url = f"{self.BASE_API_URL}{path}"
        try:
            response = self.session.request(
                method=method.upper(),
                url=url,
                params=request_params if request_params else None,
                data=request_data,
                timeout=self.timeout,
            )
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException as exc:
            raise HashesApiError(f"Request failed: {exc}") from exc
        except ValueError as exc:
            raise HashesApiError("Received invalid JSON from hashes.com.") from exc

        if isinstance(payload, dict) and payload.get("success") is False:
            raise HashesApiError(str(payload.get("message", "API request failed.")))
        if not isinstance(payload, dict):
            raise HashesApiError("Unexpected API response format.")
        return payload

    @staticmethod
    def _sort_value(value: Any) -> Any:
        if value is None:
            return ""
        if isinstance(value, (int, float)):
            return value
        try:
            return int(value)
        except (TypeError, ValueError):
            pass
        try:
            return float(value)
        except (TypeError, ValueError):
            return str(value).lower()
