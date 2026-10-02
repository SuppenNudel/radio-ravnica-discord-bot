# Push deployment

The `.github/workflows/deploy.yml` workflow deploys each push to `main` to the production RR bot and admin web app.

One-time GitHub setup:

1. Add the deployment public key as a read-only deploy key on `SuppenNudel/radio-ravnica-discord-bot`.
2. Add the matching private key as the repository Actions secret `RR_DEPLOY_SSH_KEY`.

The SSH key on the server is restricted to the deployment command. Its forwarded agent is used only to fetch the private RR repository; the private key is not installed on the server. The deploy script preserves local working-tree changes, fast-forwards `main`, keeps the pushed `discord_bot_shared/__init__.py` version, syncs `admin_web/` into the web runtime, installs requirements, and restarts the two RR services.

Blacky is deployed separately from its own repository. Its `/discord-tools/` login is the shared sign-in for the RR admin and tournament routes; RR validates the Blacky dashboard session locally without sharing signing secrets.