"""Explicit semantic views of saved data; never launch another paid task."""

from __future__ import annotations

import csv
import io
import json
import math
import re
from typing import Any

MAX_BYTES = 200_000
CLUSTER_FIELDS = (
    "clustered",
    "words",
    "wssumm",
    "topurl",
    "main",
    "ws",
    "ws_strict",
    "agregators",
    "toponim",
    "geo",
)


def at_pointer(value: Any, pointer: str):
    if not isinstance(pointer, str) or len(pointer) > 2048:
        raise ValueError("bounded JSON pointer required")
    if pointer == "":
        return value
    if not pointer.startswith("/") or len(pointer.split("/")) > 25:
        raise ValueError("invalid JSON pointer")
    for encoded in pointer.split("/")[1:]:
        if re.search(r"~(?![01])", encoded):
            raise ValueError("invalid JSON pointer escape")
        key = encoded.replace("~1", "/").replace("~0", "~")
        if isinstance(value, dict) and key in value:
            value = value[key]
        elif isinstance(value, list) and re.fullmatch(r"0|[1-9][0-9]*", key) and int(key) < len(value):
            value = value[int(key)]
        else:
            raise ValueError("result pointer not found; inspect the saved result")
    return value


def _escape(value):
    return str(value).replace("~", "~0").replace("/", "~1")


def _cell(value):
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False, allow_nan=False)
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def result_table(job, view, result_pointer="", format="json", max_rows=500):
    if view not in {"serp_urls", "serp_hosts", "clusters"} or format not in {"json", "csv"}:
        raise ValueError("unsupported saved-result view or format")
    if type(max_rows) is not int or not 1 <= max_rows <= 1000:
        raise ValueError("max_rows must be between 1 and 1000")
    if "result" not in job or job["result"] is None:
        return {"job_id": job["job_id"], "status": job["status"], "available": False}
    selected = at_pointer(job["result"], result_pointer)
    columns = ["pointer"] + (list(CLUSTER_FIELDS) if view == "clusters" else ["value", "occurrences"])
    if view == "clusters":
        if not isinstance(selected, list):
            raise ValueError("clusters requires a pointer to the cluster record list")
        items = enumerate(selected)
    else:
        if not isinstance(selected, dict):
            raise ValueError("SERP summary requires a pointer to a repeatUrl or repeatHost object")
        items = selected.items()
    rows, reason = [], "end_of_selected_view"
    for key, value in items:
        if len(rows) >= max_rows:
            reason = "row_limit"
            break
        pointer = result_pointer + "/" + _escape(key)
        if view == "clusters":
            if not isinstance(value, dict) or "clustered" not in value or "words" not in value:
                raise ValueError("unrecognized cluster record; use the original JSON/JSONL export")
            if not isinstance(value["words"], (list, dict)):
                raise ValueError("cluster words must be a structured list or mapping")
            row = {"pointer": pointer, **{field: value.get(field) for field in CLUSTER_FIELDS}}
        else:
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                raise ValueError("SERP occurrence counts must be finite nonnegative numbers")
            row = {"pointer": pointer, "value": key, "occurrences": value}
        # Conservative bound also covers doubled quotes in CSV.
        if 2 * len(json.dumps(rows + [row], ensure_ascii=False, allow_nan=False).encode()) > MAX_BYTES:
            reason = "byte_limit"
            break
        rows.append(row)
    result = {
        "job_id": job["job_id"],
        "status": job["status"],
        "available": True,
        "view": view,
        "format": format,
        "row_count": len(rows),
        "columns": columns,
        "selected_count": len(selected),
        "selected_view_complete": len(rows) == len(selected),
        "stop_reason": reason,
        "provider_completeness": "not_asserted",
        "source": {
            "kind": "saved_sanitized_job_result",
            "pointer": result_pointer,
            "job_updated_at": job.get("updated_at"),
        },
        "provider_calls": 0,
    }
    if format == "json":
        result["rows"] = rows
    else:
        stream = io.StringIO(newline="")
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(columns)
        writer.writerows([_cell(row.get(column)) for column in columns] for row in rows)
        result.update(content=stream.getvalue(), formula_strings_escaped=True)
    if len(json.dumps(result, ensure_ascii=False, allow_nan=False).encode()) > MAX_BYTES:
        raise ValueError("export envelope too large; request fewer rows")
    return result
