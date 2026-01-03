# 🕷️ Commercial Curator - Agent 0

## RAGNAROK Video Generation Pipeline | Foundation Agent

---

## 📦 What Is This?

Commercial Curator is the **first agent** in the RAGNAROK video generation pipeline. It crawls business websites using Firecrawl, chunks the content, generates embeddings, and indexes into Qdrant for downstream RAG retrieval.

```
┌─────────────────────┐     ┌─────────────────────┐
│  Commercial Curator │     │   Neural RAG Brain  │
│  (This Service)     │     │   (Query Service)   │
│                     │     │                     │
│  Firecrawl API ─────┼──┐  │   Query endpoint    │
│  Chunking           │  │  │   Dual-process      │
│  Embedding (Voyage) │  │  │   CRAG pipeline     │
│  Qdrant Indexing    │  │  │   Self-reflection   │
└─────────────────────┘  │  └──────────┬──────────┘
                         │             │
                         └──────┬──────┘
                                ↓
                       ┌─────────────────┐
                       │   Qdrant Cloud  │
                       │ (shared vector) │
                       └─────────────────┘
```

---

## 🚀 Quick Deploy to Render

### Step 1: Push to GitHub

```bash
git init
git add .
git commit -m "Commercial Curator v1.0.0"
git remote add origin https://github.com/YOUR_USERNAME/commercial-curator.git
git push -u origin main
```

### Step 2: Deploy via Blueprint

1. Go to [Render Dashboard](https://dashboard.render.com)
2. Click **New** → **Blueprint**
3. Connect your GitHub repo
4. Select `render.yaml`
5. Configure secrets:
   - `FIRECRAWL_API_KEY` - Your Firecrawl API key
   - `QDRANT_URL` - Same Qdrant instance as Neural RAG Brain
   - `QDRANT_API_KEY` - Qdrant API key
   - `VOYAGE_API_KEY` - Voyage AI API key for embeddings
6. Click **Apply**

### Step 3: Verify Deployment

```bash
# Health check
curl https://commercial-curator.onrender.com/health

# Service status
curl https://commercial-curator.onrender.com/status
```

---

## 📊 API Endpoints

### Crawl Endpoints

```bash
# Queue async crawl job (recommended)
POST /crawl
{
  "url": "https://example.com",
  "business_name": "Example Corp",
  "industry": "technology",
  "crawl_depth": 2,
  "max_pages": 50
}

# Synchronous crawl (blocking - small sites only)
POST /crawl/sync
{
  "url": "https://example.com",
  "business_name": "Example Corp",
  "max_pages": 20
}

# Check job status
GET /job/{job_id}

# List all jobs
GET /jobs?status=completed&limit=50
```

### Search Endpoints

```bash
# Search indexed content
POST /search
{
  "query": "company services and products",
  "business_filter": "Example Corp",
  "top_k": 10,
  "min_score": 0.5
}

# Delete business data
DELETE /business/{business_name}
```

### Monitoring

```bash
# Health check
GET /health

# Prometheus metrics
GET /metrics

# Service status
GET /status
```

---

## ⚙️ Environment Variables

### Required

| Variable | Description |
|----------|-------------|
| `FIRECRAWL_API_KEY` | Firecrawl API key (fc-...) |
| `QDRANT_URL` | Qdrant Cloud URL |
| `QDRANT_API_KEY` | Qdrant API key |
| `VOYAGE_API_KEY` | Voyage AI API key |

### Optional

| Variable | Default | Description |
|----------|---------|-------------|
| `QDRANT_COLLECTION` | `commercial_reference` | Collection name |
| `EMBEDDING_MODEL` | `voyage-3-lite` | Embedding model |
| `EMBEDDING_DIMENSION` | `1024` | Vector dimension |
| `CHUNK_SIZE` | `1000` | Target chunk size |
| `CHUNK_OVERLAP` | `200` | Overlap between chunks |
| `REQUESTS_PER_MINUTE` | `20` | Rate limit |
| `LOG_LEVEL` | `INFO` | Log verbosity |

---

## 💰 Pricing

### Render Services

| Service | Plan | Cost/mo |
|---------|------|---------|
| Web Service | Starter | $7 |
| Worker | Starter | $7 |
| Redis | Starter | $10 |
| **Total** | | **$24/mo** |

### External APIs

| API | Cost |
|-----|------|
| Firecrawl | ~$0.0002/page |
| Voyage | ~$0.00001/chunk |
| **Per website** | **~$0.02** |

---

## 🔗 Integration with Neural RAG Brain

Both services share the same Qdrant instance. Set the **same** `QDRANT_URL`, `QDRANT_API_KEY`, and `QDRANT_COLLECTION` in both services.

### Workflow

1. **Commercial Curator** crawls a client website
2. Content is chunked and embedded
3. Vectors are stored in Qdrant collection `commercial_reference`
4. **Neural RAG Brain** retrieves from the same collection
5. RAGNAROK agents use the context for video generation

---

## 📈 Metrics

Prometheus metrics available at `/metrics`:

- `curator_crawls_total` - Total crawl operations by status
- `curator_crawl_latency_seconds` - Crawl latency histogram
- `curator_pages_crawled_total` - Total pages crawled
- `curator_chunks_indexed_total` - Total chunks indexed
- `curator_searches_total` - Search operations
- `curator_search_latency_seconds` - Search latency
- `curator_active_jobs` - Currently running jobs

---

## 🛡️ Resilience Features

- **Circuit Breaker** - Trips after 5 failures, recovers after 60s
- **Rate Limiter** - 20 requests/minute to Firecrawl
- **Retry with Backoff** - Exponential backoff on failures
- **Job Queue** - Redis-backed async processing
- **Health Checks** - Render monitors `/health`

---

## 📁 Package Contents

```
commercial_curator_deploy/
├── main.py                 # FastAPI entry point
├── commercial_curator.py   # Core agent implementation
├── worker.py               # Background job processor
├── Dockerfile              # Web service container
├── Dockerfile.worker       # Worker container
├── render.yaml             # Render Blueprint
├── requirements.txt        # Python dependencies
├── .env.template           # Environment template
└── README.md               # This file
```

---

## 🐛 Troubleshooting

### Circuit breaker OPEN

Firecrawl is failing. Check:
1. API key validity
2. Firecrawl service status
3. Rate limits

### No chunks indexed

Check:
1. Voyage API key configured
2. Qdrant connection
3. Page content extractable (not blocked by robots.txt)

### High latency

Check:
1. Redis connection for async jobs
2. Qdrant response time
3. Number of concurrent jobs

---

## 📞 Support

Barrios A2I | "Alienation 2 Innovation"
- GitHub: BarriosA2I
- Pipeline: RAGNAROK v7.0
- Architecture: Neural RAG Brain v3.0
