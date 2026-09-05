"""Сервис для вычисления и расчетов биллинга подписок."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from dateutil.relativedelta import relativedelta
from src.services.billing_constants import (
    DAYS_IN_MONTH,
    SECONDS_IN_DAY,
    SUBSCRIPTION_RATES,
    AuditScenario,
    SubscriptionTier,
    UpgradeMode,
)

logger = logging.getLogger(__name__)

__all__ = [
    "BillingContext",
    "BillingResult",
    "TierRateInfo",
    "calculate_subscription_upgrade",
]


def _ensure_utc(dt: datetime) -> datetime:
    """Приводит datetime к часовому поясу UTC.

    Args:
        dt: Объект datetime.

    Returns:
        datetime в часовом поясе UTC.
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _ensure_utc_opt(dt: datetime | None) -> datetime | None:
    """Приводит опциональный datetime к часовому поясу UTC.

    Args:
        dt: Дата и время или None.

    Returns:
        datetime в часовом поясе UTC или None.
    """
    if dt is None:
        return None
    return _ensure_utc(dt)


def _round_money(val: Decimal) -> Decimal:
    """Безопасное округление Decimal до 2 знаков без конвертации во float.

    Args:
        val: Исходная финансовая сумма в Decimal.

    Returns:
        Округленное значение Decimal с точностью 0.01.
    """
    return val.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _decimal_to_timedelta(days: Decimal) -> timedelta:
    """Преобразует Decimal дней в timedelta.

    Args:
        days: Количество дней в виде Decimal.

    Returns:
        Объект timedelta периода времени.
    """
    return timedelta(days=float(days))


@dataclass(frozen=True)
class BillingContext:
    """Контекст для выполнения расчетов биллинга подписки.

    Attributes:
        current_tier: Текущий тариф подписки (например, SubscriptionTier.BASE).
        target_tier: Целевой тариф подписки.
        amount_paid: Сумма оплаты в рублях.
        months: Количество купленных месяцев.
        upgrade_mode: Режим обновления ('convert_now' или 'deferred').
        now: Дата и время проведения операции.
        sub_end: Дата окончания текущей подписки.
        trial_end: Дата окончания пробного периода.
    """

    current_tier: SubscriptionTier | str
    target_tier: SubscriptionTier | str
    amount_paid: Decimal
    months: int
    upgrade_mode: UpgradeMode
    now: datetime
    sub_end: datetime | None
    trial_end: datetime | None


@dataclass(frozen=True)
class TierRateInfo:
    """Вспомогательный контейнер с тарифными ставками.

    Attributes:
        target_rate: Полная цена целевого тарифа за месяц.
        target_daily_rate: Дневная ставка целевого тарифа.
        is_downgrade: Флаг понижения уровня подписки.
    """

    target_rate: Decimal
    target_daily_rate: Decimal
    is_downgrade: bool


@dataclass(frozen=True)
class BillingResult:
    """Результат расчета параметров подписки.

    Attributes:
        new_tier: Действующий тариф после операции.
        new_deadline: Новая дата окончания подписки.
        audit_details: Детальная информация для аудита и сохранения.
        pending_tier: Запланированный тариф (для отложенной смены).
        pending_tier_activates_at: Дата активации запланированного тарифа.
    """

    new_tier: SubscriptionTier | str
    new_deadline: datetime
    audit_details: dict[str, Any]
    pending_tier: SubscriptionTier | str | None = None
    pending_tier_activates_at: datetime | None = None


def _calculate_deadline(
    anchor: datetime, amount_paid: Decimal, months: int, daily_rate: Decimal
) -> datetime:
    """Рассчитывает новую дату окончания подписки от точки отсчета.

    Args:
        anchor: Точка отсчета времени.
        amount_paid: Сумма оплаты.
        months: Количество купленных месяцев.
        daily_rate: Дневная ставка целевого тарифа.

    Returns:
        Новая дата окончания подписки.

    Raises:
        ValueError: Если дневная ставка не положительна при расчете по дням.
    """
    if months > 0:
        return anchor + relativedelta(months=months)

    if daily_rate <= Decimal("0"):
        raise ValueError(
            "Daily rate must be greater than 0 for day-based billing."
        )

    days_decimal = amount_paid / daily_rate
    return anchor + _decimal_to_timedelta(days_decimal)


