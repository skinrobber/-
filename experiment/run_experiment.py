from __future__ import annotations

import csv
import importlib.util
import re
import shutil
import sys
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path


ROOT = Path(__file__).resolve().parent
TARGET = ROOT / "order_pricing.py"
DATASET = ROOT / "data" / "test_dataset.csv"
RESULT_DIR = ROOT / "result"
MONEY = Decimal("0.01")


FAULTS = [
    {
        "id": "category_coupon_ignores_category",
        "name": "category coupon uses whole-order amount",
        "replacements": [
            ("eligible_total = _category_total(items, coupon.category)", "eligible_total = subtotal_after_member"),
        ],
    },
    {
        "id": "coupon_threshold_strict",
        "name": "coupon threshold uses > instead of >=",
        "replacements": [
            ("if eligible_total >= coupon.threshold:", "if eligible_total > coupon.threshold:"),
        ],
    },
    {
        "id": "discount_order_before_member",
        "name": "coupon threshold is checked before member discount",
        "replacements": [
            ("eligible_total = subtotal_after_member", "eligible_total = _subtotal(items)"),
        ],
    },
    {
        "id": "negative_quantity_allowed",
        "name": "zero quantity is not rejected",
        "replacements": [
            ("if item.quantity <= 0:", "if item.quantity < 0:"),
        ],
    },
    {
        "id": "new_customer_not_capped",
        "name": "new customer discount is not capped",
        "replacements": [
            ("member_discount = min(member_discount, subtotal)", "# faulty version: missing cap"),
        ],
    },
    {
        "id": "rounding_down",
        "name": "money is rounded down instead of half-up",
        "replacements": [
            ("from decimal import Decimal, ROUND_HALF_UP", "from decimal import Decimal, ROUND_DOWN"),
            ("rounding=ROUND_HALF_UP", "rounding=ROUND_DOWN"),
        ],
    },
    {
        "id": "shipping_100_boundary",
        "name": "100-yuan shipping boundary uses > instead of >=",
        "replacements": [
            ('if subtotal_after_discount >= Decimal("100.00"):', 'if subtotal_after_discount > Decimal("100.00"):'),
        ],
    },
    {
        "id": "shipping_200_boundary",
        "name": "200-yuan free-shipping boundary uses > instead of >=",
        "replacements": [
            ('if subtotal_after_discount >= Decimal("200.00"):', 'if subtotal_after_discount > Decimal("200.00"):'),
        ],
    },
]


@dataclass(frozen=True)
class ItemData:
    sku: str
    unit_price: Decimal
    quantity: int
    category: str


@dataclass(frozen=True)
class CaseData:
    case_id: str
    suite: str
    level: str
    is_new: bool
    items: list[ItemData]
    coupon_threshold: Decimal | None
    coupon_discount: Decimal | None
    coupon_category: str | None
    expect_error: bool
    expected_subtotal: Decimal | None
    expected_member_discount: Decimal | None
    expected_coupon_discount: Decimal | None
    expected_shipping: Decimal | None
    expected_payable: Decimal | None


@dataclass(frozen=True)
class SuiteResult:
    suite: str
    tests: int
    statement_coverage: float
    branch_coverage: float


@dataclass(frozen=True)
class FaultResult:
    fault_id: str
    fault_name: str
    baseline_detected: bool
    enhanced_detected: bool


def q(value: Decimal | str | int) -> Decimal:
    return Decimal(str(value)).quantize(MONEY, rounding=ROUND_HALF_UP)


def parse_items(raw: str) -> list[ItemData]:
    if raw == "NONE":
        return []
    items: list[ItemData] = []
    for part in raw.split("|"):
        sku, price, quantity, category = part.split(":")
        items.append(ItemData(sku, Decimal(price), int(quantity), category))
    return items


