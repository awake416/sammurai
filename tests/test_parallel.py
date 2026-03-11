import pytest
from unittest.mock import MagicMock, patch
from src.backend.cli import process_groups_parallel
from src.backend.llm_client import LLMClient


def test_process_groups_parallel():
    db = MagicMock()
    config = {"parallel": {"workers": 2, "batch_workers": 2}}
    groups = ["Group A", "Group B", "Group C"]
    llm_client = MagicMock()

    # Mock extract_from_group to return a specific string for each group
    def mock_extract_side_effect(db, group, *args, **kwargs):
        return f"Report for {group}"

    with patch(
        "src.backend.cli.extract_from_group", side_effect=mock_extract_side_effect
    ) as mock_extract:
        result = process_groups_parallel(
            db=db,
            groups=groups,
            config=config,
            llm_client=llm_client,
            workers=2,
            parallel_batches=2,
        )

        assert mock_extract.call_count == 3
        # Check if it was called for each group
        called_groups = [
            call.kwargs.get("group") for call in mock_extract.call_args_list
        ]
        assert set(called_groups) == set(groups)

        # Assert on the returned string and check ordering
        assert "FINAL REPORTS" in result
        assert "Report for Group A" in result
        assert "Report for Group B" in result
        assert "Report for Group C" in result

        # Check ordering: Group A should come before Group B, and Group B before Group C
        pos_a = result.find("Report for Group A")
        pos_b = result.find("Report for Group B")
        pos_c = result.find("Report for Group C")
        assert pos_a < pos_b < pos_c


def test_llm_client_extract_batch_parallel():
    client = LLMClient(
        base_url="http://localhost:4000", api_key="test-key", model="test-model"
    )

    messages = [{"message": f"msg {i}"} for i in range(10)]

    with patch.object(
        client, "_extract_batch_single", return_value=[{"task": "test"}]
    ) as mock_extract_single:
        results = client.extract_batch(messages, batch_size=2, parallel_batches=3)

        # 10 messages, batch_size 2 => 5 batches
        assert mock_extract_single.call_count == 5
        assert len(results) == 5