# Единая сигнатура для функциональных стратегий (rates передаются снаружи)
StrategyFn = Callable[
    [BillingContext, TierRateInfo, dict[SubscriptionTier, Decimal]],
    tuple[BillingResult, str],
]


def _strategy_trial_upgrade(
    ctx: BillingContext,
    rate_info: TierRateInfo,
    rates: dict[SubscriptionTier, Decimal],
) -> tuple[BillingResult, str]:
    """Рассчитывает параметры подписки при апгрейде с триала.

    Args:
        ctx: Контекст расчетов биллинга.
        rate_info: Данные о ставках тарифа.

    Returns:
        Кортеж из результата BillingResult и строки для логирования.
    """
    new_deadline = _calculate_deadline(
        ctx.now, ctx.amount_paid, ctx.months, rate_info.target_daily_rate
    )
    audit = {
        "scenario": AuditScenario.UPGRADE_FROM_TRIAL.value,
        "previous_tier": str(ctx.current_tier),
        "target_tier": str(ctx.target_tier),
        "amount_paid": str(_round_money(ctx.amount_paid)),
        "months": ctx.months,
        "effective_date_iso": ctx.now.isoformat(),
    }
    log_msg = f"upgraded from trial to {ctx.target_tier} until {new_deadline}"
    return (
        BillingResult(
            new_tier=ctx.target_tier,
            new_deadline=new_deadline,
            audit_details=audit,
        ),
        log_msg,
    )


def _strategy_same_tier_extension(
    ctx: BillingContext,
    rate_info: TierRateInfo,
    rates: dict[SubscriptionTier, Decimal],
) -> tuple[BillingResult, str]:
    """Рассчитывает параметры продления текущего аналогичного тарифа.

    Args:
        ctx: Контекст расчетов биллинга.
        rate_info: Данные о ставках тарифа.
        rates: Словарь тарифных ставок (не используется, передаётся
            для симметрии сигнатуры).

    Returns:
        Кортеж из результата BillingResult и строки для логирования.
    """
    anchor = (
        max(ctx.now, ctx.sub_end)
        if (ctx.sub_end and ctx.sub_end > ctx.now)
        else ctx.now
    )
    new_deadline = _calculate_deadline(
        anchor, ctx.amount_paid, ctx.months, rate_info.target_daily_rate
    )
    audit = {
        "scenario": AuditScenario.SAME_TIER_EXTENSION.value,
        "previous_tier": str(ctx.current_tier),
        "target_tier": str(ctx.target_tier),
        "amount_paid": str(_round_money(ctx.amount_paid)),
        "months": ctx.months,
        "effective_date_iso": anchor.isoformat(),
    }
    log_msg = f"extended {ctx.target_tier} until {new_deadline}"
    return (
        BillingResult(
            new_tier=ctx.target_tier,
            new_deadline=new_deadline,
            audit_details=audit,
        ),
        log_msg,
    )


def _strategy_deferred_change(
    ctx: BillingContext,
    rate_info: TierRateInfo,
    rates: dict[SubscriptionTier, Decimal],
) -> tuple[BillingResult, str]:
    """Рассчитывает отложенную смену тарифа (upgrade или downgrade).

    Args:
        ctx: Контекст расчетов биллинга.
        rate_info: Данные о ставках тарифа.
        rates: Словарь тарифных ставок (не используется, передаётся
            для симметрии сигнатуры).

    Returns:
        Кортеж из результата BillingResult и строки для логирования.
    """
    anchor_def = (
        ctx.sub_end if (ctx.sub_end and ctx.sub_end > ctx.now) else ctx.now
    )
    new_deadline = _calculate_deadline(
        anchor_def, ctx.amount_paid, ctx.months, rate_info.target_daily_rate
    )
    is_downgrade = rate_info.is_downgrade
    scenario = (
        AuditScenario.DOWNGRADE_DEFERRED
        if is_downgrade
        else AuditScenario.UPGRADE_DEFERRED
    )
    action_name = "deferred downgrade" if is_downgrade else "deferred upgrade"

    audit = {
        "scenario": scenario.value,
        "previous_tier": str(ctx.current_tier),
        "target_tier": str(ctx.target_tier),
        "amount_paid": str(_round_money(ctx.amount_paid)),
        "months": ctx.months,
        "effective_date_iso": anchor_def.isoformat(),
    }
    log_msg = f"{action_name} to {ctx.target_tier} until {new_deadline}"
    return (
        BillingResult(
            new_tier=ctx.current_tier,
            new_deadline=new_deadline,
            audit_details=audit,
            pending_tier=ctx.target_tier,
            pending_tier_activates_at=anchor_def,
        ),
        log_msg,
    )


