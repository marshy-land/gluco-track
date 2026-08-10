FROM python:3.9-slim

# Install ADB
RUN apt-get update && apt-get install -y adb && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt frida-tools

COPY bridge.py .

CMD ["python", "-u", "bridge.py"]
