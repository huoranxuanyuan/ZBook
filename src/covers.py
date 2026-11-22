# SPDX-License-Identifier: AGPL-3.0-only
"""Multi-source cover search. Book metadata first; image-search candidates stay labelled."""

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, asdict, field
from html import unescape
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path
import hashlib, ipaddress, json, logging, re, socket, time, threading
from urllib.parse import urlparse, urljoin, quote
import requests
from PIL import Image, ImageOps
import os
from paths import DATA
from cache_maintenance import create_maintenance, touch_covers
import cover_sources
import cover_browser
import safe_network
from safe_image import open_image, NETWORK_FORMATS

CACHE = DATA / "covers"
LOG = logging.getLogger(__name__)
_NETWORK = threading.local()
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36"
}


@dataclass
class Cover:
    path: str
    label: str
    source: str
    image_url: str
    width: int
    height: int
    provider: str = ""
    author: str = ""
    match: str = "图片候选，作者待核对"
    score: float = 0


@dataclass
class SearchResult:
    covers: list[Cover] = field(default_factory=list)
    diagnostics: list[str] = field(default_factory=list)
    matched_count: int = 0


def norm(s):
    return re.sub(r"[^\w\u3400-\u9fff]", "", unescape(s)).casefold()


def clean(s):
    return unescape(re.sub(r"<[^>]*>", "", s)).strip()


def _get(url, params=None):
    for attempt in range(2):
        try:
            r = safe_network.get(
                url, params=params, headers=HEADERS, session=getattr(_NETWORK, "client", None)
            )
            break
        except requests.Timeout, requests.ConnectionError:
            if attempt:
                raise
            LOG.info("来源连接临时失败，重试一次")
    charset = re.search(r'charset=["\']?([\w-]+)', r.content[:3000].decode("ascii", "ignore"), re.I)
    r.encoding = (
        charset.group(1)
        if charset
        else ("utf8" if not r.encoding or r.encoding.lower() == "iso-8859-1" else r.encoding)
    )
    return r


def _public(url):
    return safe_network.public_url(url)


def _fetch(url, referer):
    target = safe_network.public_url(url)
    if target.scheme == "http":
        url = target._replace(
            scheme="https",
            netloc=target.hostname if ":" not in target.hostname else f"[{target.hostname}]",
        ).geturl()
    headers = HEADERS.copy()
    try:
        origin = safe_network.public_url(referer)
        if origin.scheme == "https":
            headers["Referer"] = origin._replace(
                path="/", params="", query="", fragment=""
            ).geturl()
    except TypeError, ValueError:
        pass
    return safe_network.get(url, headers=headers, max_bytes=10_000_000).content


