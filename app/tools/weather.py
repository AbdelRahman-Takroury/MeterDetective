"""Open-Meteo context tool with bounded retries and a TTL cache."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from threading import Lock
from typing import Any
from urllib.parse import urlencode
from urllib.request import urlopen

from pydantic import Field

from app.contracts.common import EvidenceReference
from app.contracts.tool import ToolInput, ToolOutput

JsonTransport = Callable[[str, float], dict[str, Any]]


def _http_get_json(url: str, timeout: float) -> dict[str, Any]:
    with urlopen(url, timeout=timeout) as response:  # noqa: S310 - configured HTTPS endpoint
        return json.loads(response.read().decode("utf-8"))


class WeatherContextInput(ToolInput):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    start: datetime
    end: datetime


class WeatherObservation(ToolOutput):
    timestamp: datetime
    temperature_c: float
    precipitation_mm: float | None = None


class WeatherContextOutput(ToolOutput):
    status: str
    provider: str = "Open-Meteo"
    source_url: str
    observations: list[WeatherObservation] = Field(default_factory=list)
    temperature_c_min: float | None = None
    temperature_c_max: float | None = None
    temperature_c_mean: float | None = None
    fetched_at: datetime
    from_cache: bool = False
    confidence: float = Field(ge=0, le=1)


class WeatherClient:
    def __init__(
        self,
        *,
        base_url: str,
        timeout_seconds: float = 3,
        retry_count: int = 1,
        cache_ttl_seconds: int = 3600,
        transport: JsonTransport = _http_get_json,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.retry_count = retry_count
        self.cache_ttl = timedelta(seconds=cache_ttl_seconds)
        self.transport = transport
        self._cache: dict[str, tuple[datetime, dict[str, Any]]] = {}
        self._lock = Lock()

    def _url(self, data: WeatherContextInput) -> str:
        params = {
            "latitude": data.latitude,
            "longitude": data.longitude,
            "start_date": data.start.date().isoformat(),
            "end_date": data.end.date().isoformat(),
            "hourly": "temperature_2m,precipitation",
            "timezone": "UTC",
        }
        return f"{self.base_url}?{urlencode(params)}"

    def get(self, data: WeatherContextInput) -> WeatherContextOutput:
        if data.end <= data.start:
            raise ValueError("Weather window end must be after start")
        url = self._url(data)
        now = datetime.now(UTC)
        with self._lock:
            cached = self._cache.get(url)
        if cached and now - cached[0] <= self.cache_ttl:
            return self._parse(cached[1], url, now, from_cache=True)

        last_error: Exception | None = None
        for _ in range(self.retry_count + 1):
            try:
                payload = self.transport(url, self.timeout_seconds)
                with self._lock:
                    self._cache[url] = (now, payload)
                return self._parse(payload, url, now, from_cache=False)
            except Exception as exc:  # noqa: BLE001 - external boundary is controlled below
                last_error = exc
        return WeatherContextOutput(
            status="unavailable",
            source_url=url,
            fetched_at=now,
            confidence=0,
            warnings=[
                "Weather context is unavailable after bounded retries; "
                "no weather conclusion was made.",
                f"Failure type: {type(last_error).__name__}",
            ],
        )

    @staticmethod
    def _parse(
        payload: dict[str, Any], url: str, fetched_at: datetime, *, from_cache: bool
    ) -> WeatherContextOutput:
        hourly = payload.get("hourly") or {}
        times = hourly.get("time") or []
        temperatures = hourly.get("temperature_2m") or []
        precipitation = hourly.get("precipitation") or []
        observations = []
        for index, (raw_time, raw_temp) in enumerate(zip(times, temperatures, strict=False)):
            if raw_temp is None:
                continue
            timestamp = datetime.fromisoformat(raw_time)
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=UTC)
            observations.append(
                WeatherObservation(
                    timestamp=timestamp,
                    temperature_c=float(raw_temp),
                    precipitation_mm=(
                        float(precipitation[index])
                        if index < len(precipitation) and precipitation[index] is not None
                        else None
                    ),
                )
            )
        if not observations:
            return WeatherContextOutput(
                status="unavailable",
                source_url=url,
                fetched_at=fetched_at,
                from_cache=from_cache,
                confidence=0,
                warnings=["Open-Meteo returned no usable hourly observations."],
            )
        values = [item.temperature_c for item in observations]
        observed_at = max(item.timestamp for item in observations)
        return WeatherContextOutput(
            status="answered",
            source_url=url,
            observations=observations,
            temperature_c_min=min(values),
            temperature_c_max=max(values),
            temperature_c_mean=sum(values) / len(values),
            fetched_at=fetched_at,
            from_cache=from_cache,
            confidence=0.9,
            evidence=[
                EvidenceReference(
                    source="open-meteo",
                    kind="weather_observations",
                    observed_at=observed_at,
                    reliability=0.9,
                    metadata={"source_url": url, "observation_count": len(observations)},
                )
            ],
        )
