from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Tuple

import boto3
from botocore.exceptions import ClientError

MAPPING_KEY = "mapping.json"
CE_REGION = "us-east-1"

logger = logging.getLogger()
logger.setLevel(logging.INFO)

DEFAULT_MAPPING = {
    "Amazon Simple Storage Service": "File storage",
    "AWS Lambda": "Serverless",
    "Amazon Elastic Compute Cloud - Compute": "Compute",
    "Amazon CloudFront": "CDN",
    "AWS CloudFormation": "Infrastructure",
    "AWS Key Management Service": "Security",
    "Tax": "Taxes & fees",
}


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _bucket_name() -> str:
    return (os.environ.get("DASHBOARD_BUCKET_NAME") or "").strip()


def _topic_arn() -> str:
    return (os.environ.get("ALERT_TOPIC_ARN") or "").strip()


def _stage_name() -> str:
    return (os.environ.get("STAGE_NAME") or "prod").strip()


def load_mapping(s3, bucket: str) -> Dict[str, str]:
    """
    Loads mapping.json from your dashboard bucket, merges it over DEFAULT_MAPPING.
    If missing, falls back to DEFAULT_MAPPING.
    """
    if not bucket:
        return dict(DEFAULT_MAPPING)

    try:
        obj = s3.get_object(Bucket=bucket, Key=MAPPING_KEY)
        raw = obj["Body"].read().decode("utf-8")
        data = json.loads(raw)

        if isinstance(data, dict):
            cleaned = {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, str)}
            return {**DEFAULT_MAPPING, **cleaned}
    except ClientError as e:
        # Most common is NoSuchKey (mapping file not created yet)
        logger.info("Mapping not found or not accessible (%s). Using defaults.", str(e))
        return dict(DEFAULT_MAPPING)
    except Exception as e:
        logger.warning("Mapping parse error (%s). Using defaults.", str(e))
        return dict(DEFAULT_MAPPING)

    return dict(DEFAULT_MAPPING)


def label_for(service: str, mapping: Dict[str, str]) -> str:
    if service in mapping:
        return mapping[service]

    s = service.lower()
    if "storage" in s or "s3" in s or "glacier" in s or "efs" in s:
        return "File storage"
    if "lambda" in s:
        return "Serverless"
    if "ec2" in s or "compute" in s:
        return "Compute"
    if "cloudfront" in s:
        return "CDN"
    if "tax" in s:
        return "Taxes & fees"
    return "Other"


def week_windows() -> Tuple[Tuple[date, date], Tuple[date, date]]:
    """
    Returns two 7-day windows:
      current: last 7 days including today (end exclusive = tomorrow)
      previous: the 7 days before that
    """
    today = _now_utc().date()

    curr_end = today + timedelta(days=1)      # exclusive
    curr_start = today - timedelta(days=6)    # inclusive => 7 days

    prev_end = curr_start                      # exclusive
    prev_start = prev_end - timedelta(days=7)

    return (curr_start, curr_end), (prev_start, prev_end)


def cost_by_service(ce, start: date, end: date) -> Dict[str, float]:
    """
    Aggregates UnblendedCost by SERVICE across the date window.
    """
    resp = ce.get_cost_and_usage(
        TimePeriod={"Start": start.isoformat(), "End": end.isoformat()},
        Granularity="DAILY",
        Metrics=["UnblendedCost"],
        GroupBy=[{"Type": "DIMENSION", "Key": "SERVICE"}],
    )

    agg: Dict[str, float] = {}
    for r in resp.get("ResultsByTime", []):
        for g in r.get("Groups", []):
            name = (g.get("Keys") or ["Unknown"])[0]
            amt = g.get("Metrics", {}).get("UnblendedCost", {}).get("Amount", "0") or "0"
            try:
                agg[name] = agg.get(name, 0.0) + float(amt)
            except Exception:
                pass

    return agg


def fmt_money(x: float) -> str:
    return f"${x:,.2f}"


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    ce = boto3.client("ce", region_name=CE_REGION)
    sns = boto3.client("sns")
    s3 = boto3.client("s3")

    bucket = _bucket_name()
    topic = _topic_arn()
    stage = _stage_name()

    mapping = load_mapping(s3, bucket)

    (cs, ce_end), (ps, pe_end) = week_windows()

    try:
        curr = cost_by_service(ce, cs, ce_end)
        prev = cost_by_service(ce, ps, pe_end)
    except ClientError as e:
        logger.exception("Cost Explorer error in weekly_report: %s", e)

        # If topic is set, send a failure report so you still get visibility.
        if topic:
            sns.publish(
                TopicArn=topic,
                Subject=f"[CloudCostCalculator:{stage}] Weekly Report FAILED",
                Message=(
                    "Weekly report failed while querying Cost Explorer.\n\n"
                    f"Stage: {stage}\n"
                    f"Error: {str(e)}\n"
                ),
            )

        return {"ok": False, "sent": bool(topic), "stage": stage, "error": str(e)}

    curr_total = sum(curr.values())
    prev_total = sum(prev.values())
    delta = curr_total - prev_total
    pct = (delta / prev_total * 100.0) if prev_total > 0 else 0.0

    top_curr = sorted(curr.items(), key=lambda x: x[1], reverse=True)[:5]

    all_services = set(curr.keys()) | set(prev.keys())
    deltas = [(svc, curr.get(svc, 0.0) - prev.get(svc, 0.0)) for svc in all_services]
    top_spikes = sorted(deltas, key=lambda x: x[1], reverse=True)[:5]

    # Category rollup
    cat: Dict[str, float] = {}
    for svc, cost in curr.items():
        cat_name = label_for(svc, mapping)
        cat[cat_name] = cat.get(cat_name, 0.0) + cost
    top_cats = sorted(cat.items(), key=lambda x: x[1], reverse=True)[:5]

    subject = f"[CloudCostCalculator:{stage}] Weekly AWS Cost Report"

    lines = []
    lines.append(f"Weekly report ({ps} → {pe_end - timedelta(days=1)}) vs ({cs} → {ce_end - timedelta(days=1)})")
    lines.append("")
    lines.append(f"Total (this week): {fmt_money(curr_total)}")
    lines.append(f"Total (last week): {fmt_money(prev_total)}")
    lines.append(f"Change: {fmt_money(delta)} ({pct:+.1f}%)")
    lines.append("")
    lines.append("Top services (this week):")
    for svc, cost in top_curr:
        lines.append(f"  - {svc}: {fmt_money(cost)}")
    lines.append("")
    lines.append("Biggest increases (WoW):")
    added_any = False
    for svc, d in top_spikes:
        if d <= 0:
            continue
        added_any = True
        lines.append(f"  - {svc}: +{fmt_money(d)}")
    if not added_any:
        lines.append("  - No positive spikes detected.")
    lines.append("")
    lines.append("Top business categories (this week):")
    for c, cost in top_cats:
        lines.append(f"  - {c}: {fmt_money(cost)}")

    msg = "\n".join(lines)

    sent = False
    if topic:
        try:
            sns.publish(TopicArn=topic, Subject=subject, Message=msg)
            sent = True
        except ClientError as e:
            logger.exception("SNS publish failed: %s", e)
            return {"ok": False, "sent": False, "stage": stage, "error": str(e)}

    return {
        "ok": True,
        "sent": sent,
        "stage": stage,
        "current_total": curr_total,
        "previous_total": prev_total,
        "delta": delta,
        "pct": pct,
    }
