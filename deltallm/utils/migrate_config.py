"""Utility to migrate config.yaml model_list to database.

This script migrates static model deployments from config.yaml to the database,
enabling dynamic provider management via API.

Usage:
    python -m deltallm.utils.migrate_config --config config.yaml

    Or programmatically:
        from deltallm.utils.migrate_config import migrate_config
        await migrate_config(config_path="config.yaml")
"""

import argparse
import asyncio
import logging
import sys
from pathlib import Path
from typing import Any, Optional
from uuid import UUID

from deltallm.db.models import ModelDeployment, ProviderConfig
from deltallm.db.session import get_session, init_db
from deltallm.proxy.config import load_config
from deltallm.utils.encryption import encrypt_api_key

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


# Mapping of model prefixes/patterns to provider types
PROVIDER_TYPE_MAPPINGS = {
    "gpt-": "openai",
    "o1-": "openai",
    "chatgpt-": "openai",
    "text-embedding-": "openai",
    "claude-": "anthropic",
    "gemini-": "gemini",
    "command": "cohere",
    "embed-": "cohere",
    "mistral-": "mistral",
    "mixtral-": "mistral",
    "codestral-": "mistral",
    "llama": "groq",
    "amazon.titan": "bedrock",
    "anthropic.claude": "bedrock",
    "ai21.": "bedrock",
    "cohere.": "bedrock",
    "meta.llama": "bedrock",
}


def detect_provider_type(model_name: str, params: dict[str, Any]) -> str:
    """Detect provider type from model name or parameters.

    Args:
        model_name: The model name
        params: The litellm_params dict

    Returns:
        Provider type string
    """
    # Check if explicitly specified
    if "custom_llm_provider" in params:
        return params["custom_llm_provider"]

    # Check model prefixes
    model_lower = model_name.lower()
    for prefix, provider_type in PROVIDER_TYPE_MAPPINGS.items():
        if model_lower.startswith(prefix) or prefix in model_lower:
            return provider_type

    # Check API base for hints
    api_base = params.get("api_base", "")
    if "openai" in api_base.lower():
        return "openai"
    elif "anthropic" in api_base.lower():
        return "anthropic"
    elif "azure" in api_base.lower():
        return "azure"

    # Default to openai for unknown
    logger.warning(f"Could not detect provider type for {model_name}, defaulting to 'openai'")
    return "openai"


def extract_api_key(params: dict[str, Any]) -> Optional[str]:
    """Extract API key from litellm_params.

    Args:
        params: The litellm_params dict

    Returns:
        API key string or None
    """
    # Check various key names
    key_names = ["api_key", "anthropic_api_key", "openai_api_key", "azure_api_key"]
    for key_name in key_names:
        if key_name in params:
            return params[key_name]
    return None


def extract_api_base(params: dict[str, Any]) -> Optional[str]:
    """Extract API base URL from litellm_params.

    Args:
        params: The litellm_params dict

    Returns:
        API base URL or None
    """
    return params.get("api_base") or params.get("base_url")


