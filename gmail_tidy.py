"""Gmail tidy: label your email into TIDY/... folders and find senders you never read.

Nothing is ever deleted, trashed, or unsubscribed. The only change this script
can make is ADDING labels (and undo removes them again).

  py gmail_tidy.py scan            read-only report: mailbox overview + top senders
  py gmail_tidy.py sort            preview which labels would be added
  py gmail_tidy.py sort --apply    add the labels
  py gmail_tidy.py undo            remove the labels added by the last sort
"""

import argparse
import csv
import json
import os
import re
import sys
import time
from collections import defaultdict
from datetime import datetime
from email.utils import parseaddr
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOGS = HERE / "logs"
CREDENTIALS = HERE / "credentials.json"   # OAuth client you download from Google Cloud
# your sign-in, saved after the first login - kept outside OneDrive so it never syncs to the cloud
TOKEN = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "TIDYOS" / "gmail-token.json"
SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]

SETUP_HELP = """
Gmail isn't connected yet. One-time setup (about 5 minutes):

 1. Go to https://console.cloud.google.com and create a project called "TIDY OS".
 2. APIs & Services > Library > search "Gmail API" > Enable.
 3. Google Auth Platform (OAuth consent screen) > Get started:
      app name TIDY OS, your email, audience "External".
    Then Audience > Test users > add your own Gmail address.
 4. Clients > Create client > type "Desktop app" > Create > Download JSON.
 5. Save that file as:
      {path}
 6. Click SCAN INBOX again. A browser window opens - sign in and allow access.
    Google will warn "Google hasn't verified this app": that's expected because
    it's YOUR private app. Click Continue.
"""


def load_config():
    with open(HERE / "config.json", encoding="utf-8") as f:
        return json.load(f)["gmail"]


def connect():
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    creds = None
    if TOKEN.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN), SCOPES)
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except RefreshError:
            creds = None  # apps in "Testing" mode need a fresh sign-in every 7 days
    if not creds or not creds.valid:
        if not CREDENTIALS.exists():
            print(SETUP_HELP.format(path=CREDENTIALS))
            sys.exit(2)
        print("Opening your browser to sign in to Google...")
        flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS), SCOPES)
        creds = flow.run_local_server(port=0, open_browser=True)
    TOKEN.parent.mkdir(parents=True, exist_ok=True)
    TOKEN.write_text(creds.to_json(), "utf-8")
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


# ---------------------------------------------------------------- Gmail helpers

def list_ids(svc, query=None, label_ids=None, limit=10000):
    ids, page = [], None
    while len(ids) < limit:
        resp = svc.users().messages().list(
            userId="me", q=query, labelIds=label_ids, pageToken=page,
            maxResults=min(500, limit - len(ids))).execute()
        ids += [m["id"] for m in resp.get("messages", [])]
        page = resp.get("nextPageToken")
        if not page:
            break
    return ids


def fetch_metadata(svc, ids):
    """Fetch From / List-Unsubscribe headers in batches, retrying rate-limited ones."""
    results, pending = {}, list(ids)
    for attempt in range(4):
        failed = []

        def on_done(request_id, response, exception):
            if exception is None:
                results[request_id] = response
            else:
                failed.append(request_id)

        for i in range(0, len(pending), 50):
            batch = svc.new_batch_http_request(callback=on_done)
            for mid in pending[i:i + 50]:
                batch.add(svc.users().messages().get(
                    userId="me", id=mid, format="metadata",
                    metadataHeaders=["From", "List-Unsubscribe"]), request_id=mid)
            batch.execute()
            print(f"  read {len(results)}/{len(ids)} messages", end="\r")
            time.sleep(0.3)
        if not failed:
            break
        pending = failed
        time.sleep(2 * (attempt + 1))
    print()
    return results


def header(msg, name):
    for h in msg.get("payload", {}).get("headers", []):
        if h["name"].lower() == name.lower():
            return h["value"]
    return ""


def label_map(svc):
    labels = svc.users().labels().list(userId="me").execute().get("labels", [])
    return {l["name"]: l["id"] for l in labels}


def ensure_label(svc, name, labels):
    if name not in labels:
        created = svc.users().labels().create(userId="me", body={
            "name": name, "labelListVisibility": "labelShow", "messageListVisibility": "show",
        }).execute()
        labels[name] = created["id"]
    return labels[name]


def batch_modify(svc, ids, add=None, remove=None):
    for i in range(0, len(ids), 1000):
        body = {"ids": ids[i:i + 1000]}
        if add:
            body["addLabelIds"] = add
        if remove:
            body["removeLabelIds"] = remove
        svc.users().messages().batchModify(userId="me", body=body).execute()


# ---------------------------------------------------------------- commands

