"""Bloat report: find what's eating disk space and mark likely junk for review.

READ-ONLY. This script never moves, changes or deletes anything. It writes:
  logs/bloat-report.txt      everything it measured
  logs/potential-junk.csv    just the items marked "Potential Junk", for you to review

  py bloat.py
  py bloat.py --min-gb 0.5     (list big files down to 500 MB)
"""

import argparse
import csv
import os
import shutil
import stat
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
HOME = Path.home()
LOCAL = Path(os.environ.get("LOCALAPPDATA", HOME / "AppData/Local"))
ROAMING = Path(os.environ.get("APPDATA", HOME / "AppData/Roaming"))
PROGDATA = Path(os.environ.get("PROGRAMDATA", "C:/ProgramData"))

VM_EXTS = {".vhd", ".vhdx", ".avhdx", ".vmdk", ".vdi", ".qcow2", ".ova", ".ovf",
           ".vmem", ".vmsn", ".nvram", ".sav", ".hdd"}
IMAGE_EXTS = {".iso", ".img", ".wim", ".esd"}
MAIL_CACHE_EXTS = {".ost"}

LINK_TAGS = {0xA0000003, 0xA000000C}  # junctions and symlinks - skipped to avoid loops
CLOUD_ONLY = 0x400000 | 0x1000  # RECALL_ON_DATA_ACCESS | OFFLINE (OneDrive/iCloud placeholders)

STALE_VM_DAYS = 180
STALE_IMAGE_DAYS = 180
STALE_MAIL_CACHE_DAYS = 365

# Places where space piles up: (kind, path, note). Kinds: VM (junk if unused for a while),
# Temp/Cache/Old Windows (always potential junk), Info (just reported).
KNOWN_SPOTS = [
    ("VM", HOME / "VirtualBox VMs", "VirtualBox virtual machines"),
    ("VM", HOME / "Documents/Virtual Machines", "VMware virtual machines"),
    ("VM", HOME / "OneDrive/Documents/Virtual Machines", "VMware VMs stored in OneDrive (also eats cloud storage)"),
    ("VM", Path("C:/ProgramData/Microsoft/Windows/Virtual Hard Disks"), "Hyper-V disks"),
    ("VM", Path("C:/Users/Public/Documents/Hyper-V"), "Hyper-V disks"),
    ("VM", LOCAL / "Docker", "Docker Desktop data"),
    ("VM", PROGDATA / "DockerDesktop", "Docker Desktop data"),
    ("VM", LOCAL / "wsl", "WSL distro disks"),
    ("VM", HOME / ".android/avd", "Android emulator images"),
    ("VM", LOCAL / "Android/Sdk/system-images", "Android SDK system images"),
    ("VM", HOME / ".vagrant.d/boxes", "Vagrant boxes"),
    ("Temp", LOCAL / "Temp", "Temp files - clear with Settings > Storage > Temporary files"),
    ("Temp", Path("C:/Windows/Temp"), "Windows temp - clear with Settings > Storage > Temporary files"),
    ("Temp", LOCAL / "CrashDumps", "App crash dumps"),
    ("Temp", Path("C:/Windows/SoftwareDistribution/Download"), "Old Windows Update downloads - Temporary files cleanup handles it"),
    ("Old Windows", Path("C:/Windows.old"), "Previous Windows install - Temporary files > 'Previous Windows installation'"),
    ("Old Windows", Path("C:/$WINDOWS.~BT"), "Windows upgrade leftovers"),
    ("Cache", LOCAL / "npm-cache", "npm cache - rebuilds itself"),
    ("Cache", LOCAL / "pip/cache", "pip cache - rebuilds itself"),
    ("Cache", HOME / ".gradle/caches", "Gradle cache - rebuilds itself"),
    ("Cache", HOME / ".nuget/packages", "NuGet cache - rebuilds itself"),
    ("Cache", LOCAL / "Google/Chrome/User Data/Default/Cache", "Chrome cache - clear from Chrome settings"),
    ("Cache", LOCAL / "Microsoft/Edge/User Data/Default/Cache", "Edge cache - clear from Edge settings"),
    ("Cache", ROAMING / "Code/Cache", "VS Code cache"),
    ("Cache", ROAMING / "discord/Cache", "Discord cache - rebuilds itself"),
    ("Cache", LOCAL / "NVIDIA/DXCache", "NVIDIA shader cache - rebuilds itself"),
    ("Info", PROGDATA / "Package Cache", "Installer cache - needed to repair/uninstall apps, leave it"),
]

