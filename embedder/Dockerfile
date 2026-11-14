FROM python:3.12-slim
WORKDIR /app
# CPU-only torch keeps the image ~1 GB instead of ~5 GB with CUDA.
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
ARG EMBED_MODEL=intfloat/multilingual-e5-large
ENV EMBED_MODEL=${EMBED_MODEL} HF_HOME=/models
# Bake the model into the image so restarts don't re-download ~2.2 GB.
RUN python -c "import os; from sentence_transformers import SentenceTransformer as S; S(os.environ['EMBED_MODEL'])"
COPY app.py .
EXPOSE 8001
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8001"]
