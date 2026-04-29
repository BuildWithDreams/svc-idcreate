# Use the official image from Astral (creators of uv)
# It includes Python 3.12 and uv pre-installed
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

# Prevent Python from writing pyc files to disc
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# 1. Copy dependency files first (to leverage Docker caching)
COPY pyproject.toml uv.lock ./

# 2. Install dependencies
# --frozen: ensures we use exactly the versions in uv.lock
# --no-install-project: installs deps but not your app code yet (good for caching)
RUN uv sync --frozen --no-install-project

# 3. Copy the rest of the application code
COPY . .

# 4. Expose the port
EXPOSE 5003

# 5. Run the app using 'uv run' 
# This automatically uses the virtual environment uv created
CMD ["uv", "run", "fastapi", "run", "id_create_service.py", "--port", "5003", "--host", "0.0.0.0"]

