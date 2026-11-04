"""Low-concurrency public job board and RSS adapters; no authenticated scraping."""

from __future__ import annotations

import asyncio
import html
import ipaddress
import json
import re
import socket
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from app.models import JobSource

REQUEST_TIMEOUT = httpx.Timeout(15.0, connect=5.0)
MAX_FEED_BYTES = 5 * 1024 * 1024
TRACKING_KEYS = {"fbclid", "gclid", "mc_cid", "mc_eid"}


class _TextParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        clean = " ".join(data.split())
        if clean:
            self.parts.append(clean)


class _JsonLdParser(HTMLParser):
    """Collect only JSON-LD script bodies from an explicitly configured page."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._capture = False
        self._parts: list[str] = []
        self.documents: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        attributes = dict(attrs)
        self._capture = tag.casefold() == "script" and (attributes.get("type") or "").split(";", 1)[0].strip().casefold() == "application/ld+json"
        if self._capture:
            self._parts = []

    def handle_data(self, data: str) -> None:
        if self._capture:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self._capture and tag.casefold() == "script":
            self.documents.append("".join(self._parts))
            self._parts = []
            self._capture = False


def plain_text(value: str | None) -> str:
    parser = _TextParser()
    parser.feed(html.unescape(value or ""))
    return " ".join(parser.parts).strip()


def normalize_job_url(value: str) -> str:
    parts = urlsplit(value.strip())
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
        raise ValueError("Job links must be absolute HTTP(S) URLs without embedded credentials")
    host = parts.hostname.lower()
    port = parts.port
    netloc = host if port is None or (parts.scheme == "https" and port == 443) or (parts.scheme == "http" and port == 80) else f"{host}:{port}"
    query = [(key, val) for key, val in parse_qsl(parts.query, keep_blank_values=True) if not key.lower().startswith("utm_") and key.lower() not in TRACKING_KEYS]
    return urlunsplit((parts.scheme.lower(), netloc, parts.path.rstrip("/") or "/", urlencode(query, doseq=True), ""))


def _date(value: str | None) -> datetime | None:
    if not value:
        return None
    raw = value.strip()
    try:
        if raw.isdigit():
            number = int(raw)
            if number > 10_000_000_000:
                number /= 1000
            return datetime.fromtimestamp(number, tz=timezone.utc)
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError, OSError):
        try:
            parsed = parsedate_to_datetime(raw)
            return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
        except (TypeError, ValueError, OverflowError):
            return None


def _work_mode(location: str, description: str) -> str:
    sample = f"{location} {description[:12000]}".casefold()
    if re.search(r"\b(remote|work from anywhere|distributed team)\b", sample):
        return "remote"
    if re.search(r"\b(hybrid|partly remote)\b", sample):
        return "hybrid"
    if re.search(r"\b(on[- ]site|in office|office based)\b", sample):
        return "on-site"
    return "unknown"


SKILLS = (
    "AWS", "EC2", "S3", "RDS", "IAM", "VPC", "ALB", "ECS", "EKS", "Lambda",
    "Terraform", "CloudFormation", "Ansible", "Docker", "Kubernetes", "Jenkins",
    "GitHub Actions", "Git", "CI/CD", "Prometheus", "Grafana", "CloudWatch", "Kibana",
    "Python", "Bash", "Groovy", "Linux", "FastAPI", "AWS Bedrock", "MLOps",
)


def extract_skills(description: str) -> tuple[list[str], list[str], str | None]:
    lines = [line.strip(" •\t-") for line in description.splitlines() if line.strip()]
    required_text: list[str] = []
    preferred_text: list[str] = []
    active: str | None = None
    for line in lines:
        lowered = line.casefold()
        if re.match(r"^(requirements|required|must[- ]have|what you.ll need|minimum qualifications)\b", lowered):
            active = "required"
            required_text.append(line)
        elif re.match(r"^(preferred|nice[- ]to[- ]have|bonus|desired|you may also have)\b", lowered):
            active = "preferred"
            preferred_text.append(line)
        elif re.match(r"^(responsibilities|what you.ll do|about the role|benefits|what we offer)\b", lowered):
            active = None
        elif active == "required":
            required_text.append(line)
        elif active == "preferred":
            preferred_text.append(line)
    if not required_text and not preferred_text:
        required_text = lines
    required = [skill for skill in SKILLS if re.search(rf"(?<![\w]){re.escape(skill)}(?![\w])", " ".join(required_text), re.I)]
    preferred = [skill for skill in SKILLS if re.search(rf"(?<![\w]){re.escape(skill)}(?![\w])", " ".join(preferred_text), re.I) and skill not in required]
    experience = next((m.group(0) for m in re.finditer(r"(?:\d+\+?|at least\s+\d+)\s+years?[^.\n]{0,100}", description, re.I)), None)
    return required, preferred, experience


def _record(source: JobSource, *, identifier: str | None, title: str, url: str,
            location: str = "", description: str = "", application_url: str | None = None,
            posted_at: datetime | None = None, employment_type: str | None = None,
            salary_min: float | None = None, salary_max: float | None = None,
            salary_currency: str | None = None, company: str | None = None,
            work_mode: str | None = None, expires_at: datetime | None = None) -> dict:
    url = normalize_job_url(url)
    required, preferred, experience = extract_skills(description)
    return {
        "title": title[:240], "company": (company or source.name)[:240], "location": location[:240] or None,
        "work_mode": work_mode if work_mode in {"remote", "hybrid", "on-site"} else _work_mode(location, description),
        "source_url": url, "application_url": normalize_job_url(application_url) if application_url else url,
        "source_name": f"{source.kind.title()}:{source.id}", "source_identifier": str(identifier)[:256] if identifier else None,
        "employment_type": employment_type[:80] if employment_type else None,
        "salary_min": salary_min, "salary_max": salary_max, "salary_currency": salary_currency,
        "required_skills": required, "preferred_skills": preferred,
        "experience_requirements": experience, "posted_at": posted_at, "expires_at": expires_at,
    }


def validate_public_https_url(url: str) -> str:
    parts = urlsplit(url.strip())
    hostname = (parts.hostname or "").casefold().rstrip(".")
    if parts.scheme.casefold() != "https" or not hostname or parts.username or parts.password:
        raise ValueError("Public job pages and feeds must use HTTPS without embedded credentials")
    if hostname == "localhost" or hostname.endswith((".localhost", ".local", ".internal")):
        raise ValueError("Public job pages and feeds cannot use local hostnames")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise ValueError("Public job pages and feeds cannot use private IP addresses")
    if parts.port not in (None, 443):
        raise ValueError("Public job pages and feeds must use the standard HTTPS port")
    return url


async def _read_bounded(url: str) -> bytes:
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, follow_redirects=False, trust_env=False,
                                 headers={"User-Agent": "PersonalJobHunter/0.1 (local personal job discovery)"}) as client:
        async with client.stream("GET", url) as response:
            response.raise_for_status()
            chunks: list[bytes] = []
            total = 0
            async for chunk in response.aiter_bytes():
                total += len(chunk)
                if total > MAX_FEED_BYTES:
                    raise ValueError("Job source response exceeded the 5 MB limit")
                chunks.append(chunk)
            return b"".join(chunks)


async def _get_json(url: str) -> object:
    try:
        return json.loads(await _read_bounded(url))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("Job source returned invalid JSON") from None


async def _safe_public_https_bytes(url: str) -> bytes:
    validate_public_https_url(url)
    parts = urlsplit(url)
    try:
        resolved = await asyncio.to_thread(socket.getaddrinfo, parts.hostname, parts.port or 443, type=socket.SOCK_STREAM)
    except OSError:
        raise ValueError("Public job source hostname could not be resolved") from None
    addresses = {item[4][0] for item in resolved}
    if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise ValueError("Public job source must resolve only to public IP addresses")
    return await _read_bounded(url)


async def _safe_rss_bytes(url: str) -> bytes:
    return await _safe_public_https_bytes(url)


def _greenhouse_items(payload: object, source: JobSource) -> list[dict]:
    if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
        raise ValueError("Greenhouse returned an unexpected job board response")
    items = []
    for item in payload["jobs"]:
        if not isinstance(item, dict) or not item.get("absolute_url") or not item.get("title"):
            continue
        location = (item.get("location") or {}).get("name", "") if isinstance(item.get("location"), dict) else ""
        description = plain_text(item.get("content"))
        items.append(_record(source, identifier=str(item.get("id")), title=item["title"],
                             url=item["absolute_url"], location=location, description=description,
                             posted_at=None))  # Greenhouse's updated_at is not an original posting date.
    return items


def _lever_items(payload: object, source: JobSource) -> list[dict]:
    if not isinstance(payload, list):
        raise ValueError("Lever returned an unexpected job board response")
    items = []
    for item in payload:
        if not isinstance(item, dict) or not item.get("hostedUrl") or not item.get("text"):
            continue
        categories = item.get("categories") if isinstance(item.get("categories"), dict) else {}
        location = categories.get("location", "")
        description = item.get("descriptionPlain") or plain_text(item.get("description"))
        salary = item.get("salaryRange") if isinstance(item.get("salaryRange"), dict) else {}
        commitment = categories.get("commitment")
        items.append(_record(
            source, identifier=str(item.get("id")), title=item["text"], url=item["hostedUrl"],
            application_url=item.get("applyUrl") or item["hostedUrl"], location=location,
            description=description, posted_at=_date(str(item.get("createdAt", ""))),
            employment_type=commitment,
            salary_min=_as_float(salary.get("min")), salary_max=_as_float(salary.get("max")),
            salary_currency=salary.get("currency"),
        ))
    return items


def _ashby_items(payload: object, source: JobSource) -> list[dict]:
    if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
        raise ValueError("Ashby returned an unexpected public job board response")
    items = []
    for item in payload["jobs"]:
        if not isinstance(item, dict) or item.get("isListed") is False:
            continue
        title, url = item.get("title"), item.get("jobUrl")
        if not isinstance(title, str) or not isinstance(url, str):
            continue
        locations = [item.get("location", "")]
        secondary = item.get("secondaryLocations")
        if isinstance(secondary, list):
            locations.extend(place.get("location", "") for place in secondary if isinstance(place, dict))
        location = ", ".join(dict.fromkeys(place.strip() for place in locations if isinstance(place, str) and place.strip()))
        mode_value = str(item.get("workplaceType", "")).casefold()
        work_mode = {"remote": "remote", "hybrid": "hybrid", "onsite": "on-site", "on-site": "on-site"}.get(mode_value)
        description = item.get("descriptionPlain") or plain_text(item.get("descriptionHtml"))
        salary_min = salary_max = None
        salary_currency = None
        compensation = item.get("compensation") if isinstance(item.get("compensation"), dict) else {}
        components = compensation.get("summaryComponents") or []
        if not components:
            for tier in compensation.get("compensationTiers", []) if isinstance(compensation.get("compensationTiers"), list) else []:
                if isinstance(tier, dict) and isinstance(tier.get("components"), list):
                    components.extend(tier["components"])
        salary = next((part for part in components if isinstance(part, dict) and str(part.get("compensationType", "")).casefold() == "salary"), None)
        if salary:
            salary_min = _as_float(salary.get("minValue"))
            salary_max = _as_float(salary.get("maxValue"))
            salary_currency = salary.get("currencyCode")
        items.append(_record(
            source, identifier=item.get("id"), title=title, url=url,
            application_url=item.get("applyUrl") or url, location=location,
            description=description if isinstance(description, str) else "",
            posted_at=_date(item.get("publishedAt")), employment_type=item.get("employmentType"),
            salary_min=salary_min, salary_max=salary_max, salary_currency=salary_currency,
            work_mode=work_mode,
        ))
    return items


def _walk_job_postings(value: object, depth: int = 0):
    if depth > 12:
        return
    if isinstance(value, list):
        for item in value:
            yield from _walk_job_postings(item, depth + 1)
    elif isinstance(value, dict):
        types = value.get("@type", [])
        types = [types] if isinstance(types, str) else types if isinstance(types, list) else []
        if any(str(kind).rsplit("/", 1)[-1].casefold() == "jobposting" for kind in types):
            yield value
        else:
            for key in ("@graph", "mainEntity", "itemListElement", "hasPart", "@included"):
                if key in value:
                    yield from _walk_job_postings(value[key], depth + 1)


def _jsonld_documents(raw: bytes) -> list[object]:
    parser = _JsonLdParser()
    try:
        parser.feed(raw.decode("utf-8", errors="replace"))
    except Exception:
        raise ValueError("Career page HTML could not be parsed") from None
    documents = []
    for script in parser.documents:
        try:
            documents.append(json.loads(script))
        except json.JSONDecodeError:
            continue
    return documents


def _schema_locations(value: object) -> str:
    if not isinstance(value, list):
        value = [value]
    found = []
    for place in value:
        if not isinstance(place, dict):
            continue
        name = place.get("name")
        address = place.get("address")
        if isinstance(address, dict):
            postal = address.get("address") if isinstance(address.get("address"), dict) else address
            pieces = [postal.get(key) for key in ("addressLocality", "addressRegion", "addressCountry")]
            formatted = ", ".join(dict.fromkeys(str(part).strip() for part in pieces if isinstance(part, str) and part.strip()))
            name = name or formatted
        if isinstance(name, str) and name.strip() and name.strip() not in found:
            found.append(name.strip())
    return ", ".join(found)


def _jsonld_job_items(documents: list[object], source: JobSource) -> list[dict]:
    items = []
    for document in documents:
        for posting in _walk_job_postings(document):
            title, url = posting.get("title"), posting.get("url")
            if not isinstance(title, str) or not isinstance(url, str):
                continue
            hiring_org = posting.get("hiringOrganization")
            company = hiring_org.get("name") if isinstance(hiring_org, dict) else None
            identifier = posting.get("identifier")
            if isinstance(identifier, dict):
                identifier = identifier.get("value")
            salary_min = salary_max = None
            salary_currency = None
            salary = posting.get("baseSalary")
            if isinstance(salary, dict):
                salary_currency = salary.get("currency")
                value = salary.get("value")
                if isinstance(value, dict):
                    salary_min = _as_float(value.get("minValue") or value.get("value"))
                    salary_max = _as_float(value.get("maxValue") or value.get("value"))
                else:
                    salary_min = salary_max = _as_float(value)
            mode = "remote" if str(posting.get("jobLocationType", "")).casefold() in {"telecommute", "remote"} else None
            employment = posting.get("employmentType")
            if isinstance(employment, list):
                employment = ", ".join(str(item) for item in employment if item)
            items.append(_record(
                source, identifier=str(identifier) if identifier is not None else None,
                title=title, url=url, application_url=url,
                location=_schema_locations(posting.get("jobLocation")),
                description=plain_text(posting.get("description") if isinstance(posting.get("description"), str) else ""),
                posted_at=_date(posting.get("datePosted")), expires_at=_date(posting.get("validThrough")),
                employment_type=employment, salary_min=salary_min, salary_max=salary_max,
                salary_currency=salary_currency, company=company, work_mode=mode,
            ))
    return items


def _career_page_items(raw: bytes, source: JobSource) -> list[dict]:
    return _jsonld_job_items(_jsonld_documents(raw), source)


def _as_float(value) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _rss_items(raw: bytes, source: JobSource) -> list[dict]:
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        raise ValueError("RSS/Atom feed is not valid XML") from None
    items = []
    for node in root.iter():
        tag = node.tag.rsplit("}", 1)[-1].lower()
        if tag not in {"item", "entry"}:
            continue
        fields: dict[str, str] = {}
        for child in list(node):
            key = child.tag.rsplit("}", 1)[-1].lower()
            fields[key] = "".join(child.itertext()).strip()
        link = fields.get("link", "")
        if not link:
            link_node = next((child for child in list(node) if child.tag.rsplit("}", 1)[-1].lower() == "link"), None)
            link = (link_node.get("href", "") if link_node is not None else "")
        title = plain_text(fields.get("title"))
        if not title or not link:
            continue
        description = plain_text(fields.get("description") or fields.get("summary") or fields.get("content"))
        items.append(_record(
            source, identifier=fields.get("guid") or fields.get("id"), title=title, url=link,
            application_url=link, location=fields.get("location", ""), description=description,
            posted_at=_date(fields.get("pubdate") or fields.get("published") or fields.get("updated")),
        ))
    return items


async def fetch_source(source: JobSource) -> list[dict]:
    slug = source.board_slug or ""
    if source.kind == "greenhouse":
        payload = await _get_json(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true")
        return _greenhouse_items(payload, source)
    if source.kind == "lever":
        payload = await _get_json(f"https://api.lever.co/v0/postings/{slug}?mode=json")
        return _lever_items(payload, source)
    if source.kind == "ashby":
        payload = await _get_json(f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true")
        return _ashby_items(payload, source)
    if source.kind == "rss" and source.feed_url:
        raw = await _safe_rss_bytes(source.feed_url)
        return _rss_items(raw, source)
    if source.kind == "career_page" and source.page_url:
        raw = await _safe_public_https_bytes(source.page_url)
        return _career_page_items(raw, source)
    raise ValueError(f"Unsupported configured job source type: {source.kind}")
