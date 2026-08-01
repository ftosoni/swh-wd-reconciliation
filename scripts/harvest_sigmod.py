#!/usr/bin/env python3
# Copyright (c) 2026 Francesco Tosoni
# SPDX-License-Identifier: BSD-3-Clause
#
# This file is part of swh-wd-reconciliation
# (https://github.com/ftosoni/swh-wd-reconciliation).
# Licensed under the BSD 3-Clause License; see the LICENSE file for details.
"""
harvest_sigmod.py -- Harvest SIGMOD ARI publication-to-repository mappings.

Coverage strategy:
  2020-2023: reproducibility report PDFs hosted on reproducibility.sigmod.org
             -> download + URL extraction (regex over text + annotation links)
  2024-2025: PDFs behind ACM DL paywall (HTTP 403) -> no data available

Only official SIGMOD ARI sources are used; no GitHub search or external APIs.

Usage:
  python harvest_sigmod.py           # all years
  python harvest_sigmod.py --limit 20
  python harvest_sigmod.py --refresh   # force re-fetch of reports.html
"""
import os, re, csv, sys, time, hashlib, logging, urllib.request, urllib.parse
from bs4 import BeautifulSoup
import pypdf

HERE      = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(HERE, "cache")
PDF_DIR   = os.path.join(CACHE_DIR, "sigmod_pdfs")
os.makedirs(PDF_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(os.path.join(HERE, "scrape_sigmod.log"), encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)

UA        = "SWH-Wikidata-thesis/1.0 (mailto:Francesco.Tosoni@santannapisa.it)"
CODE_HOST = re.compile(
    r"(?:github|gitlab|bitbucket|sourceforge|codeberg|zenodo)\.(?:com|org|net)",
    re.IGNORECASE,
)
# Basic English function words only -- keep domain terms (graph, query, ...) for better search
SECTIONS = [
    ("SIGMOD 2025", "SIGMOD25-reports", "2025"),
    ("SIGMOD 2024", "SIGMOD24-reports", "2024"),
    ("SIGMOD 2023", "SIGMOD23-reports", "2023"),
    ("SIGMOD 2022", "SIGMOD22-reports", "2022"),
    ("SIGMOD 2021", "SIGMOD21-reports", "2021"),
    ("SIGMOD 2020", "SIGMOD20-reports", "2020"),
]

# ---------------------------------------------------------------------------
# Fetch helpers
# ---------------------------------------------------------------------------

_BROWSER_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/125.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
}


def _fetch_raw(url: str, delay: float, extra_headers: dict = None) -> bytes | None:
    time.sleep(delay)
    headers = {"User-Agent": UA}
    if extra_headers:
        headers.update(extra_headers)
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.read()
    except Exception as e:
        logging.error(f"Fetch failed {url}: {e}")
        return None


def _fetch_raw_browser(url: str, delay: float, referer: str = "") -> bytes | None:
    """Fetch with full browser headers — needed for ACM DL open-access PDFs."""
    time.sleep(delay)
    headers = dict(_BROWSER_HEADERS)
    if referer:
        headers["Referer"] = referer
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.read()
    except Exception as e:
        logging.error(f"Browser-fetch failed {url}: {e}")
        return None


def _cache_path(url: str, ext: str, subdir: str) -> str:
    h = hashlib.md5(url.encode()).hexdigest()
    d = os.path.join(CACHE_DIR, subdir)
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, f"{h}.{ext}")


def fetch_html(url: str, delay: float = 1.0) -> str | None:
    path = _cache_path(url, "html", "sigmod")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return f.read()
    raw = _fetch_raw(url, delay)
    if raw is None:
        return None
    with open(path, "wb") as f:
        f.write(raw)
    return raw.decode("utf-8", errors="ignore")


