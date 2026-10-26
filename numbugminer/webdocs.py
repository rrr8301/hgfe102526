"""Directly referenced Web resources, fetched as documents.

A repository rarely carries everything needed to build and run it: installation
instructions live on a docs site, an API page defines the function under test, a
reproduction guide sits in a gist. Those pages are *referenced* by the PR, the
issue discussion or the README, so they are evidence the miner is entitled to —
but only the pages actually referenced.

The rule is deliberate: follow links that appear in the collected artifacts, once
each, and never follow links found in the fetched pages. That bounds the work to
the candidate's own references and keeps the corpus about this bug rather than
about the Web. Every document records where it was referenced from, so a later
answer can be traced to the artifact that cited it.
"""

from __future__ import annotations

import ipaddress
import re
import socket
from dataclasses import dataclass, field
from urllib.parse import urlparse

import requests

#: bare URLs and the target of a markdown link
_URL = re.compile(r"""(?:\]\()?(https?://[^\s<>()\[\]"'`]+)""", re.I)

#: Extensions that are not documents. Fetching them wastes the budget and adds
#: nothing an embedder can use.
_BINARY = re.compile(
    r"\.(?:png|jpe?g|gif|svg|webp|ico|pdf|zip|t?gz|bz2|xz|whl|jar|exe|dll|so|dylib"
    r"|mp4|mov|avi|woff2?|ttf|eot|parquet|npz|npy|h5|bin)(?:[?#].*)?$", re.I)

#: GitHub links that the miner already has through the API — refetching the HTML
#: of a PR it has structured data for only adds noise.
_ALREADY_HAVE = re.compile(
    r"^https?://(?:www\.)?github\.com/[^/]+/[^/]+/(?:pull|issues|commit|compare|blob|tree)/", re.I)

#: Hosts that serve badges, chat rooms and social pages. They are referenced from
#: almost every README and carry no information about the repository, but they are
#: long enough to win retrieval slots from the diff that answers the question.
_NON_DOCUMENTATION = re.compile(
    r"^(?:img\.)?shields\.io$|badge|^gitter\.im$|^app\.gitter\.im$|^discord\.|^twitter\.com$"
    r"|^x\.com$|^www\.linkedin\.com$|^codecov\.io$|^coveralls\.io$|^travis-ci\.|^ci\.appveyor\.com$"
    r"|^zenodo\.org$|^doi\.org$|^opencollective\.com$|^patreon\.com$|^www\.paypal\.com$", re.I)

#: Shorter than this and the "page" is a redirect stub or a badge image wrapper.
MIN_CHARS = 400

#: A page beyond this is a dump, not documentation.
MAX_BYTES = 400_000
#: How many referenced pages one candidate may pull in.
MAX_DOCS = 8
TIMEOUT = 15


@dataclass
class WebDoc:
    url: str
    origin: str                 # the artifact that referenced it
    title: str = ""
    text: str = ""
    error: str = ""
    metadata: dict = field(default_factory=dict)


def normalize(url: str) -> str:
    """A URL reduced to what identifies the page, for comparison.

    Templates and PR bodies rarely reproduce a link byte-for-byte — one carries
    a tracking query, the other a trailing slash or an anchor — so matching them
    raw would let boilerplate through on a punctuation difference.
    """
    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    path = (parsed.path or "").rstrip("/")
    return f"{host}{path}".lower()


def template_urls(repo_files) -> set:
    """Every URL the repository's own PR/issue templates carry.

    A PR body begins life as a copy of the template, so these links are the
    project's boilerplate — contribution policy, release-note instructions, a git
    tutorial — and say nothing about the bug at hand. Taking them from the
    repository keeps the rule repository-independent: no maintained blacklist,
    and a project whose template links to genuinely useful docs is unaffected in
    every OTHER candidate, where the author cites them deliberately.
    """
    out = set()
    for rf in (repo_files or []):
        if getattr(rf, "role", "") != "template":
            continue
        for m in _URL.finditer(rf.text or ""):
            out.add(normalize(m.group(1).rstrip(".,;:)\"'")))
    return out


