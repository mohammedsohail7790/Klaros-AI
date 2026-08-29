from typing import Any

from pydantic import BaseModel

from app.models.rbac import Permission
from app.services.cash_forecast_service import CashForecastService
from app.tools.base import ExecutionContext, Tool


class EmptyInput(BaseModel):
    pass


class GenerateCashForecastOutput(BaseModel):
    forecast_id: str
    starting_cash: str | None
    starting_cash_source: str
    weeks: list[dict[str, Any]]


class GenerateCashForecast(Tool):
    name = "finance.generate_cash_forecast"
    description = (
        "Generate a 13-week cash forecast from real open invoices and vendor bills. "
        "Starting cash is 'NOT_CONNECTED' unless manually set — never fabricated."
    )
    input_schema = EmptyInput
    output_schema = GenerateCashForecastOutput
    required_permission = Permission.VIEW_CASH_FORECAST

    def __init__(self, cash_forecast_service: CashForecastService) -> None:
        self._cash_forecast_service = cash_forecast_service

    async def execute(self, input: EmptyInput, context: ExecutionContext) -> GenerateCashForecastOutput:
        forecast = await self._cash_forecast_service.generate(context.tenant_id)
        weeks = await self._cash_forecast_service.weekly_projection(context.tenant_id, forecast.id)
        return GenerateCashForecastOutput(
            forecast_id=str(forecast.id),
            starting_cash=str(forecast.starting_cash) if forecast.starting_cash is not None else None,
            starting_cash_source=forecast.starting_cash_source,
            weeks=weeks,
        )
