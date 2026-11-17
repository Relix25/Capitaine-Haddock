"""Print non-secret format diagnostics only, never any portion of a credential."""


def main():
    import base64
    import json
    import re
    from courswatch.runtime import load_discord_secrets

    value = load_discord_secrets().get('DISCORD_BOT_TOKEN','')
    parts = value.split('.')
    matches_application = False
    if len(parts) == 3:
        try:
            decoded = base64.urlsafe_b64decode(parts[0] + '=' * (-len(parts[0]) % 4)).decode()
            matches_application = decoded == '1510767148006641866'
        except Exception:
            pass
    print(json.dumps({'length':len(value),'segments':len(parts),'hexadecimal':bool(re.fullmatch(r'[0-9a-fA-F]+',value)),
                      'expected_application_prefix':matches_application,
                      'matches_supplied_public_key':value == '53281c0de45da8abdb8798aa679eba94445c6ca313c91dc581784b2c47427000'}))


if __name__ == '__main__':
    main()
