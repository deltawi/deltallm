#!/bin/bash

# DeltaLLM Development Runner
# Runs dependencies (Postgres, Redis) in Docker, backend and frontend locally
#
# Usage:
#   ./dev_run.sh --deps     # Start only dependencies (Postgres, Redis)
#   ./dev_run.sh --server   # Check/start dependencies + FastAPI server
#   ./dev_run.sh --ui       # Check/start dependencies + Dashboard UI
#   ./dev_run.sh --all      # Start everything (deps + server + ui)
#   ./dev_run.sh --stop     # Stop all dependencies
#
# Prerequisites:
#   - Docker and docker-compose installed
#   - Python 3.11+ with uv/pip
#   - Node.js 18+ with npm

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DOCKER_DIR="$SCRIPT_DIR/docker"
COMPOSE_FILE="$DOCKER_DIR/docker-compose.yaml"
PROJECT_NAME="deltallm"

# Service names for dependency checking
POSTGRES_CONTAINER="deltallm-postgres"
REDIS_CONTAINER="deltallm-redis"

# Default environment variables
# Note: Using postgresql+asyncpg:// for async SQLAlchemy support
export DATABASE_URL="${DATABASE_URL:-postgresql+asyncpg://deltallm:deltallm@localhost:5432/deltallm}"
export REDIS_URL="${REDIS_URL:-redis://localhost:6379/0}"
export DELTALLM_MASTER_KEY="${DELTALLM_MASTER_KEY:-sk-master-default}"
export ADMIN_EMAIL="${ADMIN_EMAIL:-admin@deltallm.local}"
export ADMIN_PASSWORD="${ADMIN_PASSWORD:-admin123}"

# Help message
show_help() {
    cat << EOF
DeltaLLM Development Runner

Usage: $0 [OPTION]

Options:
    --deps      Start only dependencies (Postgres, Redis) in Docker
    --server    Check/start dependencies + run FastAPI backend server
    --ui        Check/start dependencies + run Dashboard UI (React)
    --all       Start everything (deps + server + ui in separate terminals)
    --stop      Stop all dependencies
    --reset-db  Stop dependencies and reset database volumes
    --status    Check status of all services
    -h, --help  Show this help message

Examples:
    $0 --deps       # Just start Postgres and Redis
    $0 --server     # Ensure deps are running, then run FastAPI server
    $0 --ui         # Ensure deps are running, then run Dashboard UI
    $0 --all        # Start everything for full development

EOF
}

# Log functions
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Check prerequisites
check_prerequisites() {
    log_info "Checking prerequisites..."
    
    if ! command -v docker &> /dev/null; then
        log_error "Docker is not installed. Please install Docker first."
        exit 1
    fi
    
    if ! command -v docker-compose &> /dev/null; then
        log_error "docker-compose is not installed. Please install docker-compose first."
        exit 1
    fi
    
    if [ ! -f "$COMPOSE_FILE" ]; then
        log_error "Docker compose file not found: $COMPOSE_FILE"
        exit 1
    fi
    
    log_success "Prerequisites check passed"
}

# Check if a container is running
check_container_running() {
    local container_name="$1"
    docker ps --format "{{.Names}}" | grep -q "^${container_name}$"
}

# Check if a container is healthy
check_container_healthy() {
    local container_name="$1"
    local health_status
    health_status=$(docker inspect --format="{{.State.Health.Status}}" "$container_name" 2>/dev/null || echo "none")
    
    if [ "$health_status" = "healthy" ]; then
        return 0
    elif [ "$health_status" = "none" ]; then
        check_container_running "$container_name"
        return $?
    else
        return 1
    fi
}

# Check if dependencies are running
check_deps() {
    if check_container_running "$POSTGRES_CONTAINER" && check_container_running "$REDIS_CONTAINER"; then
        return 0
    else
        return 1
    fi
}

# Wait for dependencies to be healthy
wait_for_deps() {
    log_info "Waiting for dependencies to be healthy..."
    
    local max_attempts=30
    local attempt=1
    
    while [ $attempt -le $max_attempts ]; do
        local postgres_ready=false
        local redis_ready=false
        
        if check_container_healthy "$POSTGRES_CONTAINER"; then
            postgres_ready=true
        fi
        
        if check_container_healthy "$REDIS_CONTAINER"; then
            redis_ready=true
        fi
        
        if [ "$postgres_ready" = true ] && [ "$redis_ready" = true ]; then
            log_success "All dependencies are healthy!"
            return 0
        fi
        
        echo -n "."
        sleep 1
        attempt=$((attempt + 1))
    done
    
    echo ""
    log_error "Dependencies failed to become healthy within $max_attempts seconds"
    return 1
}

