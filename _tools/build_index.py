import csv
import re
from pathlib import Path

from PyPDF2 import PdfReader

REPO_ROOT = Path(__file__).resolve().parents[1]
PDF_DIR = REPO_ROOT / "pdfs"
INDEX_PATH = REPO_ROOT / "00_index" / "00_index.csv"

YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")
MULTISPACE_RE = re.compile(r"\s+")


def normalize_text(s: str) -> str:
    s = s.replace("\u00ad", "")  # soft hyphen
    s = s.replace("\ufb01", "fi").replace("\ufb02", "fl")  # ligatures common in PDFs
    s = MULTISPACE_RE.sub(" ", s).strip()
    return s


def detect_delimiter(csv_path: Path) -> str:
    """
    Detect delimiter used in existing index file.
    Excel may save "CSV" with semicolons depending on locale.
    Sometimes the file becomes tab-delimited too.
    We inspect the header line and pick the delimiter with the most separators.
    """
    if not csv_path.exists():
        return ","
    text = csv_path.read_text(encoding="utf-8", errors="ignore")
    lines = text.splitlines()
    if not lines:
        return ","
    header = lines[0]
    candidates = [",", "\t", ";"]
    counts = {d: header.count(d) for d in candidates}
    best = max(counts, key=counts.get)
    return best if counts[best] > 0 else ","


def extract_page1_text(pdf_path: Path, max_chars: int = 5000) -> str:
    try:
        reader = PdfReader(str(pdf_path))
        if not reader.pages:
            return ""
        text = reader.pages[0].extract_text() or ""
        return normalize_text(text)[:max_chars]
    except Exception:
        return ""


def guess_year(text: str) -> str:
    years = [m.group(0) for m in YEAR_RE.finditer(text or "")]
    if not years:
        return ""
    years_int = sorted({int(y) for y in years if 1900 <= int(y) <= 2099}, reverse=True)
    return str(years_int[0]) if years_int else ""


def guess_title(text: str, fallback_name: str) -> str:
    """
    Heuristic title extraction.
    Uses early lines on page 1 that look like a report title.
    """
    if not text:
        return fallback_name.replace(".pdf", "").replace("-", " ").strip()

    # Split into lines (PDF extraction sometimes preserves newlines)
    lines = [normalize_text(l) for l in (text.split("\n") if "\n" in text else text.split("\r"))]
    lines = [l for l in lines if l and len(l) >= 8]

    bad_starts = (
        "institute for family studies",
        "ifs",
        "report",
        "brief",
        "www.",
        "http",
    )

    candidates = []
    for l in lines[:30]:
        low = l.lower()
        if any(low.startswith(b) for b in bad_starts):
            continue
        if low.startswith("by "):
            continue
        # avoid lines mostly numeric
        if sum(ch.isdigit() for ch in l) > max(4, len(l) // 3):
            continue
        candidates.append(l)

    if not candidates:
        return fallback_name.replace(".pdf", "").replace("-", " ").strip()

    title = candidates[0]
    # Sometimes title spans two lines; combine cautiously
    if len(candidates) > 1 and len(title) < 65 and len(candidates[1]) < 90:
        nxt = candidates[1].lower()
        if not nxt.startswith(("by ", "published", "updated", "date")):
            title = f"{title} {candidates[1]}"

    return title.strip()


def ensure_header(index_path: Path):
    """
    Ensures the CSV exists and has a header row.
    If the file is missing or empty, writes the default header.
    If it exists, keeps its header (whatever delimiter it uses).
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
            writer = csv.DictWriter(f, fieldnames=default_fields, delimiter=",")
            writer.writeheader()
        return default_fields

    content = index_path.read_text(encoding="utf-8", errors="ignore").strip()
    if not content:
        with index_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=default_fields, delimiter=",")
            writer.writeheader()
        return default_fields

    # Read existing header using detected delimiter
    delim = detect_delimiter(index_path)
    with index_path.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter=delim)
        fields = reader.fieldnames or []
    return fields if fields else default_fields


def load_existing_rows(index_path: Path):
    """
    Load existing rows from the index, supporting comma/tab/semicolon.
    Returns:
      existing_map: filename -> row dict
      fieldnames: list of columns
      existing_rows: list of row dicts in file order
      delim: detected delimiter
    """
    if not index_path.exists():
        return {}, [], [], ","

    delim = detect_delimiter(index_path)

    with index_path.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter=delim)
        fieldnames = reader.fieldnames or []
        rows = []
        existing = {}
        for r in reader:
            rows.append(r)
            fn = (r.get("filename") or "").strip()
            if fn:
                existing[fn] = r
        return existing, fieldnames, rows, delim


def main():
    if not PDF_DIR.exists():
        print(f"ERROR: Could not find pdfs folder at: {PDF_DIR}")
        return

    fields = ensure_header(INDEX_PATH)

    existing, existing_fields, existing_rows, _ = load_existing_rows(INDEX_PATH)

    # If existing header differs from defaults, keep existing header,
    # but ensure required columns exist; if not, extend header safely.
    required = ["filename", "title", "year", "notes"]
    fields_set = set(fields)

    if any(r not in fields_set for r in required):
        # If we have an existing header and it's missing required fields,
        # extend the header with the missing ones.
        for r in required:
            if r not in fields_set:
                fields.append(r)
                fields_set.add(r)

    pdf_files = sorted(PDF_DIR.glob("*.pdf"))
    if not pdf_files:
        print(f"No PDFs found in: {PDF_DIR}")
        return

    # Normalize existing rows to our final field list
    final_rows = []
    for r in existing_rows:
        fixed = {k: (r.get(k, "") if isinstance(r, dict) else "") for k in fields}
        final_rows.append(fixed)

    existing_filenames = {r.get("filename", "").strip() for r in final_rows if r.get("filename")}

    skipped = 0
    added = 0

    for pdf_path in pdf_files:
        filename = pdf_path.name

        # If already present with a title, skip
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

        # Mark notes for review
        if not title or not year:
            row["notes"] = "needs_review"
        else:
            row["notes"] = "auto_seeded"

        # Append only if not already in file
        if filename not in existing_filenames:
            final_rows.append(row)
            existing_filenames.add(filename)
            added += 1
        else:
            # If row exists but title was blank, update it in place
            for existing_row in final_rows:
                if existing_row.get("filename", "").strip() == filename:
                    if not (existing_row.get("title") or "").strip():
                        existing_row["title"] = title
                    if not (existing_row.get("year") or "").strip():
                        existing_row["year"] = year
                    if not (existing_row.get("notes") or "").strip():
                        existing_row["notes"] = row["notes"]
                    break

    # Write back ALWAYS as comma-delimited UTF-8
    with INDEX_PATH.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, delimiter=",")
        writer.writeheader()
        writer.writerows(final_rows)

    print("DONE.")
    print(f"PDFs found: {len(pdf_files)}")
    print(f"Rows skipped (already indexed): {skipped}")
    print(f"Rows added/updated (auto-seeded): {added}")
    print(f"Index written to: {INDEX_PATH}")


if __name__ == "__main__":
    main()
