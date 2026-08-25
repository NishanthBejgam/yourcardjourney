"""
Karat Board (website build) - every jeweller's gold rate on one screen.

This is the PUBLIC copy. The app you run day to day is ../gold-board/, on port
8780, and it is deliberately left alone. This one exists to be built into a
static site: `python build_site.py site` runs one sweep and writes a folder any
web host can serve, and deploy/karat-board.yml has GitHub do that every 15
minutes for free. See DEPLOY.md.

Its own server (9780) is only for previewing the result locally.

Eight merchants publish the same two numbers eight different ways: one buries
them in a marquee, one paints them into a React app, one drops a PDF on S3, one
prints 22K and leaves you to do the 24K arithmetic yourself. Checking all of
them by hand means eight tabs and a calculator.

This does the checking. A background thread walks the merchant list once an
hour, normalises whatever each site hands back into a plain per-gram 22K and
24K rate, and the single screen at http://127.0.0.1:8780 shows the lot side by
side with the cheapest one called out.

Nothing about any merchant lives in this file. Every URL, every pattern, every
way of reading a page is a recipe in merchants.json - so when a site changes
shape, you edit that file, press Refresh, and the board is right again. Adding
a ninth merchant is a new block in the same file.

Only 22K and 24K are kept. 18K and 14K are dropped on purpose. Where a site
publishes 22K alone, 24K is derived as 22K x 24/22 and marked as derived so you
always know which number came off the site and which one came off a calculator.

Start it with "Start Karat Board.cmd" or:
    python app.py
"""

import json
import os
import re
import html as htmllib
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST = "127.0.0.1"
# 9780, NOT 8780. This is the website copy; the local app you actually use lives
# in ../gold-board/ and owns 8780. Binding it here would shadow the real one -
# the same rule the ycj-suite copies follow (original port + 1000).
PORT = int(os.environ.get("KB_PORT") or 9780)
LIVE_APP_PORTS = range(8765, 8800)
if PORT in LIVE_APP_PORTS:
    raise SystemExit("Port %d belongs to a live app. The website copy uses 9780." % PORT)
HERE = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(HERE, "static")
MERCHANTS = os.path.join(HERE, "merchants.json")
BOARD = os.environ.get("KB_STATE") or os.path.join(HERE, "board.json")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

# Windows ships curl.exe; every CI runner and Linux box just calls it curl. The
# binary matters because Python's own TLS gets 403'd by the bot walls in front
# of a couple of these sites - see fetch().
CURL = "curl.exe" if os.name == "nt" else "curl"

# 24K is pure; 22K is 916 parts per thousand. The ratio between the two prices is
# just the purity ratio, which is why a board that prints only one of them can
# still be read - in either direction. A jeweller posts 22K and no 24K; a bullion
# refiner posts fine 999 and no 22K.
K24_FROM_K22 = 24.0 / 22.0
K22_FROM_K24 = 22.0 / 24.0

IST = timezone(timedelta(hours=5, minutes=30))

_lock = threading.RLock()
_refreshing = threading.Event()


# --------------------------------------------------------------------------- #
#  Config + state
# --------------------------------------------------------------------------- #
def load_merchants():
    with open(MERCHANTS, encoding="utf-8") as fh:
        return json.load(fh)


def load_board():
    try:
        with open(BOARD, encoding="utf-8") as fh:
            board = json.load(fh)
    except Exception:
        board = {}
    board.setdefault("rates", {})
    board.setdefault("history", {})
    board.setdefault("manual", {})
    board.setdefault("lastRefresh", None)
    return board


def save_board(board):
    tmp = BOARD + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(board, fh, indent=2)
    os.replace(tmp, BOARD)


def now_iso():
    return datetime.now(IST).isoformat(timespec="seconds")