# Start dependencies using existing docker-compose
start_deps() {
    log_info "Starting dependencies (Postgres, Redis)..."
    
    cd "$DOCKER_DIR"
    
    # Start only postgres and redis services
    docker-compose -p "$PROJECT_NAME" up -d postgres redis
    
    # Wait for them to be healthy
    wait_for_deps
    
    log_success "Dependencies are running!"
    echo ""
    echo -e "  Postgres: ${GREEN}postgresql://deltallm:deltallm@localhost:5432/deltallm${NC}"
    echo -e "  Redis:    ${GREEN}redis://localhost:6379/0${NC}"
    echo ""
}

# Ensure dependencies are running
ensure_deps() {
    if check_deps; then
        log_info "Dependencies are already running"
        if ! check_container_healthy "$POSTGRES_CONTAINER" || ! check_container_healthy "$REDIS_CONTAINER"; then
            log_warn "Dependencies running but may not be fully ready yet..."
            wait_for_deps
        fi
    else
        log_info "Dependencies not running, starting them..."
        start_deps
    fi
}

# Stop dependencies
stop_deps() {
    log_info "Stopping dependencies..."
    
    cd "$DOCKER_DIR"
    docker-compose -p "$PROJECT_NAME" stop postgres redis
    log_success "Dependencies stopped"
}

# Reset database
reset_db() {
    log_warn "This will DELETE all data in the development database!"
    read -p "Are you sure? (y/N) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        log_info "Resetting database..."
        cd "$DOCKER_DIR"
        docker-compose -p "$PROJECT_NAME" down -v postgres redis
        log_success "Database reset. Run '$0 --deps' to start fresh."
    else
        log_info "Cancelled"
    fi
}

# Check status
check_status() {
    log_info "Checking service status..."
    echo ""
    
    # Check Docker containers
    echo "Docker Containers:"
    docker ps --filter "name=deltallm" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" 2>/dev/null || echo "  No containers running"
    echo ""
    
    # Check ports
    echo "Port Status:"
    if lsof -Pi :5432 -sTCP:LISTEN -t >/dev/null 2>&1; then
        echo -e "  Postgres (5432): ${GREEN}LISTENING${NC}"
    else
        echo -e "  Postgres (5432): ${RED}NOT LISTENING${NC}"
    fi
    
    if lsof -Pi :6379 -sTCP:LISTEN -t >/dev/null 2>&1; then
        echo -e "  Redis (6379):    ${GREEN}LISTENING${NC}"
    else
        echo -e "  Redis (6379):    ${RED}NOT LISTENING${NC}"
    fi
    
    if lsof -Pi :8000 -sTCP:LISTEN -t >/dev/null 2>&1; then
        echo -e "  API (8000):      ${GREEN}LISTENING${NC}"
    else
        echo -e "  API (8000):      ${RED}NOT LISTENING${NC}"
    fi
    
    if lsof -Pi :3000 -sTCP:LISTEN -t >/dev/null 2>&1; then
        echo -e "  UI (3000):       ${GREEN}LISTENING${NC}"
    else
        echo -e "  UI (3000):       ${RED}NOT LISTENING${NC}"
    fi
    echo ""
}

# Setup Python environment
setup_python() {
    log_info "Setting up Python environment..."
    
    cd "$SCRIPT_DIR"
    
    # Determine Python command to use
    if command -v python &> /dev/null; then
        PYTHON_CMD="python"
    else
        PYTHON_CMD="python3"
    fi
    
    # Check if already in a virtual environment
    if [ -n "$VIRTUAL_ENV" ]; then
        log_info "Already in virtual environment: $VIRTUAL_ENV"
        PIP_CMD="$PYTHON_CMD -m pip"
    elif [ -d ".venv" ]; then
        log_info "Activating virtual environment..."
        source .venv/bin/activate
        PIP_CMD="$PYTHON_CMD -m pip"
    else
        log_info "Creating virtual environment..."
        $PYTHON_CMD -m venv .venv
        source .venv/bin/activate
        PIP_CMD="$PYTHON_CMD -m pip"
    fi
    
    if ! $PIP_CMD show deltallm > /dev/null 2>&1; then
        log_info "Installing Python dependencies..."
        $PIP_CMD install -e ".[providers]"
    fi
    
    log_success "Python environment ready"
}

