"""Tidy: sort loose files on Desktop / Downloads / Documents into folders.

Nothing is ever deleted. Files are only moved, and every run with --apply
writes an undo log to logs/ that undo.py can reverse.

  py tidy.py              preview only (default) - shows what would move
  py tidy.py --apply      actually move the files
  py tidy.py --no-ai      sort documents by type instead of by topic
  py tidy.py --only Downloads
"""

import argparse
import csv
import hashlib
import json
import os
import shutil
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOGS = HERE / "logs"
CACHE_FILE = HERE / ".topic_cache.json"

FILES_PER_REQUEST = 15
SNIPPET_CHARS = 1500


def load_config():
    with open(HERE / "config.json", encoding="utf-8") as f:
        cfg = json.load(f)
    cfg["folders"] = [Path(os.path.expanduser(p)) for p in cfg["folders"]]
    cfg["ext_to_type"] = {
        ext: folder for folder, exts in cfg["type_folders"].items() for ext in exts
    }
    return cfg


# ---------------------------------------------------------------- scanning

def is_hidden_or_system(path: Path) -> bool:
    if path.name.startswith("."):
        return True
    try:
        attrs = os.stat(path).st_file_attributes  # Windows only
        return bool(attrs & (0x2 | 0x4))  # HIDDEN | SYSTEM
    except (AttributeError, OSError):
        return False


def scan_folder(folder: Path, cfg):
    """Top-level files only - existing subfolders are left alone."""
    cutoff = time.time() - cfg["skip_if_modified_within_minutes"] * 60
    never = set(cfg["never_move_extensions"])
    for entry in sorted(folder.iterdir()):
        if not entry.is_file() or is_hidden_or_system(entry):
            continue
        if entry.suffix.lower() in never:
            continue
        if entry.stat().st_mtime > cutoff:
            continue  # probably still in use / downloading
        yield entry


def junk_reason(path: Path, cfg):
    """Return a Potential Junk subfolder name if this file looks like junk, else None."""
    ext = path.suffix.lower()
    age_days = (time.time() - path.stat().st_mtime) / 86400
    rules = cfg["potential_junk"]
    if ext in rules["unfinished_download_extensions"] and age_days > 1:
        return "Unfinished Downloads"
    if ext in rules["installer_extensions"] and age_days > rules["installer_older_than_days"]:
        return "Old Installers"
    if ext in rules["disk_image_extensions"] and age_days > rules["disk_image_older_than_days"]:
        return "Old Disk Images"
    return None


def file_hash(path: Path):
    """None if the file can't be read (e.g. a OneDrive file that isn't downloaded)."""
    h = hashlib.sha1()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
    except OSError:
        return None
    return h.hexdigest()


def find_duplicates(files):
    """Return the set of files that are exact copies of another file in the list.
    Only files sharing a size are hashed, so big unique ISOs aren't read."""
    by_size = defaultdict(list)
    for f in files:
        by_size[f.stat().st_size].append(f)
    dupes = set()
    for group in by_size.values():
        if len(group) < 2:
            continue
        by_hash = defaultdict(list)
        for f in group:
            digest = file_hash(f)
            if digest:
                by_hash[digest].append(f)
        for same in by_hash.values():
            # keep the copy with the shortest name ("report.pdf" over "report (1).pdf")
            same.sort(key=lambda p: (len(p.name), p.stat().st_mtime))
            dupes.update(same[1:])
    return dupes


# ---------------------------------------------------------------- AI topics

def extract_snippet(path: Path) -> str:
    ext = path.suffix.lower()
    try:
        if ext == ".pdf":
            from pypdf import PdfReader
            reader = PdfReader(str(path))
            text = ""
            for page in reader.pages[:3]:
                text += page.extract_text() or ""
                if len(text) >= SNIPPET_CHARS:
                    break
            return text[:SNIPPET_CHARS]
        if ext == ".docx":
            import docx
            d = docx.Document(str(path))
            return "\n".join(p.text for p in d.paragraphs)[:SNIPPET_CHARS]
        if ext in (".txt", ".md", ".rtf"):
            return path.read_text(encoding="utf-8", errors="ignore")[:SNIPPET_CHARS]
    except Exception:
        pass
    return ""  # scanned PDF, old .doc, etc. - the filename still helps


def cache_key(path: Path) -> str:
    st = path.stat()
    return f"{path.name}|{st.st_size}|{int(st.st_mtime)}"


def classify_topics(docs, cfg):
    """Ask Claude which topic folder each document belongs in. Results are cached
    so re-running the preview doesn't cost anything for files already seen."""
    import anthropic

    cache = json.loads(CACHE_FILE.read_text("utf-8")) if CACHE_FILE.exists() else {}
    todo = [d for d in docs if cache_key(d) not in cache]
    if todo:
        print(f"Asking Claude to classify {len(todo)} documents "
              f"({len(docs) - len(todo)} already cached)...")
    client = anthropic.Anthropic()
    topics = cfg["topics"]
    schema = {
        "type": "object",
        "properties": {
            "results": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "topic": {"type": "string", "enum": topics},
                    },
                    "required": ["id", "topic"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["results"],
        "additionalProperties": False,
    }
    system = (
        "You sort a person's personal files into folders. For each file you get its "
        "name and the start of its text (which may be empty for scans). Pick the single "
        "best folder from the allowed list. Use 'Other Documents' only when nothing fits."
    )

    for start in range(0, len(todo), FILES_PER_REQUEST):
        batch = todo[start:start + FILES_PER_REQUEST]
        parts = []
        for i, path in enumerate(batch):
            snippet = extract_snippet(path).strip() or "(no text)"
            parts.append(f"<file id=\"{i}\">\nname: {path.name}\ntext:\n{snippet}\n</file>")
        try:
            response = client.beta.messages.create(
                model=cfg["model"],
                max_tokens=4000,
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema}},
                system=system,
                messages=[{"role": "user", "content": "\n\n".join(parts)}],
            )
        except anthropic.AuthenticationError:
            sys.exit("No valid Anthropic API key. Set ANTHROPIC_API_KEY, or run with --no-ai.")
        except anthropic.APIConnectionError:
            sys.exit("Couldn't reach the Anthropic API. Check your connection, or run with --no-ai.")
        except anthropic.APIStatusError as e:
            print(f"  API error ({e.status_code}) on a batch - those files will be sorted by type.")
            continue

        if response.stop_reason == "refusal":
            continue
        text = next((b.text for b in response.content if b.type == "text"), "")
        try:
            results = json.loads(text)["results"]
        except (json.JSONDecodeError, KeyError):
            continue
        for r in results:
            if 0 <= r["id"] < len(batch):
                cache[cache_key(batch[r["id"]])] = r["topic"]
        CACHE_FILE.write_text(json.dumps(cache, indent=1), "utf-8")
        print(f"  {min(start + FILES_PER_REQUEST, len(todo))}/{len(todo)}")

    return {d: cache.get(cache_key(d)) for d in docs}


