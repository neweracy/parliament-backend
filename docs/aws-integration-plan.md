# AWS Integration Plan — Node Transcription

## Overview

This document recommends how to integrate the transcription app's datasets and backend services with AWS. The app already uses Amazon Bedrock for LLM post-processing; this plan extends that to a full AWS-managed deployment.

---

## Current Architecture

| Component | Technology | Role |
|-----------|-----------|------|
| Backend | Express (Node.js, port 8081) | API server, transcription orchestration, post-processing |
| Frontend | React + Vite (port 8080) | SPA, served by Caddy in production |
| Deepgram | External API | Primary speech-to-text (Nova-3) |
| Khaya AI | External API | African-language ASR (Twi, Ewe, Ga, Dagbani) |
| Bedrock | AWS SDK | Claude LLM post-processing (optional) |
| Datasets | In-memory JS (~250 KB) | Locations, persons, MPs, parties — rule-based correction |
| Deployment | Docker (Node + Caddy) | Single container, multi-stage build |

---

## Recommended AWS Services

### Tier 1 — Deploy Now (High Impact, Low Effort)

#### 1. Amazon ECS Fargate (Compute)

Run the existing Docker container as a serverless task — no EC2 instances to manage.

- **Why:** You already have a working Dockerfile. Fargate handles scaling, patching, and availability.
- **How:** Push image to ECR, create ECS task definition with env vars from Secrets Manager, attach to ALB.
- **Config:** 0.5 vCPU, 1 GB RAM is sufficient for current traffic patterns.
- **Cost:** ~$15/month for always-on single task.

```
ECS Task Definition:
  CPU: 512 (.5 vCPU)
  Memory: 1024 (1 GB)
  Container: <account>.dkr.ecr.us-east-1.amazonaws.com/node-transcription:latest
  Port Mappings: 8080 (Caddy)
  Secrets: DEEPGRAM_API_KEY, KHAYA_API_KEY, SESSION_SECRET (from Secrets Manager)
  Environment: AWS_REGION=us-east-1, PORT=8081
```

#### 2. Amazon ECR (Container Registry)

Store and version Docker images with automatic vulnerability scanning.

- **Why:** Your CI/CD pushes images here; Fargate pulls from here. Faster cold starts than Docker Hub.
- **How:** `aws ecr create-repository --repository-name node-transcription`
- **Cost:** ~$1–2/month for <1 GB storage.

#### 3. AWS Secrets Manager (Credentials)

Store API keys and secrets with automatic rotation.

- **Why:** Eliminates hardcoded env vars. ECS injects secrets at runtime. Supports rotation for SESSION_SECRET.
- **Secrets to store:**
  - `DEEPGRAM_API_KEY`
  - `KHAYA_API_KEY`
  - `SESSION_SECRET`
- **How:** Create secret in console or CLI, reference ARN in ECS task definition.
- **Cost:** $0.40/secret/month.

Note: For Bedrock, use **IAM Task Role** instead of access keys — the ECS task role gets implicit credentials.

#### 4. Amazon Bedrock (Already Using — Optimize)

Switch from hardcoded AWS access keys to IAM role-based auth.

