# Migration structurelle locale — 17 novembre 2026

Référence et point de départ : `f0259d7b3325a71f42293c39fef6e85215425748`, identique à la tête distante examinée. Clone neuf, aucun changement préexistant. Branche de travail : `codex/conversation-memory-media`. Cette tranche modifie les chemins et les contrats de lancement, sans changer la logique de conversation. Aucun service n’a été remplacé ou redémarré.

## Inventaire

Les 89 fichiers Python racine de la référence sont tous couverts : 20 adaptateurs applicatifs et 27 adaptateurs de tests retirés après migration des imports/patches ; 42 outils déplacés. `main.py` est l’unique fichier Python racine à l’arrivée. `courswatch/` reste le paquet canonique. `courswatch.compat` conserve seulement la carte historique et son helper, sans wrapper racine ni utilisation interne de ces alias.

Les 16 outils qui exécutaient leur opération à l’import ont désormais un `main()` protégé. `inspect_sources` est aussi normalisé à ce point d’entrée ; son ancienne garde empêchait déjà l’exécution à l’import. Les 42 imports frais sont vérifiés sous transports/écritures interdits. Les anciens chemins exécutables externes devront être changés lors d’un mandat d’exploitation ; aucun alias Python ne répare un chemin supprimé sur disque.

## Adaptateurs applicatifs retirés

| Ancien fichier | Import canonique |
| --- | --- |
| `cours_watch.py` | `courswatch.runtime` |
| `course_bot.py` | `courswatch.discord_app.bot` |
| `bot_ui_data.py` | `courswatch.discord_app.ui_data` |
| `course_tasks.py` | `courswatch.discord_app.tasks` |
| `course_health.py` | `courswatch.discord_app.health` |
| `discord_presentation.py` | `courswatch.discord_app.presentation` |
| `haddock_emojis.py` | `courswatch.discord_app.emojis` |
| `course_reminders.py` | `courswatch.reminders` |
| `course_capture.py` | `courswatch.publishing.capture` |
| `course_obsidian.py` | `courswatch.publishing.obsidian` |
| `course_pdf_publish.py` | `courswatch.documents.publish` |
| `publish_course_vault.py` | `courswatch.publishing.vault` |
| `moodle_pdf_versions.py` | `courswatch.documents.versions` |
| `course_translation.py` | `courswatch.translation` |
| `translate_worker.py` | `courswatch.translation_worker` |
| `luna_brain.py` | `courswatch.conversation.brain` |
| `luna_chat.py` | `courswatch.conversation.chat` |
| `haddock_easter_egg.py` | `courswatch.conversation.easter_egg` |
| `haddock_voice.py` | `courswatch.conversation.voice` |
| `esn_watch.py` | `courswatch.collectors.esn` |

## Adaptateurs de tests retirés

| Ancien fichier | Module canonique unique du runner |
| --- | --- |
| `test_apply_course_seed_pc.py` | `tests.migration.test_apply_course_seed_pc` |
| `test_attachment_bindings.py` | `tests.integration.test_attachment_bindings` |
| `test_compatibility.py` | `tests.unit.test_compatibility` |
| `test_configure_course_sync.py` | `tests.migration.test_configure_course_sync` |
| `test_course_bot.py` | `tests.unit.test_course_bot` |
| `test_course_capture.py` | `tests.integration.test_course_capture` |
| `test_course_health.py` | `tests.unit.test_course_health` |
| `test_course_obsidian.py` | `tests.integration.test_course_obsidian` |
| `test_course_pdf_publish.py` | `tests.integration.test_course_pdf_publish` |
| `test_course_tasks.py` | `tests.unit.test_course_tasks` |
| `test_course_translation.py` | `tests.unit.test_course_translation` |
| `test_discord_presentation.py` | `tests.unit.test_discord_presentation` |
| `test_esn_watch.py` | `tests.unit.test_esn_watch` |
| `test_haddock_easter_egg.py` | `tests.unit.test_haddock_easter_egg` |
| `test_haddock_emojis.py` | `tests.unit.test_haddock_emojis` |
| `test_haddock_voice.py` | `tests.unit.test_haddock_voice` |
| `test_luna_brain.py` | `tests.unit.test_luna_brain` |
| `test_luna_chat.py` | `tests.unit.test_luna_chat` |
| `test_moodle_pdf_collection.py` | `tests.integration.test_moodle_pdf_collection` |
| `test_moodle_pdf_versions.py` | `tests.integration.test_moodle_pdf_versions` |
| `test_operations.py` | `tests.operations.test_operations` |
| `test_pipeline.py` | `tests.unit.test_pipeline` |
| `test_prepare_course_seed.py` | `tests.migration.test_prepare_course_seed` |
| `test_refresh.py` | `tests.unit.test_refresh` |
| `test_reminders.py` | `tests.unit.test_reminders` |
| `test_schema.py` | `tests.unit.test_schema` |
| `test_state.py` | `tests.unit.test_state` |