class Attributes(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


def sfacg(title, author):
    r = _get("https://s.sfacg.com/", {"Key": title, "S": "1", "SS": "0"})
    rows = []
    for block in re.findall(r"<ul\b[^>]*>(.*?)</ul>", r.text, re.S | re.I):
        tags = Attributes()
        tags.feed(block)
        image = next(
            (a for t, a in tags.tags if t == "img" and "NovelCover" in a.get("src", "")), None
        )
        link = next(
            (a for t, a in tags.tags if t == "a" and re.search(r"/Novel/\d+", a.get("href", ""))),
            None,
        )
        if not image or not link:
            continue
        label = image.get("alt", "")
        am = re.search(r"综合信息[：:]\s*([^/<]+)", block)
        rows.append(
            {
                "label": label,
                "author": clean(am.group(1)) if am else "",
                "url": urljoin(r.url, image["src"]),
                "source": urljoin(r.url, link["href"]),
                "provider": "SF 轻小说",
                "official": True,
            }
        )
    return rows


def weread(title, author):
    r = _get("https://weread.qq.com/web/search/books", {"keyword": title})
    marker = "window.__INITIAL_STATE__="
    if marker not in r.text:
        raise ValueError("书籍搜索页面需要验证或已变化")
    j = json.JSONDecoder().raw_decode(r.text.split(marker, 1)[1])[0]
    rows = []
    for item in j.get("searchBooksStoreModule", {}).get("bookInfos", []):
        b = item.get("bookInfo", {})
        url = b.get("cover", "")
        if not url:
            continue
        # Publisher's larger thumbnail variant; keep the original as fallback.
        larger = re.sub(r"/[sbt]\d*_([^/]+)$", r"/o_\1", url)
        rows.append(
            {
                "label": b.get("title", ""),
                "author": b.get("author", ""),
                "url": larger,
                "fallback": url,
                "source": b.get("deepLink") or r.url,
                "provider": "微信读书",
                "official": True,
            }
        )
    return rows


def sogou(title, author):
    r = _get("https://pic.sogou.com/pics", {"query": f'"{title}"'})
    marker = "window.__INITIAL_STATE__="
    if marker not in r.text:
        raise ValueError("搜图页面需要验证或已变化")
    j = json.JSONDecoder().raw_decode(r.text.split(marker, 1)[1])[0]
    return [
        {
            "label": x.get("title", ""),
            "url": x.get("picUrl", ""),
            "fallback": x.get("thumbUrl", ""),
            "source": x.get("url", r.url),
            "width": x.get("width", 0),
            "height": x.get("height", 0),
            "provider": "搜狗图片",
        }
        for x in j.get("searchList", {}).get("searchList", [])
    ]


def so360(title, author):
    r = _get("https://image.so.com/j", {"q": title, "sn": 0, "pn": 40})
    return [
        {
            "label": x.get("title", ""),
            "url": x.get("img", ""),
            "fallback": x.get("thumb", ""),
            "source": x.get("link", r.url),
            "width": x.get("width", 0),
            "height": x.get("height", 0),
            "provider": "360 图片",
        }
        for x in r.json().get("list", [])
    ]


def bing(title, author):
    r = _get("https://cn.bing.com/images/search", {"q": f'"{title}" 小说封面'})
    rows = []
    for attrs in re.findall(r'\bm="([^"]+)"', r.text):
        try:
            x = json.loads(unescape(attrs))
            if x.get("murl"):
                rows.append(
                    {
                        "label": x.get("t", ""),
                        "url": x["murl"],
                        "fallback": x.get("turl", ""),
                        "source": x.get("purl", r.url),
                        "provider": "必应图片",
                    }
                )
        except ValueError, TypeError:
            pass
    return rows


def ciweimao(title, author):
    r = _get(
        "https://www.ciweimao.com/get-search-book-list/0-0-0-0-0-0/全部/"
        + quote(title, safe="")
        + "/1"
    )
    if "man_machine_verify" in r.url:
        raise ValueError("站点要求人工验证，已保留其他来源")
    rows = []
    for block in re.findall(r"<li\b[^>]*data-book-id[^>]*>(.*?)</li>", r.text, re.S | re.I):
        tags = Attributes()
        tags.feed(block)
        link = next(
            (
                a
                for t, a in tags.tags
                if t == "a" and a.get("title") and "/book/" in a.get("href", "")
            ),
            None,
        )
        image = next(
            (a for t, a in tags.tags if t == "img" and "kuangxiangit" in a.get("src", "")), None
        )
        am = re.search(r"小说作者[：:]\s*<a[^>]*>(.*?)</a>", block, re.S)
        if link and image:
            rows.append(
                dict(
                    label=link["title"],
                    author=clean(am.group(1)) if am else "",
                    url=urljoin(r.url, image["src"]),
                    source=urljoin(r.url, link["href"]),
                    provider="刺猬猫",
                    official=True,
                )
            )
    return rows


def qqreading(title, author):
    r = _get("https://book.qq.com/book-search/" + quote(title, safe=""))
    rows = []
    for block in re.split(r'<div class="book-large result-item"[^>]*>', r.text)[1:]:
        m = re.search(r'<a\b[^>]*title="([^"]+)"[^>]*href="[^"]*/book-detail/(\d+)"', block)
        am = re.search(r'<a[^>]*href="[^"]*/book-writer/[^>]*>(.*?)</a>', block, re.S)
        if not m:
            continue
        label, bid = m.groups()
        bid = int(bid)
        rows.append(
            dict(
                label=unescape(label),
                author=clean(am.group(1)) if am else "",
                url=f"https://wfqqreader-1252317822.image.myqcloud.com/cover/{bid%1000}/{bid}/o_{bid}.jpg",
                fallback=f"https://wfqqreader-1252317822.image.myqcloud.com/cover/{bid%1000}/{bid}/b_{bid}.jpg",
                source=f"https://book.qq.com/book-detail/{bid}",
                provider="QQ 阅读",
                official=True,
            )
        )
    return rows


def zongheng(title, author):
    r = _get(
        "https://search.zongheng.com/search/book",
        dict(keyword=title, pageNo=1, pageNum=20, isFromHuayu=0, sort=""),
    )
    j = r.json()
    if j.get("code") != 0:
        raise ValueError("纵横搜索暂不可用")
    rows = []
    for b in j.get("data", {}).get("datas", {}).get("list", []):
        # Highlight markup is escaped a second time in this public API.
        label = re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m[1], 16)), b.get("name", ""))
        label = clean(label.replace('\\"', '"'))
        if b.get("coverUrl"):
            rows.append(
                dict(
                    label=label,
                    author=b.get("authorName", ""),
                    url="https://static.zongheng.com/upload/" + b["coverUrl"].lstrip("/"),
                    source=f"https://www.zongheng.com/detail/{b['bookId']}",
                    provider="纵横中文网",
                    official=True,
                )
            )
    return rows


