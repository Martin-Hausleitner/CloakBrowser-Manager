"""Redacted profile-health normalization and classification helpers."""

from __future__ import annotations

import asyncio
import datetime
import ipaddress
import json
import math
import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Collection, Mapping
from urllib.parse import urlsplit

import httpx

_MAX_EXTERNAL_TEXT = 200_000
_BROWSERSCAN_SCORE_RE = re.compile(
    r"\bauthenticity(?:\s+score)?\s*[:\-]?\s*(100|[0-9]{1,2})\s*%",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class NormalizedProxyCheckerResult:
    reachable: bool | None
    latency_ms: float | None
    risk_score: int | None
    authenticity_score: int | None
    warnings: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()


@dataclass(frozen=True)
class FingerprintConsistencyResult:
    score: int | None
    warnings: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()


@dataclass(frozen=True)
class BrowserScanClassification:
    score: int | None
    warnings: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProfileHealthResult:
    state: str
    checked_at: str
    proxy_configured: bool
    proxy_reachable: bool | None
    outbound_ip_masked: str | None
    proxy_latency_ms: float | None
    proxy_risk_score: int | None
    proxy_authenticity_score: int | None
    fingerprint_consistency_score: int | None
    browser_scan_score: int | None
    warnings: tuple[str, ...]
    blockers: tuple[str, ...]
    error_code: str | None
    sources: dict[str, str]

    def as_record(self) -> dict[str, object]:
        return {
            "state": self.state,
            "checked_at": self.checked_at,
            "proxy_configured": self.proxy_configured,
            "proxy_reachable": self.proxy_reachable,
            "outbound_ip_masked": self.outbound_ip_masked,
            "proxy_latency_ms": self.proxy_latency_ms,
            "proxy_risk_score": self.proxy_risk_score,
            "proxy_authenticity_score": self.proxy_authenticity_score,
            "fingerprint_consistency_score": self.fingerprint_consistency_score,
            "browser_scan_score": self.browser_scan_score,
            "warnings": list(self.warnings),
            "blockers": list(self.blockers),
            "error_code": self.error_code,
            "sources": dict(self.sources),
        }


def mask_ip_address(value: str) -> str | None:
    """Return a coarse display-only IP mask or None for invalid input."""
    try:
        address = ipaddress.ip_address(value.strip())
    except (AttributeError, ValueError):
        return None

    if address.version == 4:
        octets = str(address).split(".")
        return ".".join((*octets[:3], "x"))

    hextets = [f"{int(part, 16):x}" for part in address.exploded.split(":")[:3]]
    return f"{':'.join(hextets)}:…"


def _clamp_score(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numeric):
        return None
    return max(0, min(100, int(round(numeric))))


def derive_authenticity_score(risk_score: object) -> int | None:
    """Convert a risk score to a clearly derived inverse score."""
    normalized = _clamp_score(risk_score)
    return None if normalized is None else 100 - normalized


def is_trusted_proxychecker_url(
    url: str,
    *,
    allowed_hosts: Collection[str] | None = None,
) -> bool:
    """Allow only credential-free local or explicitly configured service URLs."""
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower().rstrip(".")
        _ = parsed.port
    except (TypeError, ValueError):
        return False

    if parsed.scheme not in {"http", "https"} or not host:
        return False
    if parsed.username is not None or parsed.password is not None:
        return False
    if parsed.query or parsed.fragment:
        return False

    allowed = {item.lower().rstrip(".") for item in (allowed_hosts or ())}
    if host in {"localhost", "host.docker.internal"} or host in allowed:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _safe_proxy_warnings(reasons: object, risk_score: int | None) -> tuple[str, ...]:
    haystack = " ".join(item for item in reasons if isinstance(item, str)).lower() if isinstance(reasons, list) else ""
    warnings: list[str] = []
    for category, terms in (
        ("tor_detected", ("tor exit", "tor network")),
        ("vpn_detected", ("vpn",)),
        ("datacenter_network", ("datacenter", "data center", "hosting")),
        ("proxy_detected", ("open proxy", "anonymous proxy", "proxy signal")),
    ):
        if any(term in haystack for term in terms):
            warnings.append(category)
    if risk_score is not None and risk_score >= 70:
        warnings.append("proxy_risk_high")
    return tuple(warnings)


def normalize_proxychecker_response(payload: object) -> NormalizedProxyCheckerResult:
    """Reduce a proxychecker payload to safe, bounded product fields."""
    if not isinstance(payload, Mapping):
        return NormalizedProxyCheckerResult(None, None, None, None, blockers=("proxychecker_invalid_response",))

    results = payload.get("results")
    scoring = payload.get("scoring")
    if (
        not isinstance(results, list)
        or not results
        or not isinstance(results[0], Mapping)
        or not isinstance(scoring, Mapping)
    ):
        return NormalizedProxyCheckerResult(None, None, None, None, blockers=("proxychecker_invalid_response",))

    primary = results[0]
    reachable_value = primary.get("ok")
    reachable = reachable_value if isinstance(reachable_value, bool) else None

    latency_value = primary.get("latency_ms")
    latency_ms: float | None = None
    if not isinstance(latency_value, bool):
        try:
            candidate = float(latency_value)
        except (TypeError, ValueError):
            candidate = -1
        if math.isfinite(candidate) and candidate >= 0:
            latency_ms = round(candidate, 3)

    risk_score = _clamp_score(scoring.get("risk_score"))
    return NormalizedProxyCheckerResult(
        reachable=reachable,
        latency_ms=latency_ms,
        risk_score=risk_score,
        authenticity_score=derive_authenticity_score(risk_score),
        warnings=_safe_proxy_warnings(scoring.get("reasons"), risk_score),
    )


def _platform_family(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    lowered = value.lower()
    if "win" in lowered:
        return "windows"
    if "mac" in lowered or "darwin" in lowered:
        return "macos"
    if "linux" in lowered:
        return "linux"
    return None


def _user_agent_family(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    lowered = value.lower()
    if "edg/" in lowered or "edgios/" in lowered:
        return "edge"
    if "chrome/" in lowered or "crios/" in lowered:
        return "chrome"
    if "firefox/" in lowered or "fxios/" in lowered:
        return "firefox"
    if "safari/" in lowered:
        return "safari"
    return None


def _normalized_locale(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip().replace("_", "-").lower()


def score_fingerprint_consistency(
    config: Mapping[str, Any],
    runtime: Mapping[str, Any],
) -> FingerprintConsistencyResult:
    """Score saved configuration against runtime signals without returning values."""
    comparisons: list[tuple[str, bool | None]] = []

    expected_platform = _platform_family(config.get("platform"))
    if expected_platform is not None:
        actual_platform = _platform_family(runtime.get("platform"))
        comparisons.append(("platform", None if actual_platform is None else actual_platform == expected_platform))

    expected_width = config.get("screen_width")
    expected_height = config.get("screen_height")
    if isinstance(expected_width, int) and isinstance(expected_height, int):
        actual_width = runtime.get("screen_width")
        actual_height = runtime.get("screen_height")
        if not isinstance(actual_width, int) or not isinstance(actual_height, int):
            comparisons.append(("screen", None))
        else:
            comparisons.append(("screen", (actual_width, actual_height) == (expected_width, expected_height)))

    expected_timezone = config.get("timezone")
    if isinstance(expected_timezone, str) and expected_timezone:
        actual_timezone = runtime.get("timezone")
        comparisons.append(
            ("timezone", None if not isinstance(actual_timezone, str) else actual_timezone == expected_timezone)
        )

    expected_locale = _normalized_locale(config.get("locale"))
    if expected_locale is not None:
        actual_locale = _normalized_locale(runtime.get("language"))
        comparisons.append(("locale", None if actual_locale is None else actual_locale == expected_locale))

    expected_hardware = config.get("hardware_concurrency")
    if isinstance(expected_hardware, int):
        actual_hardware = runtime.get("hardware_concurrency")
        comparisons.append(
            ("hardware_concurrency", None if not isinstance(actual_hardware, int) else actual_hardware == expected_hardware)
        )

    expected_ua = _user_agent_family(config.get("user_agent"))
    if expected_ua is not None:
        actual_ua = _user_agent_family(runtime.get("user_agent"))
        comparisons.append(("user_agent_family", None if actual_ua is None else actual_ua == expected_ua))

    if not comparisons:
        return FingerprintConsistencyResult(None, blockers=("fingerprint_signals_missing",))

    matches = sum(outcome is True for _, outcome in comparisons)
    score = int(round((matches / len(comparisons)) * 100))
    warnings = tuple(f"{name}_mismatch" for name, outcome in comparisons if outcome is False)
    blockers = tuple(f"{name}_missing" for name, outcome in comparisons if outcome is None)
    return FingerprintConsistencyResult(score, warnings=warnings, blockers=blockers)


def classify_browserscan_text(text: object) -> BrowserScanClassification:
    """Conservatively reduce visible BrowserScan text without retaining it."""
    if not isinstance(text, str):
        return BrowserScanClassification(None, blockers=("browser_scan_score_missing",))

    bounded = text[:_MAX_EXTERNAL_TEXT]
    lowered = bounded.lower()
    if any(term in lowered for term in ("verify you are human", "captcha", "cloudflare challenge")):
        return BrowserScanClassification(None, blockers=("browser_scan_challenge",))
    if any(term in lowered for term in ("cookie consent", "accept all cookies", "manage consent")):
        return BrowserScanClassification(None, blockers=("browser_scan_consent",))

    match = _BROWSERSCAN_SCORE_RE.search(bounded)
    if match is None:
        return BrowserScanClassification(None, blockers=("browser_scan_score_missing",))

    warnings: list[str] = []
    for category, terms in (
        (
            "automation_detected",
            (
                "automation detected",
                "webdriver detected",
                "webdriver: true",
                "webdriver true",
            ),
        ),
        ("network_mismatch", ("network mismatch", "webrtc mismatch", "dns mismatch", "timezone mismatch")),
        ("fingerprint_warning", ("canvas fingerprint warning", "webgl warning", "audio fingerprint warning")),
        ("profile_warning", ("incognito warning", "profile warning")),
    ):
        if any(term in lowered for term in terms):
            warnings.append(category)

    return BrowserScanClassification(int(match.group(1)), warnings=tuple(warnings))


def _utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _unique(values: list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


class ProfileHealthProbe:
    """Measure one running profile while reducing all outputs to safe fields."""

    def __init__(
        self,
        *,
        proxychecker_url: str = "",
        allowed_proxychecker_hosts: Collection[str] | None = None,
        ip_echo_url: str = "https://api.ipify.org?format=json",
        browserscan_url: str = "https://www.browserscan.net/",
        component_timeout_s: float = 12.0,
        http_client_factory: Callable[..., Any] = httpx.AsyncClient,
        monotonic: Callable[[], float] = time.monotonic,
        now: Callable[[], str] = _utc_now,
    ):
        normalized_url = proxychecker_url.strip().rstrip("/")
        if normalized_url and not is_trusted_proxychecker_url(
            normalized_url,
            allowed_hosts=allowed_proxychecker_hosts,
        ):
            raise ValueError("PROXYCHECKER_URL must be a trusted local endpoint")
        self.proxychecker_url = normalized_url
        self.ip_echo_url = ip_echo_url
        self.browserscan_url = browserscan_url
        self.component_timeout_s = max(0.1, float(component_timeout_s))
        self.http_client_factory = http_client_factory
        self.monotonic = monotonic
        self.now = now

    async def _wait(self, awaitable: Any) -> Any:
        return await asyncio.wait_for(awaitable, timeout=self.component_timeout_s)

    async def _close_page(self, page: Any) -> None:
        try:
            await self._wait(page.close())
        except Exception:
            return

    async def _observe_network(self, page: Any) -> dict[str, object]:
        started = self.monotonic()
        try:
            await self._wait(
                page.goto(
                    self.ip_echo_url,
                    wait_until="domcontentloaded",
                    timeout=int(self.component_timeout_s * 1000),
                )
            )
            body = await self._wait(page.text_content("body"))
        except (TimeoutError, asyncio.TimeoutError):
            self.monotonic()
            return {
                "reachable": False,
                "masked_ip": None,
                "latency_ms": None,
                "blocker": "network_timeout",
                "error_code": "network_timeout",
                "source": "unavailable",
            }
        except Exception:
            self.monotonic()
            return {
                "reachable": False,
                "masked_ip": None,
                "latency_ms": None,
                "blocker": "network_unavailable",
                "error_code": "network_unavailable",
                "source": "unavailable",
            }

        elapsed_ms = round(max(0.0, self.monotonic() - started) * 1000, 3)
        try:
            payload = json.loads(body) if isinstance(body, str) and len(body) <= 4096 else None
        except json.JSONDecodeError:
            payload = None
        raw_ip = payload.get("ip") if isinstance(payload, dict) else None
        masked_ip = mask_ip_address(raw_ip) if isinstance(raw_ip, str) else None
        if masked_ip is None:
            return {
                "reachable": True,
                "masked_ip": None,
                "latency_ms": elapsed_ms,
                "blocker": "network_invalid_response",
                "error_code": "network_invalid_response",
                "source": "unavailable",
            }
        return {
            "reachable": True,
            "masked_ip": masked_ip,
            "latency_ms": elapsed_ms,
            "blocker": None,
            "error_code": None,
            "source": "measured",
        }

    async def _observe_fingerprint(
        self,
        profile: Mapping[str, Any],
        page: Any,
    ) -> FingerprintConsistencyResult:
        script = """() => ({
            platform: navigator.platform || null,
            screen_width: Number.isFinite(screen.width) ? screen.width : null,
            screen_height: Number.isFinite(screen.height) ? screen.height : null,
            timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || null,
            language: navigator.language || null,
            hardware_concurrency: navigator.hardwareConcurrency || null,
            user_agent: navigator.userAgent || null,
        })"""
        try:
            runtime = await self._wait(page.evaluate(script))
        except Exception:
            return FingerprintConsistencyResult(None, blockers=("fingerprint_runtime_unavailable",))
        if not isinstance(runtime, Mapping):
            return FingerprintConsistencyResult(None, blockers=("fingerprint_runtime_unavailable",))
        return score_fingerprint_consistency(profile, runtime)

    async def _observe_browserscan(self, context: Any) -> BrowserScanClassification:
        page = None
        try:
            page = await self._wait(context.new_page())
            await self._wait(
                page.goto(
                    self.browserscan_url,
                    wait_until="domcontentloaded",
                    timeout=int(self.component_timeout_s * 1000),
                )
            )
            text = await self._wait(page.text_content("body"))
            return classify_browserscan_text(text)
        except (TimeoutError, asyncio.TimeoutError):
            return BrowserScanClassification(None, blockers=("browser_scan_timeout",))
        except Exception:
            return BrowserScanClassification(None, blockers=("browser_scan_unavailable",))
        finally:
            if page is not None:
                await self._close_page(page)

    async def _observe_proxychecker(self, proxy: str) -> NormalizedProxyCheckerResult:
        if not self.proxychecker_url:
            return NormalizedProxyCheckerResult(None, None, None, None)
        try:
            async with self.http_client_factory(timeout=self.component_timeout_s) as client:
                response = await self._wait(
                    client.post(
                        f"{self.proxychecker_url}/check",
                        json={
                            "target": self.ip_echo_url,
                            "proxies": [proxy],
                            "limit": 1,
                            "scoring_profile": "default",
                        },
                    )
                )
                response.raise_for_status()
                return normalize_proxychecker_response(response.json())
        except (TimeoutError, asyncio.TimeoutError, httpx.HTTPError):
            return NormalizedProxyCheckerResult(
                None,
                None,
                None,
                None,
                blockers=("proxychecker_unavailable",),
            )
        except Exception:
            return NormalizedProxyCheckerResult(
                None,
                None,
                None,
                None,
                blockers=("proxychecker_unavailable",),
            )

    async def run(self, profile: Mapping[str, Any], running: Any) -> ProfileHealthResult:
        proxy = profile.get("proxy")
        proxy_configured = isinstance(proxy, str) and bool(proxy)
        sources: dict[str, str] = {}
        warnings: list[str] = []
        blockers: list[str] = []
        error_code: str | None = None

        proxy_reachable: bool | None = None
        outbound_ip_masked: str | None = None
        proxy_latency_ms: float | None = None
        fingerprint_score: int | None = None
        browser_scan_score: int | None = None
        risk_score: int | None = None
        authenticity_score: int | None = None

        context = getattr(running, "context", None)
        network_page = None
        if context is None:
            blockers.append("browser_context_unavailable")
            error_code = "browser_context_unavailable"
            sources["browser_network"] = "unavailable"
            sources["fingerprint_consistency"] = "unavailable"
        else:
            try:
                network_page = await self._wait(context.new_page())
            except Exception:
                blockers.append("browser_context_unavailable")
                error_code = "browser_context_unavailable"
                sources["browser_network"] = "unavailable"
                sources["fingerprint_consistency"] = "unavailable"
            else:
                try:
                    network = await self._observe_network(network_page)
                    proxy_reachable = network["reachable"] if isinstance(network["reachable"], bool) else None
                    outbound_ip_masked = (
                        network["masked_ip"] if isinstance(network["masked_ip"], str) else None
                    )
                    if proxy_configured and isinstance(network["latency_ms"], float):
                        proxy_latency_ms = network["latency_ms"]
                    sources["browser_network"] = str(network["source"])
                    if isinstance(network["blocker"], str):
                        blockers.append(network["blocker"])
                    if isinstance(network["error_code"], str):
                        error_code = network["error_code"]

                    fingerprint = await self._observe_fingerprint(profile, network_page)
                    fingerprint_score = fingerprint.score
                    warnings.extend(fingerprint.warnings)
                    blockers.extend(fingerprint.blockers)
                    sources["fingerprint_consistency"] = (
                        "measured" if fingerprint.score is not None else "unavailable"
                    )
                finally:
                    await self._close_page(network_page)

        if context is None:
            browserscan = BrowserScanClassification(None, blockers=("browser_scan_unavailable",))
        else:
            browserscan = await self._observe_browserscan(context)
        browser_scan_score = browserscan.score
        warnings.extend(browserscan.warnings)
        blockers.extend(browserscan.blockers)
        sources["browser_scan"] = "measured" if browserscan.score is not None else "unavailable"

        if self.proxychecker_url and proxy_configured:
            checker = await self._observe_proxychecker(proxy)
            risk_score = checker.risk_score
            authenticity_score = checker.authenticity_score
            if checker.reachable is not None:
                proxy_reachable = checker.reachable
            if checker.latency_ms is not None:
                proxy_latency_ms = checker.latency_ms
            warnings.extend(checker.warnings)
            blockers.extend(checker.blockers)
            sources["proxychecker"] = "unavailable" if checker.blockers else "measured"
            if authenticity_score is not None:
                sources["proxy_authenticity"] = "derived"
        else:
            sources["proxychecker"] = "skipped"

        meaningful = any(
            value is not None
            for value in (
                proxy_reachable,
                outbound_ip_masked,
                fingerprint_score,
                browser_scan_score,
                risk_score,
            )
        )
        normalized_warnings = _unique(warnings)
        normalized_blockers = _unique(blockers)
        if not meaningful:
            state = "unavailable"
        elif normalized_warnings or normalized_blockers or proxy_reachable is False:
            state = "warning"
        else:
            state = "passed"

        return ProfileHealthResult(
            state=state,
            checked_at=self.now(),
            proxy_configured=proxy_configured,
            proxy_reachable=proxy_reachable,
            outbound_ip_masked=outbound_ip_masked,
            proxy_latency_ms=proxy_latency_ms,
            proxy_risk_score=risk_score,
            proxy_authenticity_score=authenticity_score,
            fingerprint_consistency_score=fingerprint_score,
            browser_scan_score=browser_scan_score,
            warnings=normalized_warnings,
            blockers=normalized_blockers,
            error_code=error_code,
            sources=sources,
        )
