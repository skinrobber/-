from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable


MONEY = Decimal("0.01")


@dataclass(frozen=True)
class OrderItem:
    sku: str
    unit_price: Decimal
    quantity: int
    category: str = "normal"


@dataclass(frozen=True)
class Customer:
    level: str = "standard"
    is_new: bool = False


@dataclass(frozen=True)
class Coupon:
    code: str
    threshold: Decimal
    discount: Decimal
    category: str | None = None


def money(value: str | int | float | Decimal) -> Decimal:
    return Decimal(str(value)).quantize(MONEY, rounding=ROUND_HALF_UP)


def _validate_items(items: Iterable[OrderItem]) -> list[OrderItem]:
    checked = list(items)
    if not checked:
        raise ValueError("order must contain at least one item")

    for item in checked:
        if not item.sku:
            raise ValueError("sku must not be empty")
        if item.unit_price < 0:
            raise ValueError("unit price must not be negative")
        if item.quantity <= 0:
            raise ValueError("quantity must be positive")
    return checked


def _member_discount_rate(customer: Customer) -> Decimal:
    if customer.level == "standard":
        return Decimal("0.00")
    if customer.level == "silver":
        return Decimal("0.05")
    if customer.level == "gold":
        return Decimal("0.10")
    if customer.level == "platinum":
        return Decimal("0.15")
    raise ValueError(f"unknown customer level: {customer.level}")


def _subtotal(items: list[OrderItem]) -> Decimal:
    total = Decimal("0.00")
    for item in items:
        total += item.unit_price * item.quantity
    return total.quantize(MONEY, rounding=ROUND_HALF_UP)


def _category_total(items: list[OrderItem], category: str) -> Decimal:
    total = Decimal("0.00")
    for item in items:
        if item.category == category:
            total += item.unit_price * item.quantity
    return total.quantize(MONEY, rounding=ROUND_HALF_UP)


def _apply_coupon(items: list[OrderItem], subtotal_after_member: Decimal, coupon: Coupon | None) -> Decimal:
    if coupon is None:
        return Decimal("0.00")
    if coupon.discount < 0:
        raise ValueError("coupon discount must not be negative")
    if coupon.threshold < 0:
        raise ValueError("coupon threshold must not be negative")

    eligible_total = subtotal_after_member
    if coupon.category is not None:
        eligible_total = _category_total(items, coupon.category)

    if eligible_total >= coupon.threshold:
        return min(coupon.discount, subtotal_after_member).quantize(MONEY, rounding=ROUND_HALF_UP)
    return Decimal("0.00")


def _shipping_fee(subtotal_after_discount: Decimal) -> Decimal:
    if subtotal_after_discount <= 0:
        return Decimal("0.00")
    if subtotal_after_discount >= Decimal("200.00"):
        return Decimal("0.00")
    if subtotal_after_discount >= Decimal("100.00"):
        return Decimal("6.00")
    return Decimal("12.00")


def calculate_order_total(
    items: Iterable[OrderItem],
    customer: Customer,
    coupon: Coupon | None = None,
) -> dict[str, Decimal]:
    checked_items = _validate_items(items)
    subtotal = _subtotal(checked_items)

    member_discount = (subtotal * _member_discount_rate(customer)).quantize(MONEY, rounding=ROUND_HALF_UP)
    if customer.is_new:
        member_discount = (member_discount + Decimal("5.00")).quantize(MONEY, rounding=ROUND_HALF_UP)
    member_discount = min(member_discount, subtotal)

    after_member = (subtotal - member_discount).quantize(MONEY, rounding=ROUND_HALF_UP)
    coupon_discount = _apply_coupon(checked_items, after_member, coupon)
    after_discount = (after_member - coupon_discount).quantize(MONEY, rounding=ROUND_HALF_UP)
    shipping = _shipping_fee(after_discount)
    payable = (after_discount + shipping).quantize(MONEY, rounding=ROUND_HALF_UP)

    return {
        "subtotal": subtotal,
        "member_discount": member_discount,
        "coupon_discount": coupon_discount,
        "shipping": shipping,
        "payable": payable,
    }