def seventeen(title, author):
    r = _get("https://search.17k.com/search.xhtml", {"c.st": 0, "c.q": title})
    rows = []
    for block in re.split(r'<div class="textlist">', r.text)[1:]:
        tags = Attributes()
        tags.feed(block)
        image = next(
            (
                a
                for t, a in tags.tags
                if t == "img" and "/book/" in a.get("src", "") and a.get("alt")
            ),
            None,
        )
        link = next(
            (
                a
                for t, a in tags.tags
                if t == "a" and re.search(r"/book/\d+\.html", a.get("href", ""))
            ),
            None,
        )
        am = re.search(r"作者[：:]\s*<a[^>]*>(.*?)</a>", block, re.S)
        if image and link:
            rows.append(
                dict(
                    label=image["alt"],
                    author=clean(am.group(1)) if am else "",
                    url=urljoin(r.url, image["src"]),
                    source=urljoin(r.url, link["href"]),
                    provider="17K 小说",
                    official=True,
                )
            )
    return rows


def jinjiang(title, author):
    r = _get(
        "https://wap.jjwxc.net/search",
        {"kw": title.encode("gb18030", errors="replace"), "t": 1, "version": 1},
    )
    rows = []
    for block in re.findall(r"<li\b[^>]*>(.*?)</li>", r.text, re.S):
        m = re.search(r'<a[^>]*href="/book2/(\d+)"[^>]*>(.*?)</a>', block, re.S)
        am = re.search(r'<a[^>]*href="/wapauthor/[^>]*>(.*?)</a>', block, re.S)
        if not m or norm(clean(m[2])) != norm(title):
            continue
        known = clean(am[1]) if am else ""
        if author and known and norm(known) != norm(author):
            continue
        detail = _get("https://www.jjwxc.net/onebook.php", {"novelid": m[1]})
        tags = Attributes()
        tags.feed(detail.text)
        image = next(
            (
                a
                for t, a in tags.tags
                if t == "img" and "noveldefaultimage" in a.get("class", "").split()
            ),
            None,
        )
        if image and image.get("src"):
            rows.append(
                dict(
                    label=clean(m[2]),
                    author=known,
                    url=urljoin(detail.url, image["src"]),
                    source=detail.url,
                    provider="晋江文学城",
                    official=True,
                )
            )
        if len(rows) >= 3:
            break
    return rows


def fanqie(title, author):
    # Public search currently returns an empty body. Discover indexed book pages,
    # then verify metadata on the publisher itself; never trust a search snippet.
    r = _get("https://cn.bing.com/search", {"q": f'site:fanqienovel.com/page/ "{title}"'})
    urls = list(dict.fromkeys(re.findall(r"https://fanqienovel\.com/page/\d+", unescape(r.text))))[
        :4
    ]
    rows = []
    for url in urls:
        try:
            detail = _get(url)
            marker = "window.__INITIAL_STATE__="
            if marker not in detail.text:
                continue
            state = json.JSONDecoder().raw_decode(detail.text.split(marker, 1)[1])[0]
            b = state.get("page", {})
            label = b.get("bookName", "")
            known = b.get("authorName") or b.get("author", "")
            if norm(label) != norm(title) or (author and known and norm(known) != norm(author)):
                continue
            image = b.get("thumbUri") or b.get("thumbUrl")
            if image:
                rows.append(
                    dict(
                        label=label,
                        author=known,
                        url=image,
                        source=url,
                        provider="番茄小说",
                        official=True,
                    )
                )
        except (requests.RequestException, ValueError, TypeError) as exc:
            LOG.info("番茄公开书页失败 %s: %s", url, exc)
    return rows


