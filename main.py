"""
================================================================================
COMMERCIAL CURATOR - FASTAPI ENTRY POINT
================================================================================
Agent 0: Web scraping → Chunking → Embedding → Qdrant indexing
Standalone service for the RAGNAROK video generation pipeline

Endpoints:
- POST /crawl          - Queue a website crawl job
- POST /crawl/sync     - Synchronous crawl (blocking)
- POST /search         - Search indexed content
- GET  /job/{job_id}   - Get job status
- DELETE /business/{name} - Delete business data
- GET  /health         - Health check
- GET  /metrics        - Prometheus metrics
- GET  /status         - Service status

Author: Barrios A2I | Version: 1.0.0
================================================================================
"""

import os
import time
import uuid
import logging
import asyncio
from typing import Dict, Any, List, Optional
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, HTTPException, Request, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, HttpUrl

# Prometheus metrics
from prometheus_client import (
    Counter, Histogram, Gauge,
    generate_latest, CONTENT_TYPE_LATEST
)
from starlette.responses import Response

# Import curator components
from commercial_curator import (
    CommercialCuratorAgent,
    CuratorConfig,
    CrawlRequest,
    CrawlResult,
    SearchRequest,
    SearchResult,
    CrawlStatus
)

# Configure logging
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)


# =============================================================================
# PROMETHEUS METRICS
# =============================================================================

CRAWL_COUNT = Counter(
    "curator_crawls_total",
    "Total crawl operations",
    ["status"]
)

CRAWL_LATENCY = Histogram(
    "curator_crawl_latency_seconds",
    "Crawl operation latency",
    buckets=[1.0, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0]
)

PAGES_CRAWLED = Counter(
    "curator_pages_crawled_total",
    "Total pages crawled"
)

CHUNKS_INDEXED = Counter(
    "curator_chunks_indexed_total",
    "Total chunks indexed"
)

SEARCH_COUNT = Counter(
    "curator_searches_total",
    "Total search operations"
)

SEARCH_LATENCY = Histogram(
    "curator_search_latency_seconds",
    "Search latency",
    buckets=[0.05, 0.1, 0.25, 0.5, 1.0, 2.0]
)

ACTIVE_JOBS = Gauge(
    "curator_active_jobs",
    "Currently running crawl jobs"
)


# =============================================================================
# REQUEST/RESPONSE MODELS
# =============================================================================

class CrawlJobRequest(BaseModel):
    """API request for crawl job"""
    url: HttpUrl = Field(description="Website URL to crawl")
    business_name: str = Field(description="Name of the business")
    industry: Optional[str] = Field(default=None, description="Business industry")
    crawl_depth: int = Field(default=2, ge=1, le=5)
    max_pages: int = Field(default=50, ge=1, le=200)
    priority: int = Field(default=5, ge=1, le=10)


class CrawlJobResponse(BaseModel):
    """API response for crawl job submission"""
    job_id: str
    status: str
    message: str
    estimated_duration_seconds: int


class JobStatusResponse(BaseModel):
    """API response for job status"""
    job_id: str
    status: CrawlStatus
    business_name: str
    url: str
    pages_crawled: int
    chunks_indexed: int
    errors: List[str]
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    duration_seconds: float
    cost_usd: float


# =============================================================================
# GLOBAL STATE
# =============================================================================

curator: Optional[CommercialCuratorAgent] = None
jobs: Dict[str, CrawlResult] = {}


