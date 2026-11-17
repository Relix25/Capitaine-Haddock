"""Owner-bound administrative modal for persistent chatbot preferences."""
import discord

from courswatch.conversation.models import MODEL_CHOICES, MODEL_LABELS
from courswatch.discord_app.responses import send_private


async def authorize_settings(bot, interaction, owner_id=None):
    if not await bot.authorize(interaction):
        return False
    if (not getattr(getattr(interaction.user,'guild_permissions',None),'administrator',False)
            or (owner_id is not None and interaction.user.id != owner_id)):
        await send_private(interaction, 'Réglages réservés à l’administrateur qui a ouvert /settings.')
        return False
    if not bot.luna or bot.luna.brain.provider != 'commandcode':
        await send_private(interaction, 'Le chatbot CommandCode est désactivé.')
        return False
    return True


class SettingsModal(discord.ui.Modal):
    def __init__(self, bot, owner_id):
        super().__init__(title='Réglages de Haddock', timeout=300)
        self.bot, self.owner_id = bot, owner_id
        configured_zdr = getattr(bot.luna, 'model_defaults', {}).get('require_zdr') is True
        self.model = discord.ui.Select(custom_id='haddock:settings:model',min_values=1,max_values=1,
            options=[discord.SelectOption(label=label,value=key,
                description='ZDR exigé' if configured_zdr or MODEL_CHOICES[key]['require_zdr'] else 'ZDR non exigé',
                default=bot.luna.brain.model == MODEL_CHOICES[key]['model'])
                for key,label in MODEL_LABELS.items()])
        self.debug = discord.ui.Select(custom_id='haddock:settings:debug',min_values=1,max_values=1,
            options=[discord.SelectOption(label='Activé',value='on',default=bot.luna.debug_enabled,
                        description='Afficher TTFT, durée, débit et tokens'),
                     discord.SelectOption(label='Désactivé',value='off',default=not bot.luna.debug_enabled,
                        description='Masquer les statistiques des réponses')])
        self.add_item(discord.ui.Label(text='Modèle du chatbot',component=self.model))
        self.add_item(discord.ui.Label(text='Mode debug',component=self.debug,
            description='Les statistiques concernent uniquement les réponses du chatbot.'))

    async def on_submit(self, interaction):
        # Recheck access on submission: roles/permissions can change while open.
        if not await authorize_settings(self.bot, interaction, self.owner_id):
            return
        model, debug = self.model.values, self.debug.values
        if (len(model) != 1 or model[0] not in MODEL_CHOICES
                or len(debug) != 1 or debug[0] not in ('on','off')):
            await send_private(interaction, 'Réglages invalides. Rouvre /settings pour réessayer.')
            return
        await interaction.response.defer(ephemeral=True,thinking=True)
        try:
            await self.bot.luna.apply_settings(model[0], debug[0] == 'on')
        except (OSError,ValueError):
            await send_private(interaction, 'Enregistrement impossible. Les réglages actifs sont conservés.')
            return
        brain = self.bot.luna.brain
        try:
            self.bot.write_status(luna_model=brain.model,luna_provider=brain.provider,
                                  luna_debug_enabled=self.bot.luna.debug_enabled)
        except OSError:
            pass
        privacy = 'ZDR exigé.' if brain.require_zdr else 'ZDR non exigé : pas de garantie de non-conservation par le fournisseur.'
        await send_private(interaction, '**Réglages enregistrés**\nModèle : '+MODEL_LABELS[model[0]]+
            '\nDebug : '+('activé' if self.bot.luna.debug_enabled else 'désactivé')+'\n'+privacy)

    async def on_error(self, interaction, error):
        # Never use Modal's default exception logger with interaction data.
        try:
            self.bot.write_status(last_settings_error={'error_type':type(error).__name__})
        except OSError:
            pass
        try:
            await send_private(interaction, 'Les réglages sont indisponibles. Rouvre /settings pour réessayer.')
        except discord.HTTPException:
            pass
