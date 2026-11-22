# SPDX-License-Identifier: AGPL-3.0-only
"""Public book metadata and image search adapters, without a browser dependency."""

from html import unescape
from html.parser import HTMLParser
import json
import logging
import re
from urllib.parse import parse_qs, unquote, urljoin, urlparse
from requests import RequestException

LOG = logging.getLogger(__name__)


def _norm(value):
    return re.sub(r"[^\w\u3400-\u9fff]", "", unescape(str(value))).casefold()


def _text(value):
    return unescape(re.sub(r"<[^>]*>", "", value)).strip()


def _web_url(value):
    return isinstance(value, str) and urlparse(value).scheme in ("http", "https")


def _book_nodes(value):
    if isinstance(value, list):
        for item in value:
            yield from _book_nodes(item)
    elif isinstance(value, dict):
        types = value.get("@type", [])
        if isinstance(types, str):
            types = [types]
        if "Book" in types:
            yield value
        yield from _book_nodes(value.get("@graph", []))


def parse_book_metadata(html, source, title, author=""):
    """Use the detail page's Book metadata; never infer an author from a query."""
    rows = []
    for attrs, body in re.findall(r"<script\b([^>]*)>(.*?)</script\s*>", html, re.S | re.I):
        if not re.search(r"\btype\s*=\s*[\"\']application/ld\+json[\"\']", attrs, re.I):
            continue
        try:
            data = json.loads(body)
        except ValueError, TypeError:
            continue
        for book in _book_nodes(data):
            label = book.get("name", "")
            people = book.get("author", [])
            if not isinstance(people, list):
                people = [people]
            names = [
                person.get("name", "") if isinstance(person, dict) else person for person in people
            ]
            names = [name for name in names if isinstance(name, str)]
            if _norm(label) != _norm(title):
                continue
            if author and not any(_norm(name) == _norm(author) for name in names):
                continue
            images = book.get("image", [])
            if not isinstance(images, list):
                images = [images]
            known = next(
                (name for name in names if _norm(name) == _norm(author)), names[0] if names else ""
            )
            for image in images:
                if isinstance(image, dict):
                    image = image.get("contentUrl") or image.get("url")
                if not _web_url(image):
                    continue
                rows.append(
                    dict(
                        label=label,
                        author=known,
                        url=image,
                        source=source,
                        provider="阅评说（书评站）",
                        metadata_verified=bool(known),
                    )
                )
    return rows


def ypshuo(title, author, get):
    # Read links from the visible search results rather than evaluating Nuxt code.
    links = []
    for keyword in dict.fromkeys([title, author] if author else [title]):
        response = get("https://www.ypshuo.com/search", {"keyword": keyword})
        for attrs, body in re.findall(r"<a\b([^>]*)>(.*?)</a\s*>", response.text, re.S | re.I):
            match = re.search(r"\bhref\s*=\s*[\"\'](/novel/\d+\.html)[\"\']", attrs, re.I)
            if match and _norm(_text(body)) == _norm(title):
                links.append(urljoin(response.url, match[1]))
        if links:
            break
    rows = []
    for link in list(dict.fromkeys(links))[:3]:
        detail = get(link)
        rows.extend(parse_book_metadata(detail.text, detail.url, title, author))
        # The rating site's image can be stale even when its publisher link works.
        # Follow only a public book-detail link, then verify the publisher's own
        # metadata again; a search heading or referring page is not proof.
        publisher_links = []
        for attrs, _ in re.findall(r"<a\b([^>]*)>(.*?)</a\s*>", detail.text, re.S | re.I):
            match = re.search(
                r"\bhref\s*=\s*[\"\'](https://(?:www\.)?ciweimao\.com/book/\d+)/?[\"\']",
                attrs,
                re.I,
            )
            if match:
                publisher_links.append(unescape(match[1]))
        for publisher_link in list(dict.fromkeys(publisher_links))[:1]:
            # Both are ordinary public book views. The mobile URL is the
            # publisher's mobile-agent canonical form, not a verification API.
            mobile_link = publisher_link.replace("www.ciweimao.com", "wap.ciweimao.com")
            if mobile_link == publisher_link:
                mobile_link = publisher_link.replace("://ciweimao.com/", "://wap.ciweimao.com/")
            for book_link in dict.fromkeys([publisher_link, mobile_link]):
                try:
                    publisher = get(book_link)
                    if urlparse(publisher.url).hostname not in (
                        "www.ciweimao.com",
                        "ciweimao.com",
                        "wap.ciweimao.com",
                    ):
                        continue
                    metadata = NovelMetadata()
                    metadata.feed(publisher.text)
                    verified = parse_novel_metadata(
                        publisher.text, publisher.url, title, author, "刺猬猫（书页定位）"
                    )
                    for row in verified:
                        row["official"] = True
                        rows.append(row)
                    # Explicit metadata also lets us reject a wrong work once,
                    # rather than searching another view for a convenient match.
                    if metadata.values.get("og:novel:book_name"):
                        break
                except (RequestException, ValueError) as exc:
                    LOG.info("原站书页定位不可用 %s: %s", book_link, exc)
    return rows


