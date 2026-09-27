"""Put files back where they were before a tidy.py --apply run.

  py undo.py                      undo the most recent run
  py undo.py moves-20260926-1430.json
"""

import json
import shutil
import sys
from pathlib import Path

LOGS = Path(__file__).resolve().parent / "logs"


def main():
    if len(sys.argv) > 1:
        log_path = LOGS / sys.argv[1]
    else:
        logs = sorted(LOGS.glob("moves-*.json"))
        if not logs:
            sys.exit("No move logs found - nothing to undo.")
        log_path = logs[-1]

    log = json.loads(log_path.read_text("utf-8"))
    moves = log["moves"]
    restored = 0
    for original, moved_to in reversed(moves):
        src, dest = Path(moved_to), Path(original)
        if not src.exists():
            print(f"  missing, skipped: {src}")
        elif dest.exists():
            print(f"  something already at original spot, skipped: {dest}")
        else:
            shutil.move(str(src), str(dest))
            restored += 1

    # remove folders that tidy itself created, and only if they're now empty
    for folder in log["created_dirs"]:
        try:
            Path(folder).rmdir()  # fails harmlessly if anything is inside
        except OSError:
            pass

    log_path.rename(log_path.with_suffix(".undone.json"))
    print(f"Restored {restored} of {len(moves)} files from {log_path.name}.")


if __name__ == "__main__":
    main()
