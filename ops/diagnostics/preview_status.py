"""Read-only production preview of /status; no message sent and no credential read."""


def main():
    import json
    from pathlib import Path
    from courswatch.discord_app.health import build_status_snapshot, status_embed, read_json

    data = Path('/var/lib/cours-watch')
    config = json.loads(Path('/etc/cours-watch/config.json').read_text())
    bot = read_json(data / 'reports/bot-status.json')
    snapshot = build_status_snapshot(data,config,bot.get('ready',False),None)
    print(json.dumps({'snapshot':snapshot,'embed':status_embed(snapshot)},ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