# Installed programs commonly considered bloat: (name contains, why)
BLOATWARE = [
    ("McAfee", "Trial-style antivirus; Windows Security (Defender) already protects you"),
    ("Teams Machine-Wide Installer", "Installer for the retired 'classic' Teams"),
    ("Desktop Runtime - 5.0", ".NET 5 is out of support; remove only if no app needs it"),
    ("Desktop Runtime - 6.0", ".NET 6 is out of support; remove only if no app needs it"),
    ("WildTangent", "Preinstalled games"),
    ("Candy Crush", "Preinstalled game"),
    ("ExpressVPN", "Preinstalled trial"),
]


def gb(n):
    return f"{n / 1e9:6.1f} GB"


def age_days(ts):
    return (time.time() - ts) / 86400


def walk_files(root: Path):
    """Yield (path, size, mtime, cloud_only) without following junctions/symlinks."""
    stack = [root]
    while stack:
        d = stack.pop()
        try:
            with os.scandir(d) as it:
                for e in it:
                    try:
                        st = e.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    # OneDrive files are reparse points too, so only skip real links
                    if getattr(st, "st_reparse_tag", 0) in LINK_TAGS:
                        continue
                    if stat.S_ISDIR(st.st_mode):
                        stack.append(Path(e.path))
                    else:
                        attrs = getattr(st, "st_file_attributes", 0)
                        yield Path(e.path), st.st_size, st.st_mtime, bool(attrs & CLOUD_ONLY)
        except OSError:
            continue  # access denied etc.


def folder_stats(path: Path):
    """(bytes stored locally, newest modification time)"""
    local = newest = 0
    for _, size, mtime, cloud in walk_files(path):
        if not cloud:
            local += size
        newest = max(newest, mtime)
    return local, newest


def installed_programs():
    try:
        import winreg
    except ImportError:
        return []
    roots = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]
    seen, progs = set(), []
    for hive, key_path in roots:
        try:
            key = winreg.OpenKey(hive, key_path)
        except OSError:
            continue
        for i in range(winreg.QueryInfoKey(key)[0]):
            try:
                sub = winreg.OpenKey(key, winreg.EnumKey(key, i))
                name = winreg.QueryValueEx(sub, "DisplayName")[0]
            except OSError:
                continue

            def val(field, default=None):
                try:
                    return winreg.QueryValueEx(sub, field)[0]
                except OSError:
                    return default

            if val("SystemComponent") == 1 or name in seen:
                continue
            seen.add(name)
            size_kb = val("EstimatedSize", 0) or 0
            progs.append((name, size_kb * 1024, val("InstallDate", "") or "", val("Publisher", "") or ""))
    return sorted(progs, key=lambda p: -p[1])


