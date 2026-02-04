import axios, { AxiosInstance, AxiosError } from 'axios';
import type {
  User, Organization, Team, OrgMember, TeamMember, BudgetStatus,
  SpendLog, SpendSummary, AuditLog, ApiKey, LoginCredentials, AuthResponse,
  CreateOrganizationRequest, UpdateOrganizationRequest, CreateTeamRequest,
  UpdateTeamRequest, AddMemberRequest, PaginatedResponse, CreateApiKeyRequest,
  CreateApiKeyResponse, ModelList, ModelInfo, ProviderConfig, ProviderHealthStatus,
  ProviderTestResult, CreateProviderRequest, UpdateProviderRequest,
  ModelDeployment, ModelDeploymentWithProvider, CreateDeploymentRequest, UpdateDeploymentRequest,
  GuardrailPolicy, GuardrailsStatus, ContentCheckRequest, ContentCheckResponse, SetOrgPolicyResponse,
  ModelPricing, PricingCreateRequest
} from '@/types';

const API_BASE_URL = import.meta.env.VITE_API_URL || '/api';

class ApiService {
  private client: AxiosInstance;

  constructor() {
    this.client = axios.create({
      baseURL: API_BASE_URL,
      headers: {
        'Content-Type': 'application/json',
      },
    });

    // Request interceptor to add auth token
    this.client.interceptors.request.use((config) => {
      const token = localStorage.getItem('token');
      console.log('[API Request]', config.method?.toUpperCase(), config.url, 'Token:', token ? 'present' : 'missing');
      if (token) {
        config.headers.Authorization = `Bearer ${token}`;
      }
      return config;
    });

    // Response interceptor for error handling
    this.client.interceptors.response.use(
      (response) => {
        console.log('[API Response]', response.status, response.config.url);
        return response;
      },
      (error: AxiosError) => {
        console.error('[API Error]', error.response?.status, error.config?.url, error.message);
        if (error.response?.status === 401) {
          localStorage.removeItem('token');
          window.location.href = '/login';
        }
        return Promise.reject(error);
      }
    );
  }

  // Auth
  async login(credentials: LoginCredentials): Promise<AuthResponse> {
    const formData = new URLSearchParams();
    formData.append('username', credentials.username);
    formData.append('password', credentials.password);
    
    const response = await this.client.post<AuthResponse>('/auth/login', formData, {
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    });
    return response.data;
  }

  async getCurrentUser(): Promise<User> {
    const response = await this.client.get<User>('/auth/me');
    return response.data;
  }

  // Organizations
  async getOrganizations(): Promise<PaginatedResponse<Organization>> {
    const response = await this.client.get<PaginatedResponse<Organization>>('/org/list');
    return response.data;
  }

  async getOrganization(id: string): Promise<Organization> {
    const response = await this.client.get<Organization>(`/org/${id}`);
    return response.data;
  }

  async createOrganization(data: CreateOrganizationRequest): Promise<Organization> {
    const response = await this.client.post<Organization>('/org/create', data);
    return response.data;
  }

  async updateOrganization(id: string, data: UpdateOrganizationRequest): Promise<Organization> {
    const response = await this.client.post<Organization>(`/org/${id}/update`, data);
    return response.data;
  }

  async deleteOrganization(id: string): Promise<void> {
    await this.client.delete(`/org/${id}`);
  }

  // Organization Members
  async getOrgMembers(orgId: string): Promise<PaginatedResponse<OrgMember>> {
    const response = await this.client.get<PaginatedResponse<OrgMember>>(`/org/${orgId}/members`);
    return response.data;
  }

  async addOrgMember(orgId: string, data: AddMemberRequest): Promise<OrgMember> {
    const response = await this.client.post<OrgMember>(`/org/${orgId}/member/add`, data);
    return response.data;
  }

  async updateOrgMemberRole(orgId: string, userId: string, role: string): Promise<OrgMember> {
    const response = await this.client.post<OrgMember>(`/org/${orgId}/member/${userId}/update`, { role });
    return response.data;
  }

  async removeOrgMember(orgId: string, userId: string): Promise<void> {
    await this.client.delete(`/org/${orgId}/member/${userId}`);
  }

  // Teams
  async getTeams(orgId?: string): Promise<PaginatedResponse<Team>> {
    const params = orgId ? { org_id: orgId } : {};
    const response = await this.client.get<PaginatedResponse<Team>>('/team/list', { params });
    return response.data;
  }

  async getTeam(id: string): Promise<Team> {
    const response = await this.client.get<Team>(`/team/${id}`);
    return response.data;
  }

  async createTeam(data: CreateTeamRequest): Promise<Team> {
    const response = await this.client.post<Team>('/team/create', data);
    return response.data;
  }

  async updateTeam(id: string, data: UpdateTeamRequest): Promise<Team> {
    const response = await this.client.post<Team>(`/team/${id}/update`, data);
    return response.data;
  }

