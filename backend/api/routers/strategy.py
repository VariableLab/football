from fastapi import APIRouter, Depends

from database.models import User
from api.auth import get_current_active_user

router = APIRouter(prefix="/api/strategy", tags=["Strategy"])

@router.post("/optimize")
def trigger_param_optimize(
    max_combinations: int = 200,
    user: User = Depends(get_current_active_user),
):
    from core.param_optimizer import run_grid_search
    results = run_grid_search(max_combinations=max_combinations, sample_limit=5000)
    if not results:
        return {"status": "no_data"}
    best = results[0]
    return {"status": "completed", "best_combined_roi": best.combined_roi}

@router.get("/monitor")
def get_strategy_monitor_status():
    from core.strategy_monitor import get_iteration_status, load_baseline_snapshot
    return {
        "iteration": get_iteration_status(),
        "baseline_snapshot": load_baseline_snapshot(),
    }

@router.post("/monitor/check")
def trigger_drift_check(user: User = Depends(get_current_active_user)):
    from core.strategy_monitor import check_drift, should_trigger_optimize
    triggered = check_drift()
    return {"triggered_count": len(triggered), "should_optimize": should_trigger_optimize(triggered)}