def image_urls(url):
    """Only known publisher originals. Never request arbitrary upscaled sizes."""
    urls = []
    host = (urlparse(url).hostname or "").lower()
    if host.endswith("image.myqcloud.com"):
        urls.append(re.sub(r"/[sbt]\d*_([^/?]+)", r"/o_\1", url))
    if host in ("bookcover.yuewen.com", "qidian.qpic.cn") or host.endswith(".qidian.com"):
        if "/qdbimg/" in url:
            urls.append(re.sub(r"/\d+(?=\?|$)", "/0", url))
    if host.endswith("jjwxc.net"):
        urls.append(re.sub(r"_\d{2,4}_\d{2,4}(?=\.(?:jpg|png|jpeg))", "", url))
    if host == "cdn.static.17k.com":
        urls.append(re.sub(r"\.(jpg|png)-\d+x\d+", r".\1", url))
    if re.fullmatch(r"p\d+-novel-sign\.byteimg\.com", host):
        path = urlparse(url).path.split("~", 1)[0]
        if path.startswith("/novel-pic/"):
            urls.append("https://" + host.replace("-sign", "") + "/origin" + path)
    urls.append(url)
    return list(dict.fromkeys(urls))


def quality_label(width, height):
    if width >= 600 and height >= 800:
        return "高清尺寸"
    if width >= 300 and height >= 400:
        return "标准封面"
    return "小图 · 原站尺寸有限"


def ypshuo(title, author):
    return cover_sources.ypshuo(title, author, _get)


def ixdzs(title, author):
    return cover_sources.ixdzs(title, author, _get)


def google(title, author):
    return cover_sources.google_images(title, author, _get)


def baidu(title, author):
    return cover_sources.baidu_images(title, author, _get)


PROVIDERS = {
    "阅评说（书评站）": ypshuo,
    "爱下电子书（目录站）": ixdzs,
    "Google 图片": google,
    "百度图片": baidu,
    "番茄小说": fanqie,
    "QQ 阅读": qqreading,
    "纵横中文网": zongheng,
    "刺猬猫": ciweimao,
    "晋江文学城": jinjiang,
    "17K 小说": seventeen,
    "SF 轻小说": sfacg,
    "微信读书": weread,
    "搜狗图片": sogou,
    "360 图片": so360,
    "必应图片": bing,
}


def rank_rows(rows, title, author):
    wanted = norm(title)
    ranked = []
    seen = set()
    for row in rows:
        label = clean(row.get("label", ""))
        url = row.get("url", "")
        known = clean(row.get("author", ""))
        if not wanted or wanted not in norm(label) or not url:
            continue
        exact = norm(label) == wanted
        verified = row.get("official") or row.get("metadata_verified")
        if verified and not exact:
            continue
        if known and author and norm(known) != norm(author):
            continue
        try:
            w, h = float(row.get("width") or 0), float(row.get("height") or 0)
        except TypeError, ValueError:
            w = h = 0
        if w and h and (not 0.38 <= w / h <= 0.96 or w < 90 or h < 120):
            continue
        if (
            not verified
            and not exact
            and not any(
                s in label for s in ["小说", "封面", "最新章节", "在线阅读", "书院", "书库"]
            )
        ):
            continue
        if any(s in label for s in ["封面制作", "求封面", "制作封面", "卡牌", "图片交流"]):
            continue
        score = (
            (500 if row.get("official") else 450 if verified else 0)
            + (300 if known and author else 0)
            + (100 if exact else 0)
        )
        score += 160 if not verified and author and norm(author) in norm(label) else 0
        score += (
            80
            if any(
                s in url.lower()
                for s in ["novelcover", "kuangxiangit", "/cover/", "/baoimg/", "qdbimg"]
            )
            else 0
        )
        row = dict(row, label=label, author=known, score=score)
        row["match"] = (
            "书名、作者匹配"
            if verified and known and author
            else ("书籍站点，核对作者" if verified else "图片候选，作者待核对")
        )
        ranked.append(row)
    unique = []
    for row in sorted(ranked, key=lambda x: -x["score"]):
        if row["url"] not in seen:
            unique.append(row)
            seen.add(row["url"])
    return unique[:16]


