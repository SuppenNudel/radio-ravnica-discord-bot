# Push deployment

The `.github/workflows/deploy.yml` workflow deploys each push to `main` to the production RR bot and admin web app.

One-time GitHub setup:

1. Add the deployment public key as a read-only deploy key on `SuppenNudel/radio-ravnica-discord-bot`.
2. Add the matching private key as the repository Actions secret `RR_DEPLOY_SSH_KEY`.

The SSH key on the server is restricted to the deployment command. Its forwarded agent is used only to fetch the private RR repository; the private key is not installed on the server. The deploy script preserves local working-tree changes, fast-forwards `main`, keeps the pushed `discord_bot_shared/__init__.py` version, syncs `admin_web/` into the web runtime, installs requirements, and restarts the two RR services.

The admin web app reads bot logs from systemd journals. Install the tracked service drop-in once so only the web service process receives journal-read access:

```sh
sudo install -D -o root -g root -m 0644 ops/rr-tournaments-web.service.d/10-journal-access.conf /etc/systemd/system/rr-tournaments-web.service.d/10-journal-access.conf
sudo systemctl daemon-reload
sudo systemctl restart rr-tournaments-web.service
```

Blacky runs from `/home/discord/blacky` and has a separate GitHub Actions deployment. The log view reads its `blacky-discord-bot.service` journal alongside the RR bot; deployments for the two bots remain separate.