## Outils déplacés

Chaque outil se lance depuis la racine par `python -m ops.<dossier>.<module>` ; ne pas traiter les probes comme des tests automatiques.

| Ancien fichier | Nouveau fichier |
| --- | --- |
| `configure-login-proxy.py` | `ops/configuration/configure_login_proxy.py` |
| `configure_bot.py` | `ops/configuration/configure_bot.py` |
| `configure_course_sync.py` | `ops/configuration/configure_course_sync.py` |
| `configure_discord.py` | `ops/configuration/configure_discord.py` |
| `configure_esn.py` | `ops/configuration/configure_esn.py` |
| `configure_role.py` | `ops/configuration/configure_role.py` |
| `configure_webhook.py` | `ops/configuration/configure_webhook.py` |
| `prepare_bot_env.py` | `ops/configuration/prepare_bot_env.py` |
| `audit_luna_usage.py` | `ops/diagnostics/audit_luna_usage.py` |
| `audit_moodle_pdf_versions.py` | `ops/diagnostics/audit_moodle_pdf_versions.py` |
| `check_bot_token_format.py` | `ops/diagnostics/check_bot_token_format.py` |
| `diagnose_easter_egg.py` | `ops/diagnostics/diagnose_easter_egg.py` |
| `diagnose_luna_failure.py` | `ops/diagnostics/diagnose_luna_failure.py` |
| `inspect_course_sync_live.py` | `ops/diagnostics/inspect_course_sync_live.py` |
| `inspect_esn_setup.py` | `ops/diagnostics/inspect_esn_setup.py` |
| `inspect_items.py` | `ops/diagnostics/inspect_items.py` |
| `inspect_sos_teams_shared.py` | `ops/diagnostics/inspect_sos_teams_shared.py` |
| `inspect_sources.py` | `ops/diagnostics/inspect_sources.py` |
| `inspect_sync_config.py` | `ops/diagnostics/inspect_sync_config.py` |
| `preview_reminders.py` | `ops/diagnostics/preview_reminders.py` |
| `preview_status.py` | `ops/diagnostics/preview_status.py` |
| `browser_smoke.py` | `ops/probes/browser_smoke.py` |
| `probe_linktree_browser.py` | `ops/probes/probe_linktree_browser.py` |
| `probe_moodle_browser.py` | `ops/probes/probe_moodle_browser.py` |
| `probe_resource.py` | `ops/probes/probe_resource.py` |
| `verify_bot_live.py` | `ops/probes/verify_bot_live.py` |
| `verify_course_master_vps.py` | `ops/probes/verify_course_master_vps.py` |
| `verify_esn_watch.py` | `ops/probes/verify_esn_watch.py` |
| `verify_reminder_production.py` | `ops/probes/verify_reminder_production.py` |
| `verify_translation_runtime.py` | `ops/probes/verify_translation_runtime.py` |
| `send_voice_demo.py` | `ops/probes/send_voice_demo.py` |
| `backfill_translations.py` | `ops/maintenance/backfill_translations.py` |
| `install_reminders.py` | `ops/maintenance/install_reminders.py` |
| `install_translation_models.py` | `ops/maintenance/install_translation_models.py` |
| `recover_moodle_session.py` | `ops/maintenance/recover_moodle_session.py` |
| `apply_course_seed_pc.py` | `ops/migration/apply_course_seed_pc.py` |
| `prepare_course_seed.py` | `ops/migration/prepare_course_seed.py` |
| `discord_emoji_admin.py` | `ops/assets/discord_emoji_admin.py` |
| `prepare_haddock_audio.py` | `ops/assets/prepare_haddock_audio.py` |
| `export_bot_source.py` | `ops/export/export_bot_source.py` |
| `export_course_evidence.py` | `ops/export/export_course_evidence.py` |
| `prepare_bot_repository.py` | `ops/export/prepare_bot_repository.py` |