# =============================================================================
# LIFESPAN
# =============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize and cleanup resources"""
    global curator
    
    logger.info("🚀 Starting Commercial Curator Agent 0...")
    
    # Load config from environment
    config = CuratorConfig(
        firecrawl_api_key=os.getenv("FIRECRAWL_API_KEY", ""),
        qdrant_url=os.getenv("QDRANT_URL", "http://localhost:6333"),
        qdrant_collection=os.getenv("QDRANT_COLLECTION", "commercial_reference"),
        embedding_model=os.getenv("EMBEDDING_MODEL", "voyage-3-lite"),
        embedding_dimension=int(os.getenv("EMBEDDING_DIMENSION", "1024")),
        chunk_size=int(os.getenv("CHUNK_SIZE", "1000")),
        chunk_overlap=int(os.getenv("CHUNK_OVERLAP", "200")),
        requests_per_minute=int(os.getenv("REQUESTS_PER_MINUTE", "20")),
        failure_threshold=int(os.getenv("CIRCUIT_FAILURE_THRESHOLD", "5")),
        recovery_timeout_seconds=int(os.getenv("CIRCUIT_RECOVERY_TIMEOUT", "60"))
    )
    
    # Initialize curator
    curator = CommercialCuratorAgent(config)
    
    logger.info("✅ Commercial Curator Agent 0 ready")
    logger.info(f"   Firecrawl: {'configured' if config.firecrawl_api_key else 'NOT SET'}")
    logger.info(f"   Qdrant: {config.qdrant_url}")
    logger.info(f"   Collection: {config.qdrant_collection}")
    
    yield
    
    # Cleanup
    logger.info("🛑 Shutting down Commercial Curator...")
    if curator:
        await curator.close()


# =============================================================================
# FASTAPI APP
# =============================================================================

