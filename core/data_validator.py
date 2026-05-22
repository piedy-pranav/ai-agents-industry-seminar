REQUIRED_DELIVERY_FIELDS = ["region", "destination", "medicine", "priority"]
IMPORTANT_DELIVERY_FIELDS = ["scheduledTime", "estimatedDelay", "requiresColdChain", "weight"]
ALL_QUALITY_FIELDS = [
    "region", "origin", "destination", "medicine", "priority",
    "quantity", "scheduledTime", "estimatedDelay", "requiresColdChain", "weight",
]
UNKNOWN_SENTINEL = "Unknown"

MEDICAL_HINTS = [
    "medicine", "drug", "insulin", "vaccine", "antibiotic", "blood", "chemotherapy",
    "pharmaceutical", "medication", "cold chain", "hospital", "clinic", "patient",
    "药品", "药物", "胰岛素", "疫苗", "抗生素", "血液", "化疗", "医院", "冷链", "配送",
]


def calculate_data_quality(deliveries: list[dict]) -> int:
    """Return 0-100 quality score based on field completeness across deliveries."""
    if not deliveries:
        return 0

    total_checks = len(deliveries) * len(ALL_QUALITY_FIELDS)
    valid_checks = 0

    for d in deliveries:
        for field in ALL_QUALITY_FIELDS:
            val = d.get(field)
            if val is None or val == UNKNOWN_SENTINEL or val == "":
                continue
            # Augmented weight does not count as real data
            if field == "weight" and d.get("_augmented"):
                continue
            valid_checks += 1

    return round((valid_checks / total_checks) * 100)


def validate_uploaded_data(parsed_data: dict, raw_files: list[dict]) -> dict:
    """
    Validate uploaded file data against medical logistics schema.
    Returns a dict with: valid, issues, warnings, fileReports, isMedicalData, summary.
    """
    issues = []
    warnings = []
    file_reports = []

    # Check if data looks like medical logistics
    all_text = " ".join(
        f["filename"] + " " + f["content"][:2000]
        for f in raw_files
    ).lower()
    medical_score = sum(1 for h in MEDICAL_HINTS if h.lower() in all_text)
    is_medical = medical_score >= 2

    if not is_medical:
        warnings.append({
            "type": "schema_mismatch",
            "severity": "high",
            "message": (
                f"Uploaded data does not appear to be medical logistics data. "
                f"Medical keyword match: {medical_score}/{len(MEDICAL_HINTS)}. "
                "System will attempt analysis but results may not be meaningful."
            ),
        })

    deliveries = parsed_data.get("deliveries", [])

    if not deliveries:
        issues.append({
            "type": "no_deliveries",
            "severity": "critical",
            "message": "No delivery data found. At least one delivery record is required.",
        })
    else:
        all_fields = REQUIRED_DELIVERY_FIELDS + IMPORTANT_DELIVERY_FIELDS
        field_coverage = {}
        for field in all_fields:
            filled = sum(
                1 for d in deliveries
                if d.get(field) not in (None, UNKNOWN_SENTINEL, "", 0)
            )
            field_coverage[field] = {
                "filled": filled,
                "total": len(deliveries),
                "percent": round(filled / len(deliveries) * 100),
            }

        for field in REQUIRED_DELIVERY_FIELDS:
            cov = field_coverage[field]
            if cov["percent"] < 30:
                issues.append({
                    "type": "missing_required_field",
                    "severity": "critical",
                    "field": field,
                    "message": (
                        f'Required field "{field}" has only {cov["percent"]}% coverage '
                        f'({cov["filled"]}/{cov["total"]} records). '
                        "Minimum 30% required for meaningful analysis."
                    ),
                })
            elif cov["percent"] < 70:
                warnings.append({
                    "type": "low_field_coverage",
                    "severity": "medium",
                    "field": field,
                    "message": f'Field "{field}" has {cov["percent"]}% coverage. Some analysis may be limited.',
                })

        for field in IMPORTANT_DELIVERY_FIELDS:
            cov = field_coverage[field]
            if cov["percent"] < 10:
                warnings.append({
                    "type": "missing_important_field",
                    "severity": "medium",
                    "field": field,
                    "message": f'Field "{field}" has {cov["percent"]}% coverage. Default values will be used.',
                    "defaultUsed": True,
                })

        regions = {d["region"] for d in deliveries if d.get("region") not in (None, UNKNOWN_SENTINEL)}
        if not regions:
            issues.append({
                "type": "no_regions",
                "severity": "critical",
                "message": "No valid region data found. Route planning requires region information.",
            })
        elif len(regions) == 1:
            warnings.append({
                "type": "single_region",
                "severity": "low",
                "message": f'All deliveries are in a single region: "{next(iter(regions))}". Multi-region analysis will be limited.',
            })

        file_reports.append({
            "type": "deliveries",
            "count": len(deliveries),
            "fieldCoverage": field_coverage,
            "regions": list(regions),
        })

    for dtype in ("inventory", "drivers", "weather"):
        records = parsed_data.get(dtype, [])
        if records:
            file_reports.append({"type": dtype, "count": len(records)})

    critical = [i for i in issues if i["severity"] == "critical"]
    valid = len(critical) == 0

    return {
        "valid": valid,
        "issues": issues,
        "warnings": warnings,
        "fileReports": file_reports,
        "isMedicalData": is_medical,
        "medicalScore": medical_score,
        "summary": (
            f"Data validation passed with {len(warnings)} warning(s)."
            if valid
            else f"Data validation failed: {len(critical)} critical issue(s) found."
        ),
    }
