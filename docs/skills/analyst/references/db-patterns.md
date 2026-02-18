# Database Schema Patterns

## Prisma Schema Template

```prisma
generator client {
  provider = "prisma-client-py"
  interface = "asyncio"
}

datasource db {
  provider = "postgresql"
  url      = env("DATABASE_URL")
}

// API Keys / Virtual Keys
model LiteLLM_VerificationToken {
  id                String   @id @default(uuid())
  token             String   @unique  // SHA-256 hash of key
  key_name          String?
  user_id           String?
  team_id           String?
  models            String[]  // allowed model names
  max_budget        Float?
  spend             Float    @default(0)
  budget_duration   String?   // "1h", "1d", "30d"
  budget_reset_at   DateTime?
  rpm_limit         Int?
  tpm_limit         Int?
  max_parallel_requests Int?
  expires           DateTime?
  permissions       Json?     // {"allow_model_list": true, ...}
  metadata          Json?
  created_at        DateTime @default(now())
  updated_at        DateTime @updatedAt
  
  user              LiteLLM_UserTable? @relation(fields: [user_id], references: [user_id])
  team              LiteLLM_TeamTable? @relation(fields: [team_id], references: [team_id])
  spend_logs        LiteLLM_SpendLogs[]
  
  @@index([token])
  @@index([user_id])
  @@index([team_id])
  @@index([expires])
  @@map("litellm_verificationtoken")
}

// Users
model LiteLLM_UserTable {
  user_id           String   @id
  user_email        String?  @unique
  user_role         String   @default("internal_user")  // proxy_admin, team_admin, etc
  max_budget        Float?
  spend             Float    @default(0)
  budget_duration   String?
  budget_reset_at   DateTime?
  models            String[]
  tpm_limit         Int?
  rpm_limit         Int?
  team_id           String?
  metadata          Json?
  created_at        DateTime @default(now())
  updated_at        DateTime @updatedAt
  
  team              LiteLLM_TeamTable? @relation(fields: [team_id], references: [team_id])
  keys              LiteLLM_VerificationToken[]
  
  @@index([team_id])
  @@map("litellm_usertable")
}

// Teams
model LiteLLM_TeamTable {
  team_id           String   @id @default(uuid())
  team_alias        String?
  organization_id   String?
  max_budget        Float?
  spend             Float    @default(0)
  budget_duration   String?
  budget_reset_at   DateTime?
  model_max_budget  Json?     // {"gpt-4": 100.0, "gpt-3.5": 50.0}
  tpm_limit         Int?
  rpm_limit         Int?
  models            String[]
  blocked           Boolean  @default(false)
  metadata          Json?
  created_at        DateTime @default(now())
  updated_at        DateTime @updatedAt
  
  members           LiteLLM_UserTable[]
  keys              LiteLLM_VerificationToken[]
  
  @@index([organization_id])
  @@map("litellm_teamtable")
}

// Spend Logs (append-only)
model LiteLLM_SpendLogs {
  id                String   @id @default(uuid())
  request_id        String
  call_type         String   // "completion", "embedding", etc
  api_key           String   // hashed key
  spend             Float
  total_tokens      Int
  prompt_tokens     Int
  completion_tokens Int
  start_time        DateTime
  end_time          DateTime
  model             String
  api_base          String?
  user              String?  // end-user ID from request
  team_id           String?
  end_user          String?
  metadata          Json?
  cache_hit         Boolean  @default(false)
  cache_key         String?
  request_tags      String[]
  
  key               LiteLLM_VerificationToken? @relation(fields: [api_key], references: [token])
  
  @@index([api_key])
  @@index([team_id])
  @@index([user])
  @@index([start_time])
  @@index([model])
  @@index([request_tags])
  @@map("litellm_spendlogs")
}

// Dynamic Config
model LiteLLM_Config {
  id          String   @id @default(uuid())
  config_name String   @unique
  config_value Json
  updated_at  DateTime @updatedAt
  updated_by  String?
  
  @@map("litellm_config")
}
```

## Redis Key Patterns

```
# Rate Limiting
ratelimit:{scope}:{entity_id}:{window}
# e.g., ratelimit:key:abc123:2024-01-15T10:00 (hourly window)
# e.g., ratelimit:user:user_123:2024-01-15T10:05 (minutely)

# API Key Cache
key:{key_hash} -> JSON(key_data, expiry)

# Cooldown State
cooldown:{deployment_id} -> TTL timestamp

# Routing State
active_requests:{deployment_id} -> counter
latency:{deployment_id} -> sorted set of (timestamp, latency_ms)

# Response Cache
 cache:{sha256_key} -> JSON(cached_response)

# Config Pub/Sub
channel: config_updates -> "{config_name: updated_at}"
```

## Indexing Strategy

| Table | Query Pattern | Index |
|-------|--------------|-------|
| VerificationToken | Key lookup by token | `token` unique |
| VerificationToken | List keys by user | `user_id` |
| SpendLogs | Time-series queries | `start_time` |
| SpendLogs | Per-key spend analysis | `api_key` + `start_time` |
| SpendLogs | Tag-based filtering | `request_tags` GIN |
| UserTable | Team membership | `team_id` |
| TeamTable | Org hierarchy | `organization_id` |