def fetch_pdf(url: str, delay: float = 0.5) -> bool:
    """Download PDF to PDF_DIR; return True if on disk (cached or fresh).

    For ACM DL report pages (dl.acm.org/doi/10.xxx/yyy), converts the landing
    page URL to the direct PDF URL (/doi/pdf/10.xxx/yyy) and uses browser
    headers — ACM DL serves open-access PDFs without cookies when the request
    looks like a real browser navigation.
    """
    path = _cache_path(url, "pdf", "sigmod_pdfs")
    if os.path.exists(path):
        return True

    if "dl.acm.org/doi/" in url and "/doi/pdf/" not in url:
        # Convert landing page URL to direct PDF URL
        pdf_url = url.replace("dl.acm.org/doi/", "dl.acm.org/doi/pdf/", 1)
        # Strip any fragment (#heading1 etc.)
        pdf_url = pdf_url.split("#")[0]
        raw = _fetch_raw_browser(pdf_url, delay, referer=url)
    else:
        raw = _fetch_raw(url, delay)

    if raw is None or raw[:4] != b"%PDF":
        if raw is not None:
            logging.warning(f"Response is not a PDF for {url} (got {raw[:20]!r})")
        return False
    with open(path, "wb") as f:
        f.write(raw)
    return True


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------

def clean_url(raw: str) -> str:
    """Canonicalise a code-hosting URL to https://<host>/<org>/<repo>."""
    # Strip trailing punctuation that clings to URLs in prose, including
    # the closing bracket of "(https://...)" constructs.
    url = raw.strip().rstrip(".,;:'\")]>}")
    # Heal a doubled scheme ("http://https://github.com/...") seen in some
    # reports where the authors pasted a URL after an existing "http://".
    url = re.sub(r"^https?://(?=https?://)", "", url)
    if url.startswith("www."):
        url = "https://" + url
    elif not url.startswith("http"):
        url = "https://" + url
    p      = urllib.parse.urlparse(url)
    netloc = p.netloc.lower()
    path   = p.path
    for host in ("github.com", "gitlab.com", "bitbucket.org", "codeberg.org"):
        if host in netloc:
            parts = [x for x in path.split("/") if x]
            if len(parts) >= 2:
                # Lowercase org/repo: GitHub paths are case-insensitive, so
                # "User/Repo" and "user/repo" are the same repo. Use
                # removesuffix(".git") -- NOT rstrip(".git"), which would
                # strip any trailing g/i/t/. characters (e.g. turning
                # "adsampling" into "adsamplin" or "facet" into "face").
                repo = parts[1].lower().removesuffix(".git")
                return f"https://{netloc}/{parts[0].lower()}/{repo}"
            if len(parts) == 1:
                return f"https://{netloc}/{parts[0].lower()}"
    if "zenodo.org" in netloc:
        parts = [x for x in path.split("/") if x]
        if len(parts) >= 2 and parts[0] in ("record", "records"):
            return f"https://zenodo.org/records/{parts[1]}"
    return url

# ---------------------------------------------------------------------------
# PDF extraction
# ---------------------------------------------------------------------------

# Host/path fragments that are never a paper's own artifact repository.
# NB: do NOT add "sigmod-reproducibility" here -- it matches legitimate
# artifact repos named "sigmod-reproducibility-*" (e.g. tomtseng's).
_PDF_SKIP = (
    "reproducibility.sigmod.org", "doi.org",
    "dl.acm.org", "arxiv.org", "springer.com",
)

# GitHub orgs the ARI reviewers use to host their own *clones* of an
# artifact. We want the authors' canonical origin, not the reviewer mirror,
# so candidates under these orgs are discarded (e.g. COMPASS, 3452840:
# repro-reviews/compass_query_optimizer -> authors' yizenov/... is kept).
_ARI_CLONE_ORGS = ("repro-reviews",)

