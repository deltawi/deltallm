"""Unit tests for Team API endpoints."""

import uuid
from decimal import Decimal

import pytest
from fastapi import status
from fastapi.testclient import TestClient


@pytest.fixture
def sample_org_data():
    """Sample organization create data."""
    return {
        "name": "Test Organization",
        "slug": "test-org",
    }


@pytest.fixture
def sample_team_data():
    """Sample team create data."""
    return {
        "name": "Test Team",
        "slug": "test-team",
        "description": "A test team",
        "max_budget": 500.00,
    }


class TestTeamCreate:
    """Tests for POST /team/create endpoint."""
    
    def test_create_team_success(self, client: TestClient, sample_org_data, sample_team_data):
        """Test creating a team with valid data."""
        # Create organization first
        org_response = client.post("/org/create", json=sample_org_data)
        org_id = org_response.json()["id"]
        
        # Create team
        team_data = {**sample_team_data, "org_id": org_id}
        response = client.post("/team/create", json=team_data)
        
        assert response.status_code == status.HTTP_201_CREATED
        data = response.json()
        
        assert data["name"] == sample_team_data["name"]
        assert data["slug"] == sample_team_data["slug"]
        assert data["org_id"] == org_id
        assert Decimal(data["max_budget"]) == Decimal(str(sample_team_data["max_budget"]))
        assert data["spend"] == "0"
        assert "id" in data
        assert "created_at" in data
    
    def test_create_team_org_not_found(self, client: TestClient, sample_team_data):
        """Test creating a team in non-existent organization fails."""
        fake_org_id = str(uuid.uuid4())
        team_data = {**sample_team_data, "org_id": fake_org_id}
        
        response = client.post("/team/create", json=team_data)
        
        assert response.status_code == status.HTTP_404_NOT_FOUND
    
    def test_create_team_missing_name(self, client: TestClient, sample_org_data):
        """Test creating a team without name fails."""
        # Create organization
        org_response = client.post("/org/create", json=sample_org_data)
        org_id = org_response.json()["id"]
        
        response = client.post("/team/create", json={
            "org_id": org_id,
            "slug": "test-team",
        })
        
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    
    def test_create_team_invalid_slug(self, client: TestClient, sample_org_data):
        """Test creating a team with invalid slug fails."""
        # Create organization
        org_response = client.post("/org/create", json=sample_org_data)
        org_id = org_response.json()["id"]
        
        response = client.post("/team/create", json={
            "org_id": org_id,
            "name": "Test Team",
            "slug": "Invalid_Slug",
        })
        
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    
    def test_create_team_duplicate_slug_same_org(self, client: TestClient, sample_org_data, sample_team_data):
        """Test creating teams with same slug in same org fails."""
        # Create organization
        org_response = client.post("/org/create", json=sample_org_data)
        org_id = org_response.json()["id"]
        
        # Create first team
        team_data = {**sample_team_data, "org_id": org_id}
        response1 = client.post("/team/create", json=team_data)
        assert response1.status_code == status.HTTP_201_CREATED
        
        # Try to create second team with same slug
        response2 = client.post("/team/create", json={
            **team_data,
            "name": "Another Team",
        })
        
        assert response2.status_code == status.HTTP_409_CONFLICT
    
    def test_create_team_same_slug_different_org(self, client: TestClient, sample_team_data):
        """Test creating teams with same slug in different orgs succeeds."""
        # Create two organizations
        org1_response = client.post("/org/create", json={
            "name": "Org 1",
            "slug": "org-1",
        })
        org1_id = org1_response.json()["id"]
        
        org2_response = client.post("/org/create", json={
            "name": "Org 2",
            "slug": "org-2",
        })
        org2_id = org2_response.json()["id"]
        
        # Create team in first org
        team_data1 = {**sample_team_data, "org_id": org1_id}
        response1 = client.post("/team/create", json=team_data1)
        assert response1.status_code == status.HTTP_201_CREATED
        
        # Create team with same slug in second org
        team_data2 = {**sample_team_data, "org_id": org2_id}
        response2 = client.post("/team/create", json=team_data2)
        assert response2.status_code == status.HTTP_201_CREATED


