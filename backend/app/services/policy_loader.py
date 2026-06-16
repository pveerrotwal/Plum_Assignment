"""Load and query policy configuration from policy_terms.json."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

from dateutil import parser as date_parser


POLICY_PATH = Path(__file__).resolve().parents[3] / "data" / "policy_terms.json"


@lru_cache(maxsize=1)
def load_policy() -> dict[str, Any]:
    with open(POLICY_PATH, encoding="utf-8") as f:
        return json.load(f)


def get_member(member_id: str) -> Optional[dict[str, Any]]:
    policy = load_policy()
    for member in policy["members"]:
        if member["member_id"] == member_id:
            return member
    return None


def get_document_requirements(claim_category: str) -> dict[str, list[str]]:
    policy = load_policy()
    return policy["document_requirements"].get(claim_category, {"required": [], "optional": []})


def get_category_config(claim_category: str) -> dict[str, Any]:
    policy = load_policy()
    key = claim_category.lower()
    return policy["opd_categories"].get(key, {})


def is_network_hospital(hospital_name: str) -> bool:
    if not hospital_name:
        return False
    policy = load_policy()
    name_lower = hospital_name.lower()
    return any(nh.lower() in name_lower or name_lower in nh.lower() for nh in policy["network_hospitals"])


def get_waiting_period_days(condition_key: str) -> Optional[int]:
    policy = load_policy()
    return policy["waiting_periods"]["specific_conditions"].get(condition_key)


def match_condition_to_waiting_period(diagnosis: str) -> Optional[str]:
    if not diagnosis:
        return None
    import re

    d = diagnosis.lower()
    mappings = {
        "diabetes": ["diabetes", "t2dm", "type 2 diabetes", "diabetic"],
        "hypertension": ["hypertension", "htn", "high blood pressure"],
        "thyroid_disorders": ["thyroid", "hypothyroid", "hyperthyroid"],
        "joint_replacement": ["joint replacement", "knee replacement", "hip replacement"],
        "maternity": ["maternity", "pregnancy", "prenatal"],
        "mental_health": ["depression", "anxiety", "mental health", "psychiatric"],
        "obesity_treatment": ["obesity", "bariatric", "weight loss", "morbid obesity"],
        "hernia": [r"\bhernia\b"],
        "cataract": ["cataract"],
    }
    for key, keywords in mappings.items():
        for kw in keywords:
            if kw.startswith("\\b"):
                if re.search(kw, d):
                    return key
            elif kw in d:
                return key
    return None


def is_excluded_condition(diagnosis: str, treatment: str = "") -> tuple[bool, str]:
    policy = load_policy()
    text = f"{diagnosis} {treatment}".lower()
    exclusion_keywords = {
        "Obesity and weight loss programs": ["obesity", "weight loss", "diet program", "bariatric", "morbid obesity"],
        "Bariatric surgery": ["bariatric"],
        "Cosmetic or aesthetic procedures": ["cosmetic", "aesthetic", "whitening", "veneers", "lasik"],
        "Health supplements and tonics": ["supplement", "tonic"],
    }
    for exclusion in policy["exclusions"]["conditions"]:
        keywords = exclusion_keywords.get(exclusion, [exclusion.lower()])
        if any(kw in text for kw in keywords):
            return True, exclusion
    return False, ""


def is_dental_excluded(description: str) -> tuple[bool, str]:
    policy = load_policy()
    desc_lower = description.lower()
    for proc in policy["opd_categories"]["dental"]["excluded_procedures"]:
        if proc.lower() in desc_lower:
            return True, proc
    for exc in policy["exclusions"]["dental_exclusions"]:
        if exc.lower() in desc_lower:
            return True, exc
    if "whitening" in desc_lower or "bleaching" in desc_lower:
        return True, "Teeth Whitening"
    return False, ""


def is_dental_covered(description: str) -> bool:
    policy = load_policy()
    desc_lower = description.lower()
    for proc in policy["opd_categories"]["dental"]["covered_procedures"]:
        if proc.lower() in desc_lower:
            return True
    return False


def requires_pre_auth(test_name: str, amount: float) -> bool:
    policy = load_policy()
    category = policy["opd_categories"]["diagnostic"]
    threshold = category.get("pre_auth_threshold", 10000)
    tests = category.get("high_value_tests_requiring_pre_auth", [])
    test_upper = (test_name or "").upper()
    for t in tests:
        if t.upper() in test_upper and amount > threshold:
            return True
    return False


def compute_eligibility_date(member_join_date: str, condition_key: str) -> str:
    join = date_parser.parse(member_join_date).date()
    days = get_waiting_period_days(condition_key) or 0
    eligible = join + timedelta(days=days)
    return eligible.strftime("%Y-%m-%d")


def get_fraud_thresholds() -> dict[str, Any]:
    return load_policy()["fraud_thresholds"]


def get_per_claim_limit() -> float:
    return load_policy()["coverage"]["per_claim_limit"]
