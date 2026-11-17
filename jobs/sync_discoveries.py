"""Pull federated discoveries from peer instances into this deployment.

Self-hosted instances run this on a cron (or call POST /api/discoveries/sync
directly). It asks the local API to pull every configured remote feed and
import the new signed records; the API verifies each record's ed25519
signature against the origin instance's registered public key.

    */30 * * * *  cd /srv/one-verifiable-record && python jobs/sync_discoveries.py

Environment:
    OVR_API_BASE   API base URL (default http://localhost:8000)
"""
import argparse
import json
import os
import urllib.request


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--api-base", default=os.environ.get("OVR_API_BASE", "http://localhost:8000"))
    args = ap.parse_args()
    url = args.api_base.rstrip("/") + "/api/discoveries/sync"
    req = urllib.request.Request(url, data=b"{}", method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            print(json.dumps(json.loads(resp.read()), indent=2))
    except urllib.error.HTTPError as exc:
        print(f"sync failed: HTTP {exc.code} {exc.read().decode()[:500]}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
