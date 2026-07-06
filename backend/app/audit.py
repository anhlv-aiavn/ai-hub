"""Audit log cấu hình — append-only, không bao giờ chứa secret thật.
Chỉ ghi thay đổi CẤU HÌNH (site_config/s3_connections); không ghi nghiệp vụ
thường ngày (đó là phạm vi access-log, phase sau)."""

from datetime import datetime, timezone
from enum import Enum

from app.db import audit_log


class AuditAction(str, Enum):
    SITE_CONFIG_UPDATE = "site_config.update"
    S3_CONNECTION_CREATE = "s3_connection.create"
    S3_CONNECTION_UPDATE = "s3_connection.update"
    S3_CONNECTION_DELETE = "s3_connection.delete"
    S3_CONNECTION_TEST = "s3_connection.test"


def _redact(detail: dict | None) -> dict:
    """Không bao giờ để lọt secret_access_key ra audit log."""
    if not detail:
        return {}

    def _walk(v):
        if isinstance(v, dict):
            out = {}
            for k, val in v.items():
                if k == "secret_access_key":
                    out[k] = "<redacted>" if val else None
                else:
                    out[k] = _walk(val)
            return out
        if isinstance(v, list):
            return [_walk(x) for x in v]
        return v

    return _walk(detail)


async def log_action(actor: str, action: AuditAction, target: str, detail: dict | None = None) -> None:
    await audit_log().insert_one({
        "at": datetime.now(timezone.utc),
        "actor": actor,
        "action": action.value if isinstance(action, AuditAction) else action,
        "target": target,
        "detail": _redact(detail),
    })
