"""
AetherLens — License Management Module
Generates, verifies, and validates deployment licenses.
Dev mode: auto-generates a local license on first run.
"""

import hashlib
import uuid
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path


def get_machine_id() -> str:
    """Return a 16-char MD5 fingerprint of this machine."""
    import platform
    machine_str = (
        platform.node() +
        platform.processor() +
        platform.machine()
    )
    return hashlib.md5(machine_str.encode()).hexdigest()[:16]


def generate_license_key(
    organization: str,
    expiry_days: int = 365,
    machine_id: str = None,
) -> str:
    """
    Generate a base64-encoded signed license key.
    Signature = SHA256(payload_json + SECRET_KEY).
    """
    SECRET_KEY = os.getenv("LICENSE_SECRET", "AETHERLENS_2024_SECRET")
    data = {
        "org":        organization,
        "issued":     datetime.now().isoformat(),
        "expiry":     (datetime.now() + timedelta(days=expiry_days)).isoformat(),
        "machine_id": machine_id or get_machine_id(),
        "version":    "1.0",
    }
    payload   = json.dumps(data, sort_keys=True)
    signature = hashlib.sha256((payload + SECRET_KEY).encode()).hexdigest()
    license_data = {"payload": payload, "signature": signature}
    import base64
    return base64.b64encode(json.dumps(license_data).encode()).decode()


def verify_license(license_key: str) -> tuple:
    """
    Verify a license key.
    Returns (True, data_dict) on success, (False, reason_str) on failure.
    """
    import base64
    SECRET_KEY = os.getenv("LICENSE_SECRET", "AETHERLENS_2024_SECRET")
    try:
        decoded   = json.loads(base64.b64decode(license_key).decode())
        payload   = decoded["payload"]
        signature = decoded["signature"]

        expected = hashlib.sha256((payload + SECRET_KEY).encode()).hexdigest()
        if signature != expected:
            return False, "Invalid license signature"

        data   = json.loads(payload)
        expiry = datetime.fromisoformat(data["expiry"])
        if datetime.now() > expiry:
            return False, f"License expired on {data['expiry']}"

        stored_mid = data.get("machine_id")
        if stored_mid and stored_mid != get_machine_id():
            return False, "License not valid for this machine"

        return True, data
    except Exception as e:
        return False, str(e)


def check_license_on_startup() -> dict:
    """
    Check for aetherlens.license in the working directory.
    If missing: auto-generate a dev license and continue.
    Returns the license data dict (or a warning dict on failure).

    On Streamlit Cloud (read-only filesystem, ephemeral containers)
    the whole block is wrapped in a try/except so a hardware-check
    or file-write failure never crashes the app.
    """
    try:
        license_file = Path("aetherlens.license")

        if not license_file.exists():
            try:
                print("[LICENSE] No license file found. Generating dev license.")
            except Exception:
                pass
            dev_key = generate_license_key(
                organization="AETHERLENS_DEV",
                expiry_days=365,
                machine_id=get_machine_id(),
            )
            license_file.write_text(dev_key)
            try:
                print("[LICENSE] Dev license created: aetherlens.license")
            except Exception:
                pass

        license_key    = license_file.read_text().strip()
        valid, result  = verify_license(license_key)

        if not valid:
            try:
                print(f"[LICENSE] Invalid: {result}")
            except Exception:
                pass
            # Warn but do not sys.exit in dev mode
            return {"org": "INVALID", "warning": result}

        try:
            print(
                f"[LICENSE] Valid"
                f" Org: {result.get('org')}"
                f" Expires: {result.get('expiry', '')[:10]}"
            )
        except Exception:
            pass
        return result

    except Exception as e:
        print(
            f"[LICENSE] Cloud mode"
            f" — skipping hardware"
            f" check: {e}"
        )
        return {
            "valid":   True,
            "org":     "Demo",
            "expiry":  "2099-12-31",
            "mode":    "cloud",
        }
