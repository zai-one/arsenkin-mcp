"""Bounded exports of saved results, retaining JSON pointers instead of guessing fields."""

from __future__ import annotations

import csv
import io
import json
from typing import Any

MAX_BYTES = 200_000


def export_result(job: dict[str, Any], format: str = "jsonl", max_rows: int = 500) -> dict[str, Any]:
    if format not in {"jsonl", "csv"}:
        raise ValueError("format must be jsonl or csv")
    if isinstance(max_rows, bool) or not isinstance(max_rows, int) or not 1 <= max_rows <= 1000:
        raise ValueError("max_rows must be between 1 and 1000")
    if "result" not in job or job["result"] is None:
        return {"schema_version": 1, "job_id": job["job_id"], "status": job["status"], "available": False}
    stack = [("", job["result"], 0)]
    rows = []
    incomplete = False
    encoded_size = 0
    while stack and len(rows) < max_rows:
        pointer, value, depth = stack.pop()
        if depth < 24 and isinstance(value, dict) and value:
            for key, item in reversed(list(value.items())):
                escaped = str(key).replace("~", "~0").replace("/", "~1")
                stack.append((pointer + "/" + escaped, item, depth + 1))
            continue
        if depth < 24 and isinstance(value, list) and value:
            stack.extend(
                (pointer + "/" + str(index), value[index], depth + 1)
                for index in range(len(value) - 1, -1, -1)
            )
            continue
        row = {
            "job_id": job["job_id"],
            "status": job["status"],
            "pointer": pointer,
            "value_json": json.dumps(value, ensure_ascii=False, separators=(",", ":")),
        }
        # A conservative bound covers JSON escaping and CSV quoting.
        encoded_size += 2 * len(json.dumps(row, ensure_ascii=False).encode("utf-8"))
        if encoded_size > MAX_BYTES:
            incomplete = True
            break
        rows.append(row)
    if format == "jsonl":
        content = "".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows)
    else:
        buffer = io.StringIO(newline="")
        writer = csv.DictWriter(buffer, fieldnames=["job_id", "status", "pointer", "value_json"])
        writer.writeheader()
        writer.writerows(rows)
        content = buffer.getvalue()
    return {
        "schema_version": 1,
        "job_id": job["job_id"],
        "status": job["status"],
        "available": True,
        "format": format,
        "row_count": len(rows),
        "truncated": bool(stack) or incomplete,
        "source": "saved_sanitized_job_result",
        "completeness": "partial",
        "content": content,
    }
