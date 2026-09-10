"""An independent, text-only selector and two separately capable answer models."""

from src.router.policy_validation import PolicyMemberInventoryItem


def independent_group():
    return {
        "key": "selected",
        "mode": "chat",
        "strategy": "priority-based-routing",
        "context": {"mode": "eligible-only", "unknown_capacity": "exclude"},
        "selector": {
            "kind": "llm-tier",
            "classifier_deployment_id": "tiny",
            "lanes": [
                {"id": "economy", "rank": 0, "description": "Routine work"},
                {"id": "quality", "rank": 1, "description": "Complex work"},
            ],
        },
        "members": [
            {"deployment_id": "economy", "lane": "economy"},
            {"deployment_id": "quality", "lane": "quality"},
        ],
    }


def independent_models():
    return {
        key: [
            {
                "deployment_id": key,
                "routing_state_incarnation": f"incarnation-{key}",
                "deltallm_params": {"model": f"openai/{key}", "api_base": "https://mock.test/v1"},
                "model_info": {
                    "mode": "chat",
                    "max_tokens": 8192 if key == "tiny" else 131072,
                    "input_cost_per_token": "0.000001",
                    "output_cost_per_token": "0.000002",
                    "rpm_limit": 100,
                    "tpm_limit": 1000000,
                    "chat_capabilities": {}
                    if key == "tiny"
                    else {
                        "tools": True,
                        "streaming": True,
                        "json_object": True,
                    },
                },
            }
        ]
        for key in ("tiny", "economy", "quality")
    }


def independent_inventory():
    return {
        key: PolicyMemberInventoryItem(
            deployment_id=key,
            workload_mode="chat",
            model_info=entries[0]["model_info"],
            provider_model=entries[0]["deltallm_params"]["model"],
        )
        for key, entries in independent_models().items()
    }


def independent_policy():
    return {key: value for key, value in independent_group().items() if key not in {"key", "mode"}}
