FROM alpine:3.22

WORKDIR /app

COPY entrypoint.sh /app/entrypoint.sh

RUN chmod +x /app/entrypoint.sh

STOPSIGNAL SIGTERM

ENTRYPOINT ["/app/entrypoint.sh"]