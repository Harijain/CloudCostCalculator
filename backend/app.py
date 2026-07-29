from __future__ import annotations

import json
import os
from datetime import datetime, timezone, timedelta, date
from typing import Any, Dict, List, Tuple

import boto3
from botocore.exceptions import ClientError


# -------------------------
# Response helpers (CORS)
# -------------------------
CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "*",
    "Access-Control-Allow-Methods": "GET,PUT,OPTIONS",
    "Content-Type": "application/json",
}


def _resp(status: int, body: Any) -> Dict[str, Any]:
    return {
        "statusCode": status,
        "headers": CORS_HEADERS,
        "body": json.dumps(body, default=str),
    }


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


# -------------------------
# Mapping (Upgrade C)
# -------------------------
MAPPING_KEY = "mapping.json"

DEFAULT_MAPPING: Dict[str, str] = {
    # Storage / data
    "Amazon Simple Storage Service": "File storage",
    "Amazon Elastic File System": "File storage",
    "Amazon Glacier": "File storage",
    "Amazon DynamoDB": "Database",
    "Amazon Relational Database Service": "Database",
    # Compute / serverless
    "Amazon Elastic Compute Cloud - Compute": "Compute",
    "AWS Lambda": "Serverless",
    "Amazon Elastic Container Service": "Containers",
    "Amazon Elastic Kubernetes Service": "Containers",
    # Network / edge
    "Amazon CloudFront": "CDN",
    "Amazon Route 53": "DNS",
    "Elastic Load Balancing": "Networking",
    # Ops / misc
    "AWS CloudFormation": "Infrastructure",
    "AWS Key Management Service": "Security",
    "AWS Glue": "Data pipelines",
    "Tax": "Taxes & fees",
}


def _bucket_name() -> str:
    bn = os.environ.get("DASHBOARD_BUCKET_NAME", "").strip()
    if not bn:
        raise RuntimeError("Missing env var DASHBOARD_BUCKET_NAME")
    return bn


def _admin_token() -> str:
    return (os.environ.get("ADMIN_TOKEN") or "").strip()


def _stage_name() -> str:
    return (os.environ.get("STAGE_NAME") or "prod").strip()


def load_mapping(s3, bucket: str) -> Dict[str, str]:
    """
    Loads mapping.json from the dashboard S3 bucket.
    If missing, returns DEFAULT_MAPPING (and does NOT require pre-upload).
    """
    try:
        obj = s3.get_object(Bucket=bucket, Key=MAPPING_KEY)
        raw = obj["Body"].read().decode("utf-8")
        data = json.loads(raw)
        if isinstance(data, dict):
            # Ensure all values are strings
            cleaned = {}
            for k, v in data.items():
                if isinstance(k, str) and isinstance(v, str):
                    cleaned[k] = v
            # Merge defaults as fallback
            merged = {**DEFAULT_MAPPING, **cleaned}
            return merged
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code in ("NoSuchKey", "NoSuchBucket", "404"):
            return dict(DEFAULT_MAPPING)
        # Anything else is real failure
        raise

    return dict(DEFAULT_MAPPING)


def save_mapping(s3, bucket: str, mapping: Dict[str, str]) -> None:
    """
    Writes mapping.json to S3.
    """
    body = json.dumps(mapping, indent=2, sort_keys=True).encode("utf-8")
    s3.put_object(
        Bucket=bucket,
        Key=MAPPING_KEY,
        Body=body,
        ContentType="application/json",
        CacheControl="no-store",
    )


def label_for(service_name: str, mapping: Dict[str, str]) -> str:
    """
    Get business-friendly label for a raw AWS service name.
    Uses stored mapping first, then heuristics, then 'Other'.
    """
    if service_name in mapping:
        return mapping[service_name]

    s = service_name.lower()

    # Heuristics (nice fallback even before you set mapping.json)
    if "storage" in s or "s3" in s or "glacier" in s or "efs" in s:
        return "File storage"
    if "lambda" in s:
        return "Serverless"
    if "cloudfront" in s:
        return "CDN"
    if "ec2" in s or "compute" in s:
        return "Compute"
    if "rds" in s or "database" in s or "dynamodb" in s:
        return "Database"
    if "vpc" in s or "load balanc" in s or "route 53" in s:
        return "Networking"
    if "kms" in s or "key management" in s:
        return "Security"
    if "cloudformation" in s:
        return "Infrastructure"
    if "tax" in s:
        return "Taxes & fees"

    return "Other"


# -------------------------
# Cost Explorer helpers
# -------------------------
def _month_range_utc() -> Tuple[str, str, str]:
    """
    Returns (start_yyyy_mm_dd, end_exclusive_yyyy_mm_dd, period_yyyy_mm)
    Cost Explorer end date is EXCLUSIVE.
    """
    now = _now_utc().date()
    start = date(now.year, now.month, 1)
    end = now + timedelta(days=1)  # exclusive
    period = f"{start.year:04d}-{start.month:02d}"
    return start.isoformat(), end.isoformat(), period


def _sum_groups(groups: List[Dict[str, Any]]) -> List[Tuple[str, float]]:
    out: List[Tuple[str, float]] = []
    for g in groups:
        name = (g.get("Keys") or ["Unknown"])[0]
        amt = g.get("Metrics", {}).get("UnblendedCost", {}).get("Amount", "0")
        try:
            val = float(amt)
        except Exception:
            val = 0.0
        out.append((name, val))
    return out


