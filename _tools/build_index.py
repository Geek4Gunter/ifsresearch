import csv
import re
from pathlib import Path
from PyPDF2 import PdfReader

REPO_ROOT = Path(__file__).resolve().parents[1]
PDF_DIR = REPO_ROOT / "pdfs"
INDEX_PATH = REPO_ROOT / "00_index" / "00_index.csv"

FIELDS = [
    "filename",
    "title",
    "year",
    "document_type",
    "primary_topics",
    "policy_domain",
    "geographic_scope",
    "methodology",
    "core_mechanisms",
    "notes",
]

MULTISPACE_RE = re.compile(r"\s+")
YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")
PDF_DATE_RE = re.compile(r"D:(\d{4})(\d{2})?(\d{2})?")  # D:YYYYMMDD...

MONTHS = [
    "january","february","march","april","may","june",
    "july","august","september","october","november","december",
    "jan","feb","mar","apr","jun","jul","aug","sep","sept","oct","nov","dec"
]

# Optional curated overrides (add more anytime)
OVERRIDES = {
    # "somefile.pdf": {"title": "...", "year": "2024"}
}

def to_text(value) -> str:
    """
    Convert PyPDF2 metadata values to plain text safely.
    Handles IndirectObject and other non-string types.
    """
    if value is None:
        return ""
    try:
        # Some PyPDF2 objects have get_object()
        if hasattr(value, "get_object"):
            value = value.get_object()
    except Exception:
        pass

    # bytes -> decode
    if isinstance(value, (bytes, bytearray)):
        try:
            return value.decode("utf-8", errors="ignore")
        except Exception:
            return value.decode(errors="ignore")

    # basic types -> string
    try:
        return str(value)
    except Exception:
        return ""

def normalize_text(s) -> str:
    s = to_text(s)
    if not s:
        return ""
    s = s.replace("\u00ad", "")
    s = s.replace("\ufb01", "fi").replace("\ufb02", "fl")
    s = s.replace("\r", "\n")
    s = MULTISPACE_RE.sub(" ", s).strip()
    return s

def is_mostly_numeric(s: str) -> bool:
    s = (s or "").strip()
    if not s:
        return True
    digits = sum(ch.isdigit() for ch in s)
    letters = sum(ch.isalpha() for ch in s)
    return digits >= max(3, letters * 2) and letters < 5

def looks_like_address_or_url(s: str) -> bool:
    low = (s or "").lower().strip()
    bad_fragments = [
        "institute for family studies",
        "ifstudies.org",
        "www.",
        "http://",
        "https://",
        "ifs-admin",
        "resources/reports",
        "resources/briefs",
        "p.o.",
        "po box",
        "suite",
        "street",
        "st.",
        "ave",
        "avenue",
        "washington",
        "dc",
        "d.c.",
        "zip",
    ]
    if any(b in low for b in bad_fragments):
        return True
    if re.search(r"\b\d{2,5}\b.*\b(st|street|ave|avenue|rd|road|blvd|suite)\b", low):
        return True
    return False

def clean_metadata_title(s) -> str:
    s = normalize_text(s)
    if not s:
        return ""
    low = s.lower()
    junk = ["untitled", "microsoft word", "document"]
    if low in junk:
        return ""
    if looks_like_address_or_url(s) or len(s) < 8 or is_mostly_numeric(s):
        return ""
    return s

def parse_pdf_date_to_year(s) -> str:
    s = to_text(s)
    if not s:
        return ""
    m = PDF_DATE_RE.search(s)
    if m:
        year = m.group(1)
        if year and 1900 <= int(year) <= 2099:
            return year
    m2 = re.search(r"\b(19|20)\d{2}\b", s)
    if m2:
        yr = m2.group(0)
        if 1900 <= int(yr) <= 2099:
            return yr
    return ""

def extract_text_from_pages(pdf_path: Path, max_pages: int = 3, max_chars_per_page: int = 7000) -> str:
    try:
        reader = PdfReader(str(pdf_path))
        texts = []
        n = min(max_pages, len(reader.pages))
        for i in range(n):
            t = reader.pages[i].extract_text() or ""
            t = normalize_text(t)
            if t:
                texts.append(t[:max_chars_per_page])
        return "\n".join(texts)
    except Exception:
        return ""

def guess_year_from_text(text: str) -> str:
    if not text:
        return ""
    low = text.lower()

    cue_patterns = [
        r"©\s*(19|20)\d{2}",
        r"copyright\s*(19|20)\d{2}",
        r"published\s*(19|20)\d{2}",
        r"updated\s*(19|20)\d{2}",
        r"release(d)?\s*(19|20)\d{2}",
    ]
    for pat in cue_patterns:
        m = re.search(pat, low)
        if m:
            yr = re.search(r"\b(19|20)\d{2}\b", m.group(0))
            if yr:
                return yr.group(0)

    for month in MONTHS:
        m = re.search(rf"\b{month}\b[^0-9]{{0,10}}\b(19|20)\d{{2}}\b", low)
        if m:
            yr = re.search(r"\b(19|20)\d{2}\b", m.group(0))
            if yr:
                return yr.group(0)

    years = [m.group(0) for m in YEAR_RE.finditer(text)]
    if not years:
        return ""
    years_int = sorted({int(y) for y in years if 1900 <= int(y) <= 2099}, reverse=True)
    return str(years_int[0]) if years_int else ""