class TestTeamGet:
    """Tests for GET /team/{id} endpoint."""
    
    def test_get_team_success(self, client: TestClient, sample_org_data, sample_team_data):
        """Test getting an existing team."""
        # Create organization and team
        org_response = client.post("/org/create", json=sample_org_data)
        org_id = org_response.json()["id"]
        
        team_data = {**sample_team_data, "org_id": org_id}
        create_response = client.post("/team/create", json=team_data)
        team_id = create_response.json()["id"]
        
        # Get team
        response = client.get(f"/team/{team_id}")
        
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        
        assert data["id"] == team_id
        assert data["name"] == sample_team_data["name"]
        assert data["org_id"] == org_id
    
    def test_get_team_not_found(self, client: TestClient):
        """Test getting a non-existent team returns 404."""
        fake_id = str(uuid.uuid4())
        response = client.get(f"/team/{fake_id}")
        
        assert response.status_code == status.HTTP_404_NOT_FOUND


class TestTeamUpdate:
    """Tests for POST /team/{id}/update endpoint."""
    
    def test_update_team_success(self, client: TestClient, sample_org_data, sample_team_data):
        """Test updating a team."""
        # Create organization and team
        org_response = client.post("/org/create", json=sample_org_data)
        org_id = org_response.json()["id"]
        
        team_data = {**sample_team_data, "org_id": org_id}
        create_response = client.post("/team/create", json=team_data)
        team_id = create_response.json()["id"]
        
        # Update team
        update_data = {
            "name": "Updated Team Name",
            "description": "Updated description",
        }
        response = client.post(f"/team/{team_id}/update", json=update_data)
        
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        
        assert data["name"] == "Updated Team Name"
        assert data["description"] == "Updated description"
        assert data["slug"] == sample_team_data["slug"]  # Unchanged
    
    def test_update_team_not_found(self, client: TestClient):
        """Test updating a non-existent team returns 404."""
        fake_id = str(uuid.uuid4())
        response = client.post(f"/team/{fake_id}/update", json={
            "name": "Updated Name",
        })
        
        assert response.status_code == status.HTTP_404_NOT_FOUND


class TestTeamDelete:
    """Tests for DELETE /team/{id} endpoint."""
    
    def test_delete_team_success(self, client: TestClient, sample_org_data, sample_team_data):
        """Test deleting a team."""
        # Create organization and team
        org_response = client.post("/org/create", json=sample_org_data)
        org_id = org_response.json()["id"]
        
        team_data = {**sample_team_data, "org_id": org_id}
        create_response = client.post("/team/create", json=team_data)
        team_id = create_response.json()["id"]
        
        # Delete team
        response = client.delete(f"/team/{team_id}")
        
        assert response.status_code == status.HTTP_204_NO_CONTENT
        
        # Verify deletion
        get_response = client.get(f"/team/{team_id}")
        assert get_response.status_code == status.HTTP_404_NOT_FOUND
    
    def test_delete_team_not_found(self, client: TestClient):
        """Test deleting a non-existent team returns 404."""
        fake_id = str(uuid.uuid4())
        response = client.delete(f"/team/{fake_id}")
        
        assert response.status_code == status.HTTP_404_NOT_FOUND