app = FastAPI(
    title="Commercial Curator - Agent 0",
    description="Web scraping and indexing agent for RAGNAROK video generation pipeline",
    version="1.0.0",
    lifespan=lifespan
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =============================================================================
# ENDPOINTS
# =============================================================================

@app.get("/health")
async def health_check():
    """Health check endpoint for Render"""
    if curator is None:
        raise HTTPException(status_code=503, detail="Curator not initialized")
    
    health = curator.get_health()
    circuit_status = health.get("firecrawl_circuit", {}).get("state", "unknown")
    
    if circuit_status == "open":
        return JSONResponse(
            status_code=503,
            content={"status": "degraded", "reason": "circuit_open", **health}
        )
    
    return {"status": "healthy", **health}


@app.get("/metrics")
async def metrics():
    """Prometheus metrics endpoint"""
    return Response(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST
    )


@app.get("/status")
async def status():
    """Service status and configuration"""
    return {
        "service": "commercial-curator",
        "version": "1.0.0",
        "agent": "Agent 0",
        "pipeline": "RAGNAROK",
        "environment": os.getenv("ENVIRONMENT", "development"),
        "firecrawl_configured": bool(os.getenv("FIRECRAWL_API_KEY")),
        "qdrant_url": os.getenv("QDRANT_URL", "not set"),
        "qdrant_collection": os.getenv("QDRANT_COLLECTION", "commercial_reference"),
        "embedding_model": os.getenv("EMBEDDING_MODEL", "voyage-3-lite"),
        "active_jobs": len([j for j in jobs.values() if j.status in [CrawlStatus.PENDING, CrawlStatus.SCRAPING, CrawlStatus.PROCESSING]])
    }


@app.post("/crawl", response_model=CrawlJobResponse)
async def queue_crawl_job(request: CrawlJobRequest, background_tasks: BackgroundTasks):
    """Queue a website crawl job (async, non-blocking)"""
    job_id = str(uuid.uuid4())[:8]
    
    # Estimate duration based on max_pages
    estimated_seconds = request.max_pages * 1.2  # ~1.2s per page
    
    # Create initial job record
    jobs[job_id] = CrawlResult(
        request_id=job_id,
        business_name=request.business_name,
        url=str(request.url),
        status=CrawlStatus.PENDING,
        started_at=datetime.utcnow()
    )
    
    # Queue background task
    background_tasks.add_task(
        run_crawl_job,
        job_id,
        CrawlRequest(
            url=request.url,
            business_name=request.business_name,
            industry=request.industry,
            crawl_depth=request.crawl_depth,
            max_pages=request.max_pages,
            priority=request.priority
        )
    )
    
    ACTIVE_JOBS.inc()
    
    return CrawlJobResponse(
        job_id=job_id,
        status="queued",
        message=f"Crawl job queued for {request.business_name}",
        estimated_duration_seconds=int(estimated_seconds)
    )


@app.post("/crawl/sync", response_model=CrawlResult)
async def crawl_sync(request: CrawlJobRequest):
    """Synchronous crawl (blocking - use for small sites only)"""
    if curator is None:
        raise HTTPException(status_code=503, detail="Curator not initialized")
    
    start_time = time.time()
    
    crawl_request = CrawlRequest(
        url=request.url,
        business_name=request.business_name,
        industry=request.industry,
        crawl_depth=request.crawl_depth,
        max_pages=min(request.max_pages, 20),  # Limit sync to 20 pages
        priority=request.priority
    )
    
    result = await curator.crawl_and_index(crawl_request)
    
    # Update metrics
    CRAWL_LATENCY.observe(time.time() - start_time)
    CRAWL_COUNT.labels(status=result.status.value).inc()
    PAGES_CRAWLED.inc(result.pages_crawled)
    CHUNKS_INDEXED.inc(result.chunks_indexed)
    
    return result


@app.get("/job/{job_id}", response_model=JobStatusResponse)
async def get_job_status(job_id: str):
    """Get status of a crawl job"""
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    
    result = jobs[job_id]
    return JobStatusResponse(
        job_id=job_id,
        status=result.status,
        business_name=result.business_name,
        url=result.url,
        pages_crawled=result.pages_crawled,
        chunks_indexed=result.chunks_indexed,
        errors=result.errors,
        started_at=result.started_at,
        completed_at=result.completed_at,
        duration_seconds=result.duration_seconds,
        cost_usd=result.cost_usd
    )


@app.post("/search", response_model=SearchResult)
async def search_references(request: SearchRequest):
    """Search the indexed reference library"""
    if curator is None:
        raise HTTPException(status_code=503, detail="Curator not initialized")
    
    start_time = time.time()
    
    result = await curator.search_reference_library(request)
    
    # Update metrics
    SEARCH_COUNT.inc()
    SEARCH_LATENCY.observe(time.time() - start_time)
    
    return result


@app.delete("/business/{business_name}")
async def delete_business(business_name: str):
    """Delete all indexed content for a business"""
    if curator is None:
        raise HTTPException(status_code=503, detail="Curator not initialized")
    
    return await curator.delete_business(business_name)


@app.get("/jobs")
async def list_jobs(status: Optional[str] = None, limit: int = 50):
    """List all jobs, optionally filtered by status"""
    results = list(jobs.values())
    
    if status:
        try:
            status_filter = CrawlStatus(status)
            results = [j for j in results if j.status == status_filter]
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid status: {status}")
    
    # Sort by started_at descending
    results.sort(key=lambda x: x.started_at or datetime.min, reverse=True)
    
    return {"jobs": results[:limit], "total": len(results)}


# =============================================================================
# BACKGROUND TASK
# =============================================================================

async def run_crawl_job(job_id: str, request: CrawlRequest):
    """Background task to run crawl job"""
    global jobs
    
    start_time = time.time()
    logger.info(f"[{job_id}] Starting crawl for {request.business_name}")
    
    try:
        jobs[job_id].status = CrawlStatus.SCRAPING
        
        result = await curator.crawl_and_index(request)
        
        # Update job record
        jobs[job_id] = result
        jobs[job_id].request_id = job_id
        
        # Update metrics
        CRAWL_LATENCY.observe(time.time() - start_time)
        CRAWL_COUNT.labels(status=result.status.value).inc()
        PAGES_CRAWLED.inc(result.pages_crawled)
        CHUNKS_INDEXED.inc(result.chunks_indexed)
        
        logger.info(f"[{job_id}] Completed: {result.pages_crawled} pages, {result.chunks_indexed} chunks")
        
    except Exception as e:
        logger.error(f"[{job_id}] Failed: {e}")
        jobs[job_id].status = CrawlStatus.FAILED
        jobs[job_id].errors.append(str(e))
        jobs[job_id].completed_at = datetime.utcnow()
        jobs[job_id].duration_seconds = time.time() - start_time
        CRAWL_COUNT.labels(status="failed").inc()
    
    finally:
        ACTIVE_JOBS.dec()


# =============================================================================
# MAIN
# =============================================================================

if __name__ == "__main__":
    import uvicorn
    
    port = int(os.getenv("PORT", "8100"))
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=port,
        reload=os.getenv("ENVIRONMENT") == "development"
    )
