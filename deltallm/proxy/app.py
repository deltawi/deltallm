"""FastAPI application for ProxyLLM proxy server."""

import logging
import os
import sys
from contextlib import asynccontextmanager
from typing import Optional

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    stream=sys.stdout
)

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from deltallm.router import Router
from deltallm.dynamic_router import DynamicRouter
from deltallm.exceptions import ProxyLLMError
from deltallm.pricing.manager import PricingManager
from deltallm.pricing.calculator import CostCalculator

from .config import load_config, ProxyConfig
from .auth import APIKeyManager, AuthMiddleware
from .rate_limit import RateLimiter, RateLimitMiddleware
from .routes import (
    audio_router,
    audit_router,
    auth_router,
    batches_router,
    budget_router,
    chat_router,
    deployments_router,
    embeddings_router,
    files_router,
    guardrails_router,
    models_router,
    keys_router,
    health_router,
    org_router,
    pricing_router,
    providers_router,
    team_router,
)


async def seed_admin_user():
    """Seed the admin user from environment variables."""
    from sqlalchemy import select
    from deltallm.db.session import get_session
    from deltallm.db.models import User
    from .routes.auth import hash_password

    admin_email = os.environ.get("ADMIN_EMAIL")
    admin_password = os.environ.get("ADMIN_PASSWORD")

    if not admin_email or not admin_password:
        print("ADMIN_EMAIL or ADMIN_PASSWORD not set, skipping admin user seeding")
        return

    try:
        async with get_session() as session:
            # Check if admin user exists
            result = await session.execute(
                select(User).where(User.email == admin_email)
            )
            existing = result.scalar_one_or_none()

            if existing:
                print(f"Admin user {admin_email} already exists")
                return

            # Create admin user
            admin = User(
                email=admin_email,
                password_hash=hash_password(admin_password),
                first_name="Admin",
                last_name="User",
                is_superuser=True,
                is_active=True,
            )
            session.add(admin)
            # Commit is handled by context manager
            print(f"Created admin user: {admin_email}")
    except Exception as e:
        print(f"Failed to seed admin user: {e}")


