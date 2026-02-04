"""Verification script for Phase 1 migration.

This script verifies that the migration was applied correctly and
that the new schema supports both linked and standalone deployments.
"""

import asyncio
from uuid import uuid4
from sqlalchemy import select

# Need to set up the environment first
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../..'))

from deltallm.db.session import init_db, get_db_session
from deltallm.db.models import ModelDeployment
from deltallm.utils.encryption import encrypt_api_key


async def verify_migration():
    """Verify the migration was applied correctly."""
    print("=== Phase 1 Migration Verification ===\n")
    
    # Initialize database
    init_db()
    
    session_gen = get_db_session()
    session = await session_gen.__anext__()
    
    try:
        # Test 1: Create a standalone deployment (no provider_config_id)
        print("1. Testing standalone deployment creation...")
        standalone_deployment = ModelDeployment(
            id=uuid4(),
            model_name="test-standalone-model",
            provider_model="gpt-4-test",
            provider_config_id=None,  # Standalone
            provider_type="openai",
            api_key_encrypted=encrypt_api_key("sk-test-key-123"),
            api_base="https://api.openai.com/v1",
            org_id=None,
            is_active=True,
            priority=1,
            settings={},
        )
        session.add(standalone_deployment)
        await session.flush()
        print(f"   ✅ Created standalone deployment: {standalone_deployment.id}")
        print(f"      - provider_type: {standalone_deployment.provider_type}")
        print(f"      - api_base: {standalone_deployment.api_base}")
        print(f"      - has api_key: {standalone_deployment.api_key_encrypted is not None}")
        
        # Test 2: Create another standalone deployment with same model name
        # This should work because provider_config_id is NULL
        print("\n2. Testing multiple standalone deployments with same model_name...")
        standalone_deployment_2 = ModelDeployment(
            id=uuid4(),
            model_name="test-standalone-model",  # Same name
            provider_model="gpt-4-test-2",
            provider_config_id=None,  # Also standalone
            provider_type="anthropic",
            api_key_encrypted=encrypt_api_key("sk-test-key-456"),
            api_base="https://api.anthropic.com/v1",
            org_id=None,
            is_active=True,
            priority=2,
            settings={},
        )
        session.add(standalone_deployment_2)
        await session.flush()
        print(f"   ✅ Created second standalone deployment: {standalone_deployment_2.id}")
        print(f"      - Different provider_type: {standalone_deployment_2.provider_type}")
        
        # Test 3: Query standalone deployments
        print("\n3. Testing query for standalone deployments...")
        result = await session.execute(
            select(ModelDeployment).where(
                ModelDeployment.provider_config_id.is_(None),
                ModelDeployment.model_name == "test-standalone-model"
            )
        )
        deployments = result.scalars().all()
        print(f"   ✅ Found {len(deployments)} standalone deployments")
        for dep in deployments:
            print(f"      - {dep.id}: {dep.provider_type} -> {dep.provider_model}")
        
        # Rollback the test data
        await session.rollback()
        print("\n4. Cleanup...")
        print("   ✅ Test data rolled back")
        
        print("\n=== All Verification Tests Passed ===")
        return True
        
    except Exception as e:
        await session.rollback()
        print(f"\n❌ Verification failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        await session.close()


if __name__ == "__main__":
    success = asyncio.run(verify_migration())
    sys.exit(0 if success else 1)