def guess_title_from_text(text: str, fallback_filename: str) -> str:
    if not text:
        return fallback_filename.replace(".pdf", "").replace("-", " ").strip()

    raw_lines = []
    for chunk in text.split("\n"):
        chunk = chunk.strip()
        if not chunk:
            continue
        raw_lines.extend([c.strip() for c in re.split(r"\s{2,}", chunk) if c.strip()])

    lines = [normalize_text(l) for l in raw_lines]
    lines = [l for l in lines if len(l) >= 8]

    candidates = []
    for l in lines[:80]:
        if looks_like_address_or_url(l):
            continue
        low = l.lower()
        if low.startswith(("by ", "authors:", "author:", "institute for", "ifs", "report", "brief")):
            continue
        if is_mostly_numeric(l):
            continue
        if any(b in low for b in ["table of contents", "introduction", "executive summary", "contents"]):
            continue
        candidates.append(l)

    if not candidates:
        return fallback_filename.replace(".pdf", "").replace("-", " ").strip()

    title = candidates[0]
    if len(candidates) > 1:
        second = candidates[1]
        if len(title) < 70 and len(second) < 90 and not second.lower().startswith(("by ", "author", "published", "updated")):
            if not re.fullmatch(r"(19|20)\d{2}", second.strip()):
                title = f"{title} {second}".strip()

    return title

def ensure_index_exists():
    if not INDEX_PATH.exists():
        INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
        with INDEX_PATH.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS, delimiter=",")
            w.writeheader()
        return
    text = INDEX_PATH.read_text(encoding="utf-8", errors="ignore").strip()
    if not text:
        with INDEX_PATH.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS, delimiter=",")
            w.writeheader()

def read_existing_rows() -> dict:
    existing = {}
    if not INDEX_PATH.exists():
        return existing
    with INDEX_PATH.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter=",")
        for r in reader:
            fn = (r.get("filename") or "").strip()
            if fn:
                existing[fn] = r
    return existing

def main():
    if not PDF_DIR.exists():
        print(f"ERROR: Could not find pdfs folder at: {PDF_DIR}")
        return

    ensure_index_exists()
    existing = read_existing_rows()

    pdfs = sorted(PDF_DIR.glob("*.pdf"))
    if not pdfs:
        print(f"No PDFs found in: {PDF_DIR}")
        return

    out_rows = []
    existing_filenames = set()

    for fn, row in existing.items():
        fixed = {k: row.get(k, "") for k in FIELDS}
        out_rows.append(fixed)
        existing_filenames.add(fn)

    added_count = 0
    updated_count = 0
    still_needs_review = 0

    for pdf_path in pdfs:
        fn = pdf_path.name
        current = existing.get(fn, {k: "" for k in FIELDS})
        base = {k: current.get(k, "") for k in FIELDS}
        base["filename"] = fn

        need_title = not (base.get("title") or "").strip() or looks_like_address_or_url(base.get("title", "")) or is_mostly_numeric(base.get("title", ""))
        need_year = not (base.get("year") or "").strip()

        if fn in OVERRIDES:
            if need_title:
                base["title"] = OVERRIDES[fn].get("title", base.get("title", ""))
                need_title = False
            if need_year:
                base["year"] = OVERRIDES[fn].get("year", base.get("year", ""))
                need_year = False
            base["notes"] = "curated_override"
        else:
            # Read metadata safely
            meta = {}
            try:
                reader = PdfReader(str(pdf_path))
                meta = reader.metadata or {}
            except Exception:
                meta = {}

            meta_title = ""
            meta_year = ""

            if isinstance(meta, dict):
                meta_title = meta.get("/Title", "")
                meta_year = parse_pdf_date_to_year(meta.get("/CreationDate", "")) or parse_pdf_date_to_year(meta.get("/ModDate", ""))
            else:
                # Fallback: try attribute access, then stringify
                meta_title = getattr(meta, "title", "") if hasattr(meta, "title") else ""
                meta_year = parse_pdf_date_to_year(getattr(meta, "creation_date", "")) or parse_pdf_date_to_year(getattr(meta, "modification_date", ""))

            meta_title = clean_metadata_title(meta_title)

            text = extract_text_from_pages(pdf_path, max_pages=3)

            if need_title:
                base["title"] = meta_title or guess_title_from_text(text, fn)

            if need_year:
                base["year"] = meta_year or guess_year_from_text(text)

            if (base.get("title") or "").strip() and (base.get("year") or "").strip():
                base["notes"] = "auto_seeded_plus"
            else:
                base["notes"] = "needs_review"
                still_needs_review += 1

        if fn in existing_filenames:
            for r in out_rows:
                if r.get("filename", "").strip() == fn:
                    before_title = (r.get("title") or "").strip()
                    before_year = (r.get("year") or "").strip()
                    r.update(base)
                    after_title = (r.get("title") or "").strip()
                    after_year = (r.get("year") or "").strip()
                    if (not before_title and after_title) or (not before_year and after_year):
                        updated_count += 1
                    break
        else:
            out_rows.append(base)
            existing_filenames.add(fn)
            added_count += 1

    with INDEX_PATH.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS, delimiter=",")
        writer.writeheader()
        writer.writerows(out_rows)

    print("DONE.")
    print(f"PDFs found: {len(pdfs)}")
    print(f"Rows added: {added_count}")
    print(f"Rows updated (filled missing/junk title/year): {updated_count}")
    print(f"Rows still needs_review: {still_needs_review}")
    print(f"Index written to: {INDEX_PATH}")

if __name__ == "__main__":
    main()