# --------------------------------------------------------------------------- #
#  Fetching
# --------------------------------------------------------------------------- #
def fetch(url, mode="urllib", timeout=40):
    """Return the raw bytes of a page.

    Tanishq and Bhima sit behind bot walls that reject Python's TLS handshake
    however honest the headers look, but let Windows' own curl.exe straight
    through. So the fetch mode is per-merchant config, not a global choice.
    """
    if mode == "curl":
        # The status code is appended after a marker: curl exits 0 on a 403, and
        # a Cloudflare block page parsed as HTML looks exactly like "the pattern
        # stopped matching". Those are very different problems, so name them.
        out = subprocess.run(
            [CURL, "-sL", "--compressed", "--max-time", str(timeout),
             "-A", UA, "-H", "Accept-Language: en-IN,en;q=0.9",
             "-w", "\n%s%%{http_code}" % _STATUS_MARK, url],
            capture_output=True, timeout=timeout + 15,
        )
        if out.returncode != 0:
            raise RuntimeError("curl exit %d %s" % (out.returncode, out.stderr[:120]))
        body, status = _split_status(out.stdout)
        if status >= 400:
            raise RuntimeError("HTTP %d - the site refused us (a bot wall, most "
                               "likely; this IP is not welcome)" % status)
        if not body:
            raise RuntimeError("empty response")
        return body

    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept-Language": "en-IN,en;q=0.9",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        raise RuntimeError("HTTP %d - the site refused us" % exc.code)


_STATUS_MARK = b"__KB_HTTP__".decode()


def _split_status(raw):
    """Peel the trailing "\n__KB_HTTP__<code>" that -w appended."""
    mark = _STATUS_MARK.encode()
    at = raw.rfind(mark)
    if at < 0:
        return raw, 0
    try:
        status = int(raw[at + len(mark):].strip() or 0)
    except ValueError:
        status = 0
    return raw[:at].rstrip(b"\r\n"), status


def to_text(raw):
    """HTML -> the words a reader would see, whitespace collapsed."""
    s = raw.decode("utf-8", "replace")
    s = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", s)
    s = re.sub(r"(?s)<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", htmllib.unescape(s)).strip()


def to_number(s):
    """'1,77,019.42' and '₹ 15,075' both mean a number. Indian grouping included."""
    s = re.sub(r"[^\d.]", "", s)
    if not s:
        raise ValueError("no digits")
    return float(s)


# --------------------------------------------------------------------------- #
#  Adapters - one per way a merchant publishes its rate
# --------------------------------------------------------------------------- #
def _pick(spec, haystack):
    """Run one { pattern, divide } recipe against a body of text."""
    if not spec:
        return None
    m = re.search(spec["pattern"], haystack, re.S | re.I)
    if not m:
        return None
    return to_number(m.group(1)) / float(spec.get("divide") or 1)


def read_pairs(spec, pairs):
    """pairs_json: the body is [[label, value], ...]; match the label."""
    if not spec:
        return None
    want = re.compile(spec["pattern"], re.I)
    for label, value in pairs:
        if want.search(re.sub(r"\s+", " ", str(label)).strip()):
            return to_number(str(value)) / float(spec.get("divide") or 1)
    return None


def read_merchant(m):
    """Fetch one merchant and return {buy24, buy22, sell24, sell22, derived24}."""
    src = m.get("source") or {}
    adapter = src.get("adapter") or "link_only"
    if adapter == "link_only":
        return None

    if adapter == "socketio_livedata":
        out = read_socketio(src)
        return finish(src, out)

    raw = fetch(src["url"], src.get("fetch") or "urllib")

    if adapter == "text_regex":
        body = to_text(raw)
    elif adapter == "raw_regex":
        body = raw.decode("utf-8", "replace")
    elif adapter == "pdf_regex":
        body = pdf_text(raw)
    elif adapter == "pairs_json":
        body = json.loads(raw.decode("utf-8", "replace"))
    else:
        raise RuntimeError("unknown adapter '%s'" % adapter)

    out = {}
    for side in ("buy", "sell"):
        for karat in ("k24", "k22"):
            spec = (src.get(side) or {}).get(karat)
            if adapter == "pairs_json":
                val = read_pairs(spec, body)
            else:
                val = _pick(spec, body)
            if val:
                out[side + karat[1:]] = round(val, 2)

    return finish(src, out)


