"""TONE3000 (https://www.tone3000.com/api): sign in, pick a cabinet IR, download it.

OAuth 2.0 with PKCE and a loopback redirect: the browser opens the TONE3000 flow and comes
back to a one-shot HTTP server on ``127.0.0.1``. With ``prompt=select_tone`` the user browses
the catalogue on TONE3000 and the callback carries the chosen ``tone_id``. Tokens are kept in
``$XDG_CONFIG_HOME/ge150/tone3000.json`` (mode 0600) and refreshed when they expire.

The publishable key (``client_id``) is created by the user in their TONE3000 settings, with
:data:`REDIRECT_URI` registered there (docs/plan-ia-tone3000.md §5).
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import html
import json
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

from ge150.i18n import tr

BASE_URL = "https://www.tone3000.com"
API = "/api/v1"
REDIRECT_PORT = 53682
REDIRECT_URI = f"http://127.0.0.1:{REDIRECT_PORT}/callback"
AGENT = "ge150-linux"
TIMEOUT_S = 30
LOGIN_TIMEOUT_S = 300


class Tone3000Error(Exception):
    pass


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "ge150"


def cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "ge150" / "tone3000"


# --- credentials --------------------------------------------------------------------------


@dataclass
class Credentials:
    client_id: str = ""
    access_token: str = ""
    refresh_token: str = ""
    expires_at: float = 0.0

    @property
    def signed_in(self) -> bool:
        return bool(self.access_token)


class TokenStore:
    """JSON file only readable by the user."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or config_dir() / "tone3000.json"

    def load(self) -> Credentials:
        try:
            data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return Credentials(client_id=os.environ.get("TONE3000_CLIENT_ID", ""))
        known = Credentials.__dataclass_fields__
        return Credentials(**{k: v for k, v in data.items() if k in known})

    def save(self, creds: Credentials) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as handle:
            json.dump(asdict(creds), handle)
        tmp.replace(self.path)


# --- PKCE + loopback callback ---------------------------------------------------------------


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:96]
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


@dataclass
class Callback:
    code: str = ""
    state: str = ""
    tone_id: str = ""
    error: str = ""


def parse_callback(url: str) -> Callback:
    query = {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlparse(url).query).items()}
    return Callback(query.get("code", ""), query.get("state", ""), query.get("tone_id", ""),
                    query.get("error", ""))


@dataclass
class Pending:
    url: str
    verifier: str
    state: str


class _CallbackServer(HTTPServer):
    result: Callback | None = None


class _Handler(BaseHTTPRequestHandler):
    server: _CallbackServer

    def do_GET(self) -> None:
        url = urllib.parse.urlparse(self.path)
        if url.path != "/callback":
            self.send_error(404)
            return
        self.server.result = parse_callback(self.path)
        ok = not self.server.result.error
        message = (tr("Listo: vuelve al editor.") if ok else
                   tr("TONE3000 respondió: {error}", error=self.server.result.error))
        body = f"<h2>GE150 Editor</h2><p>{html.escape(message)}</p>".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        pass


def wait_for_callback(port: int = REDIRECT_PORT, timeout: float = LOGIN_TIMEOUT_S,
                      ready: Callable[[], object] | None = None) -> Callback:
    """Serve ``/callback`` on 127.0.0.1 until the browser comes back (or time out)."""
    try:
        server = _CallbackServer(("127.0.0.1", port), _Handler)
    except OSError as exc:
        raise Tone3000Error(tr("port {port} is busy: {error}", port=port, error=exc)) from exc
    server.timeout = 0.5
    deadline = time.monotonic() + timeout
    try:
        if ready is not None:
            ready()
        while server.result is None and time.monotonic() < deadline:
            server.handle_request()
    finally:
        server.server_close()
    if server.result is None:
        raise Tone3000Error(tr("no answer from TONE3000 (timed out)"))
    return server.result


# --- data -------------------------------------------------------------------------------------


