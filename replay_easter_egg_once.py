"""One-shot live reply to the user's identified missed test, using normal authorization."""
import argparse
import json

from course_bot import CourseBot, configuration, DATA
from course_health import read_json
from cours_watch import load_discord_secrets


class ReplayBot(CourseBot):
    def __init__(self,token,message_id):
        super().__init__(token)
        self.message_id = message_id
        self.replayed = False
        self.success = False

    async def on_ready(self):
        if self.replayed:
            return
        self.replayed = True
        try:
            await super().on_ready()
            cfg = configuration()['discord']
            guild = self.get_guild(int(cfg['guild_id']))
            channel = guild.get_channel(int(cfg['channel_id']))
            message = await channel.fetch_message(self.message_id)
            if message.author.bot or message.webhook_id:
                raise RuntimeError('Human test message required')
            # A fetched message may hold a User instead of a Member without member caching.
            message.author = await guild.fetch_member(message.author.id)
            previous = read_json(DATA / 'reports/bot-status.json').get('last_easter_egg')
            await self.on_message(message)
            record = read_json(DATA / 'reports/bot-status.json').get('last_easter_egg')
            if not record or record == previous:
                raise RuntimeError('No reply was confirmed')
            reply = await channel.fetch_message(int(record['reply_id']))
            if not reply.reference or reply.reference.message_id != self.message_id or reply.mention_everyone or reply.role_mentions:
                raise RuntimeError('Unexpected reply reference or mention')
            self.success = True
            print(json.dumps({'real_user_test_replied':True,'reply_id':str(reply.id),
                'new_ping':False,'sent_utc':record['sent_utc']},ensure_ascii=False),flush=True)
        finally:
            await self.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('message_id',type=int)
    args = parser.parse_args()
    token = load_discord_secrets()['DISCORD_BOT_TOKEN']
    client = ReplayBot(token,args.message_id)
    client.run(token,log_handler=None)
    if not client.success:
        raise SystemExit('Live test replay did not succeed')


if __name__ == '__main__':
    main()
