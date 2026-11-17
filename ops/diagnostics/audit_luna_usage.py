"""Read-only usage accounting audit. No keys, prompts, responses or user identities."""


def main():
    from contextlib import closing
    import json
    from pathlib import Path
    import sqlite3

    data = Path('/var/lib/cours-watch')
    with closing(sqlite3.connect('file:'+str(data/'luna-usage.db')+'?mode=ro',uri=True)) as db:
        db.row_factory = sqlite3.Row
        groups = [dict(row) for row in db.execute('''SELECT day,status,COUNT(*) AS calls,
            SUM(input_tokens) AS input_tokens,SUM(output_tokens) AS output_tokens,
            SUM(charged) AS quota_accounted,SUM(reserved) AS initial_reservations,
            MIN(created) AS first_utc,MAX(created) AS last_utc FROM calls GROUP BY day,status ORDER BY day,status''')]
        recent = [dict(row) for row in db.execute('''SELECT id,day,kind,status,reserved,charged,input_tokens,output_tokens,created
            FROM calls ORDER BY id DESC LIMIT 12''')]
    print(json.dumps({'groups':groups,'recent_calls':recent},ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
