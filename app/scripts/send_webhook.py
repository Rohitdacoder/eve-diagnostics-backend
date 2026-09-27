"""Pretend to be the payment provider and send a signed webhook.

python -m app.scripts.send_webhook <provider_payment_id> SUCCESS|FAILED [event_id]
"""

import json
import os
import sys
import urllib.error
import urllib.request
import uuid

from app.services.payments import sign

URL = os.environ.get("WEBHOOK_URL", "http://localhost:8000/payments/webhook/")


def main():
    if len(sys.argv) not in (3, 4):
        print(__doc__)
        sys.exit(1)
    body = json.dumps(
        {
            "event_id": sys.argv[3] if len(sys.argv) == 4 else f"evt_{uuid.uuid4().hex}",
            "provider_payment_id": sys.argv[1],
            "status": sys.argv[2].upper(),
        }
    ).encode()

    req = urllib.request.Request(
        URL,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "X-Signature": sign(body)},
    )
    try:
        with urllib.request.urlopen(req) as resp:
            print(resp.status, resp.read().decode())
    except urllib.error.HTTPError as e:
        print(e.code, e.read().decode())


if __name__ == "__main__":
    main()