# Manually verified artifact repositories that the heuristic extractor
# cannot recover because they live on self-hosted forges outside the
# CODE_HOST whitelist (confirmed by reading the report PDF text). Keyed by
# paper DOI; applied only when extraction finds nothing on a major host.
_MANUAL_ADDITIONS = {
    # WeTune (SIGMOD'22) -- self-hosted GitLab at SJTU IPADS
    "10.1145/3514221.3526125": "https://ipads.se.sjtu.edu.cn:1312/opensource/wetune",
    # EmbDI (SIGMOD'20) -- self-hosted GitLab at EURECOM
    "10.1145/3318464.3389742": "https://gitlab.eurecom.fr/cappuzzo/embdi",
}

def extract_from_pdf(pdf_path: str) -> str | None:
    try:
        reader = pypdf.PdfReader(pdf_path)
        text   = "\n".join(page.extract_text() or "" for page in reader.pages)

        raw: set[str] = set()
        # Pass 1: full URLs with scheme
        raw.update(re.findall(r"https?://\S+", text))
        # Pass 2: bare code-host references (e.g. "github.com/user/repo")
        raw.update(re.findall(
            r"(?<!\w)(?:github|gitlab|bitbucket|codeberg|zenodo|sourceforge)"
            r"\.(?:com|org|net)/\S+",
            text, re.IGNORECASE,
        ))
        # Pass 3: interactive annotation links
        for page in reader.pages:
            if "/Annots" not in page:
                continue
            for annot in page["/Annots"]:
                obj = annot.get_object()
                if obj.get("/Subtype") == "/Link":
                    a = obj.get("/A")
                    if a:
                        uri = a.get_object().get("/URI")
                        if uri:
                            raw.add(uri)

        # Count raw occurrences of each canonical repo in the full text.
        # The paper's own repo is typically mentioned more than once;
        # cited third-party repos usually appear once.
        canon_count: dict[str, int] = {}
        for u in raw:
            if not CODE_HOST.search(u): continue
            if any(s in u.lower() for s in _PDF_SKIP): continue
            canon = clean_url(u)
            p = urllib.parse.urlparse(canon)
            parts = [x for x in p.path.split("/") if x]
            if any(h in p.netloc for h in ("github.com","gitlab.com","bitbucket.org","codeberg.org")):
                if len(parts) < 2:
                    continue   # bare org URL — not a repo
                if parts[0].lower() in _ARI_CLONE_ORGS:
                    continue   # ARI reviewer clone, not the authors' origin
            canon_count[canon] = canon_count.get(canon, 0) + 1

        if not canon_count:
            return None

        # Heal line-wrapped URLs: a long URL split across two PDF lines is
        # captured only up to the wrap (e.g. "fsalc/diverse-" when the repo
        # is "fsalc/diverse-top-k"). When one canonical URL is a strict
        # prefix of a longer one, fold the fragment's count into the longer
        # URL -- unless the continuation begins with "." (a ".git"/file-ext
        # artifact such as "pmem-olap" vs a truncated "pmem-olap.gi").
        longest = sorted(canon_count, key=len, reverse=True)
        merged: dict[str, int] = {}
        for short in canon_count:
            target = short
            for cand in longest:
                if cand != short and cand.startswith(short) \
                        and not cand[len(short):].startswith("."):
                    target = cand
                    break
            merged[target] = merged.get(target, 0) + canon_count[short]
        canon_count = merged

        # Prefer GitHub/GitLab over Zenodo etc., then rank candidates.
        _ARTIFACT = ("reproducib", "artifact", "experiment", "sigmod", "repro")
        for host in ("github.com", "gitlab.com", "zenodo.org"):
            candidates = [r for r in canon_count if host in r]
            if not candidates:
                continue
            # Sort by: (1) artifact-signal in path, (2) occurrence count desc,
            # (3) URL alphabetically for tie-breaking determinism.
            def _rank(r):
                has_signal = any(t in r.lower() for t in _ARTIFACT)
                return (0 if has_signal else 1, -canon_count[r], r)
            return sorted(candidates, key=_rank)[0]

        return sorted(canon_count, key=lambda r: (-canon_count[r], r))[0]
    except Exception as e:
        logging.error(f"PDF parse error {pdf_path}: {e}")
    return None

