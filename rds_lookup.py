"""
rds_lookup.py - RDS station lookup for bl-rmo
Fetches PI/PS → station name + logo from rds-station-db on GitHub.

Usage in bl-rmo:
    from rds_lookup import RDSLookup

    lookup = RDSLookup(country="FR")
    station = lookup.get_by_pi("F888")
    if station:
        print(station["name"])   # "Graffiti Radio"
        print(station["logo_url"])
"""

import json
import time
import threading
import urllib.request
import urllib.error
from pathlib import Path

# ── Configuration ──────────────────────────────────────────────────────────────

DB_BASE_URL = "https://raw.githubusercontent.com/LyonelB/rds-station-db/main/data"
CACHE_DIR = Path.home() / ".cache" / "bl-rmo" / "rds-db"
CACHE_TTL_SECONDS = 24 * 3600  # Refresh cache every 24 h
USER_AGENT = "BL-RMO/0.9 (https://github.com/LyonelB/BL-RMO)"


# ── Lookup class ───────────────────────────────────────────────────────────────

class RDSLookup:
    """Thread-safe RDS station database lookup with local cache."""

    def __init__(self, country: str = "FR", auto_refresh: bool = True):
        self.country = country.upper()
        self._lock = threading.Lock()
        self._by_pi: dict[str, dict] = {}
        self._by_ps: dict[str, dict] = {}
        self._by_pi_ps: dict[tuple, dict] = {}
        self._loaded = False

        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        self._cache_file = CACHE_DIR / f"{self.country}.json"

        self._load(force_refresh=False)

        if auto_refresh:
            t = threading.Thread(target=self._background_refresh, daemon=True)
            t.start()

    # ── Public API ─────────────────────────────────────────────────────────────

    def get_by_pi(self, pi: str) -> dict | None:
        """Look up a station by its PI code (case-insensitive). Returns first match."""
        return self._by_pi.get(pi.upper())

    def get_by_ps(self, ps: str) -> dict | None:
        """Look up a station by its PS name (stripped, case-insensitive)."""
        return self._by_ps.get(ps.replace('_', ' ').strip().upper())

    def get_by_pi_ps(self, pi: str, ps: str) -> dict | None:
        """Look up a station by PI + PS (exact match). Preferred when both are known."""
        pi_norm = pi.upper()
        ps_norm = ps.replace('_', ' ').strip().upper()
        return self._by_pi_ps.get((pi_norm, ps_norm))

    def get(self, pi: str | None = None, ps: str | None = None) -> dict | None:
        """
        Best-effort lookup: PI+PS exact match first, then PI only, then PS only.
        Always prefer get_by_pi_ps() when both PI and PS are available.
        """
        if pi and ps:
            result = self.get_by_pi_ps(pi, ps)
            if result:
                return result
        if pi:
            result = self.get_by_pi(pi)
            if result:
                return result
        if ps:
            return self.get_by_ps(ps)
        return None

    def station_count(self) -> int:
        return len(self._by_pi)

    def force_refresh(self):
        """Force an immediate re-download of the database."""
        self._load(force_refresh=True)

    # ── Internal ───────────────────────────────────────────────────────────────

    def _load(self, force_refresh: bool = False):
        data = None

        # Try cache first
        if not force_refresh and self._cache_file.exists():
            age = time.time() - self._cache_file.stat().st_mtime
            if age < CACHE_TTL_SECONDS:
                try:
                    with open(self._cache_file) as f:
                        data = json.load(f)
                except Exception:
                    data = None

        # Fetch from GitHub if needed
        if data is None:
            data = self._fetch_remote()
            if data:
                try:
                    with open(self._cache_file, "w") as f:
                        json.dump(data, f, ensure_ascii=False)
                except Exception:
                    pass

        if data:
            self._index(data)

    def _fetch_remote(self) -> dict | None:
        url = f"{DB_BASE_URL}/{self.country}.json"
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 404:
                # Fall back to stale cache if available
                if self._cache_file.exists():
                    with open(self._cache_file) as f:
                        return json.load(f)
            return None
        except Exception:
            if self._cache_file.exists():
                try:
                    with open(self._cache_file) as f:
                        return json.load(f)
                except Exception:
                    pass
            return None

    def _index(self, data: dict):
        by_pi: dict[str, dict] = {}
        by_ps: dict[str, dict] = {}
        by_pi_ps: dict[tuple, dict] = {}
        for station in data.get("stations", []):
            pi = station.get("pi", "").upper()
            ps = station.get("ps", "").strip().upper()
            if pi:
                # Pour by_pi : garder le premier (ou celui avec logo)
                if pi not in by_pi or station.get("logo_url"):
                    by_pi[pi] = station
            if ps:
                if ps not in by_ps or station.get("logo_url"):
                    by_ps[ps] = station
            if pi and ps:
                by_pi_ps[(pi, ps)] = station
        with self._lock:
            self._by_pi    = by_pi
            self._by_ps    = by_ps
            self._by_pi_ps = by_pi_ps
            self._loaded   = True

    def _background_refresh(self):
        """Refresh the cache once it's older than TTL."""
        while True:
            time.sleep(3600)  # Check every hour
            if self._cache_file.exists():
                age = time.time() - self._cache_file.stat().st_mtime
                if age >= CACHE_TTL_SECONDS:
                    self._load(force_refresh=True)


# ── Convenience singleton ──────────────────────────────────────────────────────

_default_lookup: RDSLookup | None = None


def get_lookup(country: str = "FR") -> RDSLookup:
    """Return a cached singleton RDSLookup instance."""
    global _default_lookup
    if _default_lookup is None or _default_lookup.country != country.upper():
        _default_lookup = RDSLookup(country=country)
    return _default_lookup


# ── CLI test ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    country = sys.argv[1] if len(sys.argv) > 1 else "FR"
    query = sys.argv[2] if len(sys.argv) > 2 else None

    lookup = RDSLookup(country=country)
    print(f"Loaded {lookup.station_count()} stations for {country}")

    if query:
        result = lookup.get_by_pi(query) or lookup.get_by_ps(query)
        if result:
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            print(f"Not found: {query}")
    else:
        # Print all stations
        for pi, s in sorted(lookup._by_pi.items()):
            logo = "🖼" if s.get("logo_url") else "·"
            print(f"  {pi}  {s.get('ps',''):8s}  {s.get('name','')}  {logo}")
