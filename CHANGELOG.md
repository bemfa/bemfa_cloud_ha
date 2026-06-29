# Changelog

## 0.1.3

- Fix entities being synced even when they were not selected.
- Improve TCP keepalive with 30-second heartbeats and periodic topic resubscription.
- Reload the integration when sync options change so runtime subscriptions match saved settings.
- Support multiple Bemfa Cloud hubs/accounts and show the account suffix in hub names.
- Improve climate control for `mode`, `t`, and `v`, including full state feedback after control commands.
- Clarify README wording, FAQ, multi-account usage, and local sync removal behavior.
