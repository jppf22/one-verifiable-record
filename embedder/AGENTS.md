# Embedder AGENTS.md - Embedding Service Instructions

**READ THIS BEFORE TOUCHING embedder/ CODE**
This file overrides the root AGENTS.md for embedder-specific instructions.

## Service Overview

Local embedding service providing OpenAI-style `/v1/embeddings` endpoint using multilingual-e5-large.

**Why it exists**: Verda's inference gateway has no embeddings endpoint, so we serve the model ourselves.

### Directory Structure
```
embedder/
├── app.py              # FastAPI application
├── Dockerfile          # Container configuration
├── requirements.txt    # Python dependencies
└── __pycache__/
```

## Service Details

### API Endpoint
```
POST /v1/embeddings
```

**Request**:
```json
{
    "input": "text to embed" | ["text1", "text2"],
    "model": "intfloat/multilingual-e5-large",
    "input_type": "query" | "passage"
}
```

**Response**:
```json
{
    "object": "list",
    "model": "intfloat/multilingual-e5-large",
    "data": [
        {
            "object": "embedding",
            "index": 0,
            "embedding": [0.123, -0.456, ...]  // 1024 dimensions
        }
    ]
}
```

### Model Configuration
- **Model**: intfloat/multilingual-e5-large (1024 dimensions)
- **Environment Variable**: `EMBED_MODEL` (can override)
- **Framework**: SentenceTransformer
- **Normalization**: Enabled (normalize_embeddings=True)
- **Batch Size**: 32

### Input Prefixes (D8 in assumptions.md)
- **query**: Prefixes with "query: " (for claim-to-claim matching)
- **passage**: Prefixes with "passage: " (for retrieval/chunk matching)
- Inputs already starting with these prefixes are kept as-is

## Usage

### From Jobs (embed.py)
```python
import requests

EMBED_BASE_URL = "http://localhost:8001"

texts = ["claim text 1", "claim text 2"]
response = requests.post(
    f"{EMBED_BASE_URL}/v1/embeddings",
    json={"input": texts, "input_type": "query"}
)
embeddings = [item["embedding"] for item in response.json()["data"]]
```

### From API Server (rag.py, alignment.py)
```python
# Similar usage for query-time embeddings
# Use input_type="query" for queries
# Use input_type="passage" for document chunks
```

## Docker Configuration

### Dockerfile
```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8001"]
```

### Running
```bash
# Build and run
docker build -t ovr-embedder -f embedder/Dockerfile embedder/
docker run -p 8001:8001 ovr-embedder

# With docker-compose (already in docker-compose.yml)
docker compose up -d embedder
```

### Environment Variables
```bash
# Override model (optional)
EMBED_MODEL=intfloat/multilingual-e5-large

# For GPU (if available)
# Set in docker-compose.yml with deploy resources
```

## Health Check
```
GET /health
```

**Response**:
```json
{
    "status": "ok",
    "model": "intfloat/multilingual-e5-large",
    "dims": 1024
}
```

## Integration Points

### Used By
1. **jobs/embed.py**: Batch embedding generation
   - Embeds all claims and chunks
   - Uses batch processing for efficiency

2. **server/rag.py**: Query-time Q&A
   - Embeds user queries
   - Embeds retrieved chunks
   - Uses input_type="query" for queries, "passage" for chunks

3. **server/alignment.py**: Claim-to-claim matching
   - Embeds claims for similarity comparison
   - Uses input_type="query" for both sides

## Important Notes

### E5 Prefix Behavior
The multilingual-e5-large model requires instruction prefixes:
- `query: ` - For queries/semantic search
- `passage: ` - For passages/documents

This is automatically handled by the service based on `input_type` parameter.

### Performance
- **Throughput**: ~100-200 texts/second on CPU (batch_size=32)
- **Latency**: ~50-100ms per batch
- **GPU**: Much faster (1000+ texts/second)

### Memory
- Model size: ~1.5GB
- Needs GPU with at least 2GB VRAM for good performance
- CPU: Needs ~2GB RAM minimum

## Before You Start

1. Read root AGENTS.md
2. Read docs/assumptions.md (especially D8 for prefix usage)
3. Understand OpenAI-compatible embedding API format

## Common Tasks

### Adding New Input Type
1. Add to `Literal["query", "passage"]` type hint
2. Add prefix mapping in `_prefixed()` function
3. Update documentation
4. Test with new input type

### Changing Model
1. Update `MODEL_NAME` in app.py
2. Update `requirements.txt` if needed
3. Update Dockerfile if model has different dependencies
4. Test embedding generation
5. Verify dimension is still 1024 (or update schema)

### Adding Health Metrics
1. Add endpoint to app.py
2. Include useful metrics (model, dims, uptime, request count)
3. Update docker health check

## Gotchas

1. **Model Loading**: First request takes longer (model loads on first use)
   - Consider adding warmup endpoint
   - Or load model at startup

2. **Memory**: Model stays in memory, can be large
   - Use appropriate container resources
   - Monitor memory usage

3. **Prefix Handling**: Must prefix inputs correctly
   - Without prefixes, embeddings may be lower quality
   - Check inputs don't already have prefixes

4. **Normalization**: Always normalize embeddings
   - Ensures cosine similarity works correctly
   - `normalize_embeddings=True` is critical

## Testing

### Test Embedding
```bash
curl -X POST http://localhost:8001/v1/embeddings \
  -H "Content-Type: application/json" \
  -d '{"input": "test query", "input_type": "query"}'
```

### Test Health
```bash
curl http://localhost:8001/health
```

### Common Issues
- **Connection refused**: Service not running, check docker-compose
- **500 Error**: Model failed to load, check logs
- **Slow**: Running on CPU, use GPU or smaller batches
- **Wrong dimensions**: Model changed, check output dimension

## Final Notes

This is a simple but critical service. It:
1. Provides embeddings when Verda gateway doesn't
2. Uses standard OpenAI API format for compatibility
3. Handles E5 prefix requirements automatically
4. Works with both CPU and GPU

For production:
- Use GPU instance for bulk embedding
- Monitor memory usage
- Consider load balancing for high traffic
- Add authentication if needed
