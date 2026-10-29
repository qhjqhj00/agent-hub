# The hub only. Nodes (the machines your agents run on) use `agenthub join`:
# hubd starts agents in tmux on its own machine, so it does not belong in a container.
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY . /app
ENV HUB_DATA=/data HUB_IDENTITY=token PYTHONUNBUFFERED=1
VOLUME /data
EXPOSE 8787
CMD ["python3", "bin/agenthub", "serve", "--host", "0.0.0.0", "--port", "8787"]