def cmd_scan(svc, cfg):
    profile = svc.users().getProfile(userId="me").execute()
    print(f"Gmail scan - {profile['emailAddress']}   (read-only: nothing was changed)")
    print(f"{profile['messagesTotal']:,} messages in the whole mailbox\n")

    print("== Mailbox overview ==")
    for lid, name in [("INBOX", "Inbox"), ("CATEGORY_PROMOTIONS", "Promotions"),
                      ("CATEGORY_SOCIAL", "Social"), ("CATEGORY_UPDATES", "Updates"),
                      ("CATEGORY_FORUMS", "Forums"), ("SPAM", "Spam")]:
        try:
            lab = svc.users().labels().get(userId="me", id=lid).execute()
            print(f"  {name:12} {lab.get('messagesTotal', 0):>8,} total   {lab.get('messagesUnread', 0):>8,} unread")
        except Exception:
            pass

    print(f"\n== Top senders (last {cfg['scan_sample']:,} messages matching '{cfg['scan_query']}') ==")
    ids = list_ids(svc, query=cfg["scan_query"], limit=cfg["scan_sample"])
    msgs = fetch_metadata(svc, ids)

    senders = defaultdict(lambda: {"count": 0, "unread": 0, "name": "", "unsub": ""})
    for m in msgs.values():
        name, addr = parseaddr(header(m, "From"))
        addr = addr.lower() or "(unknown)"
        s = senders[addr]
        s["count"] += 1
        s["name"] = s["name"] or name
        if "UNREAD" in m.get("labelIds", []):
            s["unread"] += 1
        if not s["unsub"]:
            links = re.findall(r"<(https?://[^>]+)>", header(m, "List-Unsubscribe"))
            s["unsub"] = links[0] if links else ""

    ranked = sorted(senders.items(), key=lambda kv: -kv[1]["count"])
    junk = 0
    print(f"  {'count':>5}  {'unread':>6}  sender")
    for addr, s in ranked[:40]:
        unread_pct = s["unread"] / s["count"]
        flag = ""
        if s["count"] >= 5 and unread_pct >= cfg["never_open_threshold"]:
            flag = "  POTENTIAL JUNK - you almost never open these"
            junk += 1
        unsub = "  [unsubscribe link in CSV]" if s["unsub"] and flag else ""
        print(f"  {s['count']:>5}  {unread_pct:>5.0%}   {addr}{flag}{unsub}")

    LOGS.mkdir(exist_ok=True)
    out = LOGS / "gmail-senders.csv"
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["sender", "name", "count", "unread_pct", "potential_junk", "unsubscribe_link", "your_decision"])
        for addr, s in ranked:
            pct = s["unread"] / s["count"]
            is_junk = s["count"] >= 5 and pct >= cfg["never_open_threshold"]
            w.writerow([addr, s["name"], s["count"], f"{pct:.0%}", "yes" if is_junk else "", s["unsub"], ""])
    print(f"\n{junk} senders flagged as Potential Junk. Full list with unsubscribe links: {out}")
    print("Nothing was changed. Unsubscribing is up to you - open the links you choose.")


def cmd_sort(svc, cfg, apply):
    labels = label_map(svc)
    plan = defaultdict(set)  # label name -> message ids to add
    print("Checking your rules against Gmail...\n")
    for rule in cfg["rules"]:
        ids = set(list_ids(svc, query=rule["query"], limit=cfg["max_per_rule"]))
        plan[rule["label"]] |= ids

    # skip messages that already have the label
    for name in plan:
        if name in labels:
            plan[name] -= set(list_ids(svc, label_ids=[labels[name]], limit=10**6))

    total = sum(len(v) for v in plan.values())
    for name, ids in sorted(plan.items(), key=lambda kv: -len(kv[1])):
        flag = "  <- review these" if "Potential Junk" in name else ""
        print(f"  -> {name + ':':28} {len(ids):>6,} emails{flag}")
    if not total:
        print("Everything already has its label - nothing to do.")
        return

    if not apply:
        print(f"\nPreview only - {total:,} labels would be added. Nothing was changed.")
        print("Labels are just tags: emails stay in your inbox and nothing is deleted.")
        return

    LOGS.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    log_path = LOGS / f"gmail-labels-{stamp}.json"
    log = {}
    for name, ids in plan.items():
        if not ids:
            continue
        lid = ensure_label(svc, name, labels)
        batch_modify(svc, list(ids), add=[lid])
        log[name] = {"id": lid, "messages": sorted(ids)}
        log_path.write_text(json.dumps(log), "utf-8")  # save as we go so undo always works
        print(f"  labeled {len(ids):,} as {name}")
    print(f"\nDone - {total:,} labels added. Look for the TIDY folder in Gmail's sidebar.")
    print(f"Undo with:  py gmail_tidy.py undo")


def cmd_undo(svc):
    logs = sorted(LOGS.glob("gmail-labels-*.json"))
    logs = [p for p in logs if not p.name.endswith(".undone.json")]
    if not logs:
        print("No Gmail sort to undo.")
        return
    log_path = logs[-1]
    log = json.loads(log_path.read_text("utf-8"))
    for name, entry in log.items():
        batch_modify(svc, entry["messages"], remove=[entry["id"]])
        print(f"  removed {name} from {len(entry['messages']):,} emails")
    log_path.rename(log_path.with_name(log_path.stem + ".undone.json"))
    print("Undo complete. (Empty TIDY labels stay in the sidebar - remove them in Gmail settings if you like.)")


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="Label Gmail into TIDY/ folders (never deletes).")
    ap.add_argument("command", choices=["scan", "sort", "undo"])
    ap.add_argument("--apply", action="store_true", help="with sort: actually add the labels")
    args = ap.parse_args()

    cfg = load_config()
    svc = connect()
    if args.command == "scan":
        cmd_scan(svc, cfg)
    elif args.command == "sort":
        cmd_sort(svc, cfg, args.apply)
    else:
        cmd_undo(svc)


if __name__ == "__main__":
    main()
