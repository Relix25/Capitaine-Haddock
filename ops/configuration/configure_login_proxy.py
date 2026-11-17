"""Add one authenticated route to the existing WireGuard-only nginx virtual host."""


def main():
    from pathlib import Path
    import grp
    import os
    import secrets
    import subprocess

    site=Path('/etc/nginx/sites-available/opencode-private')
    snippet=Path('/etc/nginx/snippets/cours-watch-private.conf')
    password_file=Path('/etc/cours-watch/login-http-password.txt')
    if not password_file.exists():
        password_file.write_text(secrets.token_urlsafe(24)+'\n')
        password_file.chmod(0o600)
    password=password_file.read_text().strip()
    hashed=subprocess.run(['openssl','passwd','-6','-stdin'],input=password+'\n',text=True,
                          check=True,capture_output=True).stdout.strip()
    credentials=Path('/etc/nginx/cours-watch.htpasswd')
    credentials.write_text('cours-watch:'+hashed+'\n')
    credentials.chmod(0o640)
    os.chown(credentials,0,grp.getgrnam('www-data').gr_gid)
    snippet.write_text('''location /cours-watch/ {
        allow 10.77.77.2;
        deny all;
        auth_basic "Private course monitor";
        auth_basic_user_file /etc/nginx/cours-watch.htpasswd;
        proxy_pass http://127.0.0.1:6081/;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 3600s;
    }
    ''')
    snippet.chmod(0o644)
    text=site.read_text()
    include='    include /etc/nginx/snippets/cours-watch-private.conf;\n'
    if include.strip() not in text:
        if text.count('listen 10.77.77.1:8443 ssl;')!=1 or text.count('proxy_pass http://127.0.0.1:4096;')!=1:
            raise RuntimeError('Unexpected existing proxy layout; no site change made')
        backup=site.with_name('opencode-private.before-cours-watch')
        if not backup.exists():
            backup.write_text(text)
            backup.chmod(0o600)
        site.write_text(text.rsplit('}',1)[0]+include+'}\n')
    try:
        subprocess.run(['nginx','-t'],check=True)
    except Exception:
        site.write_text(text)
        raise
    try:
        subprocess.run(['systemd-run','--unit=cours-watch-login-expiry','--on-active=1h',
                        '/usr/local/sbin/cours-watch-close-login'],check=True)
        subprocess.run(['systemctl','reload','nginx'],check=True)
    except Exception:
        site.write_text(text)
        subprocess.run(['systemctl','reload','nginx'],check=False)
        raise
    print('Temporary private login route installed for at most one hour; dedicated credential; SSH and firewall unchanged.')


if __name__ == '__main__':
    main()