def extract_urls(sources, exclude: set | None = None) -> list:
    """URLs referenced by the collected artifacts, in first-seen order.

    `sources` is an iterable of ``(origin, text)``; the origin travels with the
    URL so a fetched page can be attributed to the artifact that cited it.
    `exclude` holds normalized URLs to skip — see :func:`template_urls`.
    """
    seen, out = set(), []
    for origin, text in sources:
        for m in _URL.finditer(str(text or "")):
            url = m.group(1).rstrip(".,;:)\"'")
            host = (urlparse(url).hostname or "").lower()
            if (url in seen or _BINARY.search(url) or _ALREADY_HAVE.match(url)
                    or _NON_DOCUMENTATION.search(host)
                    or (exclude and normalize(url) in exclude)):
                continue
            seen.add(url)
            out.append((url, origin))
    return out


def _is_public(url: str) -> bool:
    """Reject anything that is not a public http(s) host.

    A miner that follows links from untrusted text must not be usable to probe
    the machine it runs on, so localhost, link-local and private ranges are
    refused before any request is made.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False
    host = parsed.hostname
    if host in ("localhost", "127.0.0.1", "::1") or host.endswith(".local"):
        return False
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return False
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            return False
    return True


_TAG = re.compile(r"<[^>]+>")
_SCRIPT = re.compile(r"<(script|style|noscript)\b.*?</\1>", re.I | re.S)
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)


def html_to_text(html: str) -> tuple:
    """(title, text) from an HTML page — tags stripped, whitespace collapsed."""
    title = ""
    m = _TITLE.search(html)
    if m:
        title = _TAG.sub("", m.group(1)).strip()[:200]
    body = _SCRIPT.sub(" ", html)
    body = _TAG.sub(" ", body)
    body = (body.replace("&nbsp;", " ").replace("&amp;", "&")
                .replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"'))
    return title, re.sub(r"[ \t]+\n", "\n", re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]{2,}", " ", body))).strip()


def fetch(url: str, origin: str = "", *, timeout: float = TIMEOUT) -> WebDoc:
    """Fetch one referenced page as text. Never raises — failure is recorded."""
    doc = WebDoc(url=url, origin=origin)
    if not _is_public(url):
        doc.error = "not a public http(s) URL"
        return doc
    try:
        resp = requests.get(url, timeout=timeout, stream=True,
                            headers={"User-Agent": "numbugminer/1.0"})
        ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if resp.status_code != 200:
            doc.error = f"HTTP {resp.status_code}"
            return doc
        if ctype and not (ctype.startswith("text/") or ctype in
                          ("application/json", "application/xhtml+xml", "application/x-yaml")):
            doc.error = f"not a text document ({ctype})"
            return doc
        raw = resp.raw.read(MAX_BYTES + 1, decode_content=True) or b""
        truncated = len(raw) > MAX_BYTES
        body = raw[:MAX_BYTES].decode(resp.encoding or "utf-8", errors="replace")
    except Exception as e:  # noqa: BLE001 — an unreachable reference is not fatal
        doc.error = f"{type(e).__name__}: {e}"[:200]
        return doc
    if "html" in ctype:
        doc.title, doc.text = html_to_text(body)
    else:
        doc.title, doc.text = url.rsplit("/", 1)[-1][:200], body
    doc.metadata = {"content_type": ctype, "truncated": truncated,
                    "n_chars": len(doc.text)}
    return doc


def fetch_referenced(sources, *, exclude: set | None = None,
                     max_docs: int = MAX_DOCS, timeout: float = TIMEOUT) -> list:
    """Fetch the pages referenced by `sources` — at most `max_docs`, once each.

    Returns only the documents that came back with usable text; the failures are
    dropped rather than indexed, since an error page is not evidence.
    """
    docs = []
    for url, origin in extract_urls(sources, exclude=exclude):
        if len(docs) >= max_docs:
            break
        doc = fetch(url, origin, timeout=timeout)
        # a stub or a badge wrapper is not evidence, however reachable it was
        if not doc.error and len(doc.text.strip()) >= MIN_CHARS:
            docs.append(doc)
    return docs