@dataclass
class Tone:
    id: int
    title: str
    author: str
    author_url: str
    avatar_url: str
    license: str
    format: str
    gear: str
    description: str = ""
    image_url: str = ""

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Tone:
        user = data.get("user") or {}
        author = user.get("display_name") or user.get("username") or "?"
        return cls(int(data["id"]), str(data.get("title", "")), str(author),
                   str(user.get("url", "")), str(user.get("avatar_url") or ""),
                   str(data.get("license", "")), str(data.get("format", "")),
                   str(data.get("gear", "")), str(data.get("description") or ""),
                   str(next(iter(data.get("images") or []), "")))

    @property
    def page_url(self) -> str:
        return f"{BASE_URL}/tones/{self.id}"


@dataclass
class ToneModel:
    id: int
    name: str
    url: str
    size: str = ""

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> ToneModel:
        return cls(int(data["id"]), str(data.get("name", "")), str(data["model_url"]),
                   str(data.get("size") or ""))


@dataclass
class Download:
    tone: Tone
    model: ToneModel
    path: Path
    extra: dict[str, Any] = field(default_factory=dict)


# --- client -----------------------------------------------------------------------------------


class Tone3000Client:
    def __init__(self, store: TokenStore | None = None, base_url: str = BASE_URL,
                 open_browser: Callable[[str], object] = webbrowser.open,
                 redirect_port: int = REDIRECT_PORT) -> None:
        self.store = store or TokenStore()
        self.creds = self.store.load()
        self.base_url = base_url.rstrip("/")
        self.open_browser = open_browser
        self.redirect_port = redirect_port

    @property
    def redirect_uri(self) -> str:
        return f"http://127.0.0.1:{self.redirect_port}/callback"

    def set_client_id(self, client_id: str) -> None:
        self.creds.client_id = client_id.strip()
        self.store.save(self.creds)

    def logout(self) -> None:
        self.creds = Credentials(client_id=self.creds.client_id)
        self.store.save(self.creds)

    # --- OAuth ------------------------------------------------------------------------------

    def authorize_url(self, challenge: str, state: str, prompt: str = "",
                      tone_id: str = "") -> str:
        if not self.creds.client_id:
            raise Tone3000Error(tr("set your TONE3000 publishable key first (client_id)"))
        params = {"client_id": self.creds.client_id, "redirect_uri": self.redirect_uri,
                  "response_type": "code", "code_challenge": challenge,
                  "code_challenge_method": "S256", "state": state, "menubar": "true"}
        if prompt:
            params["prompt"] = prompt
        if prompt == "select_tone":
            params.update({"format": "ir", "preview": "true"})
        if tone_id:
            params["tone_id"] = tone_id
        return f"{self.base_url}{API}/oauth/authorize?{urllib.parse.urlencode(params)}"

    def begin(self, prompt: str = "", tone_id: str = "") -> Pending:
        """Start an authorization: the URL to show and what :meth:`finish` needs."""
        verifier, challenge = pkce_pair()
        state = secrets.token_urlsafe(24)
        return Pending(self.authorize_url(challenge, state, prompt, tone_id), verifier, state)

    def finish(self, pending: Pending, callback: Callback) -> Callback:
        """Check the redirect TONE3000 sent back and exchange its code for tokens."""
        if callback.state != pending.state:
            raise Tone3000Error(tr("state mismatch: ignoring the answer"))
        if callback.error or not callback.code:
            raise Tone3000Error(f"TONE3000: {callback.error or tr('no code')}")
        self._token({"grant_type": "authorization_code", "code": callback.code,
                     "code_verifier": pending.verifier, "redirect_uri": self.redirect_uri,
                     "client_id": self.creds.client_id})
        return callback

    def is_callback(self, url: str) -> bool:
        return url.split("?", 1)[0] == self.redirect_uri

    def _flow(self, prompt: str = "", tone_id: str = "") -> Callback:
        """In the user's browser, back through a one-shot server on 127.0.0.1."""
        pending = self.begin(prompt, tone_id)
        callback = wait_for_callback(self.redirect_port,
                                     ready=lambda: self.open_browser(pending.url))
        return self.finish(pending, callback)

    def login(self) -> None:
        self._flow()

    def select_tone(self) -> int:
        """Open the TONE3000 catalogue (IRs only); returns the chosen tone id."""
        callback = self._flow("select_tone")
        if not callback.tone_id:
            raise Tone3000Error(tr("no tone was selected"))
        return int(callback.tone_id)

    def _token(self, form: dict[str, str]) -> None:
        data = self._request("POST", "/oauth/token", form=form, auth=False)
        self.creds.access_token = str(data["access_token"])
        self.creds.refresh_token = str(data.get("refresh_token") or self.creds.refresh_token)
        self.creds.expires_at = time.time() + float(data.get("expires_in", 3600)) - 60
        self.store.save(self.creds)

    def _refresh(self) -> None:
        if not self.creds.refresh_token:
            raise Tone3000Error(tr("sign in to TONE3000 first"))
        try:
            self._token({"grant_type": "refresh_token", "refresh_token": self.creds.refresh_token,
                         "client_id": self.creds.client_id})
        except Tone3000Error:
            self.logout()  # invalid_grant: the refresh token expired
            raise Tone3000Error(tr("the TONE3000 session expired: sign in again")) from None

    # --- HTTP ------------------------------------------------------------------------------

    def _request(self, method: str, path: str, query: dict[str, Any] | None = None,
                 form: dict[str, str] | None = None, auth: bool = True) -> Any:
        url = f"{self.base_url}{API}{path}"
        if query:
            url += "?" + urllib.parse.urlencode(query)
        body = urllib.parse.urlencode(form).encode() if form is not None else None
        headers = {"User-Agent": AGENT, "Accept": "application/json"}
        if form is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        if auth:
            headers["Authorization"] = f"Bearer {self._access_token()}"
        raw = self._open(urllib.request.Request(url, body, headers, method=method))
        return json.loads(raw) if raw else None

    def _access_token(self) -> str:
        if not self.creds.signed_in:
            raise Tone3000Error(tr("sign in to TONE3000 first"))
        if time.time() >= self.creds.expires_at:
            self._refresh()
        return self.creds.access_token

    def _open(self, request: urllib.request.Request) -> bytes:
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
                return bytes(response.read())
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:200]
            if exc.code == 429:
                raise Tone3000Error(tr("TONE3000 rate limit reached: wait a minute")) from exc
            message = tr("TONE3000 HTTP {code}: {detail}", code=exc.code, detail=detail)
            raise Tone3000Error(message) from exc
        except urllib.error.URLError as exc:
            raise Tone3000Error(tr("cannot reach TONE3000: {reason}", reason=exc.reason)) from exc

    # --- API --------------------------------------------------------------------------------

    def user(self) -> dict[str, Any]:
        return dict(self._request("GET", "/user"))

    def tone(self, tone_id: int) -> Tone:
        return Tone.from_json(self._request("GET", f"/tones/{tone_id}"))

    def models(self, tone_id: int) -> list[ToneModel]:
        data = self._request("GET", "/models", {"tone_id": tone_id})
        items = data.get("data", data) if isinstance(data, dict) else data
        return [ToneModel.from_json(m) for m in items]

    def search(self, query: str, page: int = 1) -> list[Tone]:
        """Heavily rate-limited by TONE3000: prefer :meth:`select_tone` for browsing."""
        data = self._request("GET", "/tones/search",
                             {"query": query, "format": "ir", "page": page, "page_size": 10})
        return [Tone.from_json(t) for t in data.get("data", [])]

    def download(self, tone_id: int, model_id: int | None = None) -> Download:
        """Download one IR of a tone (the first one unless ``model_id`` is given)."""
        tone = self.tone(tone_id)
        if tone.format and tone.format != "ir":
            raise Tone3000Error(tr(
                "tone {tone} is a {format} model, not an IR", tone=tone_id, format=tone.format,
            ))
        models = self.models(tone_id)
        if not models:
            raise Tone3000Error(tr("tone {tone} has no downloadable files", tone=tone_id))
        model = next((m for m in models if m.id == model_id), None) if model_id else models[0]
        if model is None:
            raise Tone3000Error(tr(
                "tone {tone} has no model {model}", tone=tone_id, model=model_id,
            ))
        headers = {"User-Agent": AGENT, "Authorization": f"Bearer {self._access_token()}"}
        data = self._open(urllib.request.Request(model.url, None, headers))
        if not data.startswith(b"RIFF"):
            raise Tone3000Error(tr(
                "{name}: the file is not a WAV impulse response", name=model.name,
            ))
        folder = cache_dir() / str(tone_id)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{model.id}.wav"
        path.write_bytes(data)
        if tone.image_url:  # shown as the cabinet's photo in the editor
            with contextlib.suppress(Tone3000Error):
                fetch_image(tone.image_url)
        return Download(tone, model, path, {"models": [asdict(m) for m in models]})


