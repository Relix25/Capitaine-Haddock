"""Check live bot identity, registered commands and a real interactive message without logging secrets."""
import json
from pathlib import Path
import subprocess
import urllib.request
from cours_watch import load_discord_secrets
from luna_brain import PERSONA_VERSION
from courswatch.conversation.models import effective_settings

def main():
    config = json.loads(Path('/etc/cours-watch/config.json').read_text())['discord']
    token = load_discord_secrets()['DISCORD_BOT_TOKEN']


    def get(path):
        request = urllib.request.Request('https://discord.com/api/v10' + path,
            headers={'Authorization':'Bot ' + token,'User-Agent':'UPBCourseWatch/1.0'})
        try:
            with urllib.request.urlopen(request,timeout=20) as response:
                return json.load(response)
        except Exception:
            raise SystemExit('Discord verification failed; no credential logged') from None


    commands = get('/applications/' + config['application_id'] + '/guilds/' + config['guild_id'] + '/commands')
    names = {command['name'] for command in commands}
    if not {'cours','actualiser','status','a-faire','settings'} <= names or names & {'modele','modèle'}:
        raise SystemExit('Expected live commands are missing')
    settings_command = next(command for command in commands if command['name']=='settings')
    if int(settings_command.get('default_member_permissions') or 0) != 8 or settings_command.get('options'):
        raise SystemExit('Settings command permissions or options are unexpected')
    message = get('/channels/' + config['channel_id'] + '/messages/1570230953404665994')
    buttons = [button for row in message.get('components',[]) for button in row.get('components',[])]
    if not message.get('embeds') or not any(button.get('custom_id') == 'cours-watch:details' for button in buttons):
        raise SystemExit('Expected live embed/button missing')
    subprocess.run(['systemctl','is-enabled','--quiet','cours-watch-bot.service'],check=True)
    subprocess.run(['systemctl','is-active','--quiet','cours-watch-bot.service'],check=True)
    status = json.loads(Path('/var/lib/cours-watch/reports/bot-status.json').read_text())
    main_pid = int(subprocess.check_output(['systemctl','show','-p','MainPID','--value','cours-watch-bot.service'],text=True))
    if not status.get('ready') or status.get('process_id') != main_pid:
        raise SystemExit('The active client is not ready')
    egg_enabled = config.get('easter_egg',{}).get('enabled',False)
    if egg_enabled:
        app = get('/applications/@me')
        flags = int(app.get('flags_new',app.get('flags',0)))
        if not flags & ((1 << 18) | (1 << 19)) or not status.get('message_content_intent') or not status.get('easter_egg_enabled'):
            raise SystemExit('Easter egg or approved Message Content access is not ready')
        voice_probability = config.get('easter_egg',{}).get('voice_probability',0)
        if voice_probability and (not status.get('voice_enabled') or status.get('voice_samples') != 5
                                  or status.get('voice_probability') != voice_probability):
            raise SystemExit('Configured voice samples are not ready')
    proof = {'application_id':config['application_id'],'delivery_mode':config['mode'],
             'live_commands':sorted(names),'interactive_message_id':message['id'],
             'embeds':len(message['embeds']),'buttons':[button.get('label') for button in buttons],
             'bot_enabled':True,'bot_ready':True,'role_ping_allowed':status.get('role_ping_allowed'),
             'last_private_detail':status.get('last_private_detail'),'easter_egg_enabled':egg_enabled,
             'message_content_intent':status.get('message_content_intent'),
             'easter_egg_cooldown_seconds':config.get('easter_egg',{}).get('cooldown_seconds'),
             'last_easter_egg':status.get('last_easter_egg'),'voice_enabled':status.get('voice_enabled'),
             'voice_samples':status.get('voice_samples'),'voice_probability':status.get('voice_probability')}
    if config.get('luna',{}).get('enabled'):
        settings = effective_settings('/var/lib/cours-watch',config['luna'])
        provider = settings.get('provider','openai')
        default_model = 'deepseek/deepseek-v4.1-flash' if provider == 'commandcode' else 'gpt-6-luna'
        luna = json.loads(Path('/var/lib/cours-watch/reports/luna-status.json').read_text())
        if (not status.get('luna_enabled') or status.get('luna_model') != settings.get('model',default_model)
                or status.get('luna_provider','openai') != provider or luna.get('provider','openai') != provider
                or status.get('luna_persona') != PERSONA_VERSION or luna.get('persona_version') != PERSONA_VERSION
                or luna.get('daily_token_limit') != settings.get('daily_token_limit',1_000_000)
                or luna.get('require_zdr',True) != settings.get('require_zdr',True)
                or status.get('luna_debug_enabled') != settings.get('debug',True)
                or luna.get('debug_enabled') != settings.get('debug',True)
                or luna.get('status') not in ('ready','daily_limit')):
            raise SystemExit('Luna conversation mode or daily token ceiling is not ready')
        proof.update(luna_enabled=True,luna_provider=provider,luna_model=status['luna_model'],luna_require_zdr=luna['require_zdr'],
                     luna_debug_enabled=status['luna_debug_enabled'],luna_daily_token_limit=luna['daily_token_limit'],
                     luna_status=luna['status'],luna_used_tokens=luna['used_tokens'],luna_persona=status['luna_persona'])
    output = Path('/tmp/cours-watch-bot-proof.json')
    output.write_text(json.dumps(proof,ensure_ascii=False,indent=2))
    output.chmod(0o644)
    print(json.dumps(proof,ensure_ascii=False,indent=2))


if __name__ == "__main__":
    main()