def read_cases() -> list[CaseData]:
    with DATASET.open("r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))

    cases: list[CaseData] = []
    for row in rows:
        coupon_threshold = Decimal(row["coupon_threshold"]) if row["coupon_threshold"] else None
        coupon_discount = Decimal(row["coupon_discount"]) if row["coupon_discount"] else None
        expected_subtotal = Decimal(row["expected_subtotal"]) if row["expected_subtotal"] else None
        expected_member_discount = Decimal(row["expected_member_discount"]) if row["expected_member_discount"] else None
        expected_coupon_discount = Decimal(row["expected_coupon_discount"]) if row["expected_coupon_discount"] else None
        expected_shipping = Decimal(row["expected_shipping"]) if row["expected_shipping"] else None
        expected_payable = Decimal(row["expected_payable"]) if row["expected_payable"] else None
        cases.append(
            CaseData(
                case_id=row["case_id"],
                suite=row["suite"],
                level=row["level"],
                is_new=row["is_new"] == "1",
                items=parse_items(row["items"]),
                coupon_threshold=coupon_threshold,
                coupon_discount=coupon_discount,
                coupon_category=row["coupon_category"] or None,
                expect_error=row["expect_error"] == "1",
                expected_subtotal=expected_subtotal,
                expected_member_discount=expected_member_discount,
                expected_coupon_discount=expected_coupon_discount,
                expected_shipping=expected_shipping,
                expected_payable=expected_payable,
            )
        )
    return cases


def load_target() -> object:
    spec = importlib.util.spec_from_file_location("order_pricing_runtime", TARGET)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {TARGET}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def member_rate(level: str) -> Decimal:
    rates = {
        "standard": Decimal("0.00"),
        "silver": Decimal("0.05"),
        "gold": Decimal("0.10"),
        "platinum": Decimal("0.15"),
    }
    if level not in rates:
        raise ValueError(f"unknown customer level: {level}")
    return rates[level]


def oracle(case: CaseData) -> dict[str, Decimal]:
    if not case.items:
        raise ValueError("empty order")
    for item in case.items:
        if not item.sku:
            raise ValueError("empty sku")
        if item.unit_price < 0:
            raise ValueError("negative price")
        if item.quantity <= 0:
            raise ValueError("invalid quantity")

    subtotal = q(sum((item.unit_price * item.quantity for item in case.items), Decimal("0.00")))
    member_discount = q(subtotal * member_rate(case.level))
    if case.is_new:
        member_discount = q(member_discount + Decimal("5.00"))
    member_discount = min(member_discount, subtotal)
    after_member = q(subtotal - member_discount)

    coupon_discount = Decimal("0.00")
    if case.coupon_threshold is not None or case.coupon_discount is not None:
        if case.coupon_threshold is None or case.coupon_discount is None:
            raise ValueError("incomplete coupon")
        if case.coupon_threshold < 0 or case.coupon_discount < 0:
            raise ValueError("invalid coupon")
        eligible = after_member
        if case.coupon_category is not None:
            eligible = q(
                sum(
                    (item.unit_price * item.quantity for item in case.items if item.category == case.coupon_category),
                    Decimal("0.00"),
                )
            )
        if eligible >= case.coupon_threshold:
            coupon_discount = min(case.coupon_discount, after_member)

    after_discount = q(after_member - coupon_discount)
    if after_discount <= 0:
        shipping = Decimal("0.00")
    elif after_discount >= Decimal("200.00"):
        shipping = Decimal("0.00")
    elif after_discount >= Decimal("100.00"):
        shipping = Decimal("6.00")
    else:
        shipping = Decimal("12.00")
    payable = q(after_discount + shipping)
    return {
        "subtotal": subtotal,
        "member_discount": member_discount,
        "coupon_discount": q(coupon_discount),
        "shipping": shipping,
        "payable": payable,
    }


def run_case(module: object, case: CaseData) -> None:
    items = [module.OrderItem(item.sku, item.unit_price, item.quantity, item.category) for item in case.items]
    coupon = None
    if case.coupon_threshold is not None or case.coupon_discount is not None:
        coupon = module.Coupon(
            "C",
            case.coupon_threshold if case.coupon_threshold is not None else Decimal("0.00"),
            case.coupon_discount if case.coupon_discount is not None else Decimal("0.00"),
            case.coupon_category,
        )

    if case.expect_error:
        try:
            module.calculate_order_total(items, module.Customer(case.level, case.is_new), coupon)
        except ValueError:
            return
        raise AssertionError(f"{case.case_id} expected ValueError")

    oracle_expected = oracle(case)
    csv_expected = {
        "subtotal": case.expected_subtotal,
        "member_discount": case.expected_member_discount,
        "coupon_discount": case.expected_coupon_discount,
        "shipping": case.expected_shipping,
        "payable": case.expected_payable,
    }
    if any(value is None for value in csv_expected.values()):
        raise AssertionError(f"{case.case_id}: expected result columns must not be empty")
    expected = {key: q(value) for key, value in csv_expected.items()}
    if expected != oracle_expected:
        raise AssertionError(f"{case.case_id}: dataset expectation disagrees with oracle: {expected} != {oracle_expected}")
    actual = module.calculate_order_total(items, module.Customer(case.level, case.is_new), coupon)
    if actual != expected:
        raise AssertionError(f"{case.case_id}: expected {expected}, got {actual}")


def run_suite(cases: list[CaseData]) -> None:
    module = load_target()
    for case in cases:
        run_case(module, case)


def coverage_for_suite(name: str, cases: list[CaseData]) -> SuiteResult:
    try:
        import coverage
    except ImportError as exc:
        raise RuntimeError("please install dependencies: py -m pip install coverage matplotlib") from exc

    RESULT_DIR.mkdir(exist_ok=True)
    data_file = RESULT_DIR / f".coverage.{name}"
    xml_file = RESULT_DIR / f"coverage_{name}.xml"
    cov = coverage.Coverage(branch=True, include=[str(TARGET)], data_file=str(data_file))
    cov.start()
    run_suite(cases)
    cov.stop()
    cov.save()
    cov.xml_report(outfile=str(xml_file))

    text = xml_file.read_text(encoding="utf-8")
    statement = float(re.search(r'line-rate="([0-9.]+)"', text).group(1)) * 100
    branch = float(re.search(r'branch-rate="([0-9.]+)"', text).group(1)) * 100
    data_file.unlink(missing_ok=True)
    xml_file.unlink(missing_ok=True)
    return SuiteResult(name, len(cases), round(statement, 2), round(branch, 2))


def apply_fault(source: str, replacements: list[tuple[str, str]]) -> str:
    mutated = source
    for old, new in replacements:
        if old not in mutated:
            raise RuntimeError(f"fault replacement target not found: {old}")
        mutated = mutated.replace(old, new, 1)
    return mutated


def suite_detects_fault(cases: list[CaseData]) -> bool:
    try:
        run_suite(cases)
    except Exception:
        return True
    return False


def fault_results(baseline_cases: list[CaseData], enhanced_cases: list[CaseData]) -> list[FaultResult]:
    original = TARGET.read_text(encoding="utf-8")
    results: list[FaultResult] = []
    try:
        for fault in FAULTS:
            TARGET.write_text(apply_fault(original, fault["replacements"]), encoding="utf-8")
            results.append(
                FaultResult(
                    fault_id=fault["id"],
                    fault_name=fault["name"],
                    baseline_detected=suite_detects_fault(baseline_cases),
                    enhanced_detected=suite_detects_fault(enhanced_cases),
                )
            )
    finally:
        TARGET.write_text(original, encoding="utf-8")
        for cache in ROOT.rglob("__pycache__"):
            shutil.rmtree(cache, ignore_errors=True)
    return results


def write_csv(suite_results: list[SuiteResult], faults: list[FaultResult]) -> None:
    with (RESULT_DIR / "coverage_summary.csv").open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["suite", "tests", "statement_coverage", "branch_coverage"])
        for row in suite_results:
            writer.writerow([row.suite, row.tests, row.statement_coverage, row.branch_coverage])

    with (RESULT_DIR / "defect_detection.csv").open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["fault_id", "fault_name", "baseline_detected", "enhanced_detected"])
        for row in faults:
            writer.writerow([row.fault_id, row.fault_name, row.baseline_detected, row.enhanced_detected])

    baseline_detected = sum(1 for row in faults if row.baseline_detected)
    enhanced_detected = sum(1 for row in faults if row.enhanced_detected)
    total = len(faults)
    with (RESULT_DIR / "results.csv").open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["metric", "baseline", "enhanced"])
        writer.writerow(["test_cases", suite_results[0].tests, suite_results[1].tests])
        writer.writerow(["statement_coverage_percent", suite_results[0].statement_coverage, suite_results[1].statement_coverage])
        writer.writerow(["branch_coverage_percent", suite_results[0].branch_coverage, suite_results[1].branch_coverage])
        writer.writerow(["detected_faults", baseline_detected, enhanced_detected])
        writer.writerow(["total_faults", total, total])
        writer.writerow(
            [
                "defect_detection_rate_percent",
                round(baseline_detected / total * 100, 2),
                round(enhanced_detected / total * 100, 2),
            ]
        )


