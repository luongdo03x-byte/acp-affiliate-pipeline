#!/usr/bin/env python3
"""Render reviewable GCP systemd/nginx configuration; never installs it."""
import argparse
from pathlib import Path
import re
from textwrap import dedent
from urllib.parse import urlsplit


def render(user, acp_root, public_url):
    if not re.fullmatch(r"[a-z_][a-z0-9_-]*", user):
        raise ValueError("Invalid service user")
    if not re.fullmatch(r"/[a-zA-Z0-9_./-]+", acp_root) or ".." in Path(acp_root).parts:
        raise ValueError("ACP root must be an absolute path without spaces or shell characters")
    url = urlsplit(public_url)
    if (url.scheme != "https" or not url.hostname or url.username or url.password
            or url.path not in {"", "/"} or url.query or url.fragment
            or not re.fullmatch(r"[a-zA-Z0-9.-]+", url.netloc)):
        raise ValueError("Public URL must be an HTTPS origin without credentials or port")
    origin = "https://" + url.netloc
    root = acp_root.rstrip("/")
    return {
        "acp-factory.service": dedent(f"""\
            [Unit]
            Description=ACP physical Android Factory Controller
            Wants=network-online.target
            After=network-online.target

            [Service]
            Type=simple
            User={user}
            WorkingDirectory={root}/acp
            EnvironmentFile={root}/shared/.env.local
            # The second file overrides web port/live adapter values from shared env.
            EnvironmentFile=/etc/acp-factory-runtime.env
            ExecStart={root}/acp/.venv/bin/python {root}/acp/account_factory_server.py
            Restart=on-failure
            RestartSec=5
            NoNewPrivileges=true
            PrivateTmp=true
            UMask=0077

            [Install]
            WantedBy=multi-user.target
            """),
        "acp-factory-runtime.env": dedent(f"""\
            ACP_HOST=127.0.0.1
            ACP_PORT=5001
            ACP_FACTORY_CONTROLLER=1
            ACP_FACTORY_LOCAL_ONLY=1
            ACP_FACTORY_LAN_AUTO_ENROLL=false
            ACP_ADAPTER=mock
            ACP_SOURCE=mock
            ACP_PUBLIC_BASE_URL={origin}
            PYTHONUNBUFFERED=1
            """),
        "acp-factory-gateway.conf": dedent("""\
            # Install under /etc/nginx/conf.d; ngrok targets localhost:8080.
            limit_req_zone $binary_remote_addr zone=factory_pair:1m rate=30r/m;
            server {
                listen 127.0.0.1:8080;
                server_name _;
                client_max_body_size 32m;
                proxy_set_header Host $http_host;
                proxy_set_header X-Forwarded-Proto https;
                proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
                proxy_read_timeout 120s;

                location = /api/factory/enroll { return 403; }
                location = /api/factory/pair {
                    limit_req zone=factory_pair burst=5 nodelay;
                    limit_req_status 429;
                    access_log off;
                    proxy_pass http://127.0.0.1:5001;
                }
                location /api/factory/ {
                    proxy_pass http://127.0.0.1:5001;
                }
                location /oauth/account-factory/ {
                    # OAuth query contains a single-use authorization code.
                    access_log off;
                    proxy_pass http://127.0.0.1:5001;
                }
                location / {
                    proxy_pass http://127.0.0.1:5000;
                }
            }
            """),
        "acp-ngrok-gateway.conf": dedent(f"""\
            # Install as /etc/systemd/system/acp-ngrok.service.d/factory-gateway.conf
            [Service]
            ExecStart=
            ExecStart=/usr/local/bin/ngrok http 8080 --url {origin} --config /home/{user}/.config/ngrok/ngrok.yml
            """),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user", required=True)
    parser.add_argument("--acp-root", required=True)
    parser.add_argument("--public-url", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    files = render(args.user, args.acp_root, args.public_url)
    # Fail rather than silently replace an earlier configuration.
    args.output.mkdir(parents=True, exist_ok=False)
    for name, contents in files.items():
        (args.output / name).write_text(contents)
    print(f"Rendered {len(files)} files in {args.output}. Nothing installed or restarted.")


if __name__ == "__main__":
    main()
