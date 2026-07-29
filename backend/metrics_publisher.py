from __future__ import annotations

import logging
import os
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Tuple

import boto3
from botocore.exceptions import ClientError

# CloudWatch custom metric
NAMESPACE = "CloudCostCalculator"
METRIC_NAME = "MTDSpendUSD"

# Cost Explorer endpoint: safest as us-east-1
CE_REGION = "us-east-1"

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _stage_name() -> str:
    return (os.environ.get("STAGE_NAME") or "prod").strip()


def _month_range_utc() -> Tuple[str, str]:
    """
    Cost Explorer End is EXCLUSIVE.
    We'll query from the 1st of the month through "tomorrow" (exclusive) so MTD is included.
    """
    today = _now_utc().date()
    start = date(today.year, today.month, 1)
    end = today + timedelta(days=1)  # exclusive end
    return start.isoformat(), end.isoformat()


def _get_mtd_cost_usd() -> float:
    ce = boto3.client("ce", region_name=CE_REGION)
    start, end = _month_range_utc()

    logger.info("Querying Cost Explorer MTD: start=%s end=%s (exclusive)", start, end)

    resp = ce.get_cost_and_usage(
        TimePeriod={"Start": start, "End": end},
        Granularity="MONTHLY",
        Metrics=["UnblendedCost"],
    )

    results = resp.get("ResultsByTime", [])
    if not results:
        return 0.0

    amt = results[0].get("Total", {}).get("UnblendedCost", {}).get("Amount", "0") or "0"
    try:
        return float(amt)
    except Exception:
        logger.warning("Could not parse CE amount as float: %r", amt)
        return 0.0


def _publish_metric(stage: str, value: float) -> None:
    cw = boto3.client("cloudwatch")

    cw.put_metric_data(
        Namespace=NAMESPACE,
        MetricData=[
            {
                "MetricName": METRIC_NAME,
                "Dimensions": [{"Name": "Stage", "Value": stage}],
                "Timestamp": _now_utc(),
                "Value": value,
                "Unit": "None",
            }
        ],
    )


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    stage = _stage_name()

    try:
        total = _get_mtd_cost_usd()
    except ClientError as e:
        # Don't crash scheduled runs—return useful debug info.
        logger.exception("Cost Explorer error: %s", e)
        total = 0.0
        return {
            "ok": False,
            "where": "cost_explorer",
            "stage": stage,
            "error": str(e),
        }
    except Exception as e:
        logger.exception("Unexpected error getting MTD cost: %s", e)
        total = 0.0
        return {
            "ok": False,
            "where": "unknown",
            "stage": stage,
            "error": str(e),
        }

    try:
        _publish_metric(stage, total)
    except ClientError as e:
        logger.exception("CloudWatch PutMetricData error: %s", e)
        return {
            "ok": False,
            "where": "cloudwatch_put_metric_data",
            "stage": stage,
            "value": total,
            "error": str(e),
        }
    except Exception as e:
        logger.exception("Unexpected error publishing metric: %s", e)
        return {
            "ok": False,
            "where": "cloudwatch_unknown",
            "stage": stage,
            "value": total,
            "error": str(e),
        }

    logger.info("Published metric %s/%s stage=%s value=%.4f", NAMESPACE, METRIC_NAME, stage, total)
    return {"ok": True, "metric": f"{NAMESPACE}/{METRIC_NAME}", "stage": stage, "value": total}
