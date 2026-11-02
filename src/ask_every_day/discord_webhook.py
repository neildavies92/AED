"""Bounded Discord transport worker; secrets arrive on stdin, never argv or logs."""

import json
import math
import re
import sys
import time
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

WEBHOOK = re.compile(
    r"https://(?:discord\.com|canary\.discord\.com|ptb\.discord\.com)"
    r"/api/(?:v10/)?webhooks/[0-9]{1,20}/[A-Za-z0-9_-]{20,200}\Z"
)


def validate_url(value: object) -> str:
    if not isinstance(value, str) or WEBHOOK.fullmatch(value) is None:
        raise ValueError("invalid_discord_destination")
    return value


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request(url: str, payload: bytes, timeout: float) -> tuple[int, str | None]:
    opener = build_opener(ProxyHandler({}), NoRedirects())
    req = Request(
        validate_url(url) + "?wait=true",
        data=payload,
        headers={"Content-Type": "application/json", "User-Agent": "AskEveryDay/1"},
        method="POST",
    )
    try:
        response = opener.open(req, timeout=timeout)
    except HTTPError as error:
        response = error
    with response:
        # Neither response bodies nor webhook IDs are needed for delivery diagnostics.
        return response.code, response.headers.get("Retry-After")


def deliver(url: str, payload: bytes, *, transport=request, sleep=time.sleep) -> dict:
    """Retry only an explicit short 429 once; ambiguous sends are never replayed."""
    validate_url(url)
    if len(payload) > 4096:
        raise ValueError("notification_payload_limit")
    for attempt in range(2):
        try:
            status, retry = transport(url, payload, 2.0)
        except Exception:
            return {"delivery_result": "uncertain", "http_status": None}
        if type(status) is not int or not 100 <= status <= 599:
            return {"delivery_result": "uncertain", "http_status": None}
        if 200 <= status < 300:
            return {"delivery_result": "delivered", "http_status": status}
        if status == 429 and attempt == 0:
            try:
                delay = float(retry)
            except (TypeError, ValueError):
                delay = math.inf
            if math.isfinite(delay) and 0 <= delay <= 1:
                sleep(delay)
                continue
        return {
            "delivery_result": "rate_limited"
            if status == 429
            else "uncertain"
            if status >= 500
            else "rejected",
            "http_status": status,
        }
    raise AssertionError("unreachable")


def main() -> None:
    result = {"delivery_result": "configuration_error", "http_status": None}
    try:
        raw = sys.stdin.buffer.read(8193)
        if len(raw) > 8192:
            raise ValueError
        value = json.loads(raw)
        result = deliver(value["url"], json.dumps(value["payload"]).encode())
    except Exception:
        pass
    print(json.dumps(result))


if __name__ == "__main__":
    main()
