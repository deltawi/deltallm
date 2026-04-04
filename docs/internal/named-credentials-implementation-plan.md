# Named Credentials Implementation Plan

## Goal

Add reusable provider credentials that can be shared across multiple model deployments while preserving support for existing inline credentials.

This feature should:

- let a deployment use either a named credential or inline provider credentials
- keep gateway request-path latency unchanged by resolving effective credentials during runtime reload, not per request
- support provider model discovery when a named credential is selected
- avoid exposing raw credential values in new named credential APIs
- preserve backward compatibility for existing inline deployments

## Current State

### Existing credential model

- model deployments store provider connection data inline inside `deltallm_params`
- the model form writes `api_key`, `api_base`, and `api_version` into the deployment payload
- provider model discovery also expects raw provider credentials in the request payload
- runtime currently injects a global OpenAI fallback when inline `api_key` / `api_base` are missing
- config-level secret references already exist for env / AWS / GCP / Azure secret managers

### Architectural issues today

- credentials are duplicated across deployments
- rotating one provider key requires editing every affected deployment
- provider model discovery forces users to paste credentials into the form
- there is no shared resource representing provider connection bundles
- model admin APIs still return raw `deltallm_params`, which is not ideal for secret-bearing fields

## Target Architecture

### Core design

- introduce a first-class `NamedCredential` resource in the database
- add `named_credential_id` to model deployments
- keep inline credential fields supported for compatibility
- require each deployment to conceptually use one credential source:
  - named credential
  - inline credentials

### Ownership boundaries

Named credential owns shared provider connection fields:

- `api_key`
- `api_base`
- `api_version`
- provider-specific auth fields such as AWS access key / secret / token / region

Deployment continues to own:

- public `model_name`
- upstream provider `model`
- provider identity
- mode
- routing / weight / rate limits
- pricing / model metadata

### Runtime design

- resolve named credentials into effective deployment params while building the in-memory model registry
- do not query the database on the gateway request path
- reload runtime after named credential create / update / delete when needed

### Discovery design

- extend provider model discovery to accept `named_credential_id`
- resolve the selected named credential server-side before making provider discovery calls

### Security design

- named credential APIs must redact secret-bearing values on read
- support existing secret-ref formats inside named credential connection config:
  - `os.environ/...`
  - `aws.secretsmanager/...`
  - `gcp.secretmanager/...`
  - `azure.keyvault/...`
- reuse audit redaction behavior for request / response payloads

## Delivery Plan

### Phase 1: Backend foundation

- [x] add `deltallm_namedcredential` database table
- [x] add `named_credential_id` to `deltallm_modeldeployment`
- [x] add repository + record types for named credentials
- [x] instantiate repository in infrastructure bootstrap
- [x] resolve effective deployment params from named credentials during model registry build
- [x] extend provider model discovery to accept `named_credential_id`
- [x] add admin CRUD endpoints for named credentials with redacted responses
- [x] reload runtime after named credential updates that affect deployments

### Phase 2: Model deployment integration

- [x] allow model create / update payloads to include `named_credential_id`
- [x] validate provider consistency between deployment and named credential
- [x] allow named-credential-backed deployments without inline `api_base` when the credential supplies it
- [x] include named credential summary fields in model responses

### Phase 2A: Model admin contract stabilization

- [x] add explicit credential metadata to model responses
- [x] add connection summary fields to model responses
- [x] preserve omitted inline credential fields on model update
- [x] clear old connection fields when provider changes
- [ ] stop exposing raw inline secret-bearing params in edit/detail flows after the replacement UI is ready

### Phase 3: UI integration

- [x] add named credentials page in admin UI
- [x] add list / create / edit / delete flows
- [x] show usage counts and linked deployments
- [x] update model form with explicit credential source mode
- [x] support named credentials in live provider model refresh
- [x] keep inline mode for compatibility

### Phase 4: Hardening

- [x] stop exposing raw secret-bearing deployment fields in model admin responses
- [x] add conversion flow from inline credentials to named credentials
- [x] add stronger provider-specific validation for connection config
- [ ] evaluate encrypt-at-rest for literal credential values

## Implementation Notes

### Explicit non-goals for the first implementation pass

- no gateway request-path database lookups
- no forced migration of existing inline deployments
- no broad redesign of the model form in the first backend slice

### Internal consistency rules

- if a deployment references a named credential, that credential is the authoritative shared connection source
- runtime resolution may preserve deployment-local values for non-shared fields
- delete of a named credential should be blocked while it is referenced by deployments

## Initial Implementation Slice

This pass focuses on the backend foundation:

- [x] schema + migration
- [x] repository + bootstrap wiring
- [x] runtime resolution during registry build
- [x] named credential CRUD endpoints
- [x] provider discovery support via `named_credential_id`
- [x] update this document with progress and follow-up items

## Progress Log

- [x] Plan doc created
- [x] Backend foundation started
- [x] Verification completed
- [x] Phase 3 secret-resolution slice completed
- [x] UI and validation slice completed
- [x] Inline conversion/reporting slice completed

## Implemented In This Pass