def _cache_row(row):
    urls = []
    for u in [row.get("url"), row.get("fallback")]:
        if u:
            urls.extend(image_urls(u))
    best = None
    best_raw = None
    best_format = None
    for url in dict.fromkeys(urls):
        try:
            try:
                raw = _fetch(url, row.get("source") or "https://www.bing.com/")
            except requests.Timeout, requests.ConnectionError:
                raw = _fetch(url, row.get("source") or "https://www.bing.com/")
            with open_image(BytesIO(raw), formats=NETWORK_FORMATS) as im:
                if im.width * im.height > 28_000_000:
                    raise ValueError("图片像素过大")
                im.load()
                w, h = ImageOps.exif_transpose(im).size
                if (
                    w < 90
                    or h < 120
                    or not 0.38 <= w / h <= 0.96
                    or im.format not in ("JPEG", "PNG", "WEBP")
                ):
                    continue
                if best is None or w * h > best.width * best.height:
                    best = Cover(
                        "",
                        row["label"],
                        row.get("source", ""),
                        url,
                        w,
                        h,
                        row.get("provider", ""),
                        row.get("author", ""),
                        row.get("match", "候选"),
                        row.get("score", 0),
                    )
                    best_raw = raw
                    best_format = im.format
        except Exception as exc:
            LOG.info("%s 图片失败 %s: %s", row.get("provider"), type(exc).__name__, exc)
    if best:
        ext = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp"}[best_format]
        path = CACHE / (hashlib.sha256(best_raw).hexdigest() + ext)
        if not path.exists():
            path.write_bytes(best_raw)
        best.path = str(path)
    return best


def visual_hash(path):
    with open_image(path) as im:
        im = ImageOps.exif_transpose(im).convert("L").resize((9, 8))
        pixels = list(im.getdata())
        return sum(
            (pixels[y * 9 + x] > pixels[y * 9 + x + 1]) << (y * 8 + x)
            for y in range(8)
            for x in range(8)
        )