def finish(src, out):
    """Fill in the purity a merchant does not print, and refuse an empty read."""
    if src.get("deriveK24") and out.get("buy22") and not out.get("buy24"):
        out["buy24"] = round(out["buy22"] * K24_FROM_K22, 2)
        out["derived24"] = True
    if src.get("deriveK24") and out.get("sell22") and not out.get("sell24"):
        out["sell24"] = round(out["sell22"] * K24_FROM_K22, 2)
    if src.get("deriveK22") and out.get("buy24") and not out.get("buy22"):
        out["buy22"] = round(out["buy24"] * K22_FROM_K24, 2)
        out["derived22"] = True

    if not out.get("buy24") and not out.get("buy22"):
        raise RuntimeError("page fetched, but no rate matched - patterns need a look")
    return out


def soap_json(url, action, timeout=25):
    """Call one ASP.NET .asmx method. They wrap their JSON payload inside the XML."""
    envelope = ('<?xml version="1.0" encoding="utf-8"?>'
                '<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">'
                '<soap:Body><{0} xmlns="http://tempuri.org/" /></soap:Body>'
                '</soap:Envelope>').format(action)
    out = subprocess.run(
        [CURL, "-s", "-m", str(timeout), "-A", UA,
         "-H", "Content-Type: text/xml; charset=utf-8",
         "-H", 'SOAPAction: "http://tempuri.org/%s"' % action,
         "--data-binary", envelope, url],
        capture_output=True, timeout=timeout + 10)
    xml = out.stdout.decode("utf-8", "replace")
    m = re.search(r"<%sResult>(.*?)</%sResult>" % (action, action), xml, re.S)
    if not m:
        raise RuntimeError("%s returned no result" % action)
    outer = json.loads(htmllib.unescape(m.group(1)))
    data = outer.get("Data", outer)
    return json.loads(data) if isinstance(data, str) else data


def socketio_event(base, event, timeout=25, tries=5):
    """Read one event off a socket.io v4 server without opening a websocket.

    socket.io still speaks plain HTTP long-polling, so a handshake, a "40"
    connect and a GET are enough to catch the broadcast the browser would get.
    That is the whole trick behind reading a live ticker from a script.
    """
    def q(**kw):
        return (base.rstrip("/") + "/socket.io/?"
                + urllib.parse.urlencode(dict(EIO="4", transport="polling", **kw)))

    def hit(url, data=None):
        cmd = [CURL, "-s", "-m", str(timeout), "-A", UA]
        if data is not None:
            cmd += ["-X", "POST", "-H", "Content-Type: text/plain;charset=UTF-8",
                    "--data-binary", data]
        cmd.append(url)
        return subprocess.run(cmd, capture_output=True,
                              timeout=timeout + 10).stdout.decode("utf-8", "replace")

    handshake = hit(q(t=str(int(time.time() * 1000))))
    if not handshake.startswith("0"):
        raise RuntimeError("no socket.io handshake")
    sid = json.loads(handshake[1:])["sid"]
    hit(q(sid=sid), data="40")          # join the default namespace
    try:
        for _ in range(tries):
            body = hit(q(sid=sid))
            at = body.find('42["' + event + '"')
            if at >= 0:
                payload, _end = json.JSONDecoder().raw_decode(body[at + 2:])
                return payload[1]
        raise RuntimeError("connected, but no '%s' event arrived" % event)
    finally:
        try:
            hit(q(sid=sid), data="41")  # say goodbye rather than time out
        except Exception:
            pass