class TestTeamMembers:
    """Tests for team member management endpoints."""
    
    def test_list_members_empty(self, client: TestClient, sample_org_data, sample_team_data):
        """Test listing members when team has no members."""
        # Create organization and team
        org_response = client.post("/org/create", json=sample_org_data)
        org_id = org_response.json()["id"]
        
        team_data = {**sample_team_data, "org_id": org_id}
        create_response = client.post("/team/create", json=team_data)
        team_id = create_response.json()["id"]
        
        response = client.get(f"/team/{team_id}/members")
        
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        
        assert data["total"] == 0
        assert data["items"] == []
    
    def test_list_members_team_not_found(self, client: TestClient):
        """Test listing members of non-existent team returns 404."""
        fake_id = str(uuid.uuid4())
        response = client.get(f"/team/{fake_id}/members")
        
        assert response.status_code == status.HTTP_404_NOT_FOUND
    
    def test_add_member_team_not_found(self, client: TestClient):
        """Test adding member to non-existent team returns 404."""
        fake_id = str(uuid.uuid4())
        response = client.post(f"/team/{fake_id}/member/add", json={
            "user_id": str(uuid.uuid4()),
            "role": "member",
        })
        
        assert response.status_code == status.HTTP_404_NOT_FOUND
    
    def test_remove_member(self, client: TestClient, sample_org_data, sample_team_data):
        """Test removing a member from a team."""
        # Create organization and team
        org_response = client.post("/org/create", json=sample_org_data)
        org_id = org_response.json()["id"]
        
        team_data = {**sample_team_data, "org_id": org_id}
        create_response = client.post("/team/create", json=team_data)
        team_id = create_response.json()["id"]
        
        # Try to remove non-existent member
        fake_user_id = str(uuid.uuid4())
        response = client.delete(f"/team/{team_id}/member/{fake_user_id}")
        
        assert response.status_code == status.HTTP_404_NOT_FOUND


class TestUserTeams:
    """Tests for GET /team/user/{user_id}/teams endpoint."""
    
    def test_list_user_teams_empty(self, client: TestClient):
        """Test listing teams for a user with no teams."""
        fake_user_id = str(uuid.uuid4())
        response = client.get(f"/team/user/{fake_user_id}/teams")
        
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        
        assert data["total"] == 0
        assert data["items"] == []
    
    def test_list_user_teams_with_filter(self, client: TestClient, sample_org_data, sample_team_data):
        """Test listing teams filtered by organization."""
        # Create organization
        org_response = client.post("/org/create", json=sample_org_data)
        org_id = org_response.json()["id"]
        
        # Create team
        team_data = {**sample_team_data, "org_id": org_id}
        client.post("/team/create", json=team_data)
        
        fake_user_id = str(uuid.uuid4())
        
        # Get teams with org filter
        response = client.get(f"/team/user/{fake_user_id}/teams?org_id={org_id}")
        
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        
        # Will be empty since user isn't actually a member
        assert "total" in data
        assert "items" in data
    
    def test_list_user_teams_pagination(self, client: TestClient):
        """Test listing user teams with pagination."""
        fake_user_id = str(uuid.uuid4())
        response = client.get(f"/team/user/{fake_user_id}/teams?page=1&page_size=10")
        
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        
        assert data["page"] == 1
        assert data["page_size"] == 10


class TestTeamMemberUpdate:
    """Tests for POST /team/{id}/member/{user_id}/update endpoint."""
    
    def test_update_member_not_found(self, client: TestClient, sample_org_data, sample_team_data):
        """Test updating a non-existent member returns 404."""
        # Create organization and team
        org_response = client.post("/org/create", json=sample_org_data)
        org_id = org_response.json()["id"]
        
        team_data = {**sample_team_data, "org_id": org_id}
        create_response = client.post("/team/create", json=team_data)
        team_id = create_response.json()["id"]
        
        fake_user_id = str(uuid.uuid4())
        response = client.post(f"/team/{team_id}/member/{fake_user_id}/update", json={
            "role": "admin",
        })
        
        assert response.status_code == status.HTTP_404_NOT_FOUND
    
    def test_update_member_invalid_role(self, client: TestClient, sample_org_data, sample_team_data):
        """Test updating member with invalid role returns 422."""
        # Create organization and team
        org_response = client.post("/org/create", json=sample_org_data)
        org_id = org_response.json()["id"]
        
        team_data = {**sample_team_data, "org_id": org_id}
        create_response = client.post("/team/create", json=team_data)
        team_id = create_response.json()["id"]
        
        fake_user_id = str(uuid.uuid4())
        response = client.post(f"/team/{team_id}/member/{fake_user_id}/update", json={
            "role": "invalid_role",
        })
        
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
