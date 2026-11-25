#!/usr/bin/env bash
set -euo pipefail

DOMAIN="${1:-lijnet.et}"
EMAIL="${2:-admin@lijnet.et}"
COMPOSE_FILE="docker-compose.prod.yml"
ENV_FILE=".env.prod"

if [ ! -f "$ENV_FILE" ]; then
  echo "ERROR: $ENV_FILE not found. Copy .env.example to .env.prod first."
  exit 1
fi

echo "==> 1. Creating temporary certificate so Nginx can boot on port 443..."
docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" run --rm --entrypoint "\
  sh -c 'mkdir -p /etc/letsencrypt/live/$DOMAIN && \
  openssl req -x509 -nodes -newkey rsa:2048 -days 1 \
    -keyout /etc/letsencrypt/live/$DOMAIN/privkey.pem \
    -out /etc/letsencrypt/live/$DOMAIN/fullchain.pem \
    -subj \"/CN=localhost\"'" certbot

echo "==> 2. Starting PostgreSQL, FastAPI web, and Nginx..."
docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" up -d db web nginx

echo "==> 3. Removing temporary certificate and requesting Let's Encrypt SSL for $DOMAIN..."
docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" run --rm --entrypoint "\
  rm -rf /etc/letsencrypt/live/$DOMAIN /etc/letsencrypt/archive/$DOMAIN /etc/letsencrypt/renewal/$DOMAIN.conf" certbot

docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" run --rm --entrypoint "\
  certbot certonly --webroot -w /var/www/certbot \
    --email $EMAIL \
    -d $DOMAIN -d www.$DOMAIN \
    --rsa-key-size 4096 \
    --agree-tos \
    --non-interactive \
    --force-renewal" certbot

echo "==> 4. Reloading Nginx with trusted TLS certificate and starting renewal daemon..."
docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" exec nginx nginx -s reload
docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" up -d certbot

echo "SUCCESS: https://$DOMAIN is now live with automatic SSL renewal."