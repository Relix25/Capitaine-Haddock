"""CPU-only translation worker; stdin/stdout JSON, no credentials and no networking."""
from collections import Counter
import json
from pathlib import Path
import re
import sys

import ctranslate2
from langid.langid import LanguageIdentifier, model as language_model
import sentencepiece as spm

PROTECTED = re.compile(r'(https?://[^\s<>]+|```[\s\S]*?```|`[^`\n]+`|\bpython3?\s+[^#\n]+|\./[\w/.-]+\.sh(?:[ \t]+(?!#)[^#\n]+)?|\b\d{1,2}(?::\d{2})?\s*[ap]\.?m\.?|\bn/a\b|\b(?:notebook|pathmnist|dermamnist|flower)\b|(?-i:\b[A-Z]{1,8}\d{1,6}\b)|(?-i:\b(?:Teams(?! that)|Moodle|Crafter|GitHub|Github|Colab|FedAvg|FEP|WSL2|Ubuntu)\b)|(?<!\w)--[\w-]+|\bABS_PATH[\w./-]*|(?:\./)?[\w./-]+\.(?:py|sh|npz|png|json)\b)',re.I)
NUMBERS = re.compile(r'\d+(?:[.:/]\d+)*')


class BPETokenizer:
    def __init__(self,path,source,target):
        from sacremoses import MosesTokenizer, MosesDetokenizer, MosesPunctNormalizer
        from subword_nmt.apply_bpe import BPE
        self.tokenizer = MosesTokenizer(lang=source)
        self.detokenizer = MosesDetokenizer(lang=target)
        self.normalizer = MosesPunctNormalizer(lang=source)
        with path.open() as codes:
            self.bpe = BPE(codes)

    def encode(self,text,out_type=str):
        return self.bpe.segment_tokens(self.tokenizer.tokenize(self.normalizer.normalize(text)))

    def decode(self,tokens):
        return self.detokenizer.detokenize(' '.join(tokens).replace('@@ ','').split())


