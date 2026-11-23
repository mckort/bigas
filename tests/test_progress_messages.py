"""Per-project DevOps pipeline message tagging."""

from __future__ import annotations

from bigas.resources.devops.progress_messages import (
    complete_pipeline_progress,
    current_pipeline_project,
    pipeline_metadata,
    pipeline_project,
)


class _FakeStore:
    def __init__(self, messages):
        self.messages = messages
        self.patched = []

    def list_messages(self, thread_id):
        return list(self.messages)

    def patch_message(self, message_id, **fields):
        self.patched.append((message_id, fields))
        return {"message_id": message_id}


def test_pipeline_metadata_tags_project_key():
    with pipeline_project("aura"):
        assert current_pipeline_project() == "AURA"
        meta = pipeline_metadata(status="in_progress")
    assert meta["project_key"] == "AURA"
    assert meta["pipeline"] is True
    assert meta["status"] == "in_progress"
    assert current_pipeline_project() == ""


def test_pipeline_metadata_explicit_key_wins():
    with pipeline_project("BIG"):
        meta = pipeline_metadata(project_key="VFA")
    assert meta["project_key"] == "VFA"


def test_complete_pipeline_progress_scopes_by_project():
    store = _FakeStore(
        [
            {
                "message_id": "a",
                "metadata": {
                    "pipeline": True,
                    "status": "in_progress",
                    "project_key": "AURA",
                },
            },
            {
                "message_id": "b",
                "metadata": {
                    "pipeline": True,
                    "status": "in_progress",
                    "project_key": "BIG",
                },
            },
            {
                "message_id": "c",
                "metadata": {"pipeline": True, "status": "in_progress"},
            },
        ]
    )
    complete_pipeline_progress(store, "t1", project_key="AURA")
    ids = [item[0] for item in store.patched]
    assert ids == ["a", "c"]
