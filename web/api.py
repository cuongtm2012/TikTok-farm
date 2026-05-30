# TikTok Farm - Web API Routes
# FastAPI routes for dashboard and management

import logging
from typing import Optional, List
from pathlib import Path

logger = logging.getLogger(__name__)

try:
    from fastapi import APIRouter, HTTPException, Query, Request
    from fastapi.responses import HTMLResponse, JSONResponse
    FASTAPI_AVAILABLE = True
except ImportError:
    FASTAPI_AVAILABLE = False
    # Stub for import
    APIRouter = object


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


@router.post("/accounts")
async def create_account(
    request: Request,
    username: str = Query(..., description="TikTok username"),
    proxy_id: int = Query(0, description="Proxy ID"),
    notes: str = Query("", description="Notes"),
):
    """Create a new TikTok account."""
    state = get_state(request)
    try:
        account = state.account_manager.add_account(username, proxy_id, notes)
        if not account:
            raise HTTPException(status_code=400, detail="Account already exists or creation failed")
        return {"success": True, "account": account.to_dict()}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating account: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/accounts/{account_id}")
async def update_account(
    request: Request,
    account_id: int,
    status: Optional[str] = Query(None, description="New status"),
    proxy_id: Optional[int] = Query(None, description="Proxy ID"),
    notes: Optional[str] = Query(None, description="Notes"),
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
    """List alerts."""
    state = get_state(request)
    try:
        alerts = state.account_manager.get_unresolved_alerts()
        if resolved == 1:
            # Return all alerts including resolved (simplified)
            pass
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
