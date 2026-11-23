FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 TZ=Asia/Bangkok HF_HOME=/app/data/hf_cache TOKENIZERS_PARALLELISM=false
WORKDIR /app
COPY worker/requirements.txt /app/worker/requirements.txt
RUN pip install --no-cache-dir -r /app/worker/requirements.txt
# sentiment model (CLAUDE.md §8): CPU-only PyTorch keeps the image small; weights are cached on ./data/hf_cache
COPY worker/requirements-ml.txt /app/worker/requirements-ml.txt
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu \
 && pip install --no-cache-dir -r /app/worker/requirements-ml.txt
COPY worker /app/worker
COPY seed /app/seed
COPY supabase /app/supabase
CMD ["python", "/app/worker/server.py"]
