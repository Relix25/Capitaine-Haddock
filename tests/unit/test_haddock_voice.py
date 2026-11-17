import base64
from email.parser import BytesParser
from email.policy import default
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

from haddock_voice import (load_samples,choose_sample,voice_payload,multipart_body,
                           send_voice_message,VoiceMessageError,VOICE_FLAG)


class VoiceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.blob = b'OggS' + b'\0' * 20 + b'OpusHead' + b'x' * 200
        (self.root / 'sample.ogg').write_bytes(self.blob)
        self.row = {'id':'sample','label':'Test','file':'sample.ogg','duration_secs':2.5,
                    'waveform':base64.b64encode(bytes([0,20,80,20])).decode(),
                    'sha256':hashlib.sha256(self.blob).hexdigest()}
        self.write_manifest(self.row)

    def tearDown(self):
        self.directory.cleanup()

    def write_manifest(self,row):
        (self.root / 'manifest.json').write_text(json.dumps({'version':1,'samples':[row]}))

    def test_local_assets_validate_hash_path_duration_and_waveform(self):
        self.assertEqual(len(load_samples(self.root)),1)
        for change in ({'file':'../sample.ogg'},{'sha256':'0' * 64},{'duration_secs':float('nan')},
                       {'duration_secs':60},{'waveform':base64.b64encode(bytes(257)).decode()}):
            with self.subTest(change=change):
                self.write_manifest({**self.row,**change})
                self.assertEqual(load_samples(self.root),[])

    def test_native_multipart_contains_one_audio_and_no_text_or_pings(self):
        sample = load_samples(self.root)[0]
        payload = voice_payload(sample,'hv1570251511731191880','1570251511731191880','1570209088929800282')
        self.assertEqual(payload['flags'],VOICE_FLAG)
        self.assertNotIn('content',payload)
        self.assertNotIn('embeds',payload)
        self.assertEqual(payload['allowed_mentions']['parse'],[])
        self.assertFalse(payload['allowed_mentions']['replied_user'])
        self.assertTrue(payload['enforce_nonce'])
        body,content_type = multipart_body(sample,payload,boundary='test-boundary')
        message = BytesParser(policy=default).parsebytes(('Content-Type: ' + content_type + '\r\nMIME-Version: 1.0\r\n\r\n').encode() + body)
        parts = list(message.iter_parts())
        self.assertEqual(len(parts),2)
        self.assertEqual(json.loads(parts[0].get_content()),payload)
        self.assertEqual(parts[1].get_content_type(),'audio/ogg')
        self.assertEqual(parts[1].get_payload(decode=True),self.blob)

    def test_sample_choice_avoids_immediate_repeat(self):
        sample = load_samples(self.root)[0]
        from dataclasses import replace
        other = replace(sample,id='other')
        self.assertEqual(choose_sample([sample,other],'sample',lambda values:values[0]),other)
        self.assertEqual(choose_sample([sample],'sample'),sample)

    def test_api_must_confirm_a_native_voice_message(self):
        sample = load_samples(self.root)[0]
        response = io.BytesIO(json.dumps({'id':'123','flags':0,'attachments':[{}]}).encode())
        with patch('haddock_voice.urllib.request.urlopen',return_value=response):
            with self.assertRaises(VoiceMessageError):
                send_voice_message('1570209088929800282','fake-secret',sample,'test')

    def test_api_error_does_not_expose_bot_token(self):
        sample = load_samples(self.root)[0]
        error = urllib.error.HTTPError('https://discord.com/api/v10/channels/1/messages',403,'Forbidden',{},io.BytesIO(b'{}'))
        with patch('haddock_voice.urllib.request.urlopen',side_effect=error):
            with self.assertRaises(VoiceMessageError) as caught:
                send_voice_message('1570209088929800282','fake-private-token',sample,'test')
        self.assertIn('403',str(caught.exception))
        self.assertNotIn('fake-private-token',str(caught.exception))
