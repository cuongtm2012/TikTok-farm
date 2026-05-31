# TikTok Farm - Web API Routes
# FastAPI routes for dashboard and management

import asyncio
import json
import logging
import os
import random
import shutil
import time
from datetime import datetime
from typing import Optional, List, Any
from pathlib import Path

logger = logging.getLogger(__name__)

try:
    from fastapi import APIRouter, HTTPException, Query, Request, UploadFile, File, Body, WebSocket, WebSocketDisconnect
    from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, FileResponse
    from pydantic import BaseModel, Field
    FASTAPI_AVAILABLE = True
except ImportError:
    FASTAPI_AVAILABLE = False
    APIRouter = object
    BaseModel = object  # type: ignore


if FASTAPI_AVAILABLE:

    class AccountCreateBody(BaseModel):
        username: str
        proxy_id: int = 0
        password: str = ""
        cookie_data: str = ""
        email: str = ""
        email_password: str = ""
        notes: str = ""
        status: Optional[str] = None

    class SellerImportBody(BaseModel):
        accounts: str
        proxy_id: int = 0
        skip_existing: bool = True
        require_cookies: bool = True
        auto_assign_proxy: bool = False

    class CookieUpdateBody(BaseModel):
        cookie_data: Any = ""

    class AccountBulkBody(BaseModel):
        accounts: List[AccountCreateBody] = Field(default_factory=list)
        skip_existing: bool = True

    class AccountCsvImportBody(BaseModel):
        csv_text: str
        skip_existing: bool = True

    class ProxyCreateBody(BaseModel):
        ip: str
        port: int
        protocol: str = "http"
        username: str = ""
        password: str = ""
        status: str = "active"

    class ProxyCsvImportBody(BaseModel):
        csv_text: str
        merge: bool = True

    class PostComposeBody(BaseModel):
        caption: str = ""
        hashtags: str = "fyp foryou viral"
        affiliate_link: str = ""
        rating: Optional[float] = None
        review: str = ""
        price: str = ""
        template_name: str = "default"
        product_name: str = "Product"
        images_dir: str = ""

    class VideoUploadBody(BaseModel):
        video_path: str
        caption: str = ""
        hashtags: str = "fyp foryou viral"
        affiliate_link: str = ""

    class PostCreateBody(BaseModel):
        account_id: int
        caption: str = ""
        hashtags: str = "fyp foryou viral"
        affiliate_link: str = ""
        content_path: str = ""
        scheduled_at: Optional[str] = None


if FASTAPI_AVAILABLE:
    router = APIRouter()
else:
    router = None


def get_state(request: Request):
    """Get the app state from request."""
    return request.app.state.farm


def _account_proxy_url(state, account) -> Optional[str]:
    if not account or not account.proxy_id:
        return None
    proxy_obj = state.proxy_manager.get_proxy(account.proxy_id)
    if proxy_obj and proxy_obj.is_alive:
        return proxy_obj.url
    return None


PREVIEW_ROOT = Path("data/previews")


def _emit_session(state, session_id: str, event_type: str, account_id: int, data: dict):
    state.event_bus.emit(session_id, event_type, account_id=account_id, data=data)


def _apply_cookies_to_account(state, account_id: int, cookie_input: Any) -> dict:
    """Parse, save cookies to DB, write storage_state.json."""
    ok, cookies = state.account_manager.save_cookies_from_string(account_id, cookie_input)
    if ok and cookies:
        state.browser_manager.write_storage_state_from_cookies(account_id, cookies)
    account = state.account_manager.get_account(account_id)
    return {
        "saved": ok,
        "cookie_count": len(cookies),
        "cookie_status": account.cookie_status_from_data(account.cookie_data) if account else {},
    }


def _append_email_notes(state, account_id: int, email: str, email_password: str):
    if not email:
        return
    account = state.account_manager.get_account(account_id)
    if not account:
        return
    extra = f"email={email}"
    if email_password:
        extra += f"|email_pass={email_password[:4]}***"
    old = account.notes or ""
    notes = f"{old}; {extra}" if old else extra
    state.account_manager.update_account(account_id, notes=notes)


def _create_account_from_body(state, body: "AccountCreateBody"):
    account = state.account_manager.add_account(
        username=body.username.strip(),
        proxy_id=body.proxy_id,
        notes=body.notes,
        password=body.password,
    )
    if not account:
        raise HTTPException(status_code=400, detail="Account already exists or creation failed")

    if body.cookie_data:
        try:
            _apply_cookies_to_account(state, account.id, body.cookie_data)
        except Exception as e:
            logger.warning(f"Failed to save cookies for acc {account.id}: {e}")

    if body.email:
        _append_email_notes(state, account.id, body.email, body.email_password)

    if body.status:
        state.account_manager.set_status(account.id, body.status)

    account = state.account_manager.get_account(account.id)
    return account


# ---- Account Endpoints ----

@router.get("/accounts")
async def list_accounts(
    request: Request,
    status: Optional[str] = Query(None, description="Filter by status"),
):
    """List all TikTok accounts."""
    state = get_state(request)
    try:
        accounts = state.account_manager.get_all_accounts(status)
        return {
            "success": True,
            "count": len(accounts),
            "accounts": [a.to_dict() for a in accounts],
        }
    except Exception as e:
        logger.error(f"Error listing accounts: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/accounts/{account_id}")
