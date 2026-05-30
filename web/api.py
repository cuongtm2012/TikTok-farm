# TikTok Farm - Web API Routes
# FastAPI routes for dashboard and management

import logging
from typing import Optional, List
from pathlib import Path

logger = logging.getLogger(__name__)

try:
    from fastapi import APIRouter, HTTPException, Query, Request, UploadFile, File, Body
    from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
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
        notes: str = ""
        status: Optional[str] = None

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


if FASTAPI_AVAILABLE:
    router = APIRouter()
else:
    router = None


def get_state(request: Request):
    """Get the app state from request."""
    return request.app.state.farm


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


def _create_account_from_body(state, body: "AccountCreateBody"):
    account = state.account_manager.add_account(
        username=body.username.strip(),
        proxy_id=body.proxy_id,
        notes=body.notes,
        password=body.password,
    )
    if not account:
        raise HTTPException(status_code=400, detail="Account already exists or creation failed")
    if body.status:
        state.account_manager.set_status(account.id, body.status)
        account = state.account_manager.get_account(account.id)
    return account


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
        result = state.account_manager.import_accounts_bulk(items, skip_existing=skip)
        state.scheduler.reschedule_all()
        return {"success": True, "total_rows": len(rows), **result}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Account import error: {e}")
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
            }
            for a in body.accounts
        ]
        result = state.account_manager.import_accounts_bulk(
            items, skip_existing=body.skip_existing
        )
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
        return {"success": True, "message": f"Account {account_id} deleted"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting account {account_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ---- TikTok public profile (davidteather/TikTok-Api) ----

@router.get("/tiktok/profile/status")
async def tiktok_profile_status(request: Request):
    """Whether TikTok-Api profile lookup is configured."""
    state = get_state(request)
    return {"success": True, **state.tiktok_profile.status()}


@router.get("/tiktok/profile/{username}")
async def tiktok_profile_lookup(request: Request, username: str):
    """Fetch public TikTok profile by username (no DB write)."""
    state = get_state(request)
    try:
        profile = await state.tiktok_profile.fetch_profile(username)
        return {"success": True, "profile": profile}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.error(f"TikTok profile lookup failed for {username}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/accounts/{account_id}/sync-profile")
async def sync_account_profile(request: Request, account_id: int):
    """Fetch TikTok public stats and save to account row."""
    state = get_state(request)
    account = state.account_manager.get_account(account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    try:
        profile = await state.tiktok_profile.fetch_profile(account.username)
        updated = state.account_manager.apply_tiktok_profile(account_id, profile)
        return {
            "success": True,
            "profile": profile,
            "account": updated.to_dict() if updated else None,
        }
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.error(f"Profile sync failed for account {account_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/accounts/sync-profiles")
async def sync_all_account_profiles(request: Request):
    """Sync public TikTok stats for all accounts (sequential, may be slow)."""
    state = get_state(request)
    accounts = state.account_manager.get_all_accounts()
    results = {"synced": 0, "failed": 0, "errors": []}

    for acc in accounts:
        try:
            profile = await state.tiktok_profile.fetch_profile(acc.username)
            state.account_manager.apply_tiktok_profile(acc.id, profile)
            results["synced"] += 1
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
    """Trigger a farm session for an account."""
    state = get_state(request)
    try:
        account = state.account_manager.get_account(account_id)
        if not account:
            raise HTTPException(status_code=404, detail="Account not found")

        proxy = None
        if account.proxy_id:
            proxy_obj = state.proxy_manager.get_proxy(account.proxy_id)
            if proxy_obj and proxy_obj.is_alive:
                proxy = proxy_obj.url

        # Run in background
        import asyncio
        task = asyncio.create_task(
            state.farm_engine.run_farm_session(
                account_id=account_id,
                proxy_url=proxy,
                duration_minutes=duration,
            )
        )

        return {
            "success": True,
            "message": f"Farm session started for account {account_id} ({duration} min)",
            "task_id": id(task),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error triggering farm for {account_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/actions/post/{account_id}")
async def trigger_post(request: Request, account_id: int):
    """Trigger a post upload for an account."""
    state = get_state(request)
    try:
        account = state.account_manager.get_account(account_id)
        if not account:
            raise HTTPException(status_code=404, detail="Account not found")

        # Generate content
        import random
        post_dir = await state.content_pipeline.generate_post(
            account_id=account_id,
            rating=round(random.uniform(3.5, 5.0), 1),
            review=random.choice([
                "Amazing quality! Highly recommend.",
                "Best purchase this year!",
                "Perfect for daily use.",
                "Exceeded expectations!",
                "Fast shipping, great product!",
            ]),
            price=f"${random.randint(9, 99)}.{random.randint(0, 99):02d}",
        )

        if not post_dir:
            raise HTTPException(status_code=500, detail="Content generation failed")

        # Upload
        result = await state.post_engine.upload_slideshow(
            account_id=account_id,
            images_dir=post_dir,
            caption="Check this out! 🔥",
            hashtags="fyp foryou viral",
            username=account.username,
        )

        return {"success": result.get("success", False), "result": result}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error triggering post for {account_id}: {e}")
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
