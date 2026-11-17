"""Send one authorized native voice sample and verify Discord's actual receipt."""


def main():
    import json
    from pathlib import Path

    from courswatch.runtime import load_discord_secrets
    from courswatch.conversation.voice import load_samples, send_voice_message, VOICE_FLAG

    config = json.loads(Path('/etc/cours-watch/config.json').read_text())['discord']
    samples = load_samples()
    if len(samples) != 5:
        raise SystemExit('Five validated local samples are required')
    proof_path = Path('/etc/cours-watch/voice-demo.json')
    if proof_path.exists():
        print(proof_path.read_text())
        raise SystemExit(0)
    token = load_discord_secrets()['DISCORD_BOT_TOKEN']
    reply = send_voice_message(config['channel_id'],token,samples[0],
        nonce='haddock-voice-demo-v1',reference_id='1570251511731191880')
    proof = {'message_id':reply['id'],'channel_id':reply['channel_id'],
             'native_voice':bool(reply['flags'] & VOICE_FLAG),'duration_secs':reply['attachments'][0].get('duration_secs'),
             'waveform_present':bool(reply['attachments'][0].get('waveform')),
             'sample':samples[0].id,'role_mentions':reply.get('mention_roles',[])}
    proof_path.write_text(json.dumps(proof,ensure_ascii=False,indent=2))
    proof_path.chmod(0o644)
    print(json.dumps(proof,ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
