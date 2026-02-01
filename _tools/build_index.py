import csv
import re
from pathlib import Path

# We will use PyPDF2 (installed in Step 4)
from PyPDF2 import PdfReader

REPO_ROOT = Path(__file__).resolve().parents[1]
PDF_DIR = REPO_ROOT / "pdfs"
INDEX_PATH = REPO_ROOT / "00_index" / "00_index.csv"

# --- Helpers ---

YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")
MULTISPACE_RE = re.compile(r"\s+")

def normalize_text(s: str) -> str:
    s = s.replace("\u00ad", "")  # soft hyphen
    s = s.replace("\ufb01", "fi").replace("\ufb02", "fl")  # ligatures common in PDFs
    s = MULTISPACE_RE.sub(" ", s).strip()
    return s

def extract_page1_text(pdf_path: Path, max_chars: int = 4000) -> str:
    try:
        reader = PdfReader(str(pdf_path))
        if not reader.pages:
            return ""
        text = reader.pages[0].extract_text() or ""
        text = normalize_text(text)
        return text[:max_chars]
    except Exception:
        return ""

def guess_year(text: str) -> str:
    # pick the most common year-ish match, if any
    years = YEAR_RE.findall(text)
    # YEAR_RE.findall returns tuples because of (19|20) group; use finditer instead:
    years = [m.group(0) for m in YEAR_RE.finditer(text)]
    if not years:
        return ""
    # prefer most recent plausible year
    years_int = sorted({int(y) for y in years if 1900 <= int(y) <= 2099}, reverse=True)
    return str(years_int[0]) if years_int else ""

def guess_title(text: str, fallback_name: str) -> str:
    """
    Heuristic: use first non-trivial line(s) that look like a title.
    PDF text extraction is messy; we aim for "good enough" + mark needs_review.
    """
    if not text:
        return fallback_name.replace(".pdf", "").replace("-", " ").strip()

    # split into lines using punctuation/known breaks
    lines = [normalize_text(l) for l in text.split("\n")]
    lines = [l for l in lines if l and len(l) >= 8]

    # remove lines that look like headers/footers
    bad_starts = ("institute for family studies", "ifs", "report", "brief", "www.", "http")
    candidates = []
    for l in lines[:25]:
        low = l.lower()
        if any(low.startswith(b) for b in bad_starts):
            continue
        # avoid pure author lines
        if low.startswith("by "):
            continue
        # avoid lines that are mostly numbers
        if sum(ch.isdigit() for ch in l) > max(4, len(l)//3):
            continue
        candidates.append(l)

    if not candidates:
        return fallback_name.replace(".pdf", "").replace("-", " ").strip()

    # Title candidate is typically among first few meaningful lines
    title = candidates[0]
    # If the next line also looks like title continuation, combine
    if len(candidates) > 1 and len(title) < 60 and len(candidates[1]) < 80:
        # avoid combining if second looks like date or "by"
        if not candidates[1].lower().startswith(("by ", "published", "updated")):
            title = f"{title}: {candidates[1]}" if title.endswith("?") else f"{title} {candidates[1]}"

    return title.strip()

def load_existing_rows(index_path: Path):
    """
    Returns dict of filename -> row dict for already-indexed PDFs.
    """
    if not index_path.exists():
        return {}, []

    with index_path.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        existing = {}
        for r in rows:
            fn = (r.get("filename") or "").strip()
            if fn:
                existing[fn] = r
        return existing, reader.fieldnames or []

def ensure_header(index_path: Path):
    """
    Ensures the CSV exists and has a supported header.
    If empty, writes the default header.
    """
    default_fields = [
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

    if not index_path.exists():
        index_path.parent.mkdir(parents=True, exist_ok=True)
        with index_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=default_fields)
            writer.writeheader()
        return default_fields

    # If file exists but is empty, write header
    content = index_path.read_text(encoding="utf-8").strip()
    if not content:
        with index_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=default_fields)
            writer.writeheader()
        return default_fields

    # Otherwise, keep existing header
    _, fieldnames = load_existing_rows(index_path)
    if not fieldnames:
        # rewrite with defaults if header cannot be read
        with index_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=default_fields)
            writer.writeheader()
        return default_fields
    return fieldnames

def main():
    if not PDF_DIR.exists():
        print(f"ERROR: Could not find pdfs folder at: {PDF_DIR}")
        return

    fields = ensure_header(INDEX_PATH)
    existing, _ = load_existing_rows(INDEX_PATH)

    pdf_files = sorted(PDF_DIR.glob("*.pdf"))
    if not pdf_files:
        print(f"No PDFs found in: {PDF_DIR}")
        return

    new_rows = []
    skipped = 0

    for pdf_path in pdf_files:
        filename = pdf_path.name
        if filename in existing and (existing[filename].get("title") or "").strip():
            skipped += 1
            continue

        page1 = extract_page1_text(pdf_path)
        title = guess_title(page1, filename)
        year = guess_year(page1)

        row = {k: "" for k in fields}
        row["filename"] = filename
        row["title"] = title
        row["year"] = year
        # leave these for you/us to refine later, but give a helpful marker
        if not year or not title:
            row["notes"] = "needs_review"
        else:
            row["notes"] = "auto_seeded"

        new_rows.append(row)

    # merge rows: keep existing rows + append new ones
    # preserve existing order; add new rows at end
    final_rows = []
    if INDEX_PATH.exists():
        with INDEX_PATH.open("r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for r in reader:
                # ensure all fields exist
                fixed = {k: r.get(k, "") for k in fields}
                final_rows.append(fixed)

    # add new rows that aren't already present
    existing_filenames = {r.get("filename","").strip() for r in final_rows if r.get("filename")}
    for r in new_rows:
        if r["filename"] not in existing_filenames:
            final_rows.append(r)

    with INDEX_PATH.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(final_rows)

    print("DONE.")
    print(f"PDFs found: {len(pdf_files)}")
    print(f"Rows skipped (already indexed): {skipped}")
    print(f"Rows added (auto-seeded): {len(new_rows)}")
    print(f"Index written to: {INDEX_PATH}")

if __name__ == "__main__":
    main()
