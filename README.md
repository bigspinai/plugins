# Plugins

This repository hosts the public collection of Bigspin Claude Code plugins.

Each plugin lives under `plugins/<name>/` with a `.claude-plugin/plugin.json` manifest and the usual companion surfaces (`agents/`, `commands/`, `skills/`, `README.md`, etc.). The repo-root `.claude-plugin/marketplace.json` registers all plugins under the `bigspinai` marketplace.

## Available plugins

- [`plugins/bigspin`](plugins/bigspin) — `/persona` slash command. Renders a personal Claude Code practice report from your local session history, positioned against a measured baseline of 4,846 real sessions. Runs entirely on your machine; no data leaves your laptop.

## Install

```
/plugin marketplace add bigspinai/plugins
/plugin install bigspin@bigspinai
```

## Contributing

This repo is a synced mirror — the source of truth lives in a private monorepo at `bigspinai/bigspin-app` under `public-repos/plugins/`. Open issues for bug reports; PRs against this mirror will be closed.

## License

MIT. See individual plugin directories for per-plugin license files.
