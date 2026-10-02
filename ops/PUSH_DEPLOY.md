# Push deployment

The `.github/workflows/deploy.yml` workflow deploys each push to `main` to the production RR Discord bot.

One-time GitHub setup:

1. Add the deployment public key as a read-only deploy key on `SuppenNudel/radio-ravnica-discord-bot`.
2. Add the matching private key as the repository Actions secret `RR_DEPLOY_SSH_KEY`.

The SSH key on the server is restricted to the bot deployment command. Its forwarded agent is used only to fetch the private RR repository; the private key is not installed on the server. The deploy script preserves local working-tree changes, fast-forwards `main`, keeps the pushed `discord_bot_shared/__init__.py` version, installs bot requirements, and restarts only `rr-discord-bot.service`.

The RR tournament administration web app and Discord tools live in the separate `SuppenNudel/discord-tools` repository. Its installer owns the `rr-tournaments-web.service`; RR bot deployments do not sync or restart the web app.