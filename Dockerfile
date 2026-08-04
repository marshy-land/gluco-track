FROM python:3.11-slim

WORKDIR /app

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy all source files
COPY . .

# Run python with -u flag to prevent stdout buffering (ensures logs appear instantly in Railway)
CMD ["python", "-u", "main.py"]