def read_socketio(src):
    """A live ticker: Ask price off the socket, plus the merchant's own premium.

    Aspect prints "FINE GOLD 999 - 164139", and that number is not stored
    anywhere - it is the Ask on the raw gold symbol plus a premium the merchant
    publishes separately. So both halves get fetched and added.
    """
    rows = socketio_event(src["url"], src.get("event") or "LiveData")
    asks = {}
    for row in rows:
        try:
            asks[str(row.get("symbol") or "").lower()] = to_number(str(row.get("Ask")))
        except Exception:
            pass

    premium = {}
    if src.get("premiumUrl"):
        for item in soap_json(src["premiumUrl"], src["premiumAction"]):
            try:
                premium[item.get("name")] = float(item.get("premium") or 0)
            except Exception:
                pass

    out = {}
    for side in ("buy", "sell"):
        for karat in ("k24", "k22"):
            spec = (src.get(side) or {}).get(karat)
            if not spec:
                continue
            ask = asks.get(str(spec.get("symbol") or "").lower())
            if ask is None:
                continue
            add = premium.get(spec.get("premiumName"), 0) if spec.get("premiumName") else 0
            out[side + karat[1:]] = round((ask + add) / float(spec.get("divide") or 1), 2)
    return out


def pdf_text(raw):
    """MMTC-PAMP drops a price list PDF on S3; read the first page as text."""
    try:
        from pypdf import PdfReader
    except ImportError:
        raise RuntimeError("pypdf not installed - run: pip install pypdf")
    import io
    reader = PdfReader(io.BytesIO(raw))
    return "\n".join(p.extract_text() or "" for p in reader.pages)


# --------------------------------------------------------------------------- #
#  The refresh pass
# --------------------------------------------------------------------------- #
def refresh_all(only=None):
    """Walk the merchant list, update the board, keep a little history."""
    if _refreshing.is_set():
        return
    _refreshing.set()
    try:
        cfg = load_merchants()
        for m in cfg["merchants"]:
            if only and m["id"] != only:
                continue
            entry = {"fetched": now_iso()}
            try:
                got = read_merchant(m)
                if got is None:
                    entry["ok"] = False
                    entry["error"] = "no automatic source"
                    entry["linkOnly"] = True
                else:
                    entry.update(got)
                    entry["ok"] = True
            except Exception as exc:
                entry["ok"] = False
                entry["error"] = str(exc)[:200]

            with _lock:
                board = load_board()
                prev = board["rates"].get(m["id"]) or {}
                # A failed pass must not blank a good rate - keep the last one
                # and let the tile say how stale it is.
                if not entry.get("ok") and prev.get("buy24"):
                    for k in ("buy24", "buy22", "sell24", "sell22", "derived24"):
                        if prev.get(k):
                            entry[k] = prev[k]
                    entry["fetched"] = prev.get("fetched")
                    entry["stale"] = True
                board["rates"][m["id"]] = entry
                if entry.get("ok") and entry.get("buy24"):
                    hist = board["history"].setdefault(m["id"], [])
                    last = hist[-1] if hist else None
                    if not last or last.get("buy24") != entry["buy24"]:
                        hist.append({"t": entry["fetched"],
                                     "buy24": entry["buy24"],
                                     "buy22": entry.get("buy22")})
                        del hist[:-60]
                board["lastRefresh"] = now_iso()
                save_board(board)

            time.sleep(0.4)   # be a polite visitor
    finally:
        _refreshing.clear()


def refresher():
    """Once an hour, forever. Interval lives in merchants.json."""
    while True:
        try:
            mins = int(load_merchants().get("refreshMinutes") or 60)
        except Exception:
            mins = 60
        try:
            refresh_all()
        except Exception as exc:
            print("refresh failed:", exc)
        time.sleep(max(5, mins) * 60)


