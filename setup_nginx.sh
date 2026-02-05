#!/bin/bash
# Setup Nginx reverse proxy (for domain or IP)

set -e

echo "============================================"
echo "Nginx Setup"
echo "============================================"

read -p "Do you have a domain? (y/n): " HAS_DOMAIN

if [ "$HAS_DOMAIN" = "y" ] || [ "$HAS_DOMAIN" = "Y" ]; then
    read -p "Enter your domain name (e.g., mirror.example.com): " DOMAIN
    
    # Install Certbot
    sudo apt install -y certbot python3-certbot-nginx
    
    # Create Nginx config with SSL
    sudo tee /etc/nginx/sites-available/mirror-download > /dev/null <<EOF
server {
    listen 80;
    server_name $DOMAIN;
    return 301 https://\$server_name\$request_uri;
}

server {
    listen 443 ssl;
    server_name $DOMAIN;

    ssl_certificate /etc/letsencrypt/live/$DOMAIN/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/$DOMAIN/privkey.pem;

    client_max_body_size 0;
    proxy_request_buffering off;
    proxy_buffering off;

    location / {
        proxy_pass http://localhost:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_cache_bypass \$http_upgrade;
        proxy_connect_timeout 3600s;
        proxy_send_timeout 3600s;
        proxy_read_timeout 3600s;
    }
}
EOF

    # Enable site
    sudo ln -sf /etc/nginx/sites-available/mirror-download /etc/nginx/sites-enabled/
    sudo rm -f /etc/nginx/sites-enabled/default
    
    # Test nginx
    sudo nginx -t
    sudo systemctl reload nginx
    
    # Get SSL certificate
    echo "[*] Obtaining SSL certificate..."
    sudo certbot --nginx -d $DOMAIN --non-interactive --agree-tos --email admin@$DOMAIN
    
    echo ""
    echo "============================================"
    echo "HTTPS Setup Complete!"
    echo "============================================"
    echo "Your server: https://$DOMAIN"
    
    # Set Telegram webhook
    if [ -f /opt/mirror-download/.env ]; then
        BOT_TOKEN=$(grep TELEGRAM_BOT_TOKEN /opt/mirror-download/.env | cut -d= -f2)
        if [ -n "$BOT_TOKEN" ]; then
            echo "[*] Setting Telegram webhook..."
            curl -s "https://api.telegram.org/bot$BOT_TOKEN/setWebhook?url=https://$DOMAIN/webhook/telegram"
            echo ""
        fi
    fi
    
else
    echo "[*] Setting up for IP access (HTTP only)..."
    
    # Get EC2 public IP
    EC2_IP=$(curl -s ifconfig.me)
    
    # Create simple Nginx config (HTTP only)
    sudo tee /etc/nginx/sites-available/mirror-download > /dev/null <<EOF
server {
    listen 80;
    server_name _;

    client_max_body_size 0;
    proxy_request_buffering off;
    proxy_buffering off;

    location / {
        proxy_pass http://localhost:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_cache_bypass \$http_upgrade;
        proxy_connect_timeout 3600s;
        proxy_send_timeout 3600s;
        proxy_read_timeout 3600s;
    }
}
EOF

    # Enable site
    sudo ln -sf /etc/nginx/sites-available/mirror-download /etc/nginx/sites-enabled/
    sudo rm -f /etc/nginx/sites-enabled/default
    
    # Test and reload
    sudo nginx -t
    sudo systemctl reload nginx
    
    echo ""
    echo "============================================"
    echo "HTTP Setup Complete!"
    echo "============================================"
    echo "Your server: http://$EC2_IP"
    echo ""
    echo "⚠️  NOTE: Telegram webhook requires HTTPS."
    echo "   Options for Telegram:"
    echo "   1. Use API only (no bot commands)"
    echo "   2. Get a free domain from freenom.com or duckdns.org"
    echo "   3. Use ngrok for temporary HTTPS tunnel"
fi

echo ""
echo "Nginx is configured and running!"
