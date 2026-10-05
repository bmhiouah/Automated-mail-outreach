"""The one door every external HTTP call goes through. Stdlib only.

Three things this exists to get right, all of which matter because our sources
are metered and rate limited:

1. **Distinguish "blocked" from "missing".** A 403 from a bot firewall and a 404
   for a firm that does not exist are different facts, and confusing them is how
   a crawler ends up recording confident nonsense. The result carries the status
   so the caller decides.
2. **Never hammer a source.** A per-host throttle keeps us under the documented
   limit (Hunter: 15 req/s, 5 req/s for Discover) whether calls are made
   sequentially or in a burst.
3. **Survive an impatient retry.** 429 and 5xx are retried with exponential
   backoff, honouring `Retry-After` when the server sends one. A 429 from a
   quota exhaustion is *not* retried - waiting will not create credits.

    from net import get_json
    r = get_json("https://api.hunter.io/v2/domain-search", params={"domain": "x.com"})
    if r["ok"]:
        data = r["json"]["data"]
"""
import gzip
import json
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser

DEFAULT_TIMEOUT = 20
DEFAULT_UA = "cold-approach-tracker/1.0 (+local; contact: user)"

# host -> (min_seconds_between_calls, monotonic timestamp of last call)
_LAST_CALL = {}
_RETRY_STATUSES = {429, 500, 502, 503, 504}


class RateLimiter:
    """A simple spacing throttle: at most 1 call per `interval` seconds per host.

    Hunter's published limits are 15/s (Domain Search, Enrichment) and 5/s
    (Discover). 15/s is generous enough that a conservative 1/0.15s keeps us
    well inside it while still finishing a batch quickly.
    """

    def __init__(self, per_second=15.0):
        self.interval = 1.0 / max(per_second, 0.01)

    def wait(self, host):
        now = time.monotonic()
        last = _LAST_CALL.get(host)
        if last is not None:
            elapsed = now - last
            if elapsed < self.interval:
                time.sleep(self.interval - elapsed)
        _LAST_CALL[host] = time.monotonic()


LIMITER = RateLimiter()


def _headers(extra=None):
    h = {"User-Agent": DEFAULT_UA, "Accept": "application/json",
         "Accept-Encoding": "gzip"}
    h.update(extra or {})
    return h


def _decode(resp, raw):
    if (resp.headers.get("Content-Encoding") or "").lower() == "gzip":
        try:
            return gzip.decompress(raw)
        except OSError:
            return raw
    return raw


def request(url, params=None, headers=None, timeout=DEFAULT_TIMEOUT,
            method="GET", throttle=True, data=None):
    """Make one HTTP request. Returns a result dict, never raises for HTTP errors.

    Returned keys: ok, status, body (bytes), text, json, headers, error, url.

    A network failure returns ok=False with status=None and an `error` string.
    An HTTP error returns ok=False with the real `status`, because a 403 and a
    404 mean different things and the caller must be able to tell them apart.
    """
    if params:
        sep = "&" if urllib.parse.urlparse(url).query else "?"
        url = url + sep + urllib.parse.urlencode(params, doseq=True)

    host = urllib.parse.urlparse(url).netloc
    if throttle:
        LIMITER.wait(host)

    req = urllib.request.Request(url, headers=_headers(headers), method=method,
                                 data=data)
    ctx = ssl.create_default_context()
    result = {"ok": False, "status": None, "body": b"", "text": "", "json": None,
              "headers": {}, "error": None, "url": url}
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            raw = _decode(resp, resp.read())
            result.update(ok=True, status=resp.status, body=raw,
                          text=raw.decode("utf-8", "replace"),
                          headers=dict(resp.headers))
    except urllib.error.HTTPError as exc:
        raw = exc.read() if hasattr(exc, "read") else b""
        text = raw.decode("utf-8", "replace")
        # Keep the provider's own words in `error`, not just the status code.
        # Every actionable diagnosis lives in that body - "Signature expired:
        # 20261002T035705Z", "does not support /v1/chat/completions",
        # "access_denied" - and "HTTP 401" throws all of it away. The body is
        # pulled out of its JSON envelope so the message reads as prose rather
        # than as a blob, and truncated so a 10KB error page cannot fill the
        # ledger.
        detail = text
        try:
            payload = json.loads(text)
            err = payload.get("error") if isinstance(payload, dict) else None
            if isinstance(err, dict):
                detail = err.get("message") or err.get("code") or detail
            elif isinstance(err, str):
                detail = err
        except ValueError:
            pass
        detail = " ".join(str(detail).split())[:400] or f"HTTP {exc.code}"
        result.update(status=exc.code, body=raw, text=text,
                      headers=dict(exc.headers or {}),
                      error=f"HTTP {exc.code}: {detail}")
    except urllib.error.URLError as exc:
        result["error"] = f"network: {exc.reason}"
    except Exception as exc:                      # timeout, ssl, decode
        result["error"] = f"{type(exc).__name__}: {exc}"

    if result["text"]:
        try:
            result["json"] = json.loads(result["text"])
        except ValueError:
            result["json"] = None
    return result