def draw_figures(suite_results: list[SuiteResult], faults: list[FaultResult]) -> None:
    try:
        import matplotlib.pyplot as plt
    except ImportError as exc:
        raise RuntimeError("please install dependencies: py -m pip install coverage matplotlib") from exc

    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "Arial Unicode MS", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False

    labels = ["statement coverage", "branch coverage"]
    baseline_values = [suite_results[0].statement_coverage, suite_results[0].branch_coverage]
    enhanced_values = [suite_results[1].statement_coverage, suite_results[1].branch_coverage]
    x = range(len(labels))

    fig, ax = plt.subplots(figsize=(7, 4.2), dpi=160)
    ax.bar([i - 0.18 for i in x], baseline_values, width=0.36, label="baseline", color="#6C8EBF")
    ax.bar([i + 0.18 for i in x], enhanced_values, width=0.36, label="enhanced", color="#82B366")
    ax.set_xticks(list(x), labels)
    ax.set_ylim(0, 110)
    ax.set_ylabel("coverage (%)")
    ax.set_title("Coverage comparison")
    ax.legend()
    ax.grid(axis="y", linestyle="--", alpha=0.35)
    for i, value in enumerate(baseline_values):
        ax.text(i - 0.18, value + 2, f"{value:.1f}", ha="center", fontsize=9)
    for i, value in enumerate(enhanced_values):
        ax.text(i + 0.18, value + 2, f"{value:.1f}", ha="center", fontsize=9)
    fig.tight_layout()
    fig.savefig(RESULT_DIR / "coverage_comparison.png")
    plt.close(fig)

    baseline_detected = sum(1 for row in faults if row.baseline_detected)
    enhanced_detected = sum(1 for row in faults if row.enhanced_detected)
    total = len(faults)
    fig, ax = plt.subplots(figsize=(7, 4.2), dpi=160)
    bars = ax.bar(["baseline", "enhanced"], [baseline_detected, enhanced_detected], color=["#D79B00", "#67AB9F"])
    ax.set_ylim(0, total + 1)
    ax.set_ylabel("detected faults")
    ax.set_title(f"Fault detection comparison ({total} faulty versions)")
    ax.grid(axis="y", linestyle="--", alpha=0.35)
    for bar in bars:
        value = bar.get_height()
        ax.text(bar.get_x() + bar.get_width() / 2, value + 0.15, f"{int(value)}", ha="center", fontsize=10)
    fig.tight_layout()
    fig.savefig(RESULT_DIR / "defect_detection.png")
    plt.close(fig)


def main() -> None:
    if not DATASET.exists():
        raise FileNotFoundError(f"dataset not found: {DATASET}")
    cases = read_cases()
    baseline_cases = [case for case in cases if case.suite in {"baseline", "both"}]
    enhanced_cases = [case for case in cases if case.suite in {"enhanced", "both"}]

    RESULT_DIR.mkdir(exist_ok=True)
    suite_results = [
        coverage_for_suite("baseline", baseline_cases),
        coverage_for_suite("enhanced", enhanced_cases),
    ]
    faults = fault_results(baseline_cases, enhanced_cases)
    write_csv(suite_results, faults)
    draw_figures(suite_results, faults)

    print("Coverage summary")
    for row in suite_results:
        print(row)
    print("\nFault detection")
    for row in faults:
        print(row)
    print(f"\nDataset cases: {len(cases)}")
    print(f"Results written to: {RESULT_DIR}")


if __name__ == "__main__":
    main()
