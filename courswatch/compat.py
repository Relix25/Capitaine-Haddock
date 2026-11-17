"""Keep historical imports and script paths bound to the canonical implementation."""
import asyncio
import importlib
import inspect
import sys


ALIASES = {
    'cours_watch': 'courswatch.runtime',
    'course_bot': 'courswatch.discord_app.bot',
    'bot_ui_data': 'courswatch.discord_app.ui_data',
    'course_tasks': 'courswatch.discord_app.tasks',
    'course_health': 'courswatch.discord_app.health',
    'discord_presentation': 'courswatch.discord_app.presentation',
    'haddock_emojis': 'courswatch.discord_app.emojis',
    'course_reminders': 'courswatch.reminders',
    'course_capture': 'courswatch.publishing.capture',
    'course_obsidian': 'courswatch.publishing.obsidian',
    'course_pdf_publish': 'courswatch.documents.publish',
    'publish_course_vault': 'courswatch.publishing.vault',
    'moodle_pdf_versions': 'courswatch.documents.versions',
    'course_translation': 'courswatch.translation',
    'translate_worker': 'courswatch.translation_worker',
    'luna_brain': 'courswatch.conversation.brain',
    'luna_chat': 'courswatch.conversation.chat',
    'haddock_easter_egg': 'courswatch.conversation.easter_egg',
    'haddock_voice': 'courswatch.conversation.voice',
    'esn_watch': 'courswatch.collectors.esn',
}


def expose(name, target):
    implementation = importlib.import_module(target)
    if name != '__main__':
        # A shared object preserves monkeypatches and module-level configuration.
        sys.modules[name] = implementation
        return
    entry = getattr(implementation, 'cli', getattr(implementation, 'main', None))
    if entry is not None:
        result = entry()
        if inspect.isawaitable(result):
            result = asyncio.run(result)
        raise SystemExit(result)