async def get_account(request: Request, account_id: int):
    """Get a single account by ID."""
    state = get_state(request)
    try:
        account = state.account_manager.get_account(account_id)
        if not account:
            raise HTTPException(status_code=404, detail="Account not found")
        return {"success": True, "account": account.to_dict()}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting account {account_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/accounts/{account_id}/stats")
async def get_account_stats(request: Request, account_id: int):
    """Get detailed stats for an account."""
    state = get_state(request)
    try:
        account = state.account_manager.get_account(account_id)
        if not account:
            raise HTTPException(status_code=404, detail="Account not found")

        activities = state.account_manager.get_recent_activities(account_id, limit=50)
        alerts = [
            a for a in state.account_manager.get_unresolved_alerts()
            if a["account_id"] == account_id
        ]

        return {
            "success": True,
            "account": account.to_dict(),
            "recent_activities": activities,
            "unresolved_alerts": alerts,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting stats for account {account_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/accounts")
async def create_account(
    request: Request,
    body: Optional["AccountCreateBody"] = Body(None),
    username: Optional[str] = Query(None, description="TikTok username (legacy query)"),
    proxy_id: int = Query(0, description="Proxy ID"),
    notes: str = Query("", description="Notes"),
    password: str = Query("", description="Password"),
):
    """Create a new TikTok account (JSON body or query params)."""
    state = get_state(request)
    try:
        if body:
            account = _create_account_from_body(state, body)
        elif username:
            account = _create_account_from_body(
                state,
                AccountCreateBody(
                    username=username,
                    proxy_id=proxy_id,
                    notes=notes,
                    password=password,
                ),
            )
        else:
            raise HTTPException(status_code=422, detail="Provide JSON body or username query param")
        state.scheduler.reschedule_all()
        return {"success": True, "account": account.to_dict()}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating account: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/accounts/import")
async def import_accounts(
    request: Request,
    file: Optional[UploadFile] = File(None),
    body: Optional["AccountCsvImportBody"] = Body(None),
    skip_existing: bool = Query(True),
):
    """Bulk import accounts from CSV file or pasted csv_text."""
    from src.import_utils import parse_csv_text, normalize_account_row

    state = get_state(request)
    try:
        if file:
            content = (await file.read()).decode("utf-8-sig", errors="replace")
            skip = skip_existing
        elif body and body.csv_text.strip():
            content = body.csv_text
            skip = body.skip_existing
        else:
            raise HTTPException(status_code=422, detail="Upload CSV file or provide csv_text in JSON body")

        rows, parse_errors = parse_csv_text(content)
        if parse_errors:
            raise HTTPException(status_code=400, detail="; ".join(parse_errors))

        items = [normalize_account_row(r) for r in rows]
        acc_cfg = state.settings.get("accounts", {})
        result = state.account_manager.import_accounts_bulk(
            items,
            skip_existing=skip,
            require_cookies=acc_cfg.get("require_cookies_on_import", False),
            default_status_with_cookies=acc_cfg.get("status_with_cookies", ""),
            browser_manager=state.browser_manager,
        )
        _sync_storage_states_after_import(state, result)
        state.scheduler.reschedule_all()
        return {"success": True, "total_rows": len(rows), **result}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Account import error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


def _sync_storage_states_after_import(state, result: dict):
    """Write storage_state for accounts that got cookies during bulk import."""
    if not result.get("with_cookies"):
        return
    for acc in state.account_manager.get_all_accounts():
        if not acc.cookie_data:
            continue
        try:
            cookies = json.loads(acc.cookie_data)
            if isinstance(cookies, list) and cookies:
                state.browser_manager.write_storage_state_from_cookies(acc.id, cookies)
        except Exception:
            pass


def _assign_proxies_round_robin(state, items: List[dict]) -> None:
    """Fill proxy_id=0 rows from alive proxy pool."""
    proxies = state.proxy_manager.get_alive_proxies()
    if not proxies:
        return
    idx = 0
    for item in items:
        if int(item.get("proxy_id") or 0) == 0:
            p = proxies[idx % len(proxies)]
            item["proxy_id"] = p.id
            idx += 1


@router.post("/accounts/import/seller")
async def import_accounts_seller(request: Request, body: "SellerImportBody"):
    """Import accounts from seller format: USER|PASS|EMAIL|EMAIL_PASS|COOKIES|UID"""
    from src.import_utils import parse_seller_bulk

    state = get_state(request)
    try:
        items, parse_errors = parse_seller_bulk(body.accounts, body.proxy_id)
        if not items and parse_errors:
            raise HTTPException(
                status_code=400,
                detail=f"No valid lines; errors: {parse_errors[:5]}",
            )

        if body.auto_assign_proxy:
            _assign_proxies_round_robin(state, items)

        acc_cfg = state.settings.get("accounts", {})
        require_cookies = body.require_cookies
        if acc_cfg.get("require_cookies_on_seller_import") is not None:
            require_cookies = bool(acc_cfg["require_cookies_on_seller_import"])

        result = state.account_manager.import_accounts_bulk(
            items,
            skip_existing=body.skip_existing,
            require_cookies=require_cookies,
            default_status_with_cookies=acc_cfg.get("status_with_cookies", "warming"),
            browser_manager=state.browser_manager,
        )
        result["parse_errors"] = parse_errors
        _sync_storage_states_after_import(state, result)
        state.scheduler.reschedule_all()
        return {
            "success": True,
            "total_lines": len(items) + len(parse_errors),
            **result,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Seller import error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/accounts/import/json")
async def import_accounts_json(request: Request, body: "AccountBulkBody"):
    """Bulk import accounts from JSON array."""
    state = get_state(request)
    try:
        items = [
            {
                "username": a.username,
                "proxy_id": a.proxy_id,
                "password": a.password,
                "notes": a.notes,
                "status": a.status or "",
                "cookie_data": a.cookie_data or "",
            }
            for a in body.accounts
        ]
        acc_cfg = state.settings.get("accounts", {})
        result = state.account_manager.import_accounts_bulk(
            items,
            skip_existing=body.skip_existing,
            browser_manager=state.browser_manager,
            default_status_with_cookies=acc_cfg.get("status_with_cookies", ""),
        )
        _sync_storage_states_after_import(state, result)
        state.scheduler.reschedule_all()
        return {"success": True, "total_rows": len(items), **result}
    except Exception as e:
        logger.error(f"Account JSON import error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/accounts/{account_id}")
async def update_account(
    request: Request,
    account_id: int,
    status: Optional[str] = Query(None, description="New status"),
    proxy_id: Optional[int] = Query(None, description="Proxy ID"),
    notes: Optional[str] = Query(None, description="Notes"),
    password: Optional[str] = Query(None, description="Password"),
):
    """Update an account."""
    state = get_state(request)
    try:
        kwargs = {}
        if status is not None:
            kwargs["status"] = status
        if proxy_id is not None:
            kwargs["proxy_id"] = proxy_id
        if notes is not None:
            kwargs["notes"] = notes
        if password is not None:
            kwargs["password"] = password

        account = state.account_manager.update_account(account_id, **kwargs)
        if not account:
            raise HTTPException(status_code=404, detail="Account not found")
        return {"success": True, "account": account.to_dict()}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating account {account_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/accounts/{account_id}")
async def delete_account(request: Request, account_id: int):
    """Delete an account."""
    state = get_state(request)
    try:
        deleted = state.account_manager.delete_account(account_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Account not found")
        state.browser_manager.delete_storage_state(account_id)
        return {"success": True, "message": f"Account {account_id} deleted"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting account {account_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/accounts/{account_id}/cookies")
async def update_account_cookies(
    request: Request, account_id: int, body: "CookieUpdateBody"
):
    """Update cookies for an account (string or JSON array)."""
    state = get_state(request)
    account = state.account_manager.get_account(account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    try:
        info = _apply_cookies_to_account(state, account_id, body.cookie_data)
        if not info["saved"]:
            raise HTTPException(status_code=400, detail="No valid cookies parsed")
        account = state.account_manager.get_account(account_id)
        return {"success": True, "account": account.to_dict(), **info}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Cookie update failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/accounts/{account_id}/cookies")
async def get_account_cookies(request: Request, account_id: int):
    """Get cookies for debug (requires app.debug=true)."""
    state = get_state(request)
    debug = state.settings.get("app", {}).get("debug", False)
    if not debug:
        raise HTTPException(status_code=403, detail="Cookie dump disabled (set app.debug=true)")

    account = state.account_manager.get_account(account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    cookies = []
    if account.cookie_data:
        try:
            cookies = json.loads(account.cookie_data)
        except json.JSONDecodeError:
            pass

    return {
        "success": True,
        "account_id": account_id,
        "cookie_count": len(cookies) if isinstance(cookies, list) else 0,
        "cookies": cookies,
        "cookie_status": account.cookie_status_from_data(account.cookie_data),
    }


@router.delete("/accounts/{account_id}/cookies")
async def delete_account_cookies(request: Request, account_id: int):
    """Clear cookies and storage_state for an account."""
    state = get_state(request)
    account = state.account_manager.get_account(account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    state.account_manager.clear_cookies(account_id)
    state.browser_manager.delete_storage_state(account_id)
    account = state.account_manager.get_account(account_id)
    return {"success": True, "account": account.to_dict()}


# ---- Profile scanner (Playwright browser) ----


def _profile_proxy_url(state, proxy_id: int) -> Optional[str]:
    if not proxy_id:
        return None
    proxy_obj = state.proxy_manager.get_proxy(proxy_id)
    if proxy_obj and proxy_obj.is_alive:
        return proxy_obj.url
    return None


def _apply_profile_scan_to_account(state, account_id: int, result: dict):
    """Persist browser profile scan stats to account row (SPEC v3)."""
    if not result.get("success") or result.get("private_account"):
        return None
    state.account_manager.update_account(
        account_id,
        followers=result.get("followers", 0),
        following=result.get("following", 0),
        total_posts=result.get("total_posts", 0),
        total_views=result.get("likes", 0),
        status="active",
        last_active=datetime.now().isoformat(),
    )
    return state.account_manager.get_account(account_id)


def _profile_scan_http_error(result: dict):
    """Raise HTTPException with error_type for dashboard toasts."""
    raise HTTPException(
        status_code=400,
        detail={
            "error": result.get("error") or "Profile scan failed",
            "error_type": result.get("error_type"),
            "username": result.get("username"),
        },
    )


@router.get("/profile/status")
async def profile_scanner_status(request: Request):
    """Profile scanner readiness (browser-based, no ms_token)."""
    state = get_state(request)
    return {"success": True, "status": state.profile_scanner.status()}


@router.post("/profile/scan/{username}")
async def profile_scan_username(request: Request, username: str):
    """Scan public TikTok profile (no proxy)."""
    state = get_state(request)
    result = await state.profile_scanner.fetch_profile(username)
    return result


@router.post("/profile/scan/{username}/proxy/{proxy_id}")
async def profile_scan_with_proxy(request: Request, username: str, proxy_id: int):
    """Scan public TikTok profile using a specific proxy."""
    state = get_state(request)
    proxy_url = _profile_proxy_url(state, proxy_id)
    if proxy_id and not proxy_url:
        raise HTTPException(status_code=400, detail="Proxy not found or not alive")
    result = await state.profile_scanner.fetch_profile(username, proxy_url=proxy_url)
    return result


@router.post("/accounts/{account_id}/sync-profile")
async def sync_account_profile(request: Request, account_id: int):
    """Fetch TikTok public stats via browser and save to account row."""
    state = get_state(request)
    account = state.account_manager.get_account(account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    proxy_url = _profile_proxy_url(state, account.proxy_id)
    result = await state.profile_scanner.fetch_profile(account.username, proxy_url=proxy_url)
    if not result.get("success"):
        _profile_scan_http_error(result)
    updated = _apply_profile_scan_to_account(state, account_id, result)
    return {
        "success": True,
        "profile": result,
        "account": updated.to_dict() if updated else None,
    }


@router.post("/accounts/sync-profiles")
async def sync_all_account_profiles(request: Request):
    """Sync public TikTok stats for all accounts (sequential, rate-limited)."""
    state = get_state(request)
    accounts = state.account_manager.get_all_accounts()
    results = {"synced": 0, "failed": 0, "errors": []}

    for acc in accounts:
        try:
            proxy_url = _profile_proxy_url(state, acc.proxy_id)
            result = await state.profile_scanner.fetch_profile(acc.username, proxy_url=proxy_url)
            if result.get("success") and not result.get("private_account"):
                _apply_profile_scan_to_account(state, acc.id, result)
                results["synced"] += 1
            elif result.get("private_account"):
                results["failed"] += 1
                results["errors"].append(
                    {
                        "account_id": acc.id,
                        "username": acc.username,
                        "error": "private",
                        "error_type": "private",
                    }
                )
            else:
                results["failed"] += 1
                results["errors"].append(
                    {
                        "account_id": acc.id,
                        "username": acc.username,
                        "error": result.get("error", "scan failed"),
                        "error_type": result.get("error_type"),
                    }
                )
            await asyncio.sleep(1.5)
        except Exception as e:
            results["failed"] += 1
            results["errors"].append({"account_id": acc.id, "username": acc.username, "error": str(e)})

    return {"success": True, "results": results}


# ---- Proxy Endpoints ----

@router.get("/proxies")
async def list_proxies(request: Request):
    """List all proxies."""
    state = get_state(request)
    try:
        proxies = state.proxy_manager.get_all_proxies()
        return {
            "success": True,
            "count": len(proxies),
            "proxies": [p.to_dict() for p in proxies],
        }
    except Exception as e:
        logger.error(f"Error listing proxies: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/proxies")
async def create_proxy(request: Request, body: "ProxyCreateBody"):
    """Add a single proxy."""
    from src.proxy_manager import Proxy

    state = get_state(request)
    try:
        proxy = Proxy(
            ip=body.ip.strip(),
            port=body.port,
            protocol=body.protocol,
            username=body.username,
            password=body.password,
            status=body.status,
        )
        added = state.proxy_manager.add_proxy(proxy)
        state.proxy_manager.sync_proxies_to_db(state.db)
        return {"success": True, "proxy": added.to_dict()}
    except Exception as e:
        logger.error(f"Error creating proxy: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/proxies/import")
async def import_proxies(
    request: Request,
    file: Optional[UploadFile] = File(None),
    body: Optional["ProxyCsvImportBody"] = Body(None),
    merge: bool = Query(True),
):
    """Bulk import proxies from CSV file or pasted csv_text."""
    from src.import_utils import parse_csv_text, normalize_proxy_row

    state = get_state(request)
    try:
        if file:
            content = (await file.read()).decode("utf-8-sig", errors="replace")
            do_merge = merge
        elif body and body.csv_text.strip():
            content = body.csv_text
            do_merge = body.merge
        else:
            raise HTTPException(status_code=422, detail="Upload CSV file or provide csv_text in JSON body")

        rows, parse_errors = parse_csv_text(content)
        if parse_errors:
            raise HTTPException(status_code=400, detail="; ".join(parse_errors))

        items = [normalize_proxy_row(r) for r in rows]
        result = state.proxy_manager.import_proxies_bulk(items, merge=do_merge)
        state.proxy_manager.sync_proxies_to_db(state.db)
        return {"success": True, "total_rows": len(rows), **result}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Proxy import error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/proxies/{proxy_id}")
async def delete_proxy(request: Request, proxy_id: int):
    """Remove a proxy by ID."""
    state = get_state(request)
    try:
        removed = state.proxy_manager.remove_proxy(proxy_id)
        if not removed:
            raise HTTPException(status_code=404, detail="Proxy not found")
        state.proxy_manager.sync_proxies_to_db(state.db)
        return {"success": True, "message": f"Proxy {proxy_id} deleted"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting proxy: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/templates/accounts.csv", response_class=PlainTextResponse)
async def template_accounts_csv():
    from src.import_utils import ACCOUNT_CSV_TEMPLATE
    return PlainTextResponse(
        ACCOUNT_CSV_TEMPLATE,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=accounts_template.csv"},
    )


@router.get("/templates/proxies.csv", response_class=PlainTextResponse)
async def template_proxies_csv():
    from src.import_utils import PROXY_CSV_TEMPLATE
    return PlainTextResponse(
        PROXY_CSV_TEMPLATE,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=proxies_template.csv"},
    )


@router.post("/proxies/check")
async def check_proxies(request: Request):
    """Check health of all proxies."""
    state = get_state(request)
    try:
        results = await state.proxy_manager.check_all_proxies()
        return {"success": True, "results": results}
    except Exception as e:
        logger.error(f"Error checking proxies: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ---- Performance Endpoints ----

@router.get("/performance")
async def get_performance(request: Request):
    """Get aggregated performance metrics."""
    state = get_state(request)
    try:
        stats = state.account_manager.get_performance_stats()
        return {"success": True, "stats": stats}
    except Exception as e:
        logger.error(f"Error getting performance stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/health")
async def get_health(request: Request):
    """Get system health status."""
    state = get_state(request)
    try:
        accounts = state.account_manager.get_all_accounts()
        proxies = state.proxy_manager.get_all_proxies()
        alerts = state.account_manager.get_unresolved_alerts(limit=10)

        status_counts = {}
        for a in accounts:
            s = a.status
            status_counts[s] = status_counts.get(s, 0) + 1

        proxy_status = {}
        for p in proxies:
            s = p.status
            proxy_status[s] = proxy_status.get(s, 0) + 1

        return {
            "success": True,
            "health": {
                "total_accounts": len(accounts),
                "account_statuses": status_counts,
                "total_proxies": len(proxies),
                "proxy_statuses": proxy_status,
                "recent_alerts": alerts,
                "scheduler_running": state.scheduler._running if state.scheduler else False,
            },
        }
    except Exception as e:
        logger.error(f"Error getting health: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ---- Action Endpoints ----

@router.post("/actions/farm/{account_id}")
async def trigger_farm(
    request: Request,
    account_id: int,
    duration: int = Query(15, description="Duration in minutes"),
):
    """Trigger a farm session for an account (background + WebSocket stream)."""
    state = get_state(request)
    try:
        account = state.account_manager.get_account(account_id)
        if not account:
            raise HTTPException(status_code=404, detail="Account not found")

        if state.farm_engine.is_running or account_id in state.active_farm_tasks:
            raise HTTPException(status_code=409, detail="Farm session already running for this account")

        proxy = _account_proxy_url(state, account)
        session_id = f"farm_{account_id}_{int(time.time())}"
        state.event_bus.create_session(session_id)
        state.active_farm_tasks[account_id] = session_id

        async def _run_farm():
            try:
                await state.farm_engine.run_farm_session(
                    account_id=account_id,
                    proxy_url=proxy,
                    duration_minutes=duration,
                    session_id=session_id,
                )
            finally:
                state.active_farm_tasks.pop(account_id, None)

        task = asyncio.create_task(_run_farm())

        return {
            "success": True,
            "message": f"Farm session started for account {account_id} ({duration} min)",
            "session_id": session_id,
            "ws_url": f"/api/ws/{session_id}",
            "task_id": id(task),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error triggering farm for {account_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.websocket("/ws/{session_id}")
async def farm_websocket(websocket: WebSocket, session_id: str):
    """Subscribe to live farm or post session events."""
    await websocket.accept()
    state = websocket.app.state.farm
    if not state.event_bus.has_session(session_id):
        state.event_bus.create_session(session_id)
    try:
        async for event in state.event_bus.subscribe(session_id):
            await websocket.send_json(event)
            if event.get("type") in ("farm:complete", "post:complete", "post:error"):
                break
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.warning(f"WebSocket {session_id} closed: {e}")
        try:
            await websocket.close()
        except Exception:
            pass


@router.post("/posts")
async def create_post_draft(request: Request, body: PostCreateBody = Body(...)):
    """Create a pending post draft with optional schedule validation."""
    from src.post_engine import validate_schedule, parse_schedule_at

    state = get_state(request)
    account = state.account_manager.get_account(body.account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    schedule_dt = parse_schedule_at(body.scheduled_at)
    if schedule_dt:
        ok, msg = validate_schedule(schedule_dt)
        if not ok:
            raise HTTPException(status_code=400, detail=msg)

    post_id = state.account_manager.add_post(
        body.account_id,
        body.content_path or "",
        body.caption,
        body.hashtags,
        body.affiliate_link,
        scheduled_at=body.scheduled_at,
    )
    if not post_id:
        raise HTTPException(status_code=500, detail="Failed to create post")

    return {
        "success": True,
        "post_id": post_id,
        "message": "Post draft created",
        "scheduled_at": body.scheduled_at,
    }


@router.post("/posts/{post_id}/publish")
async def publish_post(request: Request, post_id: int):
    """Publish a draft post via PostEngine v4."""
    from src.post_engine import parse_schedule_at, CookieExpiredError

    state = get_state(request)
    row = state.account_manager.get_post(post_id)
    if not row:
        raise HTTPException(status_code=404, detail="Post not found")

    account = state.account_manager.get_account(row["account_id"])
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    content_path = row.get("content_path") or ""
    if not content_path:
        raise HTTPException(status_code=400, detail="Post has no content_path")

    proxy_url = _account_proxy_url(state, account)
    schedule_dt = parse_schedule_at(row.get("scheduled_at"))
    path = Path(content_path)

    try:
        if path.is_dir():
            result = await state.post_engine.upload_slideshow(
                account_id=account.id,
                images_dir=str(path),
                caption=row.get("caption") or "",
                hashtags=row.get("hashtags") or "",
                affiliate_link=row.get("affiliate_link") or "",
                username=account.username,
                password=account.password or "",
                cookie_data=account.cookie_data,
                proxy_url=proxy_url,
                schedule_dt=schedule_dt,
            )
        else:
            result = await state.post_engine.upload_video(
                account_id=account.id,
                video_path=str(path),
                caption=row.get("caption") or "",
                hashtags=row.get("hashtags") or "",
                affiliate_link=row.get("affiliate_link") or "",
                username=account.username,
                password=account.password or "",
                cookie_data=account.cookie_data,
                proxy_url=proxy_url,
                schedule_dt=schedule_dt,
            )
    except CookieExpiredError as e:
        if state.telegram.enabled:
            await state.telegram.send_alert(
                "error",
                f"Post {post_id} failed: cookies expired for @{account.username}",
            )
        raise HTTPException(status_code=401, detail=str(e))

    if result.get("success"):
        state.account_manager.mark_post_posted(
            post_id,
            tiktok_post_id=result.get("tiktok_post_id") or result.get("post_id"),
            views=0,
        )
        acc = state.account_manager.get_account(account.id)
        state.account_manager.update_account(
            account.id,
            total_posts=(acc.total_posts or 0) + 1,
            last_active=datetime.now().isoformat(),
        )
    else:
        state.account_manager.update_post(post_id, status="failed")
        if state.telegram.enabled:
            await state.telegram.send_alert(
                "error",
                f"Post {post_id} publish failed: {result.get('error', 'unknown')}",
            )

    return {"success": result.get("success", False), "result": result, "post_id": post_id}


@router.get("/posts")
async def list_posts(
    request: Request,
    account_id: Optional[int] = Query(None),
    limit: int = Query(20, ge=1, le=100),
    status: Optional[str] = Query(None),
):
    """Post history for dashboard."""
    state = get_state(request)
    rows = state.account_manager.list_posts(account_id=account_id, limit=limit, status=status)
    posts = []
    for r in rows:
        posts.append({
            "id": r.get("id"),
            "account_id": r.get("account_id"),
            "username": r.get("account_username") or "",
            "tiktok_post_id": r.get("tiktok_post_id"),
            "media_type": "slideshow" if r.get("content_path") else "unknown",
            "caption": r.get("caption") or "",
            "hashtags": r.get("hashtags") or "",
            "status": r.get("status") or "pending",
            "views": r.get("views") or 0,
            "likes": r.get("likes") or 0,
            "comments": r.get("comments") or 0,
            "shares": r.get("shares") or 0,
            "scheduled_at": r.get("scheduled_at"),
            "posted_at": r.get("posted_at"),
            "content_path": r.get("content_path"),
        })
    return {"success": True, "count": len(posts), "posts": posts}


@router.get("/post-templates")
async def get_post_templates(request: Request):
    """List content pipeline template names."""
    state = get_state(request)
    templates = list((state.content_pipeline.templates or {}).keys())
    return {"success": True, "templates": templates}


@router.get("/preview/{preview_id}/{filename}")
async def serve_preview_image(preview_id: str, filename: str):
    """Serve generated preview slide images."""
    if ".." in preview_id or ".." in filename:
        raise HTTPException(status_code=400, detail="Invalid path")
    path = PREVIEW_ROOT / preview_id / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Preview not found")
    return FileResponse(path)


@router.post("/actions/preview/{account_id}")
async def preview_post(request: Request, account_id: int, body: PostComposeBody = Body(default_factory=PostComposeBody)):
    """Generate slideshow preview without uploading."""
    state = get_state(request)
    account = state.account_manager.get_account(account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    rating = body.rating if body.rating is not None else round(random.uniform(3.5, 5.0), 1)
    review = body.review or random.choice([
        "Amazing quality! Highly recommend.",
        "Best purchase this year!",
        "Perfect for daily use.",
        "Exceeded expectations!",
        "Fast shipping, great product!",
    ])
    price = body.price or f"${random.randint(9, 99)}.{random.randint(0, 99):02d}"

    post_dir = await state.content_pipeline.generate_post(
        account_id=account_id,
        product_name=body.product_name,
        template_name=body.template_name,
        rating=rating,
        review=review,
        price=price,
    )
    if not post_dir:
        raise HTTPException(status_code=500, detail="Content generation failed")

    preview_id = f"preview_{account_id}_{int(time.time())}"
    dest = PREVIEW_ROOT / preview_id
    dest.mkdir(parents=True, exist_ok=True)
    src = Path(post_dir)
    images = sorted(src.glob("*.png")) + sorted(src.glob("*.jpg"))
    urls = []
    for i, img in enumerate(images[:10], start=1):
        name = f"slide_{i}{img.suffix}"
        shutil.copy2(img, dest / name)
        urls.append(f"/api/preview/{preview_id}/{name}")

    caption = body.caption or review
    tags = body.hashtags or "fyp foryou viral"
    caption_preview = f"{caption}\n\n{' '.join('#' + t.lstrip('#') for t in tags.split())}"

    return {
        "success": True,
        "preview_id": preview_id,
        "images": urls,
        "images_dir": str(src),
        "caption_preview": caption_preview,
        "rating": rating,
        "price": price,
    }


async def _run_post_upload(state, account_id: int, body: PostComposeBody, session_id: str):
    account = state.account_manager.get_account(account_id)
    if not account:
        _emit_session(state, session_id, "post:error", account_id, {"message": "Account not found"})
        return

    try:
        _emit_session(state, session_id, "post:start", account_id, {"type": "slideshow"})
        proxy = _account_proxy_url(state, account)

        images_dir = body.images_dir
        if not images_dir:
            _emit_session(state, session_id, "post:log", account_id, {"message": "Generating slideshow..."})
            rating = body.rating if body.rating is not None else round(random.uniform(3.5, 5.0), 1)
            images_dir = await state.content_pipeline.generate_post(
                account_id=account_id,
                product_name=body.product_name,
                template_name=body.template_name,
                rating=rating,
                review=body.review or "Great product!",
                price=body.price or "$29.99",
            )
        if not images_dir:
            _emit_session(state, session_id, "post:error", account_id, {"message": "Content generation failed"})
            return

        caption = body.caption or "Check this out! 🔥"
        hashtags = body.hashtags or "fyp foryou viral"
        post_id = state.account_manager.add_post(
            account_id, images_dir, caption, hashtags, body.affiliate_link
        )

        _emit_session(state, session_id, "post:log", account_id, {"message": "Uploading to TikTok..."})
        result = await state.post_engine.upload_slideshow(
            account_id=account_id,
            images_dir=images_dir,
            caption=caption,
            hashtags=hashtags,
            affiliate_link=body.affiliate_link,
            username=account.username,
            password=account.password or "",
            cookie_data=account.cookie_data,
            proxy_url=proxy,
        )
        if post_id and result.get("success"):
            state.account_manager.mark_post_posted(
                post_id, tiktok_post_id=result.get("post_id"), views=0
            )
        _emit_session(
            state,
            session_id,
            "post:complete",
            account_id,
            {"success": result.get("success", False), "result": result, "post_id": post_id},
        )
    except Exception as e:
        logger.error(f"Post upload error for {account_id}: {e}", exc_info=True)
        _emit_session(state, session_id, "post:error", account_id, {"message": str(e)})
    finally:
        state.active_post_tasks.pop(account_id, None)


@router.post("/actions/post/{account_id}")
async def trigger_post(
    request: Request,
    account_id: int,
    body: PostComposeBody = Body(default_factory=PostComposeBody),
    background: bool = Query(True, description="Run in background with WebSocket progress"),
):
    """Generate slideshow content and upload to TikTok."""
    state = get_state(request)
    try:
        account = state.account_manager.get_account(account_id)
        if not account:
            raise HTTPException(status_code=404, detail="Account not found")

        if background:
            if account_id in state.active_post_tasks:
                raise HTTPException(status_code=409, detail="Post already in progress for this account")
            session_id = f"post_{account_id}_{int(time.time())}"
            state.event_bus.create_session(session_id)
            state.active_post_tasks[account_id] = session_id
            asyncio.create_task(_run_post_upload(state, account_id, body, session_id))
            return {
                "success": True,
                "message": f"Post started for account {account_id}",
                "session_id": session_id,
                "ws_url": f"/api/ws/{session_id}",
            }

        session_id = f"post_{account_id}_{int(time.time())}"
        state.event_bus.create_session(session_id)
        await _run_post_upload(state, account_id, body, session_id)
        return {"success": True, "message": "Post completed", "session_id": session_id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error triggering post for {account_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/actions/upload/video/{account_id}")
async def upload_video_post(
    request: Request,
    account_id: int,
    body: VideoUploadBody = Body(...),
):
    """Upload an mp4 video file to TikTok."""
    state = get_state(request)
    try:
        account = state.account_manager.get_account(account_id)
        if not account:
            raise HTTPException(status_code=404, detail="Account not found")

        proxy = _account_proxy_url(state, account)
        post_id = state.account_manager.add_post(
            account_id,
            body.video_path,
            body.caption,
            body.hashtags,
            body.affiliate_link,
        )
        result = await state.post_engine.upload_video(
            account_id=account_id,
            video_path=body.video_path,
            caption=body.caption or "",
            hashtags=body.hashtags or "fyp foryou viral",
            affiliate_link=body.affiliate_link,
            username=account.username,
            password=account.password or "",
            cookie_data=account.cookie_data,
            proxy_url=proxy,
        )
        if post_id and result.get("success"):
            state.account_manager.mark_post_posted(
                post_id, tiktok_post_id=result.get("post_id"), views=0
            )
        return {"success": result.get("success", False), "result": result, "post_id": post_id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error uploading video for {account_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/actions/sync-profile/{account_id}")
async def sync_profile(request: Request, account_id: int):
    """Manually trigger browser profile sync for an account."""
    state = get_state(request)
    try:
        account = state.account_manager.get_account(account_id)
        if not account:
            raise HTTPException(status_code=404, detail="Account not found")

        scanner_status = state.profile_scanner.status()
        if not scanner_status.get("ready"):
            raise HTTPException(status_code=503, detail="Profile scanner not ready (install Playwright)")

        proxy_url = _profile_proxy_url(state, account.proxy_id)
        result = await state.profile_scanner.fetch_profile(account.username, proxy_url=proxy_url)
        if not result.get("success"):
            _profile_scan_http_error(result)

        updated = _apply_profile_scan_to_account(state, account_id, result)
        updates = {}
        if updated:
            updates = {
                "followers": result.get("followers", 0),
                "following": result.get("following", 0),
                "total_posts": result.get("total_posts", 0),
                "total_views": result.get("likes", 0),
                "status": "active",
            }
        return {"success": True, "profile": result, "updates": updates}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Profile sync error for {account_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/actions/check/{account_id}")
async def trigger_check(request: Request, account_id: int):
    """Trigger a health check for an account."""
    state = get_state(request)
    try:
        result = await state.health_monitor.check_account(account_id)
        return {"success": True, "result": result}
    except Exception as e:
        logger.error(f"Error checking account {account_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ---- Alerts Endpoints ----

@router.get("/alerts")
async def list_alerts(request: Request, resolved: Optional[int] = Query(None)):
    """List alerts. resolved=0 unresolved, resolved=1 resolved, omit for all."""
    state = get_state(request)
    try:
        if resolved is None:
            alerts = state.account_manager.get_alerts(limit=100)
        elif resolved == 1:
            alerts = state.account_manager.get_alerts(resolved=True, limit=100)
        else:
            alerts = state.account_manager.get_alerts(resolved=False, limit=100)
        return {"success": True, "count": len(alerts), "alerts": alerts}
    except Exception as e:
        logger.error(f"Error listing alerts: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/alerts/{alert_id}/resolve")
async def resolve_alert(request: Request, alert_id: int):
    """Resolve an alert."""
    state = get_state(request)
    try:
        resolved = state.account_manager.resolve_alert(alert_id)
        return {"success": resolved, "message": f"Alert {alert_id} resolved" if resolved else "Alert not found"}
    except Exception as e:
        logger.error(f"Error resolving alert {alert_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ---- Affiliate Pipeline v2.0 ----

@router.get("/affiliate/status")
async def affiliate_status(request: Request):
    state = get_state(request)
    return {"success": True, "status": state.affiliate_pipeline.status()}


@router.post("/affiliate/scan")
async def affiliate_scan(
    request: Request,
    limit: int = Query(20, ge=1, le=100),
    category: str = Query(""),
):
    state = get_state(request)
    try:
        products = await state.affiliate_pipeline.scan_and_cache(
            limit=limit, category=category
        )
        return {"success": True, "count": len(products), "products": products}
    except Exception as e:
        logger.error(f"Affiliate scan failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/affiliate/trending")
async def affiliate_trending(request: Request):
    state = get_state(request)
    products = state.affiliate_pipeline.load_cached_trending()
    return {"success": True, "count": len(products), "products": products}


@router.post("/affiliate/run/{account_id}")
async def affiliate_run_account(request: Request, account_id: int):
    """Run full affiliate pipeline for one account (Real accounts)."""
    state = get_state(request)
    account = state.account_manager.get_account(account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    session = {"ok": True, "account": account}
    if state.session_service:
        try:
            prep = await state.session_service.prepare(account_id)
            if prep.get("ok"):
                session = prep
        except Exception as e:
            logger.warning(f"Session prepare: {e}")

    try:
        result = await state.affiliate_pipeline.run_for_account(
            account_id, session
        )
        if result.get("success"):
            acc = state.account_manager.get_account(account_id)
            state.account_manager.update_account(
                account_id,
                total_posts=(acc.total_posts or 0) + 1,
                last_active=datetime.now().isoformat(),
            )
        return {"success": result.get("success", False), "result": result}
    except Exception as e:
        logger.error(f"Affiliate run failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ---- Dashboard HTML ----

@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Serve the dashboard HTML page."""
    html_path = Path(__file__).parent / "templates" / "index.html"
    if html_path.exists():
        return HTMLResponse(content=html_path.read_text())
    return HTMLResponse(content="<html><body><h1>Dashboard template not found</h1></body></html>")


# ---- Scheduler Endpoints ----

@router.post("/scheduler/reschedule")
async def reschedule_all(request: Request):
    """Reschedule all jobs."""
    state = get_state(request)
    try:
        state.scheduler.reschedule_all()
        return {"success": True, "message": "All jobs rescheduled"}
    except Exception as e:
        logger.error(f"Error rescheduling: {e}")
        raise HTTPException(status_code=500, detail=str(e))