# Start FastAPI server
start_server() {
    log_info "Starting FastAPI server..."
    
    cd "$SCRIPT_DIR"
    
    # Setup Python environment (creates venv and installs package if needed)
    # This also sets PYTHON_CMD to either 'python' or 'python3'
    setup_python
    
    # Log which environment we're using
    if [ -n "$VIRTUAL_ENV" ]; then
        log_info "Using active virtual environment: $VIRTUAL_ENV"
    elif [ -d ".venv" ]; then
        log_info "Using virtual environment: .venv"
    else
        log_warn "Using system Python"
    fi
    
    # Check if asyncpg is installed (required for async PostgreSQL)
    if ! $PYTHON_CMD -c "import asyncpg" > /dev/null 2>&1; then
        log_warn "asyncpg not found. Installing..."
        $PYTHON_CMD -m pip install asyncpg
    fi
    
    log_info "Environment variables:"
    echo "  DATABASE_URL=$DATABASE_URL"
    echo "  REDIS_URL=$REDIS_URL"
    echo "  DELTALLM_MASTER_KEY=$DELTALLM_MASTER_KEY"
    echo ""
    log_info "Starting server at http://localhost:8000"
    echo "  API docs: http://localhost:8000/docs"
    echo "  Health:   http://localhost:8000/health"
    echo ""
    
    $PYTHON_CMD -m uvicorn deltallm.proxy.app:create_app --factory --host 0.0.0.0 --port 8000 --reload --reload-dir deltallm
}

# Setup Node environment
setup_node() {
    log_info "Setting up Node.js environment..."
    
    cd "$SCRIPT_DIR/admin-dashboard"
    
    if [ ! -d "node_modules" ]; then
        log_info "Installing npm dependencies..."
        npm install --legacy-peer-deps
    fi
    
    log_success "Node.js environment ready"
}

# Start Dashboard UI
start_ui() {
    log_info "Starting Dashboard UI..."
    
    setup_node
    
    cd "$SCRIPT_DIR/admin-dashboard"
    
    log_info "Starting UI dev server at http://localhost:3000"
    echo "  API proxy: http://localhost:8000"
    echo ""
    
    npm run dev
}

# Main function
main() {
    case "${1:-}" in
        --deps)
            check_prerequisites
            start_deps
            log_success "Dependencies are running in Docker"
            log_info "To stop: $0 --stop"
            ;;
        --server)
            check_prerequisites
            ensure_deps
            echo ""
            start_server
            ;;
        --ui)
            check_prerequisites
            ensure_deps
            echo ""
            start_ui
            ;;
        --all)
            check_prerequisites
            ensure_deps
            
            echo ""
            log_info "Starting both Server and UI..."
            
            # Start UI in background terminal and server in current
            if command -v osascript &> /dev/null; then
                log_info "Opening UI in new terminal window..."
                osascript -e "tell application \"Terminal\" to do script \"cd '$SCRIPT_DIR' && $0 --ui-only\""
                sleep 2
                start_server
            elif command -v gnome-terminal &> /dev/null; then
                log_info "Opening UI in new terminal window..."
                gnome-terminal -- bash -c "cd '$SCRIPT_DIR' && $0 --ui-only; exec bash"
                sleep 2
                start_server
            else
                log_warn "Cannot open new terminal. Starting server in background..."
                (
                    cd "$SCRIPT_DIR"
                    # Setup Python environment first (sets PYTHON_CMD)
                    setup_python
                    nohup $PYTHON_CMD -m uvicorn deltallm.proxy.app:create_app --factory --host 0.0.0.0 --port 8000 --reload --reload-dir deltallm > /tmp/deltallm-server.log 2>&1 &
                )
                sleep 3
                log_info "Server logs: tail -f /tmp/deltallm-server.log"
                start_ui
            fi
            ;;
        --ui-only)
            # Internal use only
            start_ui
            ;;
        --stop)
            stop_deps
            ;;
        --reset-db)
            reset_db
            ;;
        --status)
            check_status
            ;;
        -h|--help|help)
            show_help
            ;;
        *)
            if [ -z "$1" ]; then
                show_help
            else
                log_error "Unknown option: $1"
                echo ""
                show_help
                exit 1
            fi
            ;;
    esac
}

# Run main function
main "$@"