  async deleteTeam(id: string): Promise<void> {
    await this.client.delete(`/team/${id}`);
  }

  // Team Members
  async getTeamMembers(teamId: string): Promise<PaginatedResponse<TeamMember>> {
    const response = await this.client.get<PaginatedResponse<TeamMember>>(`/team/${teamId}/members`);
    return response.data;
  }

  async addTeamMember(teamId: string, data: AddMemberRequest): Promise<TeamMember> {
    const response = await this.client.post<TeamMember>(`/team/${teamId}/member/add`, data);
    return response.data;
  }

  async updateTeamMemberRole(teamId: string, userId: string, role: string): Promise<TeamMember> {
    const response = await this.client.post<TeamMember>(`/team/${teamId}/member/${userId}/update`, { role });
    return response.data;
  }

  async removeTeamMember(teamId: string, userId: string): Promise<void> {
    await this.client.delete(`/team/${teamId}/member/${userId}`);
  }

  // Budget
  async getOrgBudget(orgId: string): Promise<BudgetStatus> {
    const response = await this.client.get<BudgetStatus>(`/budget/org/${orgId}`);
    return response.data;
  }

  async getOrgBudgetFull(orgId: string): Promise<{ org_budget: BudgetStatus; team_budgets: BudgetStatus[] }> {
    const response = await this.client.get(`/budget/org/${orgId}/full`);
    return response.data;
  }

  async setOrgBudget(orgId: string, maxBudget: number): Promise<BudgetStatus> {
    const response = await this.client.post<BudgetStatus>(`/budget/org/${orgId}/set`, { max_budget: maxBudget });
    return response.data;
  }

  async getTeamBudget(teamId: string): Promise<BudgetStatus> {
    const response = await this.client.get<BudgetStatus>(`/budget/team/${teamId}`);
    return response.data;
  }

  async setTeamBudget(teamId: string, maxBudget: number): Promise<BudgetStatus> {
    const response = await this.client.post<BudgetStatus>(`/budget/team/${teamId}/set`, { max_budget: maxBudget });
    return response.data;
  }

  async getSpendLogs(params?: {
    org_id?: string;
    team_id?: string;
    api_key_id?: string;
    days?: number;
    limit?: number;
    offset?: number;
  }): Promise<{ total: number; logs: SpendLog[] }> {
    const response = await this.client.get('/budget/logs', { params });
    return response.data;
  }

  async getSpendSummary(params?: {
    org_id?: string;
    team_id?: string;
    days?: number;
  }): Promise<SpendSummary> {
    const response = await this.client.get('/budget/summary', { params });
    return response.data;
  }

  // Audit Logs
  async getAuditLogs(params?: {
    org_id?: string;
    action?: string;
    limit?: number;
    offset?: number;
  }): Promise<PaginatedResponse<AuditLog>> {
    const response = await this.client.get<PaginatedResponse<AuditLog>>('/audit/logs', { params });
    return response.data;
  }

  // API Keys
  async getApiKeys(params?: { org_id?: string; team_id?: string }): Promise<PaginatedResponse<ApiKey>> {
    const response = await this.client.get<{ keys: ApiKey[] }>('/key/list', { params });
    // Transform backend format { keys: [] } to PaginatedResponse format
    const keys = response.data.keys || [];
    return {
      items: keys.map(k => ({ ...k, id: k.key_hash })), // Use key_hash as id since backend doesn't return id
      total: keys.length,
      page: 1,
      page_size: keys.length,
      pages: 1,
    };
  }

  async getApiKey(keyHash: string): Promise<ApiKey | null> {
    const response = await this.getApiKeys();
    return response.items.find(k => k.key_hash === keyHash) || null;
  }

  async createApiKey(data: CreateApiKeyRequest): Promise<CreateApiKeyResponse> {
    const response = await this.client.post<CreateApiKeyResponse>('/key/generate', data);
    return response.data;
  }

  async deleteApiKey(keyHash: string): Promise<void> {
    await this.client.delete(`/key/${keyHash}`);
  }

  // Models
  async getModels(orgId?: string): Promise<ModelList> {
    const params = orgId ? { org_id: orgId } : {};
    const response = await this.client.get<ModelList>('/v1/models', { params });
    return response.data;
  }

  async getModel(modelId: string): Promise<ModelInfo> {
    const response = await this.client.get<ModelInfo>(`/v1/models/${encodeURIComponent(modelId)}`);
    return response.data;
  }

  // Providers
  async getProviders(params?: {
    org_id?: string;
    provider_type?: string;
    is_active?: boolean;
  }): Promise<PaginatedResponse<ProviderConfig>> {
    const response = await this.client.get<PaginatedResponse<ProviderConfig>>('/v1/providers', { params });
    return response.data;
  }