class NovelMetadata(HTMLParser):
    def __init__(self):
        super().__init__()
        self.values = {}
        self.images = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "meta" and (attrs.get("property") or attrs.get("name")):
            self.values[attrs.get("property") or attrs.get("name")] = attrs.get("content", "")
        elif tag == "img":
            self.images.append(attrs)


def parse_novel_metadata(html, source, title, author="", provider="书籍目录站"):
    metadata = NovelMetadata()
    metadata.feed(html)
    values = metadata.values
    label = values.get("og:novel:book_name", "")
    known = values.get("og:novel:author", "")
    image = values.get("og:image", "")
    if (
        _norm(label) != _norm(title)
        or author
        and _norm(known) != _norm(author)
        or not _web_url(image)
    ):
        return []
    # Some catalogues expose a small OG preview but show a larger cover in the
    # page itself. Preserve both; the downloader chooses by decoded pixels.
    original = next(
        (
            attrs.get("src")
            for attrs in metadata.images
            if _norm(attrs.get("alt", "")) in (_norm(label), _norm(label + "封面"))
            and _web_url(attrs.get("src"))
        ),
        image,
    )
    return [
        dict(
            label=label,
            author=known,
            url=original,
            fallback=image,
            source=source,
            provider=provider,
            metadata_verified=bool(known),
        )
    ]


def ixdzs(title, author, get):
    response = get("https://ixdzs8.com/bsearch", {"q": title})
    links = []
    for attrs, body in re.findall(r"<a\b([^>]*)>(.*?)</a\s*>", response.text, re.S | re.I):
        match = re.search(r"\bhref\s*=\s*[\"\'](/read/\d+/)[\"\']", attrs, re.I)
        if match and _norm(_text(body)) == _norm(title):
            links.append(urljoin(response.url, match[1]))
    rows = []
    for link in list(dict.fromkeys(links))[:3]:
        detail = get(link)
        rows.extend(
            parse_novel_metadata(detail.text, detail.url, title, author, "爱下电子书（目录站）")
        )
    return rows


class ImageLinks(HTMLParser):
    """Parse ordinary image-result links; ignore scripts and executable data."""

    def __init__(self, engine, base):
        super().__init__()
        self.engine = engine
        self.base = base
        self.rows = []
        self.anchor = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a":
            self.anchor = {
                "href": urljoin(self.base, attrs.get("href", "")),
                "label": attrs.get("aria-label") or attrs.get("title", ""),
                "text": [],
            }
        if tag == "img" and self.anchor:
            self.anchor["label"] = self.anchor["label"] or attrs.get("alt", "")
        if self.engine == "google" and attrs.get("data-ou"):
            self.rows.append(
                dict(
                    label=attrs.get("data-pt", ""),
                    url=attrs["data-ou"],
                    source=attrs.get("data-ru", self.base),
                    width=attrs.get("data-ow", 0),
                    height=attrs.get("data-oh", 0),
                    provider="Google 图片",
                )
            )

    def handle_data(self, text):
        if self.anchor:
            self.anchor["text"].append(text)

    def handle_endtag(self, tag):
        if tag != "a" or not self.anchor:
            return
        anchor, self.anchor = self.anchor, None
        parsed = urlparse(anchor["href"])
        params = parse_qs(parsed.query)
        if self.engine == "google" and parsed.path == "/imgres":
            image = params.get("imgurl", [""])[0]
            source = params.get("imgrefurl", [self.base])[0]
            width, height = params.get("w", [0])[0], params.get("h", [0])[0]
        elif self.engine == "baidu" and parsed.path == "/search/detail":
            image = unquote(params.get("objurl", [""])[0])
            source = unquote(params.get("fromurl", [self.base])[0])
            width, height = 0, 0
        else:
            return
        if _web_url(image):
            self.rows.append(
                dict(
                    label=anchor["label"] or "".join(anchor["text"]).strip(),
                    url=image,
                    source=source if _web_url(source) else self.base,
                    width=width,
                    height=height,
                    provider="Google 图片" if self.engine == "google" else "百度图片",
                )
            )


