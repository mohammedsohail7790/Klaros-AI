from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import CurrentUser, get_current_user
from app.db.session import async_session_maker
from app.services.cash_forecast_service import CashForecastNotFoundError, CashForecastService

router = APIRouter(prefix="/cash", tags=["cash"])


@router.post("/forecast/generate")
async def generate_forecast(current_user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    service = CashForecastService(async_session_maker)
    forecast = await service.generate(current_user.tenant_id)
    try:
        weeks = await service.weekly_projection(current_user.tenant_id, forecast.id)
    except CashForecastNotFoundError as exc:
        # Should be unreachable here (forecast.id was just created for this
        # tenant), but fail closed with a 404 rather than a 500/leak if it
        # ever is.
        raise HTTPException(status_code=404, detail="Cash forecast not found") from exc
    return {
        "forecast_id": str(forecast.id),
        "generated_at": forecast.generated_at.isoformat(),
        "starting_cash": str(forecast.starting_cash) if forecast.starting_cash is not None else None,
        "starting_cash_source": forecast.starting_cash_source,
        "weeks": weeks,
    }
