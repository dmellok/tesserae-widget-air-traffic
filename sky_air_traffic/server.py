"""sky_air_traffic, flights overhead via OpenSky."""

from __future__ import annotations

import contextlib
import json
import math
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

CACHE_TTL_S = 30  # OpenSky updates every ~10s for anon; cache to stay polite
HTTP_TIMEOUT_S = 15
USER_AGENT = "tesserae/0.1 (+sky_air_traffic)"

# Route lookup (opt-in, ``show_route``). OpenSky's state vectors carry
# no departure / arrival, only the aircraft's registration country, so
# the route comes from adsbdb's callsign table instead. A scheduled
# callsign keeps its route for months, so a hit is cached for a week;
# a miss (a GA registration flown as the callsign, a ferry flight) is
# cached for a day so one unknown callsign doesn't cost a request
# every 30 s. At most a few new callsigns are looked up per fetch,
# the rest fill in on the next cycle, which keeps the first paint
# quick and the request rate polite.
ROUTE_CACHE_FILE = "routes.json"
ROUTE_HIT_TTL_S = 7 * 24 * 3600
ROUTE_MISS_TTL_S = 24 * 3600
ROUTE_HTTP_TIMEOUT_S = 8
ROUTE_LOOKUPS_PER_FETCH = 4
ROUTE_CACHE_MAX = 400

# OpenSky state-vector tuple positions (0-indexed):
#   0 icao24, 1 callsign, 2 origin_country, 3 time_position, 4 last_contact,
#   5 longitude, 6 latitude, 7 baro_altitude (m), 8 on_ground, 9 velocity (m/s),
#   10 true_track (deg), 11 vertical_rate, 13 geo_altitude, ...
S_CALLSIGN = 1
S_COUNTRY = 2
S_LON = 5
S_LAT = 6
S_BARO_ALT = 7
S_ON_GROUND = 8
S_VELOCITY = 9
S_TRACK = 10
S_VERTICAL = 11
S_GEO_ALT = 13


