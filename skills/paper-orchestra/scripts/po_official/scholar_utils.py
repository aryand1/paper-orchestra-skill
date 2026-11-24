# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

#
# MODIFIED (2026, PaperOrchestra skill pack), from utils/scholar_utils.py at
# google-research/paper-orchestra@d9bae99. Changes: (1) `thefuzz` and
# `python-dotenv` are optional; without thefuzz, fuzz.ratio is computed with
# the same formula (indel similarity, rounded); (2) every query result,
# including failures, is cached so a resumed run replays identically;
# (3) the API key is no longer printed; (4) PO_S2_MOCK_FILE lets tests supply
# canned responses. The matching logic itself is unchanged.

import warnings
import os
import json
import requests
import time
from typing import List, Dict, Union, Optional
import datetime

try:
    from thefuzz import fuzz  # type: ignore
except ImportError:  # same result as thefuzz>=0.20 fuzz.ratio

    class _Fuzz:
        @staticmethod
        def ratio(s1: str, s2: str) -> int:
            if not s1 and not s2:
                return 100
            prev = [0] * (len(s2) + 1)
            for ch in s1:
                cur = [0]
                for j, ch2 in enumerate(s2, 1):
                    cur.append(prev[j - 1] + 1 if ch == ch2 else max(prev[j], cur[j - 1]))
                prev = cur
            lcs = prev[-1]
            return int(round(200.0 * lcs / (len(s1) + len(s2))))

    fuzz = _Fuzz()

try:
    from dotenv import load_dotenv  # type: ignore
except ImportError:
    def load_dotenv(*_a, **_k):
        return False

cur_dir = os.path.dirname(os.path.realpath(__file__))
load_dotenv(os.path.join(cur_dir, "../.env"))
S2_API_KEY = os.environ.get("SEMANTIC_SCHOLAR_API_KEY")
print(f"Using S2 API Key: {'set' if S2_API_KEY else 'not set (public rate limit)'}")

_S2_CACHE = {"path": None, "data": {}}


def configure_s2_cache(path: str):
    _S2_CACHE["path"] = path
    _S2_CACHE["data"] = {}
    if path and os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            _S2_CACHE["data"] = json.load(f)


def _cache_key(title_query, year_hint, cutoff_date):
    return json.dumps([title_query, year_hint, cutoff_date])


def s2_is_cached(title_query, year_hint=None, cutoff_date=None) -> bool:
    return _cache_key(title_query, year_hint, cutoff_date) in _S2_CACHE["data"]


def _cache_put(key, value):
    _S2_CACHE["data"][key] = value
    if _S2_CACHE["path"]:
        tmp = _S2_CACHE["path"] + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_S2_CACHE["data"], f)
        os.replace(tmp, _S2_CACHE["path"])


class _MockResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


def _s2_get(url, headers, params, timeout):
    mock_file = os.environ.get("PO_S2_MOCK_FILE")
    if mock_file:
        with open(mock_file, "r", encoding="utf-8") as f:
            table = json.load(f)
        hit = table.get(params["query"])
        if hit is None:
            return _MockResponse(404, {})
        return _MockResponse(200, {"data": hit})
    return requests.get(url, headers=headers, params=params, timeout=timeout)


# --- SEMANTIC SCHOLAR UTILS ---


def s2_title_search(
    title_query: str, year_hint: int = None, cutoff_date: str = None
) -> Optional[Dict]:
    key = _cache_key(title_query, year_hint, cutoff_date)
    if key in _S2_CACHE["data"]:
        return _S2_CACHE["data"][key]
    result = _s2_title_search_uncached(title_query, year_hint, cutoff_date)
    _cache_put(key, result)
    return result


def _s2_title_search_uncached(
    title_query: str, year_hint: int = None, cutoff_date: str = None
) -> Optional[Dict]:
    headers = {"X-API-KEY": S2_API_KEY} if S2_API_KEY else {}

    def is_date_valid(p_date, p_year, cutoff_str):
        if not cutoff_str:
            return True
        try:
            c_year, c_month = map(int, cutoff_str.split("-"))
            cutoff_dt = datetime.datetime(c_year, c_month, 1)
            if p_date:
                try:
                    p_dt = datetime.datetime.strptime(p_date, "%Y-%m-%d")
                    return p_dt < cutoff_dt
                except ValueError:
                    pass
            if p_year:
                if p_year < c_year:
                    return True
                if p_year == c_year and c_month > 1:
                    return True
                return False
            return True
        except Exception:
            return True

    try:
        rsp = _s2_get(
            "https://api.semanticscholar.org/graph/v1/paper/search",
            headers=headers,
            params={
                "query": title_query,
                "limit": 3,
                "fields": "title,authors,venue,year,abstract,citationCount,journal,publicationDate",
            },
            timeout=5,
        )
        if rsp.status_code != 200:
            return None
        results = rsp.json().get("data", [])
        if not results:
            return None

        best_match = None
        highest_ratio = 0

        for r in results:
            if not r.get("title"):
                continue
            if not is_date_valid(r.get("publicationDate"), r.get("year"), cutoff_date):
                continue

            ratio = fuzz.ratio(title_query.lower(), r["title"].lower())
            if year_hint and r.get("year") == year_hint:
                ratio += 10

            if ratio > highest_ratio:
                highest_ratio = ratio
                best_match = r

        if highest_ratio > 70:
            return best_match
        return None
    except Exception as e:
        print(f"      ⚠️ S2 Error for '{title_query}': {e}")
        return None