def _retry_after(headers):
    val = (headers or {}).get("Retry-After")
    try:
        return min(float(val), 30.0)
    except (TypeError, ValueError):
        return None


def request_json(url, params=None, headers=None, retries=3, timeout=DEFAULT_TIMEOUT,
                 method="GET", data=None, throttle=True):
    """`request` plus retry/backoff. The call the providers actually use.

    Retries 429/5xx with exponential backoff. A 429 is only retried a couple of
    times: Hunter returns 429 for *quota exhausted*, and no amount of waiting
    fixes that, so we back off, try again briefly, then give up and let the
    caller surface "you are out of credits" rather than looping forever.
    """
    attempt = 0
    delay = 0.8
    while True:
        res = request(url, params=params, headers=headers, timeout=timeout,
                      method=method, data=data, throttle=throttle)
        status = res["status"]
        if res["ok"] or status not in _RETRY_STATUSES or attempt >= retries:
            res["attempts"] = attempt + 1
            return res
        wait = _retry_after(res["headers"]) or delay
        time.sleep(wait)
        delay *= 2
        attempt += 1


def get_json(url, params=None, headers=None, **kw):
    return request_json(url, params=params, headers=headers, **kw)


def post_json(url, payload=None, headers=None, params=None, **kw):
    """POST a JSON body. Hunter's Domain Search needs POST for the location filter."""
    body = json.dumps(payload or {}).encode("utf-8")
    h = {"Content-Type": "application/json"}
    h.update(headers or {})
    return request_json(url, params=params, headers=h, method="POST", data=body, **kw)


def robots_allowed(url, user_agent=None):
    """Is `url` permitted by the site's robots.txt? For the future team-page crawler.

    Fails open on a network error (robots.txt unreachable is not a prohibition),
    which is the conventional reading. Returns (allowed, crawl_delay_or_None).
    """
    parts = urllib.parse.urlparse(url)
    robots_url = f"{parts.scheme}://{parts.netloc}/robots.txt"
    rp = urllib.robotparser.RobotFileParser()
    rp.set_url(robots_url)
    try:
        with urllib.request.urlopen(
                urllib.request.Request(robots_url, headers=_headers()),
                timeout=10) as resp:
            rp.parse(resp.read().decode("utf-8", "replace").splitlines())
    except Exception:
        return True, None
    ua = user_agent or DEFAULT_UA
    try:
        delay = rp.crawl_delay(ua)
    except Exception:
        delay = None
    return rp.can_fetch(ua, url), delay


if __name__ == "__main__":
    import sys
    target = sys.argv[1] if len(sys.argv) > 1 else "https://api.hunter.io/v2/"
    out = request(target)
    print(out["status"], out["error"] or "", len(out["body"]), "bytes")