# ---------------------------------------------------------------------------
# GitHub repo search fallback
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Main harvester
# ---------------------------------------------------------------------------

def harvest_sigmod(limit: int = None, force_refresh: bool = False) -> list:
    REPORTS_URL = "https://reproducibility.sigmod.org/reports.html"

    if force_refresh:
        old = _cache_path(REPORTS_URL, "html", "sigmod")
        if os.path.exists(old):
            os.remove(old)
            logging.info("Cleared cached reports.html -- will re-fetch")

    html = fetch_html(REPORTS_URL)
    if not html:
        logging.error("Failed to load reports.html")
        return []

    soup   = BeautifulSoup(html, "html.parser")
    pairs  = []
    totals = success = 0

    for year_label, section_id, year in SECTIONS:
        sec = soup.find(id=section_id)
        if not sec:
            logging.warning(f"Section not found: {section_id}")
            continue
        table = sec.find("table") or sec.find_next("table")
        if not table:
            logging.warning(f"No table in {section_id}")
            continue

        rows = table.find_all("tr")[1:]  # skip header row
        logging.info(f"[{year_label}] {len(rows)} papers")

        for row in rows:
            if limit and totals >= limit:
                break
            cols = row.find_all("td")
            if len(cols) < 2:
                continue
            totals += 1

            # Column 0: paper title + DOI
            anchor = cols[0].find("a", href=True)
            if not anchor:
                continue
            title   = anchor.text.strip()
            doi_url = anchor["href"].strip()
            m       = re.search(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", doi_url, re.IGNORECASE)
            if not m:
                continue
            doi = m.group(0)

            # Column 1: report link
            rep_anchor  = cols[1].find("a", href=True)
            report_href = rep_anchor["href"].strip() if rep_anchor else ""
            report_abs  = urllib.parse.urljoin(REPORTS_URL, report_href) if report_href else ""

            # 2020-2023: local PDF path on reproducibility.sigmod.org
            # 2024: ACM DL landing page URL (open access, downloadable via
            #        browser headers; fetch_pdf handles the URL conversion)
            # 2025: all rows point to the same proceedings page — no individual
            #        reports exist yet, nothing to download
            has_report = bool(report_href and "proceedings" not in report_href)

            repo_url = ""

            if has_report and report_abs:
                pdf_path = _cache_path(report_abs, "pdf", "sigmod_pdfs")
                if fetch_pdf(report_abs):
                    repo_url = extract_from_pdf(pdf_path) or ""

            # Fall back to a manually verified repo for artifacts hosted on
            # self-hosted forges the CODE_HOST whitelist cannot detect.
            if not repo_url and doi in _MANUAL_ADDITIONS:
                repo_url = _MANUAL_ADDITIONS[doi]

            if repo_url:
                success += 1
                logging.info(f"[{year_label}] OK    {doi}  ->  {repo_url}")
            else:
                logging.warning(f"[{year_label}] MISS  {doi}  |  {title[:60]}")

            pairs.append([doi, year_label, title, repo_url, report_abs])

    out = os.path.join(HERE, "sigmod_pairs.csv")
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["doi", "year", "title", "repo_url", "report_url"])
        w.writerows(pairs)

    pct = f"{success / totals * 100:.1f}%" if totals else "n/a"
    logging.info(f"=== SIGMOD done  {success}/{totals}  ({pct})  -> {out} ===")
    return pairs


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--limit",   type=int, default=None, metavar="N",
                    help="Process only the first N papers (smoke-test)")
    ap.add_argument("--refresh", action="store_true",
                    help="Force re-fetch of reports.html")
    args = ap.parse_args()
    harvest_sigmod(limit=args.limit, force_refresh=args.refresh)