# ---------------------------------------------------------------- planning

def type_fallback_for_doc(path: Path) -> str:
    ext = path.suffix.lower()
    return {".pdf": "PDFs", ".docx": "Word Docs", ".doc": "Word Docs"}.get(ext, "Text Documents")


def build_plan(folders, cfg, use_ai):
    plan = []  # (src, dest_dir, reason)
    docs = []
    for folder in folders:
        if not folder.exists():
            print(f"Skipping missing folder: {folder}")
            continue
        files = list(scan_folder(folder, cfg))
        dupes = find_duplicates(files)
        junk_root = folder / cfg["potential_junk"]["folder"]
        for f in files:
            ext = f.suffix.lower()
            reason = junk_reason(f, cfg)
            if f in dupes:
                plan.append([f, junk_root / "Duplicates", "potential junk: exact duplicate"])
            elif reason:
                plan.append([f, junk_root / reason, f"potential junk: {reason.lower()}"])
            elif ext in cfg["document_extensions"]:
                plan.append([f, None, "document"])
                docs.append(f)
            elif ext in cfg["ext_to_type"]:
                plan.append([f, folder / cfg["ext_to_type"][ext], "file type"])
            # unknown types stay where they are

    topics = classify_topics(docs, cfg) if (use_ai and docs) else {}
    for item in plan:
        if item[1] is None:
            src = item[0]
            topic = topics.get(src)
            item[1] = src.parent / (topic or type_fallback_for_doc(src))
            item[2] = f"topic: {topic}" if topic else "document type"
    return plan


def unique_path(dest: Path) -> Path:
    if not dest.exists():
        return dest
    n = 2
    while True:
        candidate = dest.with_name(f"{dest.stem} ({n}){dest.suffix}")
        if not candidate.exists():
            return candidate
        n += 1


# ---------------------------------------------------------------- output

def print_summary(plan):
    if not plan:
        print("Nothing to sort - everything is already tidy.")
        return
    by_folder = defaultdict(Counter)
    for src, dest_dir, _ in plan:
        by_folder[src.parent][dest_dir.relative_to(src.parent).as_posix()] += 1
    for folder, counts in by_folder.items():
        print(f"\n{folder}  ({sum(counts.values())} files)")
        for dest, n in counts.most_common():
            print(f"   -> {dest + '/':32} {n}")


def write_plan_csv(plan, stamp):
    LOGS.mkdir(exist_ok=True)
    path = LOGS / f"plan-{stamp}.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["file", "from", "to", "reason", "size_mb"])
        for src, dest_dir, reason in plan:
            w.writerow([src.name, str(src.parent), str(dest_dir), reason,
                        round(src.stat().st_size / 1e6, 2)])
    return path


def apply_plan(plan, stamp):
    LOGS.mkdir(exist_ok=True)
    log_path = LOGS / f"moves-{stamp}.json"
    log = {"moves": [], "created_dirs": []}
    for src, dest_dir, _ in plan:
        try:
            if not dest_dir.exists():
                dest_dir.mkdir(parents=True)
                log["created_dirs"].append(str(dest_dir))
            dest = unique_path(dest_dir / src.name)
            shutil.move(str(src), str(dest))
            log["moves"].append([str(src), str(dest)])
        except (OSError, shutil.Error) as e:
            print(f"  skipped {src.name}: {e}")
        # log after every move so a crash midway is still undoable
        log_path.write_text(json.dumps(log, indent=1), "utf-8")
    print(f"\nMoved {len(log['moves'])} files. Undo with:  py undo.py {log_path.name}")


def main():
    ap = argparse.ArgumentParser(description="Sort loose files into folders (never deletes).")
    ap.add_argument("--apply", action="store_true", help="actually move files (default is preview)")
    ap.add_argument("--no-ai", action="store_true", help="sort documents by file type, not topic")
    ap.add_argument("--only", help="only process folders whose name contains this, e.g. Downloads")
    args = ap.parse_args()

    cfg = load_config()
    folders = cfg["folders"]
    if args.only:
        folders = [f for f in folders if args.only.lower() in str(f).lower()]

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    plan = build_plan(folders, cfg, use_ai=not args.no_ai)
    print_summary(plan)
    if not plan:
        return
    csv_path = write_plan_csv(plan, stamp)
    print(f"\nFull file-by-file list: {csv_path}")

    if args.apply:
        apply_plan(plan, stamp)
    else:
        print("\nPreview only - nothing was moved. Run again with --apply to do it.")


if __name__ == "__main__":
    main()
