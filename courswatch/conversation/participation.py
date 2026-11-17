"""Deterministic, bounded participation rules before any paid generation."""
import re
from courswatch.conversation.contracts import ParticipationDecision

CANCEL = re.compile(r"\b(laisse(?:z)? tomber|annule|oublie|pas besoin|ne réponds? pas)\b", re.I)
CORRECTION = re.compile(r"\b(correction|corrige|précision|en fait|report[ée]|rectifi|non[, ]|plutôt)\b", re.I)
CONTINUATION = re.compile(r"^(?:haddock\W+)?(?:et aussi|une (?:seconde |autre )?précision|précision|je précise|pour préciser|à propos de|concernant|c['’]est pour)\b", re.I)
THANKS = re.compile(r"^(?:(?:merci|ok|okay|d'accord|super|bien reçu|parfait|ça marche|bonne nuit|salut)\W*)+(?:(?:capitaine\s+)?haddock\W*)?$", re.I)
INCOMPLETE = re.compile(r"(?:\.\.\.|[:…])\s*$|\b(?:j['’]ai une question|peux-tu me dire|je voulais savoir)\s*[.!?]*$", re.I)
UNCERTAIN = re.compile(r"\b(?:je (?:ne )?sais pas|aucune id[eé]e|quelqu['’]un sait|peut[- ]être|je crois|pas s[uû]r|bonne question)\b", re.I)
DATE_REQUEST = re.compile(r'\b(?:date|quand|deadline|remise|rendu|d[eé]p[oô]t|[eé]ch[eé]ance)\b', re.I)
DATE_VALUE = re.compile(
    r'\b(?:\d{1,2}[/.-]\d{1,2}(?:[/.-]\d{2,4})?|'
    r'\d{1,2}\s+(?:janvier|f[eé]vrier|mars|avril|mai|juin|juillet|ao[uû]t|septembre|octobre|novembre|d[eé]cembre)|'
    r'lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche)\b', re.I,
)
DATE_ASSERTION = re.compile(r"(?:^|\b(?:date|remise|rendu|d[eé]p[oô]t|[eé]ch[eé]ance|c['’]est)\b[^.!?]*)\s*(?:le\s+)?$", re.I)
ANSWER_ASSERTION = re.compile(
    r"\b(?:voici (?:la|une|les) r[eé]ponse|la r[eé]ponse est|c['’]est (?:le|la|les|dans|sur)|"
    r"il faut|tu peux (?:trouver|utiliser))\b", re.I,
)


def decide(envelope, *, direct, ambient_allowed=True, mode='selective', now=None):
    if envelope.ingestion_mode != 'live':
        return ParticipationDecision('silence', 'historical_ingestion')
    if envelope.deleted or envelope.is_bot:
        return ParticipationDecision('silence', 'inactive_source')
    text = envelope.text.strip()
    if CANCEL.search(text):
        return ParticipationDecision('silence', 'cancelled')
    if THANKS.fullmatch(text):
        return ParticipationDecision('silence', 'acknowledgement')
    if not text and not envelope.media:
        return ParticipationDecision('silence', 'empty_message')
    if direct:
        if INCOMPLETE.search(text):
            return ParticipationDecision('wait', 'incomplete_request', text, 600)
        return ParticipationDecision('respond', 'direct_request', text or 'Décrire le média joint')
    if mode == 'direct_only' or not ambient_allowed or len(text) < 18:
        return ParticipationDecision('silence', 'ambient_restraint')
    # A human-addressed message is not promoted by the previous interlocutor.
    if envelope.mentioned_user_ids:
        return ParticipationDecision('silence', 'human_addressed')
    return ParticipationDecision('wait', 'ambient_debounce', text, 4000)


def substantive_answer(text, goal=''):
    """Resolve a human question only on a conservative affirmative answer signal."""
    text = str(text).strip()
    if not text or '?' in text or UNCERTAIN.search(text):
        return False
    if DATE_REQUEST.search(goal):
        date = DATE_VALUE.search(text)
        return bool(date and DATE_ASSERTION.search(text[:date.start()])
                    and not re.search(r"\b(?:pas|aucun[e]?)\b", text[:date.start()], re.I))
    return bool(ANSWER_ASSERTION.search(text))
