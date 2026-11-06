#!/usr/bin/env python3
"""Strict master<->worker JSON contracts + a dependency-free validator.

Schemas use additionalProperties:false so an unexpected/renamed field is a hard
error in both directions (plan Slice 3 / dry-run NO-GO on contract mismatch).
The validator supports the JSON-Schema subset we use: type (incl. unions for
nullable), required, properties, additionalProperties, enum, items.
"""
from __future__ import annotations

_REASONS = ["env-missing-resource", "flaky-timing", "gpu-unavailable",
            "config-drift", "unknown", None]

# master -> worker
DISPATCH_ENVELOPE = {
    "type": "object", "additionalProperties": False,
    "required": ["unit_id", "phase", "test_target", "test_case", "gpu_req",
                 "base_commit", "payload_sha256"],
    "properties": {
        "unit_id": {"type": "string"},
        "phase": {"type": "string", "enum": ["A", "B"]},
        "test_target": {"type": "string"},
        "test_case": {"type": "string"},
        "gpu_req": {"type": "string", "enum": ["cpu", "single", "multi"]},
        "route": {"type": ["string", "null"], "enum": ["cpu", "local", "local_multi", "re_fallback", "re_multi", None]},
        "base_commit": {"type": "string"},
        "payload_sha256": {"type": "string"},
        "category": {"type": ["string", "null"]},
        "timeout_s": {"type": ["integer", "null"]},
        "cluster_id": {"type": ["string", "null"]},
        "prior_attempts": {"type": "array", "items": {"type": "object"}},
        # Phase-B only: the root cause the fixer must address (null for Phase A).
        "root_cause_summary": {"type": ["string", "null"]},
        "culprit_symbol": {"type": ["string", "null"]},
        "deflake": {"type": ["boolean", "null"]},
    },
}

_ROOT_CAUSE = {
    "type": "object", "additionalProperties": False,
    "required": ["culprit_symbol", "cause_category", "signature"],
    "properties": {
        "culprit_symbol": {"type": "string"},
        "cause_category": {"type": "string"},
        "mechanism": {"type": ["string", "null"]},
        "error_signature": {"type": ["string", "null"]},
        "observed_frequency": {"type": ["number", "null"]},
        "signature": {"type": "string"},
    },
}

# worker -> master (Phase A)
PHASE_A_REPORT = {
    "type": "object", "additionalProperties": False,
    "required": ["unit_id", "status"],
    "properties": {
        "unit_id": {"type": "string"},
        "status": {"type": "string",
                   "enum": ["completed", "NEEDS_GPU_EXEC", "NEEDS_REROUTE", "error"]},
        "reproduced": {"type": ["boolean", "null"]},
        "flaky": {"type": ["boolean", "null"]},
        "pass_rate": {"type": ["number", "null"]},
        "root_causes": {"type": "array", "items": _ROOT_CAUSE},
        "reason_not_reproduced": {"type": ["string", "null"], "enum": _REASONS},
        "evidence_ref": {"type": ["string", "null"]},
        "gpu_request": {"type": ["object", "null"]},
        "reroute": {"type": ["object", "null"]},
        "duration_s": {"type": ["number", "null"]},
        "notes": {"type": ["string", "null"]},
    },
}

# worker -> master (Phase B)
PHASE_B_REPORT = {
    "type": "object", "additionalProperties": False,
    "required": ["unit_id", "status"],
    "properties": {
        "unit_id": {"type": "string"},
        "status": {"type": "string", "enum": ["completed", "DIFF_LOCAL_ONLY", "error"]},
        "diff_url": {"type": ["string", "null"]},
        "local_commit_sha": {"type": ["string", "null"]},
        "base_commit": {"type": ["string", "null"]},
        "ci_status": {"type": ["string", "null"], "enum": ["local_green", "local_fail", None]},
        "patch_export_path": {"type": ["string", "null"]},
        "submit_error": {"type": ["string", "null"]},
        "duration_s": {"type": ["number", "null"]},
        "evidence_ref": {"type": ["string", "null"]},
        # de-flake verification (Phase-B flaky protocol)
        "flaky_verified": {"type": ["boolean", "null"]},
        "rerun_total": {"type": ["integer", "null"]},
        "rerun_passes": {"type": ["integer", "null"]},
        "quarantine_recommended": {"type": ["boolean", "null"]},
        "notes": {"type": ["string", "null"]},
    },
}

SCHEMAS = {
    "dispatch": DISPATCH_ENVELOPE,
    "phase_a": PHASE_A_REPORT,
    "phase_b": PHASE_B_REPORT,
}


def _is_type(v, t: str) -> bool:
    if t == "object":
        return isinstance(v, dict)
    if t == "array":
        return isinstance(v, list)
    if t == "string":
        return isinstance(v, str)
    if t == "boolean":
        return isinstance(v, bool)
    if t == "integer":
        return isinstance(v, int) and not isinstance(v, bool)
    if t == "number":
        return isinstance(v, (int, float)) and not isinstance(v, bool)
    if t == "null":
        return v is None
    raise ValueError(f"unknown schema type {t!r}")


def validate(inst, schema, path: str = "$") -> None:
    """Raise ValueError(path-qualified) on the first violation."""
    t = schema.get("type")
    if t is not None:
        types = t if isinstance(t, list) else [t]
        if not any(_is_type(inst, tt) for tt in types):
            raise ValueError(f"{path}: expected type {t}, got {type(inst).__name__}")
    if "enum" in schema and inst not in schema["enum"]:
        raise ValueError(f"{path}: {inst!r} not in enum {schema['enum']}")
    if isinstance(inst, dict) and ("properties" in schema or schema.get("type") == "object"):
        props = schema.get("properties", {})
        for req in schema.get("required", []):
            if req not in inst:
                raise ValueError(f"{path}: missing required key '{req}'")
        if schema.get("additionalProperties", True) is False:
            extra = sorted(set(inst) - set(props))
            if extra:
                raise ValueError(f"{path}: unexpected keys {extra}")
        for k, v in inst.items():
            if k in props:
                validate(v, props[k], f"{path}.{k}")
    if isinstance(inst, list) and "items" in schema:
        for i, el in enumerate(inst):
            validate(el, schema["items"], f"{path}[{i}]")


def validate_named(inst, name: str) -> None:
    validate(inst, SCHEMAS[name])
