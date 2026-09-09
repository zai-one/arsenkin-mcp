from __future__ import annotations

from uuid import UUID

import pytest
from test_runtime import PAYLOAD, queued


@pytest.mark.parametrize("actor", ["local-operator", "other"])
async def test_new_approval_cannot_repeat_account_paid_hash(config, actor):
    runtime, _job = await queued(config)
    draft = await runtime.jobs.prepare_arsenkin(actor, "check-top", PAYLOAD, 100)
    approval = UUID(draft["approval_id"])
    assert runtime.store.accept(approval, principal=actor, digest=draft["request_hash"])
    with pytest.raises(PermissionError, match="duplicate"):
        await runtime.jobs.submit_arsenkin(actor, "check-top", PAYLOAD, approval, "different-key")
    assert (await runtime.store.get_approval(approval)).status == "accepted"


async def test_same_key_cannot_silently_accept_different_approval(config):
    runtime, _job = await queued(config)
    draft = await runtime.jobs.prepare_arsenkin(config.principal_id, "check-top", PAYLOAD, 100)
    with pytest.raises(PermissionError, match="approval"):
        await runtime.jobs.submit_arsenkin(
            config.principal_id, "check-top", PAYLOAD, UUID(draft["approval_id"]), "key-1"
        )