def create_app(config_path: Optional[str] = None) -> FastAPI:
    """Create the FastAPI application.

    Args:
        config_path: Path to configuration file

    Returns:
        FastAPI application
    """
    # Load configuration
    config = load_config(config_path)

    # Create key manager
    key_manager = APIKeyManager(master_key=config.general.master_key)

    # Create static router (for config.yaml-based deployments)
    router = Router(
        model_list=config.model_list,
        routing_strategy=config.router.routing_strategy,
        num_retries=config.router.num_retries,
        timeout=config.router.timeout,
        fallbacks=config.router.fallbacks,
        enable_cooldowns=config.router.enable_cooldowns,
        cooldown_time=config.router.cooldown_time,
        cooldown_failure_threshold=config.router.cooldown_failure_threshold,
    )

    # Create dynamic router (for database-backed deployments)
    # Parse fallbacks from config format [{model: [fallback1, fallback2]}] to {model: [fallbacks]}
    parsed_fallbacks = None
    if config.router.fallbacks:
        parsed_fallbacks = {}
        for fb_config in config.router.fallbacks:
            for primary_model, fallback_list in fb_config.items():
                parsed_fallbacks[primary_model] = fallback_list

    dynamic_router = DynamicRouter(
        routing_strategy=config.router.routing_strategy,
        num_retries=config.router.num_retries,
        timeout=config.router.timeout,
        fallbacks=parsed_fallbacks,
        enable_cooldowns=config.router.enable_cooldowns,
        cooldown_time=config.router.cooldown_time,
        cooldown_failure_threshold=config.router.cooldown_failure_threshold,
    )

    # Create rate limiter
    rate_limiter = RateLimiter(window_size=60)
    rate_limit_middleware = RateLimitMiddleware(
        rate_limiter,
        default_rpm=config.general.max_requests_per_minute or 60,
        default_tpm=config.general.max_tokens_per_minute or 10000,
    )

    # Create pricing manager and calculator
    pricing_manager = PricingManager(
        config_path=os.environ.get("PRICING_CONFIG_PATH", "config/pricing.yaml"),
        enable_hot_reload=True,
    )
    cost_calculator = CostCalculator(pricing_manager)

    # Create auth middleware
    auth_middleware = AuthMiddleware(key_manager)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """Application lifespan handler."""
        from deltallm.db.session import init_db, create_tables, get_session

        # Initialize database connection (always required)
        init_db()

        # Create tables if they don't exist
        await create_tables()

        # Store in app state
        app.state.router = router
        app.state.dynamic_router = dynamic_router
        app.state.key_manager = key_manager
        app.state.config = config
        app.state.rate_limiter = rate_limiter
        app.state.rate_limit_middleware = rate_limit_middleware
        app.state.auth_middleware = auth_middleware
        app.state.pricing_manager = pricing_manager
        app.state.cost_calculator = cost_calculator

        # Seed admin user if configured
        await seed_admin_user()

        # Load custom pricing from database
        try:
            async with get_session() as db:
                count = await pricing_manager.load_from_database(db)
                if count > 0:
                    logging.info(f"Loaded {count} custom pricing configurations from database")
        except Exception as e:
            logging.warning(f"Failed to load pricing from database: {e}")

        yield

        # Cleanup
        pass

    # Create app
    app = FastAPI(
        title="ProxyLLM",
        description="Unified LLM gateway with cost tracking, load balancing, and enterprise features",
        version="0.1.0",
        lifespan=lifespan,
    )

    # Add CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Rate limiting middleware (authentication now handled by route dependencies)
    @app.middleware("http")
    async def rate_limit_middleware_func(request: Request, call_next):
        """Apply rate limiting to requests.
        
        Note: Authentication is now handled by route-level dependencies (require_auth,
        require_user). This middleware only applies rate limiting if a valid key
        is present in the request state (set by route dependencies).
        """
        try:
            # Try to extract key info for rate limiting (from in-memory keys only)
            # DB-backed key rate limiting is handled in route dependencies
            key_info = await auth_middleware(request)
            request.state.key_info = key_info

            # Rate limit if we have key info
            rate_limit_info = None
            if key_info:
                rate_limit_info = await rate_limit_middleware.check(
                    request,
                    key_info.key_hash,
                    rpm=key_info.rpm_limit,
                    tpm=key_info.tpm_limit,
                )

            # Process request
            response = await call_next(request)

            # Add rate limit headers
            if rate_limit_info:
                headers = rate_limiter.get_headers(rate_limit_info)
                for key, value in headers.items():
                    response.headers[key] = value

            return response

        except HTTPException as e:
            return JSONResponse(
                status_code=e.status_code,
                content={
                    "error": {
                        "message": e.detail,
                        "type": "authentication_error" if e.status_code == 401 else "request_error",
                        "code": str(e.status_code),
                    }
                },
            )
        except ProxyLLMError as e:
            return JSONResponse(
                status_code=int(e.code) if e.code and e.code.isdigit() else 500,
                content={
                    "error": {
                        "message": e.message,
                        "type": e.type,
                        "param": e.param,
                        "code": e.code,
                    }
                },
            )

    # Exception handler
    @app.exception_handler(ProxyLLMError)
    async def deltallm_exception_handler(request: Request, exc: ProxyLLMError):
        """Handle ProxyLLM exceptions."""
        status_code = 500
        if exc.code and exc.code.isdigit():
            status_code = int(exc.code)

        return JSONResponse(
            status_code=status_code,
            content={
                "error": {
                    "message": exc.message,
                    "type": exc.type,
                    "param": exc.param,
                    "code": exc.code,
                }
            },
        )

    # Include routers
    app.include_router(auth_router, prefix="")  # Auth routes (login, register, etc.)
    app.include_router(chat_router, prefix="/v1")
    app.include_router(embeddings_router, prefix="/v1")
    app.include_router(audio_router, prefix="/v1")  # Audio routes (TTS, STT)
    app.include_router(files_router, prefix="/v1")  # File routes (for batch)
    app.include_router(batches_router, prefix="/v1")  # Batch routes
    app.include_router(models_router, prefix="/v1")
    app.include_router(keys_router, prefix="")
    app.include_router(health_router, prefix="")

    # Organization and Team management routes
    app.include_router(org_router, prefix="")
    app.include_router(team_router, prefix="")
    app.include_router(audit_router, prefix="")
    app.include_router(budget_router, prefix="")
    app.include_router(guardrails_router, prefix="")

    # Provider and Deployment management routes
    app.include_router(providers_router, prefix="")
    app.include_router(deployments_router, prefix="")

    # Pricing management routes
    app.include_router(pricing_router, prefix="")

    return app


def cli():
    """Command line interface for the proxy server."""
    import argparse
    import uvicorn

    parser = argparse.ArgumentParser(description="ProxyLLM Proxy Server")
    parser.add_argument(
        "--config", "-c",
        help="Path to configuration file",
        default=None,
    )
    parser.add_argument(
        "--host", "-H",
        help="Host to bind to",
        default="0.0.0.0",
    )
    parser.add_argument(
        "--port", "-p",
        help="Port to bind to",
        type=int,
        default=8000,
    )
    parser.add_argument(
        "--reload",
        help="Enable auto-reload",
        action="store_true",
    )

    args = parser.parse_args()

    # Create app
    app = create_app(args.config)

    # Run server
    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


if __name__ == "__main__":
    cli()
