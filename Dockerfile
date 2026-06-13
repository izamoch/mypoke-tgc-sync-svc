# Use official lightweight Python image
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Copy setup metadata and package source for installation caching
COPY pyproject.toml .
COPY src/ ./src/

# Install the package and its runtime dependencies declared in pyproject.toml
RUN pip install --no-cache-dir .

# Copy the rest of the project files
COPY . .

# Run the sync job by default using the CLI executable registered by pip
CMD ["mypoke-sync"]