def _bbox(lat: float, lon: float, km: float) -> tuple[float, float, float, float]:
    dlat = km / 111.0
    dlon = km / (111.0 * max(0.1, math.cos(math.radians(lat))))
    return lat - dlat, lon - dlon, lat + dlat, lon + dlon


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _load_route_cache(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _save_route_cache(path: Path, cache: dict[str, Any]) -> None:
    if len(cache) > ROUTE_CACHE_MAX:
        # Drop the oldest entries; the live ones are refreshed on use.
        oldest = sorted(cache, key=lambda k: float(cache[k].get("ts") or 0))
        for key in oldest[: len(cache) - ROUTE_CACHE_MAX]:
            cache.pop(key, None)
    with contextlib.suppress(OSError):
        path.write_text(json.dumps(cache), encoding="utf-8")


def _airport(raw: Any) -> dict[str, str] | None:
    if not isinstance(raw, dict):
        return None
    code = str(raw.get("iata_code") or raw.get("icao_code") or "").strip()
    if not code:
        return None
    return {
        "code": code,
        "city": str(raw.get("municipality") or "").strip(),
        "name": str(raw.get("name") or "").strip(),
        "country": str(raw.get("country_name") or "").strip(),
    }


def _lookup_route(callsign: str) -> dict[str, Any] | None:
    """adsbdb's route for one callsign, or None when it has none."""
    url = f"https://api.adsbdb.com/v0/callsign/{urllib.parse.quote(callsign)}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=ROUTE_HTTP_TIMEOUT_S) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        if err.code == 404:
            return None
        raise
    route = (
        (payload.get("response") or {}).get("flightroute")
        if isinstance(payload, dict)
        else None
    )
    if not isinstance(route, dict):
        return None
    origin = _airport(route.get("origin"))
    dest = _airport(route.get("destination"))
    if origin is None or dest is None:
        return None
    airline = route.get("airline") if isinstance(route.get("airline"), dict) else {}
    return {
        "from": origin,
        "to": dest,
        "airline": str(airline.get("name") or "").strip(),
        "flight": str(route.get("callsign_iata") or "").strip(),
    }


def _attach_routes(flights: list[dict[str, Any]], data_dir: Path) -> None:
    """Fill ``flight["route"]`` from the cache, looking up at most
    ``ROUTE_LOOKUPS_PER_FETCH`` callsigns the cache doesn't know yet.
    A lookup that fails for a reason other than 404 is left for the
    next cycle rather than cached, so a blip doesn't blank a route
    for a day."""
    path = data_dir / ROUTE_CACHE_FILE
    cache = _load_route_cache(path)
    now = time.time()
    lookups = 0
    dirty = False
    for f in flights:
        f["route"] = None
        callsign = f.get("callsign") or ""
        if not callsign:
            continue
        entry = cache.get(callsign)
        if isinstance(entry, dict):
            ttl = ROUTE_HIT_TTL_S if entry.get("route") else ROUTE_MISS_TTL_S
            if now - float(entry.get("ts") or 0) < ttl:
                f["route"] = entry.get("route")
                continue
        if lookups >= ROUTE_LOOKUPS_PER_FETCH:
            continue
        lookups += 1
        try:
            route = _lookup_route(callsign)
        except Exception:
            continue
        cache[callsign] = {"ts": now, "route": route}
        dirty = True
        f["route"] = route
    if dirty:
        _save_route_cache(path, cache)


def fetch(
    options: dict[str, Any], settings: dict[str, Any], *, ctx: dict[str, Any]
) -> dict[str, Any]:
    del settings
    lat = float(options.get("latitude") or 0.0)
    lon = float(options.get("longitude") or 0.0)
    radius = max(5.0, float(options.get("radius_km") or 60))
    max_results = max(1, int(options.get("max_results") or 8))
    show_route = _truthy(options.get("show_route"))

    data_dir = Path(ctx["data_dir"])
    data_dir.mkdir(parents=True, exist_ok=True)
    cache = data_dir / f"at_{lat:.3f}_{lon:.3f}_{int(radius)}.json"
    if cache.exists() and time.time() - cache.stat().st_mtime < CACHE_TTL_S:
        try:
            cached = json.loads(cache.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            cached = None
        if isinstance(cached, dict):
            if show_route and cached.get("flights"):
                _attach_routes(cached["flights"], data_dir)
            return cached

    la_min, lo_min, la_max, lo_max = _bbox(lat, lon, radius)
    url = (
        "https://opensky-network.org/api/states/all"
        f"?lamin={la_min:.4f}&lomin={lo_min:.4f}&lamax={la_max:.4f}&lomax={lo_max:.4f}"
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as err:
        return {"error": f"{type(err).__name__}: {err}", "flights": []}

    states = payload.get("states") or []
    flights = []
    for s in states:
        try:
            f_lat = s[S_LAT]
            f_lon = s[S_LON]
        except (IndexError, TypeError):
            continue
        if f_lat is None or f_lon is None:
            continue
        d = _haversine_km(lat, lon, f_lat, f_lon)
        if d > radius:
            continue
        flights.append(
            {
                "callsign": (s[S_CALLSIGN] or "").strip()
                if len(s) > S_CALLSIGN
                else "",
                "country": (s[S_COUNTRY] or "").strip() if len(s) > S_COUNTRY else "",
                "altitude": s[S_BARO_ALT] if len(s) > S_BARO_ALT else None,
                "velocity": s[S_VELOCITY] if len(s) > S_VELOCITY else None,
                "track": s[S_TRACK] if len(s) > S_TRACK else None,
                "vertical_rate": s[S_VERTICAL] if len(s) > S_VERTICAL else None,
                "on_ground": bool(s[S_ON_GROUND]) if len(s) > S_ON_GROUND else False,
                "lat": f_lat,
                "lon": f_lon,
                "distance_km": round(d, 1),
            }
        )
    flights.sort(key=lambda f: f["distance_km"])
    flights = flights[:max_results]

    result = {
        "lat": lat,
        "lon": lon,
        "radius": radius,
        "count": len(states),  # total in bounding box (incl out-of-radius)
        "shown": len(flights),
        "flights": flights,
    }
    with contextlib.suppress(OSError):
        cache.write_text(json.dumps(result), encoding="utf-8")
    # Routes go on after the states cache is written, so the cache stays a
    # plain OpenSky snapshot and a cell with the option off never sees them.
    if show_route:
        _attach_routes(flights, data_dir)
    return result
