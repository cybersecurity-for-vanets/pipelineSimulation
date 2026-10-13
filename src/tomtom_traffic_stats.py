import gzip
import json
import os
import time
from datetime import date
from pathlib import Path
import random
import requests


PROJECT_DIR = Path(__file__).resolve().parent.parent
ROUTE_ANALYSIS_URL = "https://api.tomtom.com/traffic/trafficstats/routeanalysis/1"
JOB_STATUS_URL = "https://api.tomtom.com/traffic/trafficstats/status/1"
WEEKDAYS = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")
MAX_ROUTES_PER_JOB = 20
MAX_TIME_SETS = 24
MIN_HOURS_WITH_DATA = 18


def load_api_key():
    api_key = os.environ.get("TOMTOM_API_KEY")
    if api_key:
        return api_key.strip()

    env_files = (Path.home() / ".env", PROJECT_DIR / ".env")
    for env_path in env_files:
        if not env_path.is_file():
            continue
        for line in env_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            name, value = stripped.split("=", 1)
            if name.strip() == "TOMTOM_API_KEY":
                return value.strip().strip("\"'")

    raise RuntimeError("TOMTOM_API_KEY is missing from the environment and .env files")


def flatten_routes(routes_config):
    routes = []

    for topology, route_key, route_data in iter_config_routes(routes_config):
        start_lat, start_lon = route_data["start_coordinates"]
        end_lat, end_lon = route_data["end_coordinates"]
        route_name = f"{topology}_{route_key}"
        routes.append({
            "name": route_name,
            "start": {"latitude": start_lat, "longitude": start_lon},
            "end": {"latitude": end_lat, "longitude": end_lon},
            "fullTraversal": True,
            "zoneId": routes_config.get("time_zone", "America/Sao_Paulo"),
            "probeSource": "PASSENGER",
        })

    if not routes:
        raise ValueError("routes.json does not contain any start/end coordinate pairs")
    if len(routes) > MAX_ROUTES_PER_JOB:
        raise ValueError(f"Traffic Stats allows at most {MAX_ROUTES_PER_JOB} routes per job")
    return routes


def iter_config_routes(routes_config):
    def visit(topology, name_parts, value):
        if isinstance(value, dict) and "start_coordinates" in value and "end_coordinates" in value:
            yield topology, "_".join(name_parts), value
            return

        if not isinstance(value, dict):
            raise ValueError(f"Invalid route entry under topology '{topology}'")
        for child_name, child in value.items():
            yield from visit(topology, [*name_parts, child_name], child)

    scenarios = routes_config.get("scenarios")
    if not isinstance(scenarios, dict) or not scenarios:
        raise ValueError("routes.json must contain a non-empty 'scenarios' object")

    for topology, scenario_routes in scenarios.items():
        yield from visit(topology, [], scenario_routes)