def _strategy_prorated_convert_now(
    ctx: BillingContext,
    rate_info: TierRateInfo,
    rates: dict[SubscriptionTier, Decimal],
) -> tuple[BillingResult, str]:
    """Рассчитывает мгновенную смену тарифа с пропорциональным пересчетом.

    Args:
        ctx: Контекст расчетов биллинга.
        rate_info: Данные о ставках тарифа.
        rates: Словарь тарифных ставок (из БД или константы).

    Returns:
        Кортеж из результата BillingResult и строки для логирования.

    Raises:
        ValueError: Если у текущего или целевого тарифа недопустимая ставка.
    """
    remaining_days = Decimal("0.0")
    if ctx.sub_end and ctx.sub_end > ctx.now:
        diff_seconds = Decimal(str((ctx.sub_end - ctx.now).total_seconds()))
        remaining_days = diff_seconds / SECONDS_IN_DAY

    old_rate = rates.get(SubscriptionTier(ctx.current_tier))
    if old_rate is None or old_rate <= Decimal("0"):
        raise ValueError(
            f"Invalid rate for previous tier '{ctx.current_tier}'"
        )

    old_daily_rate = old_rate / DAYS_IN_MONTH
    unused_value_rub = remaining_days * old_daily_rate

    if rate_info.target_daily_rate <= Decimal("0"):
        raise ValueError(
            "Target daily rate must be positive for prorated conversion."
        )

    if ctx.months > 0:
        base_deadline = ctx.now + relativedelta(months=ctx.months)
        extra_days = unused_value_rub / rate_info.target_daily_rate
        new_deadline = base_deadline + _decimal_to_timedelta(extra_days)
        actual_payment = (
            ctx.amount_paid
            if ctx.amount_paid > Decimal("0")
            else (Decimal(ctx.months) * rate_info.target_rate)
        )
        total_value_rub = unused_value_rub + actual_payment
        calculated_days = (Decimal(ctx.months) * DAYS_IN_MONTH) + extra_days
    else:
        actual_payment = ctx.amount_paid
        total_value_rub = unused_value_rub + actual_payment
        calculated_days = total_value_rub / rate_info.target_daily_rate
        new_deadline = ctx.now + _decimal_to_timedelta(calculated_days)

    audit = {
        "scenario": AuditScenario.UPGRADE_CONVERT_NOW_PRORATE.value,
        "previous_tier": str(ctx.current_tier),
        "target_tier": str(ctx.target_tier),
        "actual_payment": str(_round_money(actual_payment)),
        "remaining_days": str(_round_money(remaining_days)),
        "unused_value_rub": str(_round_money(unused_value_rub)),
        "total_value_rub": str(_round_money(total_value_rub)),
        "new_daily_rate": str(_round_money(rate_info.target_daily_rate)),
        "calculated_days": str(_round_money(calculated_days)),
        "effective_date_iso": ctx.now.isoformat(),
    }
    log_msg = (
        f"prorated upgrade from {ctx.current_tier} to {ctx.target_tier} "
        f"until {new_deadline}"
    )
    return (
        BillingResult(
            new_tier=ctx.target_tier,
            new_deadline=new_deadline,
            audit_details=audit,
        ),
        log_msg,
    )