  async getProvider(id: string): Promise<ProviderConfig> {
    const response = await this.client.get<ProviderConfig>(`/v1/providers/${id}`);
    return response.data;
  }

  async createProvider(data: CreateProviderRequest): Promise<ProviderConfig> {
    const response = await this.client.post<ProviderConfig>('/v1/providers', data);
    return response.data;
  }

  async updateProvider(id: string, data: UpdateProviderRequest): Promise<ProviderConfig> {
    const response = await this.client.patch<ProviderConfig>(`/v1/providers/${id}`, data);
    return response.data;
  }

  async deleteProvider(id: string, force?: boolean): Promise<void> {
    const params = force ? { force: true } : {};
    await this.client.delete(`/v1/providers/${id}`, { params });
  }

  async testProviderConnectivity(id: string): Promise<ProviderTestResult> {
    const response = await this.client.post<ProviderTestResult>(`/v1/providers/${id}/test`);
    return response.data;
  }

  async getProviderHealth(id: string): Promise<ProviderHealthStatus> {
    const response = await this.client.get<ProviderHealthStatus>(`/v1/providers/${id}/health`);
    return response.data;
  }

  // Model Deployments
  async getDeployments(params?: {
    model_name?: string;
    provider_id?: string;
    org_id?: string;
    is_active?: boolean;
    page?: number;
    page_size?: number;
  }): Promise<PaginatedResponse<ModelDeployment>> {
    const response = await this.client.get<PaginatedResponse<ModelDeployment>>('/v1/deployments', { params });
    return response.data;
  }

  async getDeployment(id: string): Promise<ModelDeploymentWithProvider> {
    const response = await this.client.get<ModelDeploymentWithProvider>(`/v1/deployments/${id}`);
    return response.data;
  }

  async createDeployment(data: CreateDeploymentRequest): Promise<ModelDeployment> {
    const response = await this.client.post<ModelDeployment>('/v1/deployments', data);
    return response.data;
  }

  async updateDeployment(id: string, data: UpdateDeploymentRequest): Promise<ModelDeployment> {
    const response = await this.client.patch<ModelDeployment>(`/v1/deployments/${id}`, data);
    return response.data;
  }

  async deleteDeployment(id: string): Promise<void> {
    await this.client.delete(`/v1/deployments/${id}`);
  }

  async enableDeployment(id: string): Promise<ModelDeployment> {
    const response = await this.client.post<ModelDeployment>(`/v1/deployments/${id}/enable`);
    return response.data;
  }

  async disableDeployment(id: string): Promise<ModelDeployment> {
    const response = await this.client.post<ModelDeployment>(`/v1/deployments/${id}/disable`);
    return response.data;
  }

  async getDeploymentsForModel(modelName: string, onlyActive?: boolean): Promise<ModelDeployment[]> {
    const params = onlyActive !== undefined ? { only_active: onlyActive } : {};
    const response = await this.client.get<ModelDeployment[]>(`/v1/deployments/model/${encodeURIComponent(modelName)}`, { params });
    return response.data;
  }

  // Guardrails
  async getGuardrailPolicies(): Promise<GuardrailPolicy[]> {
    const response = await this.client.get<GuardrailPolicy[]>('/guardrails/policies');
    return response.data;
  }

  async getGuardrailPolicy(policyId: string): Promise<GuardrailPolicy> {
    const response = await this.client.get<GuardrailPolicy>(`/guardrails/policies/${policyId}`);
    return response.data;
  }

  async checkContent(data: ContentCheckRequest): Promise<ContentCheckResponse> {
    const response = await this.client.post<ContentCheckResponse>('/guardrails/check', data);
    return response.data;
  }

  async getGuardrailsStatus(orgId?: string): Promise<GuardrailsStatus> {
    const params = orgId ? { org_id: orgId } : {};
    const response = await this.client.get<GuardrailsStatus>('/guardrails/status', { params });
    return response.data;
  }

  async setOrgPolicy(orgId: string, policyId: string): Promise<SetOrgPolicyResponse> {
    const response = await this.client.post<SetOrgPolicyResponse>(`/guardrails/org/${orgId}/policy/${policyId}`);
    return response.data;
  }

  // Pricing
  async getDeploymentPricing(deploymentId: string): Promise<ModelPricing> {
    const response = await this.client.get<ModelPricing>(
      `/v1/deployments/${deploymentId}/pricing`
    );
    return response.data;
  }

  async setDeploymentPricing(deploymentId: string, data: PricingCreateRequest): Promise<ModelPricing> {
    const response = await this.client.post<ModelPricing>(
      `/v1/deployments/${deploymentId}/pricing`,
      data
    );
    return response.data;
  }

  async deleteDeploymentPricing(deploymentId: string): Promise<void> {
    await this.client.delete(`/v1/deployments/${deploymentId}/pricing`);
  }
}

export const api = new ApiService();