def deduplicate(images):
    # Prefer the highest genuine resolution of identical artwork across sites.
    unique = []
    for candidate in sorted(images, key=lambda c: (c.width * c.height, c.score), reverse=True):
        fingerprint = visual_hash(candidate.path)
        duplicate = next(
            (
                item
                for item in unique
                if norm(item[1].label) == norm(candidate.label)
                and (item[0] ^ fingerprint).bit_count() <= 2
            ),
            None,
        )
        if duplicate:
            duplicate[1].score = max(duplicate[1].score, candidate.score)
        else:
            unique.append((fingerprint, candidate))
    return sorted(
        [c for _, c in unique],
        key=lambda c: (-int(c.score // 100), -min(c.width * c.height, 4_000_000), -c.score),
    )


def _source_task(function, title, author):
    # Reuse a connection across a source's search and book-detail requests.
    # Sessions and their public-site cookies stay within this single task.
    with safe_network.PublicSession() as client:
        _NETWORK.client = client
        try:
            return function(title, author)
        finally:
            del _NETWORK.client


def _search_details(title, author="", refresh=False, progress=None, deep_search=False):
    title = title.strip()[:120]
    author = author.strip()[:80]
    if not title:
        raise ValueError("请填写书名")
    progress = progress or (lambda s: None)
    CACHE.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(("v4-sources\0" + title + "\0" + author).encode("utf8")).hexdigest()
    manifest = CACHE / (key + ".json")
    cached = []
    if manifest.exists():
        try:
            values = [Cover(**x) for x in json.loads(manifest.read_text(encoding="utf8"))]
            if values and all(Path(x.path).is_file() for x in values):
                cached = values
            if (
                cached
                and not refresh
                and not deep_search
                and time.time() - manifest.stat().st_mtime < 86400
            ):
                return SearchResult(cached, ["使用最近检索的封面缓存"], len(cached))
        except ValueError, TypeError:
            pass
    diagnostics = []
    rows = []
    successes = 0
    with ThreadPoolExecutor(max_workers=10) as pool:
        pending = {
            pool.submit(_source_task, fn, title, author): name for name, fn in PROVIDERS.items()
        }
        for future in as_completed(pending):
            name = pending[future]
            try:
                found = future.result()
                rows.extend(found)
                successes += 1
                status = f"{name}：返回 {len(found)} 条"
            except Exception as exc:
                if isinstance(exc, requests.HTTPError):
                    detail = (
                        f"站点返回 HTTP {exc.response.status_code}"
                        if exc.response is not None
                        else "站点请求失败"
                    )
                elif isinstance(exc, requests.exceptions.JSONDecodeError):
                    detail = "站点未返回有效数据，可能正在验证或限流"
                elif isinstance(exc, requests.Timeout):
                    detail = "连接超时"
                elif isinstance(exc, requests.ConnectionError):
                    detail = "网络连接失败"
                else:
                    detail = str(exc)[:70] if isinstance(exc, ValueError) else type(exc).__name__
                status = f"{name}：{detail}，暂不可用"
                LOG.info("%s %s", status, exc)
            diagnostics.append(status)
            progress(status)
    ranked = rank_rows(rows, title, author)
    progress(f"正在下载并检查 {len(ranked)} 张候选封面…")
    with ThreadPoolExecutor(max_workers=5) as pool:
        images = [x for x in pool.map(_cache_row, ranked) if x]
    for provider in dict.fromkeys(list(PROVIDERS) + [row.get("provider", "") for row in ranked]):
        expected = sum(1 for row in ranked if row.get("provider") == provider)
        obtained = sum(1 for image in images if image.provider == provider)
        if expected:
            diagnostics.append(f"{provider}：匹配 {expected}，图片可用 {obtained}")
    images = deduplicate(images)
    if deep_search or not images:
        for engine in ("Google 图片", "百度图片"):
            if engine not in PROVIDERS:
                continue
            progress(f"正在用{engine}补充封面…")
            try:
                extra = cover_browser.search_images(engine, title, author)
                successes += 1
                extra_ranked = rank_rows(extra, title, author)
                ranked.extend(extra_ranked)
                with ThreadPoolExecutor(max_workers=4) as pool:
                    extra_images = [x for x in pool.map(_cache_row, extra_ranked) if x]
                diagnostics.append(
                    f"{engine}补充检索：标题匹配 {len(extra_ranked)}，图片可用 {len(extra_images)}"
                )
                images = deduplicate(images + extra_images)[:24]
                if extra_images and not deep_search:
                    break
            except Exception as exc:
                detail = (
                    str(exc)[:100] if isinstance(exc, (ValueError, OSError)) else type(exc).__name__
                )
                diagnostics.append(f"{engine}补充检索：{detail}，暂不可用")
                LOG.info("%s补充检索失败: %s", engine, exc)
    diagnostics.append(f"标题匹配 {len(ranked)} 张，成功获取 {len(images)} 张")
    if images:
        manifest.write_text(
            json.dumps([asdict(x) for x in images], ensure_ascii=False, indent=2), encoding="utf8"
        )
    elif cached:
        images = cached
        diagnostics.append("本次联网未取得可用图片，使用上次成功检索的本地缓存")
    LOG.info("封面检索 %r / %r: %s", title, author, "；".join(diagnostics))
    if successes == 0 and not images:
        raise ConnectionError("所有封面来源连接失败，请检查网络或代理。" + "；".join(diagnostics))
    return SearchResult(images, diagnostics, len(ranked))


def search_covers(title, author="", refresh=False):
    return search_details(title, author, refresh).covers


def first_matching_cover(candidates, title, author=""):
    """Select the first verified match, then a matching-title image candidate."""
    matches = []
    for candidate in candidates:
        if not Path(candidate.path).is_file():
            continue
        if norm(title) not in norm(candidate.label):
            continue
        if author and candidate.author and norm(author) != norm(candidate.author):
            continue
        matches.append(candidate)
    return next(
        (row for row in matches if row.match == "书名、作者匹配"), matches[0] if matches else None
    )


def search_details(title, author="", refresh=False, progress=None, deep_search=False):
    root = CACHE.parent.parent if CACHE.parent.name == "data" else CACHE.parent
    maintenance = create_maintenance(root, CACHE, kind="cover-search")
    try:
        result = _search_details(title, author, refresh, progress, deep_search)
        maintenance.protected = [Path(row.path).resolve() for row in result.covers]
        touch_covers(result.covers, CACHE)
        return result
    finally:
        maintenance.finish()
