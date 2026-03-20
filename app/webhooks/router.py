"""
router.py — FastAPI webhook endpoint for GitHub pull_request events.

Design decisions:
* The endpoint returns HTTP 202 Accepted immediately after enqueueing the
  Celery task.  GitHub expects a response within 10 seconds; our Claude calls
  take much longer.  Any 5xx or timeout from our server causes GitHub to retry,
  so returning quickly is critical.
* We parse the payload *after* signature verification — never trust unverified
  bytes.
* Unknown actions (labeled, unlabeled, closed, etc.) are silently ignored with
  200 OK rather than 400.  GitHub sends many action types; rejecting unknown
  ones would cause noisy errors in the GitHub App delivery log.
* The `X-GitHub-Event` header is checked before parsing the JSON body so we
  can skip non-PR events cheaply.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.models.schemas import GitHubWebhookPayload
from app.tasks.review_tasks import process_pull_request
from app.webhooks.validation import verify_github_signature

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks", tags=["webhooks"])
settings = get_settings()

# Actions that trigger a security review.
REVIEW_ACTIONS = {"opened", "synchronize", "reopened"}


@router.post("/github")
async def github_webhook(
    request: Request,
    x_github_event: str = Header(default="", alias="X-GitHub-Event"),
    x_github_delivery: str = Header(default="", alias="X-GitHub-Delivery"),
) -> JSONResponse:
    """
    Receive GitHub webhook events, validate the signature, and enqueue a
    Celery task for pull_request events with relevant actions.

    Returns 202 Accepted only when a task is enqueued.
    Returns 200 OK when the event is deliberately ignored.
    Returns 401 when the signature is invalid.
    """
    # Step 1: Verify HMAC signature (raises 401 on failure).
    raw_body = await verify_github_signature(request, settings.github_webhook_secret)

    # Step 2: Filter by event type — cheaply ignore non-PR events.
    if x_github_event != "pull_request":
        logger.debug("Ignoring event type: %s (delivery: %s)", x_github_event, x_github_delivery)
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={"status": "ignored", "reason": f"event_type={x_github_event}"},
        )

    # Step 3: Parse payload.
    try:
        payload_dict = json.loads(raw_body)
        payload = GitHubWebhookPayload.model_validate(payload_dict)
    except (json.JSONDecodeError, Exception) as exc:
        logger.error("Failed to parse webhook payload (delivery %s): %s", x_github_delivery, exc)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid webhook payload",
        ) from exc

    # Step 4: Ignore non-review-triggering actions.
    if payload.action not in REVIEW_ACTIONS:
        logger.debug(
            "Ignoring PR action '%s' for %s#%d",
            payload.action,
            payload.repository.full_name,
            payload.pull_request.number,
        )
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={"status": "ignored", "reason": f"action={payload.action}"},
        )

    # Step 5: Enqueue the Celery task (non-blocking).
    task = process_pull_request.delay(
        repo_full_name=payload.repository.full_name,
        pr_number=payload.pull_request.number,
        pr_title=payload.pull_request.title,
        pr_url=payload.pull_request.html_url,
        head_sha=payload.pull_request.head["sha"],
    )

    logger.info(
        "Enqueued review task %s for %s#%d (delivery: %s)",
        task.id,
        payload.repository.full_name,
        payload.pull_request.number,
        x_github_delivery,
    )

    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content={
            "status": "accepted",
            "task_id": task.id,
            "repo": payload.repository.full_name,
            "pr": payload.pull_request.number,
        },
    )
