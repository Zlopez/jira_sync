from contextlib import nullcontext

import pytest

from jira_sync.config import model


class TestPagureQueryRepoSpec:
    @pytest.mark.parametrize(
        "input, validates",
        (
            ({"namespace": "NAMESPACE"}, True),
            ({"pattern": "PATTERN*"}, True),
            ({"namespace": "NAMESPACE", "pattern": "PATTERN*"}, True),
            ({}, False),
        ),
        ids=("namespace", "pattern", "namespace-pattern", "nothing"),
    )
    def test_check_spec_not_empty(self, input, validates):
        if validates:
            expectation = nullcontext()
        else:
            expectation = pytest.raises(ValueError)

        with expectation:
            validated = model.PagureQueryRepoSpec.model_validate(input)

        if validates:
            for attr in ("namespace", "pattern"):
                if attr in input:
                    assert getattr(validated, attr) == input[attr]
                else:
                    assert getattr(validated, attr) is None
