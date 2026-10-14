"""Sends an approved request to the n8n workflow (email to faculty + Google Sheets log)."""
import httpx

from app import config


def dispatch(payload: dict) -> dict:
    """Returns {"ok": bool, "detail": str}. Never raises: a failed dispatch is recorded on the
    request so the helpdesk can retry, instead of crashing the conversation."""
    if not config.N8N_WEBHOOK_URL:
        return {"ok": False, "detail": "N8N_WEBHOOK_URL not configured; request stored locally only"}
    headers = {"X-Sahayak-Token": config.N8N_SHARED_SECRET} if config.N8N_SHARED_SECRET else {}
    try:
        r = httpx.post(config.N8N_WEBHOOK_URL, json=payload, headers=headers, timeout=20)
        r.raise_for_status()
        return {"ok": True, "detail": r.text[:200]}
    except httpx.HTTPError as e:
        return {"ok": False, "detail": f"n8n call failed: {e}"}