def get_month_to_date_by_service(ce) -> Tuple[str, str, List[Tuple[str, float]]]:
    start, end, period = _month_range_utc()
    resp = ce.get_cost_and_usage(
        TimePeriod={"Start": start, "End": end},
        Granularity="MONTHLY",
        Metrics=["UnblendedCost"],
        GroupBy=[{"Type": "DIMENSION", "Key": "SERVICE"}],
    )

    results = resp.get("ResultsByTime", [])
    if not results:
        return period, "USD", []

    groups = results[0].get("Groups", [])
    items = _sum_groups(groups)

    # Sort by spend desc
    items.sort(key=lambda x: x[1], reverse=True)

    # Currency
    unit = results[0].get("Total", {}).get("UnblendedCost", {}).get("Unit", "USD")
    return period, unit, items


def get_week_over_week_totals(ce) -> Dict[str, float]:
    """
    Returns:
      {
        "currentWeek": <sum last 7 days>,
        "previousWeek": <sum prior 7 days>
      }
    """
    today = _now_utc().date()
    end_curr = today + timedelta(days=1)  # exclusive
    start_curr = today - timedelta(days=6)  # inclusive: 7 days window
    end_prev = start_curr  # exclusive
    start_prev = start_curr - timedelta(days=7)

    def sum_range(start_d: date, end_d: date) -> float:
        resp = ce.get_cost_and_usage(
            TimePeriod={"Start": start_d.isoformat(), "End": end_d.isoformat()},
            Granularity="DAILY",
            Metrics=["UnblendedCost"],
        )
        total = 0.0
        for r in resp.get("ResultsByTime", []):
            amt = r.get("Total", {}).get("UnblendedCost", {}).get("Amount", "0")
            try:
                total += float(amt)
            except Exception:
                pass
        return total

    return {
        "currentWeek": round(sum_range(start_curr, end_curr), 4),
        "previousWeek": round(sum_range(start_prev, end_prev), 4),
    }


# -------------------------
# Main Lambda entry
# -------------------------
def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    # Preflight
    method = (
        event.get("requestContext", {})
        .get("http", {})
        .get("method", event.get("httpMethod", "GET"))
    )
    if method == "OPTIONS": 
        return _resp(200, {"ok": True})

    path = event.get("rawPath") or (
        event.get("requestContext", {}).get("http", {}).get("path") or "/"
    )

    s3 = boto3.client("s3")
    ce = boto3.client("ce")  # Cost Explorer is effectively global

    bucket = _bucket_name()
    stage = _stage_name()
    stage_prefix = f"/{stage}"
    if stage and path.startswith(stage_prefix):
        path = path[len(stage_prefix):] or "/"

    # -------- /health
    if method == "GET" and path == "/health":
        return _resp(200, {"ok": True, "service": "CloudCostCalculator", "path": "/health", "stage": stage})

    # -------- /mapping (GET/PUT)
    if path == "/mapping":
        if method == "GET":
            mapping = load_mapping(s3, bucket)
            return _resp(
                200,
                {
                    "ok": True,
                    "bucket": bucket,
                    "key": MAPPING_KEY,
                    "mapping": mapping,
                },
            )

        if method == "PUT":
            # Simple protection (enough for a personal project)
            expected = _admin_token()
            provided = (event.get("headers", {}) or {}).get("x-admin-token") or (event.get("headers", {}) or {}).get("X-Admin-Token") or ""
            if expected and provided.strip() != expected:
                return _resp(401, {"ok": False, "error": "Unauthorized. Missing/invalid x-admin-token."})

            raw_body = event.get("body") or ""
            try:
                payload = json.loads(raw_body) if raw_body else {}
            except Exception:
                return _resp(400, {"ok": False, "error": "Invalid JSON body."})

            # Accept either {"mapping": {...}} or {...}
            mapping_obj = payload.get("mapping") if isinstance(payload, dict) and "mapping" in payload else payload
            if not isinstance(mapping_obj, dict):
                return _resp(400, {"ok": False, "error": "Body must be a JSON object (or {mapping:{...}})."})

            cleaned: Dict[str, str] = {}
            for k, v in mapping_obj.items():
                if isinstance(k, str) and isinstance(v, str) and k.strip() and v.strip():
                    cleaned[k.strip()] = v.strip()

            # Merge defaults so you never lose baseline categories
            merged = {**DEFAULT_MAPPING, **cleaned}
            save_mapping(s3, bucket, merged)
            return _resp(200, {"ok": True, "saved": True, "count": len(merged)})

        return _resp(405, {"ok": False, "error": "Method not allowed"})

    # -------- /snapshot
    if method == "GET" and path == "/snapshot":
        mapping = load_mapping(s3, bucket)

        period, currency, by_service = get_month_to_date_by_service(ce)
        wow = get_week_over_week_totals(ce)

        services: List[Dict[str, Any]] = []
        for name, cost in by_service:
            services.append(
                {
                    "service": name,
                    "businessLabel": label_for(name, mapping),
                    "cost": round(cost, 6),
                    "usage": None,
                    "unit": "",
                }
            )

        return _resp(
            200,
            {
                "period": period,
                "currency": currency,
                "services": services,
                "weekOverWeek": wow,
            },
        )

    return _resp(404, {"ok": False, "error": "Not found", "available": ["/health", "/snapshot", "/mapping"]})
