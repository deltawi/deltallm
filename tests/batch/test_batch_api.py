"""Tests for Batch API endpoints.

This module tests the file and batch API endpoints.
"""

import json
import pytest
from decimal import Decimal
from io import BytesIO
from uuid import uuid4, UUID


class TestBatchCostCalculation:
    """Test batch cost calculation with 50% discount."""
    
    def test_batch_discount_calculation(self):
        """Test that batch discount is correctly calculated."""
        from deltallm.pricing.calculator import CostCalculator
        from deltallm.pricing.manager import PricingManager
        
        pricing_manager = PricingManager(enable_hot_reload=False)
        calculator = CostCalculator(pricing_manager)
        
        # Regular cost
        regular = calculator.calculate_chat_cost(
            model="gpt-4o",
            prompt_tokens=1000,
            completion_tokens=500,
            is_batch=False,
        )
        
        # Batch cost (50% discount)
        batch = calculator.calculate_chat_cost(
            model="gpt-4o",
            prompt_tokens=1000,
            completion_tokens=500,
            is_batch=True,
        )
        
        # Verify discount was applied
        assert batch.discount_percent == 50.0
        assert batch.batch_discount is not None
        assert batch.batch_discount > 0
        assert batch.total_cost < regular.total_cost
        
        # Verify discount amount is roughly 50%
        discount_ratio = batch.total_cost / regular.total_cost
        assert 0.49 <= discount_ratio <= 0.51  # Allow small rounding differences
    
    def test_batch_cost_breakdown(self):
        """Test batch cost breakdown fields."""
        from deltallm.pricing.calculator import CostCalculator
        from deltallm.pricing.manager import PricingManager
        
        pricing_manager = PricingManager(enable_hot_reload=False)
        calculator = CostCalculator(pricing_manager)
        
        batch = calculator.calculate_chat_cost(
            model="gpt-4o",
            prompt_tokens=1000,
            completion_tokens=500,
            is_batch=True,
        )
        
        # Check that all expected fields are present
        assert batch.total_cost is not None
        assert batch.input_cost is not None
        assert batch.output_cost is not None
        assert batch.batch_discount is not None
        assert batch.discount_percent == 50.0
        
        # Verify original cost calculation
        assert batch.original_cost == batch.total_cost + batch.batch_discount


class TestBatchSchemas:
    """Test batch-related Pydantic schemas."""
    
    def test_create_batch_request_validation(self):
        """Test CreateBatchRequest validation."""
        from deltallm.proxy.schemas_batch import CreateBatchRequest
        
        # Valid request
        request = CreateBatchRequest(
            input_file_id="file-abc123",
            endpoint="/v1/chat/completions",
            completion_window="24h",
        )
        assert request.input_file_id == "file-abc123"
        assert request.endpoint == "/v1/chat/completions"
        
        # Valid with metadata
        request_with_meta = CreateBatchRequest(
            input_file_id="file-abc123",
            endpoint="/v1/chat/completions",
            metadata={"key1": "value1"},
        )
        assert request_with_meta.metadata == {"key1": "value1"}
    
    def test_create_batch_request_invalid_endpoint(self):
        """Test CreateBatchRequest with invalid endpoint."""
        from deltallm.proxy.schemas_batch import CreateBatchRequest
        
        with pytest.raises(Exception):  # ValidationError
            CreateBatchRequest(
                input_file_id="file-abc123",
                endpoint="/v1/invalid",
                completion_window="24h",
            )
    
    def test_create_batch_request_invalid_metadata(self):
        """Test CreateBatchRequest with invalid metadata."""
        from deltallm.proxy.schemas_batch import CreateBatchRequest
        
        # Too many keys
        with pytest.raises(ValueError, match="at most 16"):
            CreateBatchRequest(
                input_file_id="file-abc123",
                endpoint="/v1/chat/completions",
                metadata={f"key_{i}": "value" for i in range(20)},
            )
        
        # Key too long
        with pytest.raises(ValueError, match="exceeds 64 characters"):
            CreateBatchRequest(
                input_file_id="file-abc123",
                endpoint="/v1/chat/completions",
                metadata={"a" * 65: "value"},
            )
        
        # Value too long
        with pytest.raises(ValueError, match="exceeds 512 characters"):
            CreateBatchRequest(
                input_file_id="file-abc123",
                endpoint="/v1/chat/completions",
                metadata={"key": "a" * 513},
            )
    
    def test_batch_response_schema(self):
        """Test BatchResponse schema."""
        from deltallm.proxy.schemas_batch import BatchResponse, BatchRequestCounts, BatchCostInfo
        
        response = BatchResponse(
            id="batch-abc123",
            endpoint="/v1/chat/completions",
            input_file_id="file-abc123",
            completion_window="24h",
            status="completed",
            created_at=1234567890,
            request_counts=BatchRequestCounts(
                total=10,
                completed=8,
                failed=2,
            ),
            cost_info=BatchCostInfo(
                original_cost=1.0,
                discounted_cost=0.5,
                discount_amount=0.5,
            ),
        )
        
        assert response.id == "batch-abc123"
        assert response.status == "completed"
        assert response.request_counts.total == 10
        assert response.request_counts.completed == 8
        assert response.request_counts.failed == 2
        assert response.cost_info.discounted_cost == 0.5
    
    def test_file_schemas(self):
        """Test file schemas."""
        from deltallm.proxy.schemas_batch import (
            FileUploadResponse,
            FileInfoResponse,
            FileListResponse,
            FileDeleteResponse,
        )
        
        # Test FileUploadResponse
        upload_response = FileUploadResponse(
            id="file-abc123",
            bytes=1000,
            created_at=1234567890,
            filename="test.jsonl",
            purpose="batch",
        )
        assert upload_response.object == "file"
        assert upload_response.purpose == "batch"
        
        # Test FileListResponse
        list_response = FileListResponse(
            data=[
                FileInfoResponse(
                    id="file-abc123",
                    bytes=1000,
                    created_at=1234567890,
                    filename="test.jsonl",
                    purpose="batch",
                )
            ]
        )
        assert list_response.object == "list"
        assert len(list_response.data) == 1
        
        # Test FileDeleteResponse
        delete_response = FileDeleteResponse(
            id="file-abc123",
            deleted=True,
        )
        assert delete_response.deleted is True


