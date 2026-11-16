"""Apply reviewed French wording to six fixed, already collected announcements."""
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3

from course_translation import cache_key

data = Path('/var/lib/cours-watch')
reviewed = {
 ('teams:AAIT','message-body-1790670550738'):(
    'Cours 1',
    'Salut ! On se voit aujourd’hui en PR303 pour le premier cours !'),
 ('teams:AAIT','message-body-1793789179184'):(
    'Inscription des équipes pour le devoir Crafter',
    'Pour organiser les présentations des projets, merci de prendre une minute pour remplir le formulaire « 2026 RL Module Assignment Team Registration » ci-dessous.\n'
    'Indiquez le prénom et le nom complets de chaque membre de l’équipe (FirstName LastName). Si vous travaillez seul, saisissez « n/a » pour le deuxième membre. Choisissez également un nom d’équipe. Merci !'),
 ('teams:AAIT','message-body-1794229141308'):(
    'Crafter : report de la date limite et dates des présentations',
    'Bonjour à tous. J’ai décidé de repousser au 15 novembre la date limite du devoir Crafter. Les équipes qui ont déjà rendu leur travail peuvent améliorer leur rendu. '
    'Assurez-vous que les résultats que vous présenterez figurent bien dans les documents déposés.\n'
    'Le 15 novembre est une date limite ferme : vous ne pourrez plus déposer de projet après cette date ! '
    'À la suite de cette prolongation, vous présenterez vos résultats respectivement le 24 novembre et le 8 décembre.'),
 ('teams:AITDM','message-body-1794246893860'):(
    'Présentation du projet — phase 1',
    'Bonjour à tous. Les présentations de la première phase du projet auront lieu le mercredi 11 novembre, à partir de 14 h (2:00 p.m.), selon le planning du tableau ci-dessous.\n'
    'Pour chaque projet :\n'
    '• Équipes de 1 à 2 étudiants : 7 minutes maximum.\n'
    '• Équipes de 3 à 4 étudiants : 10 minutes maximum.\n'
    'Chaque membre de l’équipe doit présenter sa contribution. Merci de déposer également votre présentation dans le devoir correspondant sur Moodle.'),
}
sos = ('Projet — présentation intermédiaire',
       'Chers étudiants, les présentations intermédiaires auront lieu le 16 novembre.\n'
       'Préparez une courte présentation PowerPoint d’environ 5 à 7 diapositives comprenant :\n'
       '• Le sujet du projet et son contexte.\n'
       '• L’approche que vous prévoyez de suivre.\n'
       'L’objectif est d’obtenir de premiers retours et de montrer votre progression. '
       'J’ai créé un document en ligne où vous pouvez choisir un créneau pour votre présentation. Inscrivez-vous dès que possible afin de permettre l’organisation de la séance.\n'
       'Les présentations auront lieu sur Microsoft Teams. Déposez votre fichier de présentation dans le devoir « Intermediate Presentation » sur Moodle au plus tard le 15 novembre.')
reviewed[('moodle:SOS','forum:p2118')] = sos
reviewed[('teams:SOS','message-body-1793484438949')] = sos
report = json.loads((data / 'reports/translations-backfill.json').read_text())
known = {(row['source'],row.get('item_key')):row for row in report if row.get('item_key')}
plan = []
with closing(sqlite3.connect('file:' + str(data / 'state.db') + '?mode=ro',uri=True)) as source:
    for (platform,key),(title,body) in reviewed.items():
        row = source.execute('SELECT payload FROM items WHERE source=? AND key=?',(platform,key)).fetchone()
        item = json.loads(row[0]) if row else None
        previous = known.get((platform,key))
        if not item or not previous or item['body'] != previous['original_body'] or item['title'] != previous['original_title']:
            raise SystemExit('Announcement changed since review; no write performed')
        links = re.findall(r'https?://[^\s<>]+',item['body'])
        if links:
            body += '\n\nLiens du message original :\n' + '\n'.join(links)
        plan.append((cache_key(item),title,body))
with closing(sqlite3.connect(data / 'translations.db')) as cache:
    for key,title,body in plan:
        row = cache.execute('SELECT payload FROM translations WHERE key=?',(key,)).fetchone()
        if not row:
            raise SystemExit('Existing translation required')
        record = json.loads(row[0])
        record.update(status='translated',title=title,body=body,reviewed=True,
                      reviewed_utc=datetime.now(timezone.utc).isoformat(timespec='seconds'))
        cache.execute('UPDATE translations SET payload=? WHERE key=?',(json.dumps(record,ensure_ascii=False),key))
    cache.commit()
print(json.dumps({'reviewed_translations':len(plan),'source_snapshots_modified':False}))
