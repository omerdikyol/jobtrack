FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY jobtrack ./jobtrack
RUN pip install --no-cache-dir .
ENV JOBTRACK_HOME=/data
EXPOSE 8080
# Set JOBTRACK_PASSWORD, or the server refuses a public bind.
CMD ["jobtrack", "serve", "--host", "0.0.0.0", "--port", "8080", "--no-browser"]