# --------------------------------------------------------------------------- #
#  What the screen gets
# --------------------------------------------------------------------------- #
def board_state():
    cfg = load_merchants()
    with _lock:
        board = load_board()

    rows = []
    for m in cfg["merchants"]:
        mid = m["id"]
        rate = dict(board["rates"].get(mid) or {})
        manual = board["manual"].get(mid)
        if manual and (manual.get("buy24") or manual.get("buy22")):
            rate = {"ok": True, "manual": True, "fetched": manual.get("at"),
                    "buy24": manual.get("buy24"), "buy22": manual.get("buy22"),
                    "sell24": manual.get("sell24"), "sell22": manual.get("sell22")}
            # Whichever purity was keyed in, the other one follows from it.
            if rate["buy22"] and not rate["buy24"]:
                rate["buy24"] = round(rate["buy22"] * K24_FROM_K22, 2)
                rate["derived24"] = True
            elif rate["buy24"] and not rate["buy22"]:
                rate["buy22"] = round(rate["buy24"] * K22_FROM_K24, 2)
                rate["derived22"] = True

        hist = board["history"].get(mid) or []
        rows.append({
            "id": mid, "name": m["name"], "short": m.get("short") or m["name"],
            "site": m["site"], "note": m.get("note") or "",
            "adapter": (m.get("source") or {}).get("adapter") or "link_only",
            "rate": rate,
            "spark": [h["buy24"] for h in hist[-24:] if h.get("buy24")],
        })

    return {
        "merchants": rows,
        "lastRefresh": board.get("lastRefresh"),
        "refreshMinutes": cfg.get("refreshMinutes") or 60,
        "refreshing": _refreshing.is_set(),
        "now": now_iso(),
    }