def google_images(title, author, get):
    response = get("https://www.google.com/search", {"q": title, "udm": "2"})
    rows = parse_google_images(response.text, response.url)
    if not rows and any(
        marker in response.text
        for marker in ("challenge_version", "/sorry/", "/httpservice/retry/enablejs", "enablejs")
    ):
        raise ValueError("Google 图片要求浏览器验证，可在浏览器继续查找")
    return rows


def parse_google_images(html, source="https://www.google.com/search"):
    parser = ImageLinks("google", source)
    parser.feed(html)
    rows = parser.rows
    # Current Google results serialize each image as a JSON array, with original
    # pixels, thumbnail, source page and caption together. Decode only JSON;
    # never evaluate the page's scripts or pair a URL with unrelated text.
    decoder = json.JSONDecoder()
    pattern = r'\[0,\s*"[^"\n]{1,200}",\s*\["https?[^"\n]+",\s*\d+,\s*\d+\],\s*\["https?[^"\n]+",\s*\d+,\s*\d+\]'
    for index, match in enumerate(re.finditer(pattern, html)):
        if index >= 160:
            break
        try:
            record, _ = decoder.raw_decode(html, match.start())
            metadata = next(
                (
                    item["2003"]
                    for item in record
                    if isinstance(item, dict) and isinstance(item.get("2003"), list)
                ),
                None,
            )
            if (
                not metadata
                or len(metadata) < 4
                or not _web_url(metadata[2])
                or not isinstance(metadata[3], str)
            ):
                continue
            original, thumbnail = record[3], record[2]
            rows.append(
                dict(
                    label=metadata[3],
                    url=original[0],
                    fallback=thumbnail[0],
                    source=metadata[2],
                    width=original[2],
                    height=original[1],
                    provider="Google 图片",
                )
            )
        except ValueError, TypeError, IndexError, KeyError:
            continue
    seen = set()
    unique = []
    for row in rows:
        if row["url"] not in seen:
            unique.append(row)
            seen.add(row["url"])
    return unique


def baidu_images(title, author, get):
    response = get(
        "https://image.baidu.com/search/acjson",
        {"tn": "resultjson_com", "ipn": "rj", "word": title, "pn": 0, "rn": 30},
    )
    if "wappass.baidu.com" in response.url or "百度安全验证" in response.text:
        raise ValueError("百度图片要求人工验证，可在浏览器继续查找")
    data = response.json()
    if data.get("antiFlag"):
        raise ValueError("百度图片限制自动请求，可在浏览器继续查找")
    if not isinstance(data.get("data"), list):
        raise ValueError("百度图片返回格式已变化")
    rows = []
    for item in data["data"]:
        if not isinstance(item, dict):
            continue
        replacements = item.get("replaceUrl") or []
        replacements = replacements if isinstance(replacements, list) else []
        replacement = next(
            (
                entry
                for entry in replacements
                if isinstance(entry, dict) and _web_url(entry.get("ObjURL"))
            ),
            {},
        )
        image = replacement.get("ObjURL") or item.get("objURL", "")
        if not _web_url(image):
            image = item.get("middleURL", "")
        if not _web_url(image):
            continue
        source = replacement.get("FromURL") or item.get("fromURL", "")
        rows.append(
            dict(
                label=_text(item.get("fromPageTitleEnc") or item.get("fromPageTitle", "")),
                url=image,
                fallback=item.get("middleURL") or item.get("thumbURL"),
                source=source if _web_url(source) else response.url,
                width=item.get("width", 0),
                height=item.get("height", 0),
                provider="百度图片",
            )
        )
    return rows
