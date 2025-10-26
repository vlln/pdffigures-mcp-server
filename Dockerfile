# -----------------------------------------------------
# Stage 1: Build Java/Scala components (pdffigures2)
# -----------------------------------------------------
FROM openjdk:11-jdk-slim AS builder

# 1. Install build dependencies (including sbt and git)
RUN apt-get update && apt-get install -y \
    curl \
    gnupg \
    git \
    # Install dependencies for building native extensions for Python packages if needed
    build-essential \
    python3-dev \
    # Add sbt repository and install sbt
    && echo "deb https://repo.scala-sbt.org/scalasbt/debian all main" | tee /etc/apt/sources.list.d/sbt.list \
    && curl -sL "https://keyserver.ubuntu.com/pks/lookup?op=get&search=0x99E82A75642AC823" | apt-key add \
    && apt-get update \
    && apt-get install -y sbt

# 2. Build pdffigures2
WORKDIR /pdffigures2
RUN git clone https://github.com/allenai/pdffigures2.git .
RUN sbt assembly
# pdffigures2 assembly jar is now at /pdffigures2/pdffigures2.jar


# -----------------------------------------------------
# Stage 2: Final Runtime Image (Python-centric)
# -----------------------------------------------------
FROM python:3.11-slim AS runtime 

# 1. Install Java/OpenJDK runtime and Tesseract OCR
RUN apt-get update && apt-get install -y \
    default-jre-headless \
    libleptonica-dev \
    tesseract-ocr \
    # python3-pip is usually pre-installed in the official python:3.11-slim image
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /pdffigures2/pdffigures2.jar /pdffigures2/pdffigures2.jar

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt 

WORKDIR /app
COPY ./app/*.py /app/

ENV JAVA_HOME=/usr/lib/jvm/default-java
ENV PATH="${JAVA_HOME}/bin:${PATH}"
# ENV JAVA_OPTS="-Xmx12g"
ENV JAVA_OPTS="-XX:MaxRAMPercentage=75.0"
ENV OUTPUT_DIR=/app/outputs

ENV SERVER_PORT=5001
EXPOSE 5001

ENV LOG_LEVEL='INFO'

# If deployed standalone, set this to the host IP
# If deployed in Kubernetes, set this to the service name
ENV RESOURCE_BASE_URL=http://localhost:5001

ENTRYPOINT ["python", "-m", "uvicorn", "app:app"]
CMD ["--host", "0.0.0.0", "--port", "5001", "--log-level", "info"]