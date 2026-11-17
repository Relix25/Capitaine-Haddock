"""Historical entry point; implementation lives in courswatch.discord_app.tasks."""
from courswatch.compat import expose

expose(__name__, 'courswatch.discord_app.tasks')