- **Current:** `AWS_ACCESS_KEY_ID` + `AWS_SECRET_ACCESS_KEY` in env vars.
- **Recommended:** Attach an IAM role to the ECS task with `bedrock:InvokeModel` permission. Remove the env vars entirely.
- **IAM Policy:**

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": "bedrock:InvokeModel",
    "Resource": "arn:aws:bedrock:us-east-1::foundation-model/us.anthropic.claude-haiku-4-5-20251001-v1:0"
  }]
}
```

- **Code change:** Remove `credentials` from `BedrockRuntimeClient` constructor — SDK auto-discovers IAM role credentials.

#### 5. Application Load Balancer (ALB)

Route traffic to Fargate, handle HTTPS termination, health checks.

- **Why:** TLS termination, path-based routing, health checks (`/health` endpoint exists in Caddy).
- **Setup:** HTTPS listener on 443, forward to ECS target group on port 8080.
- **Cost:** ~$16/month + $0.008 per LCU-hour.

---

### Tier 2 — Add Next (Medium Effort, Improves Operations)

#### 6. Amazon CloudFront (CDN)

Cache and serve the React frontend from edge locations globally.

- **Why:** The SPA is ~2 MB of static assets. Serving from edge reduces latency for Ghanaian users (closest edge: Lagos or Johannesburg).
- **Origin:** ALB (for `/api/*` pass-through) or S3 bucket (for static assets).
- **Behavior:**
  - `/api/*` → Forward to ALB (no cache)
  - `/*` → Cache static assets (TTL 1 day, invalidate on deploy)
- **Cost:** ~$8–15/month for 100 GB transfer.

#### 7. Amazon CloudWatch (Monitoring & Logs)

Centralized logging, metrics, and alerting.

- **Logs:** ECS tasks automatically stream stdout/stderr to CloudWatch Log Groups.
- **Metrics:** Track request count, error rate, Bedrock latency, transcription duration.
- **Alarms:** Alert on error rate > 5%, Bedrock timeout, memory exhaustion.
- **Custom Metrics (via EMF):**
  - `transcription.duration` (histogram)
  - `bedrock.corrections_count` (per request)
  - `bedrock.latency_ms` (per chunk)
- **Cost:** ~$2–5/month.

#### 8. Amazon S3 (Transcript Storage)

Store processed transcripts and audio files for replay and analytics.

- **Buckets:**
  - `node-transcription-audio/` — uploaded audio files (lifecycle: delete after 30 days)
  - `node-transcription-results/` — JSON transcript results (lifecycle: move to Glacier after 90 days)
- **Why:** Enables history replay without re-transcribing, audit trail, analytics via Athena.
- **Cost:** ~$2–5/month for typical usage.

---

### Tier 3 — Future Enhancements (Higher Effort, Enables Scale)

#### 9. Amazon DynamoDB (Dataset Storage)

Move the hardcoded datasets (persons, MPs, parties) into DynamoDB for live updates.

- **Why:** Currently, adding a new MP or minister requires a code deployment. With DynamoDB, you update a record and the app picks it up immediately.
- **Tables:**
  - `GhanaPersons` (PK: canonical, attributes: role, aliases[], entityType)
  - `GhanaMPs` (PK: name, attributes: constituency, party, aliases[])
  - `GhanaParties` (PK: canonical, attributes: abbr, aliases[])
  - `GhanaLocations` (PK: canonical, attributes: region, aliases[])
- **Access Pattern:** Full table scan at startup, cache in memory (same as current), refresh every 5 minutes via TTL-based cache invalidation.
- **Cost:** ~$1–3/month (on-demand pricing, small tables).

#### 10. Amazon SQS + Lambda (Async Processing)

Decouple the expensive Bedrock and Khaya calls from the synchronous request path.

- **Flow:**
  1. Client uploads audio → API returns `{ requestId, status: "processing" }`
  2. SQS message queued with audio S3 key + config
  3. Lambda processes: Deepgram → Rule-based → Bedrock → Store result in S3/DynamoDB
  4. Client polls `/api/transcription/{requestId}/status` or uses WebSocket notification
- **Why:** Eliminates 15–30s wait time for the client. Enables retry, dead-letter queues, and per-user rate limiting.
- **Cost:** Lambda ~$2/month for 1,000 invocations at 5-min average duration.

#### 11. Amazon Bedrock Knowledge Bases (RAG for Datasets)

Instead of injecting the full dataset into every Bedrock prompt (~2000 tokens), use a Knowledge Base with vector embeddings.

- **Why:** As datasets grow (e.g., all ministers since 1957), the prompt will exceed token limits. RAG retrieves only relevant entries.
- **How:** Upload datasets as documents to S3, create a Knowledge Base with OpenSearch Serverless. Query KB for relevant persons/locations before calling Claude.
- **Trade-off:** Adds 1–2s latency per retrieval, but keeps prompt small and enables much larger datasets.

#### 12. AWS WAF (Security)

Protect the ALB from abuse, rate-limit by IP, block known bad actors.

- **Rules:** Rate-based rule (1000 req/5min per IP), geo-restriction (optional), SQL injection protection.
- **Cost:** ~$5/month.

---

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                         CloudFront                           │
│   (Static SPA assets cached at edge, /api/* pass-through)   │
└──────────────────────────────┬──────────────────────────────┘
                               │
                    ┌──────────▼──────────┐
                    │    ALB (HTTPS:443)   │
                    │    Health: /health   │
                    └──────────┬──────────┘
                               │
              ┌────────────────▼────────────────┐
              │       ECS Fargate Task          │
              │  ┌───────────┐ ┌─────────────┐  │
              │  │  Caddy    │ │  Express    │  │
              │  │  :8080    │→│  :8081      │  │
              │  │ (proxy +  │ │ (API routes)│  │
              │  │  static)  │ │             │  │
              │  └───────────┘ └──────┬──────┘  │
              │                       │         │
              │  IAM Task Role ───────┤         │
              └───────────────────────┼─────────┘
                                      │
          ┌───────────────────────────┼──────────────────┐
          │                           │                  │
    ┌─────▼─────┐           ┌────────▼────────┐   ┌────▼─────┐
    │  Deepgram │           │ Amazon Bedrock  │   │ Khaya AI │
    │  (ext.)   │           │ (Claude Haiku)  │   │  (ext.)  │
    └───────────┘           └─────────────────┘   └──────────┘
          │                           │
    ┌─────▼─────┐           ┌────────▼────────┐
    │  Secrets  │           │   CloudWatch    │
    │  Manager  │           │  (Logs/Metrics) │
    └───────────┘           └─────────────────┘
```

---

## Implementation Roadmap

### Phase 1 — Deploy to AWS (Week 1–2)

1. Create ECR repository, push Docker image
2. Create Secrets Manager entries for API keys
3. Create ECS cluster + Fargate service + task definition
4. Create ALB with HTTPS (ACM certificate)
5. Update Bedrock client to use IAM role (remove hardcoded credentials)
6. Verify end-to-end: frontend → ALB → Fargate → Deepgram/Bedrock

### Phase 2 — Observability & CDN (Week 3–4)

7. Enable CloudWatch Container Insights
8. Add custom metrics (transcription duration, Bedrock latency)
9. Set up CloudFront distribution for static assets
10. Configure alarms (error rate, latency P95)

### Phase 3 — Data Layer (Month 2)

11. Create DynamoDB tables for datasets
12. Implement dataset sync (startup load + periodic refresh)
13. Add S3 bucket for transcript archival
14. Set up lifecycle policies (30-day audio, 90-day archive)

### Phase 4 — Async & Scale (Month 3+)

15. Implement SQS queue for async transcription
16. Create Lambda processor for background jobs
17. Add WebSocket notifications for completion
18. Consider Bedrock Knowledge Base for growing datasets

---

## Cost Summary

| Scenario | Monthly Cost |
|----------|-------------|
| Current (local/manual) | $0 (infra) + $21.50 Deepgram + $5–10 Bedrock |
| Phase 1 (Fargate + ECR + ALB + Secrets) | ~$35–50 |
| Phase 2 (+ CloudFront + CloudWatch) | ~$50–70 |
| Phase 3 (+ DynamoDB + S3) | ~$55–75 |
| Full stack (all phases) | ~$80–120 |

All estimates assume ~1,000 transcriptions/month with 5-min average audio.

---

## Key Code Changes Required

### 1. Bedrock Client — Remove Hardcoded Credentials

```javascript
// BEFORE (bedrock-postprocess.js)
_client = new BedrockRuntimeClient({
  region: process.env.AWS_REGION || 'us-east-1',
  credentials: {
    accessKeyId: process.env.AWS_ACCESS_KEY_ID,
    secretAccessKey: process.env.AWS_SECRET_ACCESS_KEY,
  },
});

// AFTER — SDK auto-discovers IAM role credentials in ECS
_client = new BedrockRuntimeClient({
  region: process.env.AWS_REGION || 'us-east-1',
});
```

### 2. isBedrockConfigured — Check for IAM role OR env vars

```javascript
// BEFORE
function isBedrockConfigured() {
  return !!(process.env.AWS_ACCESS_KEY_ID && process.env.AWS_SECRET_ACCESS_KEY);
}

// AFTER — also works with IAM role (no explicit keys needed)
function isBedrockConfigured() {
  // If running in ECS with a task role, credentials are auto-discovered
  if (process.env.ECS_CONTAINER_METADATA_URI_V4) return true;
  // Fallback: explicit keys for local development
  return !!(process.env.AWS_ACCESS_KEY_ID && process.env.AWS_SECRET_ACCESS_KEY);
}
```

### 3. Secrets Manager Integration (startup)

```javascript
const { SecretsManagerClient, GetSecretValueCommand } = require('@aws-sdk/client-secrets-manager');

async function loadSecrets() {
  if (!process.env.SECRET_ARN) return; // local dev — use .env
  const client = new SecretsManagerClient({ region: 'us-east-1' });
  const { SecretString } = await client.send(
    new GetSecretValueCommand({ SecretId: process.env.SECRET_ARN })
  );
  const secrets = JSON.parse(SecretString);
  process.env.DEEPGRAM_API_KEY = secrets.DEEPGRAM_API_KEY;
  process.env.KHAYA_API_KEY = secrets.KHAYA_API_KEY;
  process.env.SESSION_SECRET = secrets.SESSION_SECRET;
}
```

---

## Summary

The recommended path is: **ECR + ECS Fargate + Secrets Manager + ALB** for immediate production deployment, followed by CloudFront and CloudWatch for operational maturity. The existing Bedrock integration should switch from hardcoded keys to IAM role-based auth. Datasets can stay in-memory for now (~250 KB is trivial) but should migrate to DynamoDB when the dataset grows to support live updates without redeployment.