## Appelants, données et rollback

- Le runner charge les modules `tests.unit`, `tests.integration`, `tests.migration`, `tests.operations` sans doublon. Son filtrage d’environnement, son garde réseau et le `sitecustomize` des sous-processus sont conservés.
- Collecte : `scan_command` utilise un tuple `collector_argv`, par défaut `-m courswatch`. `COURS_WATCH_COLLECTOR`, s’il est explicitement fourni, reste un chemin unique ; jamais une commande shell découpée implicitement.
- Traduction : `worker_argv` vaut par défaut `['-m', 'courswatch.translation_worker']`, avec répertoire de travail explicite à la racine du paquet. Le venv spécialisé, JSON stdin et environnement filtré sont conservés. Le champ privé historique `worker` demeure un chemin explicite pris en charge ; il devra être migré s’il référence le wrapper supprimé.
- Les unités de référence et `login-desktop.sh` utilisent les nouvelles entrées. L’unité Obsidian ajoute `WorkingDirectory=/opt/cours-watch`, indispensable à `-m` sans paquet installé. Utilisateurs, groupes, limites et chemins d’écriture restent identiques.
- Les racines dérivées de `__file__` des outils seed et premier import sont recalculées et testées. Le premier import conserve ses empreintes figées et sa fonction de validation ; il ne devient pas un installateur courant. Pour un seed hors arborescence historique `Cours/Outils/Capitaine-Haddock`, fournir `--root` explicitement.
- Les lots récursifs couvrent `ops/`. Leur allow-list n’accepte que `main.py` comme Python racine. Les nouveaux formats médias sont autorisés uniquement sous `tests/fixtures/media` ; les caches, données privées et documents sources demeurent exclus. L’export historique reprend cette zone de fixtures sans élargir le cache privé.
- Aucun schéma ou fichier de données n’est migré par cette tranche. Rollback : restaurer ensemble l’ancien code, ses wrappers et ses unités sauvegardés. Ne pas restaurer ni réinitialiser `state.db`, `luna-usage.db`, les reçus ou les PDF pour annuler un déplacement de code. Les nouvelles données de mémoire auront leur procédure distincte.

## Vérification

Commandes exécutées avec `work/venv/bin/python`, Python 3.12, dépendances du projet installées : compilation de `courswatch`, `ops`, `tests`, `tools` ; suite isolée complète du projet ; profils ciblés `operations` et `regression` après les renforcements ; `git diff --check`. Le runner empêche accès production et réseau externe. Aucun appel payant, probe VPS, test de salon, publication, migration de production ou mesure de performance réelle.

La preuve finale de la tranche est donnée dans le compte rendu de session : les contrôles structurels portent sur les imports, argv, chemins, archives et tests simulés. Ils ne prouvent ni l’état effectif des unités du VPS, ni la résolution des trois venv réels, ni les capacités réelles de modèle.