def normalize_route_analysis(result, routes_config, selected_date, time_zone=None):
    requested_routes = {
        f"{topology}_{route_key}": {"topology": topology, "route_key": route_key}
        for topology, route_key, _ in iter_config_routes(routes_config)
    }
    echoed_time_sets = result.get("timeSets", [])
    time_set_names = {
        str(item.get("@id", index)): item.get("name", f"hour_{index:02d}_{index + 1:02d}")
        for index, item in enumerate(echoed_time_sets)
    }
    expected_time_names = [f"hour_{hour:02d}_{hour + 1:02d}" for hour in range(MAX_TIME_SETS)]
    normalized_routes = {}

    for route_result in result.get("routes", []):
        route_name = route_result.get("routeName") or route_result.get("name")
        if route_name not in requested_routes:
            continue

        hourly_speeds = {}
        for summary in route_result.get("summaries", []):
            time_set_id = str(summary.get("timeSet"))
            time_name = time_set_names.get(time_set_id)
            if time_name is None and time_set_id.isdigit():
                position = int(time_set_id)
                if position >= len(expected_time_names):
                    position -= 1
                if 0 <= position < len(expected_time_names):
                    time_name = expected_time_names[position]
            speed = summary.get("harmonicAverageSpeed")
            if time_name and speed is not None and float(speed) > 0:
                hourly_speeds[time_name] = float(speed)

        if not hourly_speeds:
            continue
        if len(hourly_speeds) < MIN_HOURS_WITH_DATA:
            raise ValueError(
                f"TomTom returned {len(hourly_speeds)}/{MAX_TIME_SETS} hourly speeds for route '{route_name}'"
            )
        # Hours without probe data (speed 0, usually at night) are filled by interpolation
        interpolated_hours = [name for name in expected_time_names if name not in hourly_speeds]
        if interpolated_hours:
            known = [i for i, name in enumerate(expected_time_names) if name in hourly_speeds]
            for name in interpolated_hours:
                i = expected_time_names.index(name)
                prev_i = max((k for k in known if k < i), default=None)
                next_i = min((k for k in known if k > i), default=None)
                if prev_i is None:
                    hourly_speeds[name] = hourly_speeds[expected_time_names[next_i]]
                elif next_i is None:
                    hourly_speeds[name] = hourly_speeds[expected_time_names[prev_i]]
                else:
                    low = hourly_speeds[expected_time_names[prev_i]]
                    high = hourly_speeds[expected_time_names[next_i]]
                    hourly_speeds[name] = low + (high - low) * (i - prev_i) / (next_i - prev_i)
            print(f"Warning: route '{route_name}' had no data for {interpolated_hours}; interpolated")
            hourly_speeds = {name: hourly_speeds[name] for name in expected_time_names}

        segments = []
        for segment in route_result.get("segmentResults", []):
            shape = segment.get("shape", [])
            if not shape:
                continue
            segments.append({
                "segment_id": segment.get("newSegmentId", segment.get("segmentId")),
                "distance_m": segment.get("distance"),
                "shape_lon_lat": shape,
                "hourly_speeds_kmh": [
                    {
                        "time_set": time_set_names.get(str(item.get("timeSet"))),
                        "average_speed_kmh": item.get("averageSpeed"),
                        "normalized_sample_size": item.get("normalizedSampleSize"),
                    }
                    for item in segment.get("segmentTimeResults", [])
                ],
            })

        route_info = requested_routes[route_name]
        normalized_routes[route_name] = {
            **route_info,
            "hourly_harmonic_speed_kmh": hourly_speeds,
            "mean_harmonic_speed_24h_kmh": sum(hourly_speeds.values()) / len(hourly_speeds),
            "hours_with_data": len(hourly_speeds) - len(interpolated_hours),
            "interpolated_hours": interpolated_hours,
            "segments": segments,
        }

    missing = sorted(set(requested_routes) - set(normalized_routes))
    if not normalized_routes:
        raise ValueError(f"TomTom returned no usable hourly route summaries for: {', '.join(missing)}")
    if missing:
        # fullTraversal routes with no probe trace come back as all zeros; calibrate with the remaining routes
        print(
            f"WARNING: TomTom has no probe data for {', '.join(missing)}; skipping them. "
            "Move their start/end coordinates onto the same road/carriageway used by the travel direction "
            "(the matched path may be a detour that no probe travelled) and fetch again."
        )

    return {
        "date": selected_date,
        "time_zone": time_zone or routes_config.get("time_zone", "America/Sao_Paulo"),
        "time_resolution": "1h",
        "skipped_routes": missing,
        "routes": normalized_routes,
    }


