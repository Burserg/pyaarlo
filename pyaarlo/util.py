import base64
import threading
import time
from datetime import datetime, timezone

import requests


class BandwidthLimiter:
    """Token bucket rate limiter shared across download threads.

    Callers report transferred bytes with `throttle()` which sleeps as
    needed to hold the aggregate transfer to `rate` bytes per second.
    Allows up to one second of burst.
    """

    def __init__(self, rate):
        self._rate = float(rate)
        self._tokens = self._rate
        self._last = time.monotonic()
        self._lock = threading.Lock()

    def throttle(self, nbytes):
        with self._lock:
            now = time.monotonic()
            self._tokens = min(
                self._rate, self._tokens + (now - self._last) * self._rate
            )
            self._last = now
            self._tokens -= nbytes
            wait = -self._tokens / self._rate if self._tokens < 0 else 0.0
        if wait > 0:
            time.sleep(wait)


def utc_to_local(utc_dt):
    return utc_dt.replace(tzinfo=timezone.utc).astimezone(tz=None)


def the_epoch():
    return utc_to_local(datetime.fromtimestamp(0, tz=timezone.utc))


def arlotime_to_time(timestamp):
    """Convert Arlo timestamp to Unix timestamp."""
    return int(timestamp / 1000)


def arlotime_to_datetime(timestamp):
    """Convert Arlo timestamp to Python datetime."""
    return utc_to_local(datetime.fromtimestamp(int(timestamp / 1000), tz=timezone.utc))


def arlotime_strftime(timestamp, date_format="%Y-%m-%dT%H:%M:%S"):
    """Convert Arlo timestamp to time string."""
    return arlotime_to_datetime(timestamp).strftime(date_format)


def time_to_arlotime(timestamp=None):
    """Convert Unix timestamp to Arlo timestamp."""
    if timestamp is None:
        timestamp = time.time()
    return int(timestamp * 1000)


def now_strftime(date_format="%Y-%m-%dT%H:%M:%S"):
    """Convert now to time string."""
    return datetime.now().strftime(date_format)


def days_until(when):
    now = datetime.now()
    when = datetime.utcfromtimestamp(when)
    if when <= now:
        return 0
    return (when - now).days


def httptime_to_datetime(http_timestamp):
    """Convert HTTP timestamp to Python datetime."""
    return utc_to_local(datetime.strptime(http_timestamp, "%a, %d %b %Y %H:%M:%S GMT"))


def httptime_strftime(http_timestamp, date_format="%Y-%m-%dT%H:%M:%S"):
    """Convert HTTP timestamp to time string."""
    return httptime_to_datetime(http_timestamp).strftime(date_format)


def _http_get(url):
    """Download HTTP data."""

    if url is None:
        return None

    try:
        ret = requests.get(url)
    except requests.exceptions.SSLError:
        return None
    except Exception:
        return None

    if ret.status_code != 200:
        return None
    return ret


def http_get(url, filename=None):
    """Download HTTP data."""

    ret = _http_get(url)
    if ret is None:
        return False

    if filename is None:
        return ret.content

    with open(filename, "wb") as data:
        data.write(ret.content)
    return True


def http_get_img(url, ignore_date=False):
    """Download HTTP image data."""

    ret = _http_get(url)
    if ret is None:
        return None, datetime.now().astimezone()

    date = None
    if not ignore_date:
        date = ret.headers.get("Last-Modified", None)
        if date is not None:
            date = httptime_to_datetime(date)
    if date is None:
        date = datetime.now().astimezone()

    return ret.content, date


def http_download(url, filename, limiter=None, chunk=65536):
    """Stream a URL to a file, optionally holding to a bandwidth limit.

    :param url: url to download
    :param filename: where to write the data
    :param limiter: optional BandwidthLimiter shared with other downloads
    :param chunk: bytes to read per iteration
    :return: True on success, False otherwise
    """
    if url is None:
        return False
    try:
        with requests.get(url, stream=True, timeout=(10, 60)) as ret:
            if ret.status_code != 200:
                return False
            with open(filename, "wb") as data:
                for piece in ret.iter_content(chunk):
                    data.write(piece)
                    if limiter is not None:
                        limiter.throttle(len(piece))
    except Exception:
        return False
    return True


def http_stream(url, chunk=4096):
    """Generate stream for a given record video.

    :param url: url of stream to read
    :param chunk: chunk bytes to read per time
    :returns generator object
    """
    ret = requests.get(url, stream=True)
    ret.raise_for_status()
    for data in ret.iter_content(chunk):
        yield data


def rgb_to_hex(rgb):
    """Convert HA color to Arlo color."""
    return "#{:02x}{:02x}{:02x}".format(rgb[0], rgb[1], rgb[2])


def hex_to_rgb(h):
    """Convert Arlo color to HA color."""
    return {"red": int(h[1:3], 16), "green": int(h[3:5], 16), "blue": int(h[5:7], 16)}


def to_b64(in_str):
    """Convert a string into a base64 string."""
    return base64.b64encode(in_str.encode()).decode()