# --------------------------------------------------------------------------- #
#  HTTP
# --------------------------------------------------------------------------- #
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _json(self, obj, status=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        if not length:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def _serve_static(self, path):
        if path in ("/", ""):
            path = "/index.html"
        fpath = os.path.normpath(os.path.join(STATIC_DIR, path.lstrip("/")))
        if not fpath.startswith(STATIC_DIR) or not os.path.isfile(fpath):
            self.send_error(404)
            return
        ctype = ("text/html" if fpath.endswith(".html") else
                 "text/css" if fpath.endswith(".css") else
                 "application/javascript" if fpath.endswith(".js") else
                 "image/svg+xml" if fpath.endswith(".svg") else
                 "image/png" if fpath.endswith(".png") else
                 "image/x-icon" if fpath.endswith(".ico") else
                 "image/jpeg" if fpath.endswith(".jpg") else
                 "application/octet-stream")
        with open(fpath, "rb") as fh:
            body = fh.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/api/state"):
            self._json(board_state())
            return
        self._serve_static(self.path.split("?")[0])

    def do_POST(self):
        try:
            body = self._read_body()
        except Exception:
            self._json({"error": "bad request"}, 400)
            return

        if self.path.startswith("/api/refresh"):
            only = body.get("id")
            threading.Thread(target=refresh_all, args=(only,), daemon=True).start()
            self._json({"started": True})
            return

        if self.path.startswith("/api/manual"):
            mid = body.get("id")
            if not mid:
                self._json({"error": "no merchant"}, 400)
                return
            with _lock:
                board = load_board()
                if body.get("clear"):
                    board["manual"].pop(mid, None)
                else:
                    board["manual"][mid] = {
                        "buy24": _num_or_none(body.get("buy24")),
                        "buy22": _num_or_none(body.get("buy22")),
                        "sell24": _num_or_none(body.get("sell24")),
                        "sell22": _num_or_none(body.get("sell22")),
                        "at": now_iso(),
                    }
                save_board(board)
            self._json(board_state())
            return

        if self.path.startswith("/api/open"):
            url = body.get("url") or ""
            if url.startswith("http"):
                webbrowser.open(url)
            self._json({"ok": True})
            return

        self._json({"error": "unknown"}, 404)


def _num_or_none(v):
    try:
        return round(to_number(str(v)), 2) or None
    except Exception:
        return None


# --------------------------------------------------------------------------- #
#  Server plumbing (same single-instance guard the other suite apps use)
# --------------------------------------------------------------------------- #
class SingleInstanceServer(ThreadingHTTPServer):
    """Bind without SO_REUSEADDR so a genuine duplicate fails instead of starting."""
    allow_reuse_address = False


class RestartableServer(ThreadingHTTPServer):
    allow_reuse_address = True


def _instance_alive(url):
    try:
        with urllib.request.urlopen(url + "/api/state", timeout=2) as resp:
            return resp.status == 200
    except Exception:
        return False


def bind_server(url):
    try:
        return SingleInstanceServer((HOST, PORT), Handler)
    except OSError:
        pass
    if _instance_alive(url):
        print("Karat Board is already running at " + url)
        if not os.environ.get("KB_NO_OPEN"):
            webbrowser.open(url)
        return None
    for _ in range(10):
        try:
            return RestartableServer((HOST, PORT), Handler)
        except OSError:
            time.sleep(0.5)
    print("Port " + str(PORT) + " is held by another program. Close it and retry.")
    return None


def _read_board_json(kind, ref):
    if kind == "file":
        if not os.path.isfile(ref):
            return None
        with open(ref, encoding="utf-8") as fh:
            return json.load(fh)
    return json.loads(fetch(ref, "urllib", timeout=20).decode("utf-8", "replace"))


def seed_board(sources):
    """Start a build from the best rates already known, not from nothing.

    A CI runner is blank every time, and Cloudflare refuses two of these
    merchants from any datacenter IP - so without a seed those two tiles would
    be empty on every single build. Seeding keeps their last good number, which
    refresh_all() then marks stale with the time it was really read.

    Two sources, because neither alone is enough: the rates.json committed in
    the repo (which a sweep from a residential connection refreshes, and which
    is always present at checkout) and the one currently published (fresher for
    everything that is not blocked). Per merchant, the later read wins.
    """
    best = {}
    for kind, ref in sources:
        if not ref:
            continue
        try:
            data = _read_board_json(kind, ref)
        except Exception as exc:
            print("seed: skipped %s %s (%s)" % (kind, ref, exc))
            continue
        if not data:
            continue
        for m in data.get("merchants", []):
            rate = m.get("rate") or {}
            if not (rate.get("buy24") or rate.get("buy22")):
                continue
            held = best.get(m["id"])
            if not held or (rate.get("fetched") or "") > (held.get("fetched") or ""):
                best[m["id"]] = rate

    if not best:
        print("seed: nothing to carry over - starting cold")
        return
    board = load_board()
    if board["rates"]:
        return
    board["rates"] = best
    save_board(board)
    print("seed: carried %d rates over (%s)" % (len(best), ", ".join(sorted(best))))


def snapshot(out_dir, every_minutes):
    """Run one sweep and write rates.json - the whole of the hosted site's data.

    A visitor to the public page never waits on eight merchant sites; a schedule
    does that work up front and leaves a file behind. This is what turns the app
    into something GitHub Pages (or any static host) can serve for nothing.
    """
    seed_board([("file", os.environ.get("KB_SEED_FILE")),
                ("url", os.environ.get("KB_SEED_URL"))])
    refresh_all()
    state = board_state()
    state["static"] = True
    state["refreshMinutes"] = every_minutes
    state["builtAt"] = now_iso()

    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "rates.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=1)

    ok = sum(1 for m in state["merchants"] if (m.get("rate") or {}).get("ok"))
    print("wrote %s - %d of %d merchants read" % (path, ok, len(state["merchants"])))
    for m in state["merchants"]:
        r = m.get("rate") or {}
        print("  %-9s %-6s 24K %-10s 22K %-10s %s" % (
            m["id"], "ok" if r.get("ok") else "FAIL",
            r.get("buy24"), r.get("buy22"), r.get("error") or ""))
    # A sweep that read nothing is a broken build, not a quiet one.
    return 0 if ok else 1


def main():
    if "--snapshot" in sys.argv:
        i = sys.argv.index("--snapshot")
        out = sys.argv[i + 1] if len(sys.argv) > i + 1 else os.path.join(HERE, "site")
        every = int(os.environ.get("KB_EVERY") or 15)
        sys.exit(snapshot(out, every))

    url = "http://" + HOST + ":" + str(PORT)
    server = bind_server(url)
    if server is None:
        return
    threading.Thread(target=refresher, daemon=True).start()
    print("Karat Board running at " + url)
    print("Close this window (or press Ctrl+C) to stop.")
    if not os.environ.get("KB_NO_OPEN"):
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
