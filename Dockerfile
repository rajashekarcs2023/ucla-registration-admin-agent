FROM python:3.11-slim-buster

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy agent code
COPY . .

# Make entrypoint executable
RUN chmod +x /app/entrypoint.sh

# Expose the agent's port
EXPOSE 8048

# Run via entrypoint (persists data on shutdown)
CMD ["/app/entrypoint.sh"]

