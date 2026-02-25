FROM python:3.11-slim

WORKDIR /app

# git is needed for cloud-mode fix restoration (git checkout -- <file>)
RUN apt-get update && apt-get install -y git && rm -rf /var/lib/apt/lists/*

# Install CPU-only PyTorch first (avoids pulling the large CUDA build)
RUN pip install --no-cache-dir \
    torch \
    --extra-index-url https://download.pytorch.org/whl/cpu

# Install the rest of the dependencies
RUN pip install --no-cache-dir \
    fastapi \
    uvicorn \
    httpx \
    requests \
    python-dotenv \
    pytest

# Copy source (includes .git so cloud-mode git checkout works)
COPY . .

# Trust the repo inside the container
RUN git config --global --add safe.directory /app \
 && git config --global user.email "deploy@sentinel.local" \
 && git config --global user.name "Sentinel Deploy"

EXPOSE 8080

# PORT is injected by Render/Railway/Fly; falls back to 8080 locally
CMD ["sh", "-c", "uvicorn dashboard:app --host 0.0.0.0 --port ${PORT:-8080}"]