def _select_strategy(ctx: BillingContext, is_downgrade: bool) -> StrategyFn:
    """Выбирает функцию стратегии расчета на основе состояния контекста.

    Args:
        ctx: Нормализованный контекст биллинга.
        is_downgrade: Флаг понижения тарифа.

    Returns:
        Выбранная функция стратегии расчетов.
    """
    is_active_trial = ctx.current_tier == SubscriptionTier.TRIAL or (
        ctx.trial_end is not None
        and ctx.trial_end > ctx.now
        and not (ctx.sub_end is not None and ctx.sub_end > ctx.now)
    )
    is_same_tier = ctx.current_tier == ctx.target_tier
    is_deferred = is_downgrade or ctx.upgrade_mode == "deferred"

    match (is_active_trial, is_same_tier, is_deferred):
        case (True, _, _):
            return _strategy_trial_upgrade
        case (_, True, _):
            return _strategy_same_tier_extension
        case (_, _, True):
            return _strategy_deferred_change
        case _:
            return _strategy_prorated_convert_now


def calculate_subscription_upgrade(
    ctx: BillingContext,
    rates: dict[SubscriptionTier, Decimal] | None = None,
) -> BillingResult:
    """Главный оркестратор расчета параметров подписки.

    Args:
        ctx: Входной контекст биллинга.
        rates: Актуальные тарифные ставки из БД. Если None,
            используются захардкоженные ставки из billing_constants.

    Returns:
        Результат вычислений BillingResult.

    Raises:
        ValueError: Если указаны неверные параметры тарифа или оплаты.
    """
    effective_rates = rates if rates is not None else dict(SUBSCRIPTION_RATES)

    now_clean = _ensure_utc(ctx.now)
    sub_end_clean = _ensure_utc_opt(ctx.sub_end)
    trial_end_clean = _ensure_utc_opt(ctx.trial_end)

    try:
        target_tier_clean = SubscriptionTier(str(ctx.target_tier).lower())
    except ValueError as err:
        raise ValueError(
            f"Unsupported target subscription tier: '{ctx.target_tier}'. "
            f"Available tiers: {[t.value for t in SubscriptionTier]}"
        ) from err

    try:
        current_tier_clean = SubscriptionTier(str(ctx.current_tier).lower())
    except ValueError as err:
        raise ValueError(
            f"Unsupported current subscription tier: '{ctx.current_tier}'. "
            f"Available tiers: {[t.value for t in SubscriptionTier]}"
        ) from err

    if target_tier_clean == SubscriptionTier.TRIAL:
        raise ValueError("Cannot upgrade or switch to TRIAL tier.")

    if sub_end_clean and sub_end_clean < now_clean:
        current_tier_clean = SubscriptionTier.TRIAL
        sub_end_clean = None

    ctx_norm = replace(
        ctx,
        now=now_clean,
        sub_end=sub_end_clean,
        trial_end=trial_end_clean,
        target_tier=target_tier_clean,
        current_tier=current_tier_clean,
    )

    if ctx_norm.amount_paid < Decimal("0") or ctx_norm.months < 0:
        raise ValueError("amount_paid and months cannot be negative.")

    if ctx_norm.amount_paid == Decimal("0") and ctx_norm.months == 0:
        raise ValueError(
            "Either amount_paid or months must be strictly greater than 0."
        )

    rate = effective_rates.get(target_tier_clean)
    if rate is None or rate <= Decimal("0"):
        raise ValueError(
            f"Invalid rate configured for tier '{target_tier_clean}': {rate}"
        )

    daily_rate = rate / DAYS_IN_MONTH
    current_rate = effective_rates.get(current_tier_clean)
    is_downgrade = current_rate is not None and current_rate > rate

    rate_info = TierRateInfo(
        target_rate=rate,
        target_daily_rate=daily_rate,
        is_downgrade=is_downgrade,
    )

    strategy_fn = _select_strategy(ctx_norm, is_downgrade)
    res, log_msg = strategy_fn(ctx_norm, rate_info, effective_rates)

    logger.info("Billing: Context %s %s", ctx_norm.current_tier, log_msg)
    return res
