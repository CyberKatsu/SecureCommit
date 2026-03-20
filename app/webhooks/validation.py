"""
validation.py — GitHub webhook HMAC-SHA256 signature verification.

Design decisions:
* hmac.compare_digest: constant-time comparison prevents timing attacks where
  an attacker could brute-force the secret by measuring response latency.
* The function raises HTTPException rather than returning a bool so FastAPI
  can propagate the 401 cleanly without the router having to check a return
  value — fail-fast style.
* We accept the raw bytes from the request body before FastAPI parses it as
  JSON.  Once parsed, the exact byte ordering may differ from what GitHub
  signed.  This is the standard approach recommended in GitHub's docs.
"""

import hashlib
import hmac

from fastapi import HTTPException, Request, status


async def verify_github_signature(request: Request, secret: str) -> bytes:
    """
    Validate the X-Hub-Signature-256 header against the request body.

    Returns the raw body bytes on success so the caller can parse them.
    Raises HTTP 401 on any validation failure.
    """
    signature_header = request.headers.get("X-Hub-Signature-256")
    if not signature_header:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-Hub-Signature-256 header",
        )

    if not signature_header.startswith("sha256="):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Malformed signature header — expected 'sha256=<hex>'",
        )

    body = await request.body()

    expected_signature = hmac.new(
        key=secret.encode("utf-8"),
        msg=body,
        digestmod=hashlib.sha256,
    ).hexdigest()

    provided_signature = signature_header[len("sha256="):]

    if not hmac.compare_digest(expected_signature, provided_signature):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Webhook signature mismatch — is your GITHUB_WEBHOOK_SECRET correct?",
        )

    return body


def compute_signature(body: bytes, secret: str) -> str:
    """
    Utility used by tests to generate a valid signature for a given payload.
    """
    digest = hmac.new(
        key=secret.encode("utf-8"),
        msg=body,
        digestmod=hashlib.sha256,
    ).hexdigest()
    return f"sha256={digest}"