- Added schema foundation for named credentials and deployment references
- Added named credential repository and redaction / merge helpers
- Wired named credential resolution into runtime model registry loading
- Added platform-admin CRUD endpoints for named credentials
- Added delete protection while a credential is still linked to deployments
- Added runtime reload on named credential updates when linked deployments exist
- Extended provider model discovery to resolve a selected named credential
- Extended model create / update payloads to accept `named_credential_id`
- Added focused tests for repository behavior, runtime registry merging, provider discovery, and named credential API behavior
- Stabilized model admin/update semantics so omitted inline credentials are preserved
- Added explicit model response metadata for credential source and connection summary
- Added focused tests for partial-update credential behavior and credential summary responses
- Added named credential secret-ref resolution via the existing secret resolver
- Limited secret resolution to runtime registry rebuilds and admin provider discovery
- Added regression coverage for env-backed and external-secret-manager-backed named credentials
- Added provider-specific named credential validation and partial update semantics
- Added a dedicated named credentials admin page with create/edit/delete and usage visibility
- Integrated named credentials into the model form with explicit credential source selection
- Switched model detail/edit flows off raw inline secrets and onto credential metadata
- Added inline credential grouping and conversion into reusable named credentials
- Added admin-side inline credential reporting without exposing raw secret values

## Follow-up Work

- Evaluate encrypt-at-rest for literal credential values

## Remaining Architecture Plan

### Phase 3: Secret reference resolution for named credentials

- [x] resolve secret-ref values inside named credential connection configs using the existing secret resolver
- [x] perform resolution only during runtime rebuild / reload and admin discovery calls
- [x] add tests for env and external secret-manager references

### Current Implementation Slice

This pass focuses on Phase 3 only:

- [x] add named credential secret-ref resolution helper
- [x] thread the existing `SecretResolver` into runtime model-registry resolution
- [x] thread the existing `SecretResolver` into admin provider model discovery
- [x] add env-ref regression coverage
- [x] add external secret-manager regression coverage
- [x] update this document with results

### Phase 4: Provider-specific credential validation

- [x] add provider-aware validation for named credential connection config
- [x] validate allowed / required fields per provider family
- [x] add tests for OpenAI-compatible, Azure, Bedrock, Gemini, and Anthropic credential shapes

### Phase 5: Named credentials UI

- [x] add UI API bindings for named credentials
- [x] add a dedicated named credentials page and route
- [x] add navigation entry and access control wiring
- [x] support list / create / edit / delete flows
- [x] show usage counts and linked deployments

### Phase 6: Model form credential source UX

- [x] add explicit credential source selection: named vs inline
- [x] add provider-filtered named credential picker
- [x] support named credentials in live provider model refresh
- [x] add inline secret preserve / replace / clear UX
- [x] keep inline mode for compatibility

### Phase 7: Response cleanup and migration

- [x] remove dependence on raw inline secret fields in admin model edit/detail flows
- [x] stop exposing raw inline secret-bearing fields in normal model admin responses
- [x] add optional conversion flows from inline credentials to named credentials
- [x] add reporting for duplicated inline credentials

## Verification Run

- `uv run ruff check src/config.py src/api/admin/endpoints/named_credentials.py src/api/admin/endpoints/models.py src/api/admin/endpoints/common.py src/api/admin/router.py src/api/admin/endpoints/__init__.py src/audit/actions.py src/bootstrap/infrastructure.py src/bootstrap/routing.py src/config_runtime/models.py src/db/named_credentials.py src/db/repositories.py src/services/model_deployments.py src/services/named_credentials.py tests/db/test_model_deployment_repository.py tests/services/test_model_deployments.py tests/test_provider_model_discovery.py tests/test_named_credentials_api.py tests/test_ui_models.py`
- `uv run pytest tests/db/test_model_deployment_repository.py tests/services/test_model_deployments.py tests/test_provider_model_discovery.py tests/test_named_credentials_api.py tests/test_ui_models.py`
- `npm run build` in `ui`
- `uv run ruff check src/services/named_credentials.py src/services/model_deployments.py src/bootstrap/routing.py src/config_runtime/models.py src/api/admin/endpoints/models.py tests/services/test_model_deployments.py tests/test_provider_model_discovery.py tests/services/test_named_credentials.py`
- `uv run pytest tests/services/test_model_deployments.py tests/test_provider_model_discovery.py tests/services/test_named_credentials.py tests/test_named_credentials_api.py tests/db/test_model_deployment_repository.py tests/test_ui_models.py`
- `uv run ruff check src/services/named_credentials.py src/api/admin/endpoints/common.py src/api/admin/endpoints/models.py tests/test_named_credentials_api.py tests/test_ui_models.py`
- `uv run pytest tests/test_named_credentials_api.py tests/test_provider_model_discovery.py tests/services/test_model_deployments.py tests/test_ui_models.py tests/db/test_model_deployment_repository.py`
- `npm run build` in `ui`
- `uv run ruff check src/api/admin/endpoints/named_credentials.py tests/test_named_credentials_api.py`
- `uv run pytest tests/test_named_credentials_api.py tests/test_provider_model_discovery.py tests/services/test_model_deployments.py tests/test_ui_models.py tests/db/test_model_deployment_repository.py`