async def migrate_config(
    config_path: str,
    org_id: Optional[UUID] = None,
    dry_run: bool = False,
    skip_existing: bool = True,
) -> dict[str, int]:
    """Migrate config.yaml model_list to database.

    Args:
        config_path: Path to config.yaml file
        org_id: Optional organization ID (None = global)
        dry_run: If True, don't actually create records
        skip_existing: If True, skip providers/deployments that already exist

    Returns:
        Dict with counts: providers_created, deployments_created, skipped
    """
    # Load config
    config = load_config(config_path)

    if not config.model_list:
        logger.warning("No models found in config.model_list")
        return {"providers_created": 0, "deployments_created": 0, "skipped": 0}

    stats = {"providers_created": 0, "deployments_created": 0, "skipped": 0}

    # Group deployments by provider (based on api_key + api_base + provider_type)
    provider_groups: dict[str, list[dict]] = {}

    for deployment_config in config.model_list:
        params = deployment_config.get("litellm_params", {})
        model_name = deployment_config.get("model_name", params.get("model", "unknown"))

        # Detect provider type
        provider_type = detect_provider_type(model_name, params)

        # Create provider key (unique by api_key + api_base + type)
        api_key = extract_api_key(params)
        api_base = extract_api_base(params)
        provider_key = f"{provider_type}:{api_base or 'default'}:{hash(api_key or '')}"

        if provider_key not in provider_groups:
            provider_groups[provider_key] = {
                "provider_type": provider_type,
                "api_key": api_key,
                "api_base": api_base,
                "deployments": [],
            }

        provider_groups[provider_key]["deployments"].append(deployment_config)

    logger.info(f"Found {len(provider_groups)} unique providers and {len(config.model_list)} deployments")

    if dry_run:
        logger.info("DRY RUN - no changes will be made")
        for provider_key, group in provider_groups.items():
            logger.info(f"  Provider: {group['provider_type']} ({len(group['deployments'])} deployments)")
            for d in group["deployments"]:
                logger.info(f"    - {d.get('model_name', 'unknown')}")
        return stats

    # Initialize database
    init_db()

    async with get_session() as session:
        # Create providers and deployments
        for idx, (provider_key, group) in enumerate(provider_groups.items(), 1):
            provider_type = group["provider_type"]
            api_key = group["api_key"]
            api_base = group["api_base"]

            # Generate provider name
            provider_name = f"{provider_type}-{idx}"
            if api_base:
                # Add suffix for custom endpoints
                provider_name = f"{provider_type}-custom-{idx}"

            # Check if provider already exists
            from sqlalchemy import select

            existing_provider = await session.execute(
                select(ProviderConfig).where(
                    ProviderConfig.name == provider_name,
                    ProviderConfig.org_id == org_id,
                )
            )
            existing = existing_provider.scalar_one_or_none()

            if existing:
                if skip_existing:
                    logger.info(f"Skipping existing provider: {provider_name}")
                    provider = existing
                else:
                    logger.warning(f"Provider {provider_name} already exists, updating...")
                    existing.api_key_encrypted = encrypt_api_key(api_key) if api_key else None
                    existing.api_base = api_base
                    provider = existing
            else:
                # Create provider
                provider = ProviderConfig(
                    name=provider_name,
                    provider_type=provider_type,
                    api_key_encrypted=encrypt_api_key(api_key) if api_key else None,
                    api_base=api_base,
                    org_id=org_id,
                    is_active=True,
                    settings={},
                )
                session.add(provider)
                await session.flush()
                stats["providers_created"] += 1
                logger.info(f"Created provider: {provider_name} (type={provider_type})")

            # Create deployments for this provider
            for deploy_config in group["deployments"]:
                params = deploy_config.get("litellm_params", {})
                model_name = deploy_config.get("model_name", params.get("model", "unknown"))
                provider_model = params.get("model", model_name)

                # Check if deployment already exists
                existing_deployment = await session.execute(
                    select(ModelDeployment).where(
                        ModelDeployment.model_name == model_name,
                        ModelDeployment.provider_config_id == provider.id,
                    )
                )
                if existing_deployment.scalar_one_or_none():
                    if skip_existing:
                        logger.info(f"  Skipping existing deployment: {model_name}")
                        stats["skipped"] += 1
                        continue

                # Create deployment
                deployment = ModelDeployment(
                    model_name=model_name,
                    provider_model=provider_model,
                    provider_config_id=provider.id,
                    org_id=org_id,
                    is_active=True,
                    priority=1,
                    tpm_limit=deploy_config.get("tpm"),
                    rpm_limit=deploy_config.get("rpm"),
                    timeout=deploy_config.get("timeout"),
                    settings={
                        "model_info": deploy_config.get("model_info", {}),
                    },
                )
                session.add(deployment)
                stats["deployments_created"] += 1
                logger.info(f"  Created deployment: {model_name} -> {provider_model}")

        await session.commit()

    logger.info(
        f"Migration complete: "
        f"{stats['providers_created']} providers, "
        f"{stats['deployments_created']} deployments created, "
        f"{stats['skipped']} skipped"
    )

    return stats


async def list_db_deployments() -> None:
    """List all deployments currently in the database."""
    init_db()

    async with get_session() as session:
        from sqlalchemy import select
        from sqlalchemy.orm import selectinload

        result = await session.execute(
            select(ModelDeployment).options(selectinload(ModelDeployment.provider_config))
        )
        deployments = result.scalars().all()

        if not deployments:
            logger.info("No deployments found in database")
            return

        logger.info(f"Found {len(deployments)} deployments in database:")
        for d in deployments:
            provider = d.provider_config
            logger.info(
                f"  {d.model_name} -> {d.provider_model} "
                f"(provider={provider.name if provider else 'N/A'}, "
                f"active={d.is_active})"
            )


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Migrate ProxyLLM config.yaml to database"
    )
    parser.add_argument(
        "--config",
        "-c",
        required=False,
        help="Path to config.yaml file",
    )
    parser.add_argument(
        "--org-id",
        help="Organization ID (UUID) for org-scoped resources",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be migrated without making changes",
    )
    parser.add_argument(
        "--no-skip-existing",
        action="store_true",
        help="Update existing providers/deployments instead of skipping",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List current database deployments and exit",
    )

    args = parser.parse_args()

    if args.list:
        asyncio.run(list_db_deployments())
        return

    if not args.config:
        parser.error("--config is required unless using --list")

    org_id = UUID(args.org_id) if args.org_id else None

    asyncio.run(
        migrate_config(
            config_path=args.config,
            org_id=org_id,
            dry_run=args.dry_run,
            skip_existing=not args.no_skip_existing,
        )
    )


if __name__ == "__main__":
    main()