# --- where each user IR came from (attribution) -------------------------------------------------


def _image_path(url: str) -> Path:
    return cache_dir() / "images" / hashlib.sha256(url.encode()).hexdigest()[:32]


def cached_image(url: str) -> Path | None:
    """The image if it was already downloaded (never touches the network)."""
    path = _image_path(url) if url else None
    return path if path is not None and path.is_file() else None


def fetch_image(url: str) -> Path:
    """Download (once) a tone image or an avatar into the cache; returns the file."""
    path = _image_path(url)
    if path.exists():
        return path
    if not url.startswith("https://"):
        raise Tone3000Error(f"not an https image: {url[:80]}")
    request = urllib.request.Request(url, headers={"User-Agent": AGENT})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
            data = bytes(response.read(8 * 1024 * 1024))
    except (urllib.error.URLError, TimeoutError) as exc:
        raise Tone3000Error(f"cannot download the image: {exc}") from exc
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


class IrSources:
    """``~/.local/share/ge150/ir_sources.json``: user IR slot -> TONE3000 tone, author, licence."""

    def __init__(self, path: Path | None = None) -> None:
        base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
        self.path = path or Path(base) / "ge150" / "ir_sources.json"

    def load(self) -> dict[str, dict[str, Any]]:
        try:
            data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def record(self, slot: int, download: Download, name: str = "") -> None:
        """``name``: what the slot is called on the pedal (default: the tone title)."""
        data = self.load()
        tone = download.tone
        data[str(slot)] = {"source": "tone3000", "tone_id": tone.id, "title": tone.title,
                           "name": name or tone.title[:14],
                           "model": download.model.name, "author": tone.author,
                           "author_url": tone.author_url, "avatar_url": tone.avatar_url,
                           "license": tone.license, "url": tone.page_url,
                           "description": tone.description, "image_url": tone.image_url}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=1))

    def update(self, slot: int, **fields: Any) -> None:
        data = self.load()
        if str(slot) in data:
            data[str(slot)].update(fields)
            self.path.write_text(json.dumps(data, indent=1))

    def matching(self, names: list[str]) -> dict[str, dict[str, Any]]:
        """Records whose slot still holds the IR they describe (by its name on the pedal)."""
        result = {}
        for slot, source in self.load().items():
            index = int(slot) - 1
            stored = str(source.get("name") or str(source.get("title", ""))[:14])
            if 0 <= index < len(names) and names[index] == stored:
                result[slot] = source
        return result

    def forget(self, slot: int) -> None:
        data = self.load()
        if data.pop(str(slot), None) is not None:
            self.path.write_text(json.dumps(data, indent=1))
