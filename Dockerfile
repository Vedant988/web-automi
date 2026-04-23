FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system utilities needed for Playwright
RUN apt-get update && \
    apt-get install -y curl wget xvfb && \
    rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright browsers and their OS-level dependencies
RUN playwright install --with-deps chromium

# Copy the rest of the application
COPY . .

# Expose the port
EXPOSE 8000

# Start the application with a virtual framebuffer (Xvfb) to bypass headless bot detection
CMD sh -c "xvfb-run -a uvicorn server:app --host 0.0.0.0 --port ${PORT:-10000}"