def main():
    ap = argparse.ArgumentParser(description="Read-only disk bloat report.")
    ap.add_argument("--min-gb", type=float, default=1.0, help="list individual files at least this big")
    args = ap.parse_args()
    min_bytes = args.min_gb * 1e9

    out, junk = [], []  # junk rows: (bytes, kind, item, why, how to remove)

    def say(line=""):
        print(line)
        out.append(line)

    du = shutil.disk_usage("C:/")
    say(f"Bloat report - {datetime.now():%Y-%m-%d %H:%M}   (read-only: nothing was changed)")
    say(f"C: drive: {du.used / 1e9:.0f} GB used of {du.total / 1e9:.0f} GB, {du.free / 1e9:.0f} GB free")

    # 1. Known locations
    say("\n== Known space hogs ==")
    rows = []
    for kind, path, note in KNOWN_SPOTS:
        if path.exists():
            local, newest = folder_stats(path)
            if local >= 50e6:
                rows.append((local, newest, kind, path, note))
    for local, newest, kind, path, note in sorted(rows, key=lambda r: -r[0]):
        last_used = datetime.fromtimestamp(newest).strftime("%Y-%m") if newest else "?"
        flag = ""
        if kind in ("Temp", "Cache", "Old Windows"):
            flag = "POTENTIAL JUNK"
            junk.append((local, kind, str(path), note, "see note"))
        elif kind == "VM" and newest and age_days(newest) > STALE_VM_DAYS:
            flag = "POTENTIAL JUNK"
            junk.append((local, "Old VMs", str(path), f"{note}; not used since {last_used}",
                         "delete VMs you no longer need from inside VMware/VirtualBox"))
        say(f"{gb(local)}  [{kind:11}] {path}   last used {last_used}  {flag}\n              {note}")

    try:
        size = os.stat("C:/hiberfil.sys").st_size
        say(f"{gb(size)}  [System     ] C:/hiberfil.sys  (hibernation file - `powercfg /h /type reduced` shrinks it)")
    except OSError:
        pass

    # 2. VM disks / disk images / huge files anywhere in your profile + ProgramData
    say(f"\n== VM disks, disk images, and files over {args.min_gb:g} GB ==")
    say("   (cloud-only files are listed but don't use space on this PC)")
    vm_folders_flagged = tuple(j[2] for j in junk if j[1] == "Old VMs")
    big = []
    for root in (HOME, PROGDATA):
        for path, size, mtime, cloud in walk_files(root):
            ext = path.suffix.lower()
            if size >= min_bytes or (ext in VM_EXTS | IMAGE_EXTS and size >= 100e6):
                big.append((size, mtime, path, cloud))
    big.sort(key=lambda b: -b[0])
    for size, mtime, path, cloud in big[:60]:
        ext = path.suffix.lower()
        tag = ("VM disk" if ext in VM_EXTS else "disk image" if ext in IMAGE_EXTS
               else "mail cache" if ext in MAIL_CACHE_EXTS else "large file")
        changed = datetime.fromtimestamp(mtime).strftime("%Y-%m")
        note, flag = "", ""
        if cloud:
            note = "  (cloud-only)"
        elif "vm_bundles" in str(path):
            note = "  (Claude desktop app's sandbox - in use, leave it)"
        elif ext in IMAGE_EXTS and age_days(mtime) > STALE_IMAGE_DAYS:
            flag = "  POTENTIAL JUNK"
            junk.append((size, "Old disk image", str(path), f"installer image from {changed}",
                         "tidy.py moves these into Downloads/Potential Junk"))
        elif ext in MAIL_CACHE_EXTS and age_days(mtime) > STALE_MAIL_CACHE_DAYS:
            flag = "  POTENTIAL JUNK"
            junk.append((size, "Stale mail cache", str(path),
                         f"Outlook offline cache not updated since {changed}; your mail is still on the server",
                         "remove the account from Outlook, or uninstall Outlook if unused"))
        elif ext in VM_EXTS and age_days(mtime) > STALE_VM_DAYS and not str(path).startswith(vm_folders_flagged):
            flag = "  POTENTIAL JUNK"
            junk.append((size, "Old VM disk", str(path), f"not used since {changed}",
                         "delete the VM from its VM app"))
        say(f"{gb(size)}  [{tag:10}] changed {changed}  {path}{note}{flag}")
    if len(big) > 60:
        say(f"   ...and {len(big) - 60} more")

    # 3. Installed programs by size + known bloatware
    say("\n== Largest installed programs ==")
    programs = installed_programs()
    for name, size, date, pub in programs[:30]:
        when = f"{date[:4]}-{date[4:6]}" if len(date) == 8 else "       "
        say(f"{gb(size)}  installed {when}  {name}" + (f"  ({pub})" if pub else ""))
    for name, size, _, _ in programs:
        for needle, why in BLOATWARE:
            if needle.lower() in name.lower():
                junk.append((size, "Program", name, why, "Settings > Apps > Installed apps > Uninstall"))

    # 4. The review list
    junk.sort(key=lambda j: -j[0])
    say("\n== POTENTIAL JUNK - for you to review (nothing has been removed) ==")
    for size, kind, item, why, how in junk:
        say(f"{gb(size)}  [{kind}] {item}\n              why: {why}\n              how: {how}")
    say(f"\nTotal potential junk: {sum(j[0] for j in junk) / 1e9:.1f} GB across {len(junk)} items")

    logs = HERE / "logs"
    logs.mkdir(exist_ok=True)
    (logs / "bloat-report.txt").write_text("\n".join(out), "utf-8")
    with open(logs / "potential-junk.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["size_gb", "kind", "item", "why", "how_to_remove", "your_decision"])
        for size, kind, item, why, how in junk:
            w.writerow([round(size / 1e9, 2), kind, item, why, how, ""])
    print(f"\nSaved {logs / 'bloat-report.txt'} and {logs / 'potential-junk.csv'}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
