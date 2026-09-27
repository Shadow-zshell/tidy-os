"""Fake script output for `pythonw app.pyw --demo`, used for screenshots and GIFs.
Nothing runs and no real files or email are touched in demo mode."""

DEMO_DISK = (412e9, 1000e9)  # (used, total) shown in the status bar

DEMO_OUTPUT = {
    "bloat.py": """Bloat report - 2026-09-26 18:02   (read-only: nothing was changed)
C: drive: 412 GB used of 1000 GB, 588 GB free

== Known space hogs ==
  27.4 GB  [VM         ] C:\\Users\\demo\\Documents\\Virtual Machines   last used 2025-03  POTENTIAL JUNK
              VMware virtual machines
  18.2 GB  [Installers ] C:\\Users\\demo\\Downloads   last used 2026-09
   2.1 GB  [Cache      ] C:\\Users\\demo\\AppData\\Local\\npm-cache   last used 2026-08  POTENTIAL JUNK
   0.6 GB  [Cache      ] C:\\Users\\demo\\AppData\\Local\\Google\\Chrome\\...\\Cache  POTENTIAL JUNK
   0.4 GB  [Temp       ] C:\\Windows\\SoftwareDistribution\\Download  POTENTIAL JUNK

== VM disks, disk images, and files over 1 GB ==
  21.0 GB  [VM disk   ] changed 2025-03  ...\\Virtual Machines\\Ubuntu 22.04\\Ubuntu.vmdk
   4.7 GB  [disk image] changed 2024-02  C:\\Users\\demo\\Downloads\\ubuntu-22.04-desktop.iso  POTENTIAL JUNK
   4.4 GB  [disk image] changed 2023-11  C:\\Users\\demo\\Downloads\\kali-linux-2023.4.iso  POTENTIAL JUNK
   3.1 GB  [large file] changed 2026-06  C:\\Users\\demo\\Videos\\drone-footage.mp4

== POTENTIAL JUNK - for you to review (nothing has been removed) ==
  27.4 GB  [Old VMs] C:\\Users\\demo\\Documents\\Virtual Machines
              why: not used since 2025-03
   9.1 GB  [Old disk image] 2 installer ISOs older than 6 months
   2.1 GB  [Cache] npm cache - rebuilds itself
   1.0 GB  [Program] TrialAntivirus Plus
              why: Trial-style antivirus; Windows Security already protects you

Total potential junk: 39.6 GB across 7 items
""",
    "tidy.py": """C:\\Users\\demo\\Desktop  (28 files)
   -> PDFs/                            14
   -> Word Docs/                       7
   -> Images/                          5
   -> Spreadsheets/                    2

C:\\Users\\demo\\Downloads  (196 files)
   -> PDFs/                            72
   -> Potential Junk/Old Installers/   31
   -> Potential Junk/Duplicates/       24
   -> Word Docs/                       22
   -> Images/                          18
   -> Archives/                        11
   -> Spreadsheets/                    9
   -> Potential Junk/Old Disk Images/  2
   -> Code & Data/                     7

Full file-by-file list: logs\\plan-demo.csv

Preview only - nothing was moved. Run again with --apply to do it.
""",
    "tidy.py --apply": """(same plan as the preview)

Moved 224 files. Undo with:  py undo.py moves-demo.json
""",
    "undo.py": "Restored 224 of 224 files from moves-demo.json.\n",
    "gmail_tidy.py scan": """Gmail scan - demo@example.com   (read-only: nothing was changed)
48,210 messages in the whole mailbox

== Mailbox overview ==
  Inbox           6,412 total      2,981 unread
  Promotions     21,840 total     19,002 unread
  Social          3,117 total      2,640 unread

== Top senders (last 2,000 messages matching 'newer_than:1y -in:chats') ==
  count  unread  sender
    142   100%   deals@shoe-outlet.example  POTENTIAL JUNK - you almost never open these
     96    98%   news@gadget-daily.example  POTENTIAL JUNK - you almost never open these
     61     4%   alerts@bank.example
     44    12%   jobs-noreply@linkedin.com
     38   100%   promo@pizza-place.example  POTENTIAL JUNK - you almost never open these
     21     0%   noreply@github.com

3 senders flagged as Potential Junk. Full list with unsubscribe links: logs\\gmail-senders.csv
Nothing was changed. Unsubscribing is up to you - open the links you choose.
""",
    "gmail_tidy.py sort": """Checking your rules against Gmail...

  -> TIDY/Potential Junk:          14,902 emails  <- review these
  -> TIDY/Newsletters:              2,311 emails
  -> TIDY/Receipts:                   884 emails
  -> TIDY/Jobs & Career:              412 emails
  -> TIDY/Security:                   236 emails
  -> TIDY/Bills & Finance:            198 emails
  -> TIDY/Travel:                      57 emails

Preview only - 19,000 labels would be added. Nothing was changed.
Labels are just tags: emails stay in your inbox and nothing is deleted.
""",
    "gmail_tidy.py sort --apply": """  labeled 14,902 as TIDY/Potential Junk
  labeled 2,311 as TIDY/Newsletters
  labeled 884 as TIDY/Receipts
  labeled 412 as TIDY/Jobs & Career

Done - 19,000 labels added. Look for the TIDY folder in Gmail's sidebar.
""",
    "gmail_tidy.py undo": "Undo complete.\n",
}


def demo_output(args):
    key = " ".join(a for a in args if a != "--no-ai")
    return DEMO_OUTPUT.get(key, "(no demo output for this button)\n")
