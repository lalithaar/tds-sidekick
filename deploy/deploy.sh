#!/usr/bin/env bash
# TDS Sidekick one-shot server setup (Ubuntu/Debian, root or sudo).
#
# Prereqs:
#   1. Your domain's DNS must already point at this server's IP
#      (certbot needs it to issue the TLS cert).
#   2. Run this as root:  sudo bash deploy/deploy.sh
set -euo pipefail

DOMAIN="${1:-YOUR.DOMAIN}"              # pass the domain as argv[1]
APP_USER=sidekick
APP_GROUP=sidekick
APP_DIR=/srv/tds-sidekick
REPO_URL=https://github.com/lalithaar/tds-sidekick.git
ENV_DIR=/etc/tds-sidekick

if [ "$DOMAIN" = "YOUR.DOMAIN" ]; then
    echo "Usage: sudo bash deploy/deploy.sh yourdomain.com" >&2
    exit 1
fi

echo "==> Installing system packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y python3-venv python3-pip git nginx certbot python3-certbot-nginx

echo "==> App user"
id -u "$APP_USER" &>/dev/null || useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin "$APP_USER"

echo "==> App source"
if [ -d "$APP_DIR/.git" ]; then
    git -C "$APP_DIR" pull --ff-only
else
    git clone "$REPO_URL" "$APP_DIR"
fi
chown -R "$APP_USER:$APP_GROUP" "$APP_DIR"

echo "==> venv + deps"
if [ ! -x "$APP_DIR/.venv/bin/python" ]; then
    runuser -u "$APP_USER" -- python3 -m venv "$APP_DIR/.venv"
fi
runuser -u "$APP_USER" -- "$APP_DIR/.venv/bin/pip" install --upgrade pip
runuser -u "$APP_USER" -- "$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt"

echo "==> Environment file"
mkdir -p "$ENV_DIR"
cat > "$ENV_DIR/env" <<ENV
SECRET_KEY=$($APP_DIR/.venv/bin/python -c "import secrets; print(secrets.token_urlsafe(50))")
DJANGO_DEBUG=false
ALLOWED_HOSTS=$DOMAIN
CSRF_TRUSTED_ORIGINS=https://$DOMAIN
ENV
chmod 600 "$ENV_DIR/env"

echo "==> Migrate + collectstatic"
runuser -u "$APP_USER" -- "$APP_DIR/.venv/bin/python" "$APP_DIR/manage.py" migrate --noinput
runuser -u "$APP_USER" -- "$APP_DIR/.venv/bin/python" "$APP_DIR/manage.py" collectstatic --noinput

echo "==> systemd unit"
cp deploy/tds-sidekick.service /etc/systemd/system/tds-sidekick.service
systemctl daemon-reload
systemctl enable --now tds-sidekick

echo "==> nginx"
rm -f /etc/nginx/sites-enabled/default
cp deploy/nginx.conf /etc/nginx/sites-available/tds-sidekick
sed -i "s/YOUR.DOMAIN/$DOMAIN/g" /etc/nginx/sites-available/tds-sidekick
ln -sf /etc/nginx/sites-available/tds-sidekick /etc/nginx/sites-enabled/tds-sidekick
nginx -t
systemctl reload nginx

echo "==> TLS (Let's Encrypt)"
certbot --nginx -d "$DOMAIN" --redirect --agree-tos -m admin@$DOMAIN

echo
echo "DONE. App should be live at https://$DOMAIN"
echo "Next: create your admin account:"
echo "    runuser -u $APP_USER -- $APP_DIR/.venv/bin/python $APP_DIR/manage.py createsuperuser"
echo
echo "Useful:"
echo "    systemctl status tds-sidekick      # app logs: journalctl -u tds-sidekick"
echo "    sudo certbot renew                 # auto-renew is installed by default"