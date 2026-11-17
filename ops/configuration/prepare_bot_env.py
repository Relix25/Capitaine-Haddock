"""Create the user's requested private bot .env without overwriting an existing token."""


def main():
    import grp
    import os
    from pathlib import Path

    if os.geteuid() != 0:
        raise SystemExit('root required')
    os.umask(0o077)
    path = Path('/etc/cours-watch/.env')
    if not path.exists():
        path.write_text('DISCORD_BOT_TOKEN=\n')
    path.chmod(0o640)
    os.chown(path,0,grp.getgrnam('courswatch').gr_gid)
    print('Private .env ready at /etc/cours-watch/.env ; root:courswatch, mode 0640. Values not displayed.')


if __name__ == '__main__':
    main()