def build_route_analysis_payload(routes_config, tomtom_config):
    date_range = tomtom_config.get("date_range", {})
    date_value = date_range.get("24h_data")
    if not date_value:
        raise ValueError("tomtom.json must define date_range.24h_data as YYYY-MM-DD")
    selected_date = date.fromisoformat(date_value)
    range_start = date.fromisoformat(date_range["start"]) if date_range.get("start") else None
    range_end = date.fromisoformat(date_range["end"]) if date_range.get("end") else None
    if range_start and selected_date < range_start or range_end and selected_date > range_end:
        raise ValueError("date_range.24h_data must be inside the configured start/end range")
    weekday = WEEKDAYS[selected_date.weekday()]
    time_zone = tomtom_config.get("time_zone", routes_config.get("time_zone", "America/Sao_Paulo"))
    routes = flatten_routes(routes_config)
    for route in routes:
        route["zoneId"] = time_zone

    time_sets = [
        {
            "name": f"hour_{hour:02d}_{hour + 1:02d}",
            "timeGroups": [{"days": [weekday], "times": [f"{hour:02d}:00-{hour + 1:02d}:00"]}],
        }
        for hour in range(MAX_TIME_SETS)
    ]

    return {
        "jobName": f"krauss-{selected_date.isoformat()}.{random.randint(1, 1000)}",
        "distanceUnit": "KILOMETERS",
        "routes": routes,
        "dateRanges": [{
            "name": selected_date.isoformat(),
            "from": selected_date.isoformat(),
            "to": selected_date.isoformat(),
        }],
        "timeSets": time_sets,
        "mapType": "GENESIS",
        "acceptMode": "AUTO",
    }


def _response_json(response):
    try:
        print(response.json())
        return response.json()
    except requests.exceptions.JSONDecodeError as error:
        raise RuntimeError(f"TomTom returned a non-JSON response (HTTP {response.status_code})") from error


def _download_json_result(url, session, timeout):
    response = session.get(url, timeout=timeout)
    if response.status_code != 200:
        raise RuntimeError(f"TomTom result download failed (HTTP {response.status_code})")

    content = response.content
    if content.startswith(b"\x1f\x8b"):
        content = gzip.decompress(content)
    try:
        return json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError("TomTom result was not a valid JSON document") from error


def create_and_wait_for_route_analysis(
    routes_config,
    tomtom_config,
    poll_interval_seconds=15,
    max_wait_seconds=24 * 60 * 60,
    timeout_seconds=60,
    session=None,
):
    session = session or requests.Session()
    payload = build_route_analysis_payload(routes_config, tomtom_config)
    api_key = load_api_key()

    try:
        response = session.post(
            ROUTE_ANALYSIS_URL,
            params={"key": api_key},
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=timeout_seconds,
        )
    except requests.RequestException as error:
        raise RuntimeError(f"TomTom route analysis request failed ({type(error).__name__})") from None
    if response.status_code not in (200, 201, 202):
        # ToDo: If the job is been created, request a GET to get the jobId and then check the status
        # https://{baseURL}/traffic/trafficstats/job/search/{versionNumber}?key={Your_API_Key}
        raise RuntimeError(f"TomTom rejected the route analysis request (HTTP {response.status_code})")

    job = _response_json(response)
    job_id = str(job.get("jobId", ""))
    if not job_id:
        raise RuntimeError("TomTom accepted no jobId in the route analysis response")

    deadline = time.monotonic() + max_wait_seconds
    while time.monotonic() < deadline:
        try:
            status_response = session.get(
                f"{JOB_STATUS_URL}/{job_id}",
                params={"key": api_key},
                timeout=timeout_seconds,
            )
        except requests.RequestException as error:
            raise RuntimeError(f"TomTom status request failed ({type(error).__name__})") from None
        if status_response.status_code != 200:
            raise RuntimeError(f"TomTom job status request failed (HTTP {status_response.status_code})")

        status = _response_json(status_response)
        state = status.get("jobState")
        if state == "DONE":
            urls = status.get("urls", [])
            result_url = next((item for item in urls if ".json" in item.lower()), None)
            if not result_url:
                raise RuntimeError("TomTom completed the job but returned no JSON result URL")
            return job_id, _download_json_result(result_url, session, timeout_seconds)
        if state in {"ERROR", "REJECTED", "CANCELLED", "EXPIRED"}:
            messages = "; ".join(status.get("messages", []))
            raise RuntimeError(f"TomTom route analysis ended in state {state}: {messages}")
        if state == "NEED_CONFIRMATION":
            raise RuntimeError("TomTom requires manual job confirmation; acceptMode AUTO was requested")

        time.sleep(poll_interval_seconds)

    raise TimeoutError(f"TomTom route analysis job {job_id} did not finish before the timeout")


def open_download_route_analysis(file_name):
    data = None
    jobId = file_name.split("_")[1]
    
    with open(file_name, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    return jobId, data
