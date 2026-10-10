"""Keep routing simulation types independent from the HTTP route graph."""

import subprocess
import sys

from pydantic import ValidationError
import pytest

from src.models import route_policy_simulation as contracts


def test_existing_api_exports_keep_the_same_simulation_types():
    from src.api.admin import route_group_contracts as api

    for name in (
        "RoutePolicySimulationOutcome",
        "RoutePolicySimulationDeploymentOutcome",
        "RoutePolicySimulationRequest",
        "RoutePolicySimulationPrompt",
        "RoutePolicySimulationSelection",
        "RoutePolicySimulationSummary",
        "RoutePolicySimulationAttempt",
        "RoutePolicySimulationResponse",
    ):
        assert getattr(api, name) is getattr(contracts, name), name


def test_simulation_service_import_does_not_load_the_http_route_graph():
    code = (
        "import sys\n"
        "import src.services.routing.route_policy_simulation\n"
        "assert not any(name.startswith(('src.api', 'src.bootstrap', 'src.routers')) "
        "for name in sys.modules)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stderr


def test_simulation_request_keeps_defaults_and_trimmed_outcomes():
    request = contracts.RoutePolicySimulationRequest(
        outcomes=[{"deployment_id": " model-a ", "outcome": "timeout"}]
    )
    assert request.iterations == 100
    assert request.input_tokens == 0
    assert request.requested_output_tokens is None
    assert request.user_id == "policy-simulation"
    assert request.outcomes[0].deployment_id == "model-a"
    assert request.outcomes[0].outcome == "timeout"


@pytest.mark.parametrize(
    "payload",
    [
        {"iterations": 0},
        {"iterations": 5001},
        {"input_tokens": -1},
        {"requested_output_tokens": -1},
        {"outcomes": [{"deployment_id": " ", "outcome": "success"}]},
        {"outcomes": [{"deployment_id": "model-a", "outcome": "unknown"}]},
        {"outcomes": [{"deployment_id": "model-a", "outcome": "success"}] * 501},
    ],
)
def test_simulation_request_keeps_input_bounds(payload):
    with pytest.raises(ValidationError):
        contracts.RoutePolicySimulationRequest.model_validate(payload)