class Engine:
    def __init__(self,root):
        self.root = root
        self.models = {}
        self.identifier = LanguageIdentifier.from_modelstring(language_model,norm_probs=True)
        self.identifier.set_languages(['en','fr','ro'])

    def load(self,pair):
        if pair not in self.models:
            root = self.root / pair
            metadata = json.loads((root / 'metadata.json').read_text())
            tokenizer = (spm.SentencePieceProcessor(model_file=str(root / 'sentencepiece.model'))
                         if (root / 'sentencepiece.model').exists() else
                         BPETokenizer(root / 'bpe.model',metadata['from_code'],metadata['to_code']))
            translator = ctranslate2.Translator(str(root / 'model'),device='cpu',compute_type='int8',
                                                inter_threads=1,intra_threads=2)
            self.models[pair] = (tokenizer,translator,metadata.get('target_prefix',''))
        return self.models[pair]

    def sentence(self,text,pair):
        if not re.search(r'[A-Za-zÀ-ž]',text):
            return text
        leading = text[:len(text) - len(text.lstrip())]
        trailing = text[len(text.rstrip()):]
        tokenizer,translator,prefix = self.load(pair)
        if pair == 'en_fr':
            text = re.sub(r'\bhard cut-off\b','strict deadline',text,flags=re.I)
            text = re.sub(r'\bpaper report\b','written report',text,flags=re.I)
            text = re.sub(r'\bslide[- ]deck\b','presentation slides',text,flags=re.I)
            text = re.sub(r'\btoy examples\b','simplified examples',text,flags=re.I)
        # Split long prose before inference rather than letting the engine truncate it.
        words = text.strip().split()
        chunks,buffer = [],[]
        for word in words:
            if buffer and len(tokenizer.encode(' '.join(buffer + [word]),out_type=str)) > 350:
                chunks.append(' '.join(buffer))
                buffer = []
            buffer.append(word)
        if buffer:
            chunks.append(' '.join(buffer))
        tokens = [tokenizer.encode(chunk,out_type=str) for chunk in chunks]
        results = translator.translate_batch(tokens,beam_size=4,replace_unknowns=True,
            max_input_length=512,max_decoding_length=512,length_penalty=0.2,
            target_prefix=[[prefix]] * len(tokens) if prefix else None)
        output = []
        for result in results:
            sentence = tokenizer.decode(result.hypotheses[0])
            if prefix and sentence.startswith(prefix):
                sentence = sentence[len(prefix):]
            output.append(sentence.replace('▁',' ').strip())
        translated = ' '.join(output)
        if pair == 'en_fr':
            if re.search(r'\blecture\b',text,re.I):
                translated = re.sub(r'\bconférence\b','cours',translated,flags=re.I)
                translated = re.sub(r'\bla première cours\b','le premier cours',translated,flags=re.I)
                translated = re.sub(r'\bla cours\b','le cours',translated,flags=re.I)
            if re.search(r'\bassignment\b',text,re.I):
                translated = re.sub(r'\b(?:affectation|mission)\b','devoir',translated,flags=re.I)
            if re.search(r'\bupload\w*',text,re.I) and not re.search(r'\bdownload\w*',text,re.I):
                translated = re.sub(r'\btéléchargement\b','dépôt',translated,flags=re.I)
                translated = re.sub(r'\btélécharg','dépos',translated,flags=re.I)
            translated = re.sub(r'\bla devoir\b','le devoir',translated,flags=re.I)
            translated = re.sub(r"\bl'devoir\b",'le devoir',translated,flags=re.I)
        return leading + translated + trailing

    def translate(self,text,language):
        if not text.strip():
            return text
        protected = {}
        def conceal(match):
            marker = str(987654321000 + len(protected))
            if marker in text:
                raise ValueError('Marker collision')
            protected[marker] = match[0]
            return marker
        masked = PROTECTED.sub(conceal,text)
        lines = re.split(r'(\n+)',masked)
        for j,line in enumerate(lines):
            if j % 2 or not line.strip():
                continue
            sentences = re.split(r'(?<=[.!?])\s+(?=[A-ZĂÂÎȘȚ])',line)
            translated = []
            for sentence in sentences:
                if language == 'ro':
                    sentence = self.sentence(sentence,'ro_en')
                translated.append(self.sentence(sentence,'en_fr'))
            lines[j] = ' '.join(translated)
        output = ''.join(lines)
        for marker,original in protected.items():
            if output.count(marker) != 1:
                raise ValueError('Protected span changed')
            output = output.replace(marker,original)
        # Keep the assignment name instead of its ordinary French noun.
        if 'Crafter' in text:
            output = re.sub(r'\b(?:artisan|artisans|crafteur)\b','Crafter',output,flags=re.I)
        return output

    def translate_segments(self,text,language):
        pieces = PROTECTED.split(text)
        for i,piece in enumerate(pieces):
            if i % 2:
                continue
            lines = re.split(r'(\n+)',piece)
            for j,line in enumerate(lines):
                if j % 2 or not line.strip():
                    continue
                sentences = re.split(r'(?<=[.!?])\s+(?=[A-ZĂÂÎȘȚ])',line)
                output = []
                for sentence in sentences:
                    if language == 'ro':
                        sentence = self.sentence(sentence,'ro_en')
                    output.append(self.sentence(sentence,'en_fr'))
                lines[j] = ' '.join(output)
            pieces[i] = ''.join(lines)
        return ''.join(pieces)

    def run(self,item):
        title,body = item['title'],item['body']
        probe = PROTECTED.sub(' ',body)
        language,confidence = self.identifier.classify(probe or title)
        record = {'key':item['key'],'source_language':language,'target_language':'fr',
                  'confidence':round(float(confidence),4)}
        if language == 'fr':
            return {**record,'status':'not_needed','title':title,'body':body}
        if confidence < 0.7:
            return {**record,'status':'failed','reason':'language_uncertain'}
        reason = 'translation_failed'
        for translate in (self.translate,self.translate_segments):
            try:
                translated_title = title if title in ('Publication','Annonce') else translate(title,language)
                translated_body = translate(body,language)
                for original,translated in ((title,translated_title),(body,translated_body)):
                    if Counter(NUMBERS.findall(original)) != Counter(NUMBERS.findall(translated)):
                        reason = 'numbers_changed'
                        raise ValueError(reason)
                    if any(original.count(span) != translated.count(span) for span in set(PROTECTED.findall(original))):
                        reason = 'protected_content_changed'
                        raise ValueError(reason)
                return {**record,'status':'translated','title':translated_title,'body':translated_body}
            except ValueError:
                continue
        return {**record,'status':'failed','reason':reason}


def main():
    request = json.load(sys.stdin)
    engine = Engine(Path(request['models']))
    results = []
    for item in request['items']:
        try:
            results.append(engine.run(item))
        except Exception:
            results.append({'key':item['key'],'status':'failed','reason':'translation_failed'})
    print(json.dumps(results,ensure_ascii=False))


if __name__ == '__main__':
    main()