class TestBatchProcessor:
    """Test batch processor functionality."""
    
    def test_batch_processor_initialization(self):
        """Test BatchProcessor can be initialized."""
        from deltallm.batch.processor import BatchProcessor
        
        # Mock session
        mock_session = type('MockSession', (), {})()
        
        processor = BatchProcessor(mock_session)
        assert processor.db == mock_session
        assert processor.pricing is not None
        assert processor.calculator is not None
    
    @pytest.mark.asyncio
    async def test_batch_processor_process_single_request_chat(self):
        """Test processing a single chat completion request."""
        from deltallm.batch.processor import BatchProcessor
        
        mock_session = type('MockSession', (), {})()
        processor = BatchProcessor(mock_session)
        
        request_data = {
            "custom_id": "test-1",
            "method": "POST",
            "url": "/v1/chat/completions",
            "body": {
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "Hello!"}],
            }
        }
        
        result = await processor._process_single_request(
            request_data,
            "/v1/chat/completions"
        )
        
        assert result["custom_id"] == "test-1"
        assert "response" in result
        assert result["response"]["status_code"] == 200
        assert "body" in result["response"]
        assert result["response"]["body"]["object"] == "chat.completion"
    
    @pytest.mark.asyncio
    async def test_batch_processor_process_single_request_embeddings(self):
        """Test processing a single embeddings request."""
        from deltallm.batch.processor import BatchProcessor
        
        mock_session = type('MockSession', (), {})()
        processor = BatchProcessor(mock_session)
        
        request_data = {
            "custom_id": "test-1",
            "method": "POST",
            "url": "/v1/embeddings",
            "body": {
                "model": "text-embedding-3-small",
                "input": "Hello world",
            }
        }
        
        result = await processor._process_single_request(
            request_data,
            "/v1/embeddings"
        )
        
        assert result["custom_id"] == "test-1"
        assert "response" in result
        assert result["response"]["status_code"] == 200
        assert "body" in result["response"]
    
    def test_batch_processor_error(self):
        """Test BatchProcessingError."""
        from deltallm.batch.processor import BatchProcessingError
        
        error = BatchProcessingError("Test error", "batch-123")
        assert error.message == "Test error"
        assert error.batch_id == "batch-123"
        assert str(error) == "Test error"
    
    def test_batch_processor_error_no_batch_id(self):
        """Test BatchProcessingError without batch_id."""
        from deltallm.batch.processor import BatchProcessingError
        
        error = BatchProcessingError("Test error")
        assert error.message == "Test error"
        assert error.batch_id is None


class TestBatchInputOutputSchemas:
    """Test batch input/output line schemas."""
    
    def test_batch_input_line(self):
        """Test BatchInputLine schema."""
        from deltallm.proxy.schemas_batch import BatchInputLine
        
        line = BatchInputLine(
            custom_id="request-1",
            method="POST",
            url="/v1/chat/completions",
            body={
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "Hello!"}],
            }
        )
        
        assert line.custom_id == "request-1"
        assert line.method == "POST"
        assert line.url == "/v1/chat/completions"
        assert line.body["model"] == "gpt-4o"
    
    def test_batch_output_line(self):
        """Test BatchOutputLine schema."""
        from deltallm.proxy.schemas_batch import BatchOutputLine
        
        line = BatchOutputLine(
            id="response-1",
            custom_id="request-1",
            response={
                "status_code": 200,
                "body": {"choices": []},
            },
        )
        
        assert line.id == "response-1"
        assert line.custom_id == "request-1"
        assert line.response["status_code"] == 200
    
    def test_batch_error_line(self):
        """Test BatchErrorLine schema."""
        from deltallm.proxy.schemas_batch import BatchErrorLine
        
        line = BatchErrorLine(
            custom_id="request-1",
            error={
                "message": "Rate limit exceeded",
                "type": "rate_limit_error",
            },
        )
        
        assert line.custom_id == "request-1"
        assert line.error["type"] == "rate_limit_error"
