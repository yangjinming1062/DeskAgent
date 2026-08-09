# Repository Guidelines

DeskAgent is a customizable companion-type desktop partner: a cloud **Backend** (FastAPI + PostgreSQL + JWT) holding persona and assets, a native **Client** (Electron 42 + React 19 + Three.js) rendering the companion, and an isolated **Runner** (Python 3.13) executing local tools. This file is an index — it points to the authoritative docs rather than restating them.

The repo already maintains thorough, authoritative docs in Chinese. Read **[RULES.md](RULES.md)** before any change — it is the single source for collaboration, code, doc, commit, and platform conventions. This file only routes you to the right doc.

## Where to Look

| Topic | Read |
|-------|------|
| Project overview, architecture, module boundaries, invariants | [ARCHITECTURE.md](ARCHITECTURE.md) |
| Companion product design (visuals, animation, lifecycle, onboarding, interaction) | [DESIGN.md](DESIGN.md) |
| Cross-module protocol contracts (JSON-RPC methods, enums, events, security, credentials) | [PROTOCOL.md](PROTOCOL.md) |
| Code / doc / commit / testing conventions | [RULES.md](RULES.md) |
| How to run, build, test, release (quick start, commands) | [README.md](README.md) |
| Backend module structure & behavior | [backend/README.md](backend/README.md) |
| Client module structure & behavior | [client/README.md](client/README.md) |
| Runner module structure & behavior | [runner/README.md](runner/README.md) |
| Installer module & install protocol | [installer/README.md](installer/README.md) |
| 3D model & animation specs (bones / clips / morph / materials) | [docs/MODEL_SPEC.md](docs/MODEL_SPEC.md) |
| Tripo3D bone naming authority (`spec=tripo` / `spec=mixamo`) | [docs/tripo-spec.md](docs/tripo-spec.md) + [docs/mixamo-spec.md](docs/mixamo-spec.md) |
| Build / test / release scripts | [scripts/README.md](scripts/README.md) |

## Pointers for Quick Reference

- **Layout**: four modules — `backend/` (FastAPI, Docker/Linux), `client/` (Electron + React, Windows/macOS), `runner/` (Python 3.13 uv wheel, Windows/macOS), `installer/` (Tauri 2). Details and dependency directions are in each module's README.
- **Doc layers**: `ARCHITECTURE.md` = physical topology, module boundaries, cross-module invariants (no implementation details). `DESIGN.md` = product design intent for the companion layer (no code-path level details). `PROTOCOL.md` = cross-module contracts (JSON-RPC methods, enums, events, security, credentials) shared by Backend/Client/Runner. Module `README.md` files own implementation, file trees, config knobs, error codes.
- **Conventions**: commit format is defined by the [`.gitmessage`](.gitmessage) template — `git config commit.template .gitmessage` loads it into `git commit`. Python uses black + ruff (config in `.pre-commit-config.yaml`); Desktop uses ESLint + Prettier (`pnpm fix`). All rules live in [RULES.md](RULES.md), not duplicated here.
- **Doc language**: docs are in Chinese and describe the *current* state only — never "X was Y, now Z". Keep them in sync with code in the same commit.

## Before You Submit

1. Read [RULES.md](RULES.md) and the relevant module `README.md`.
2. Run the local gates for what you touched (`uvx pre-commit run -a`, `pnpm fix`, module tests).
3. For a release, run the full chain: `pwsh scripts/build_client.ps1` (see [scripts/README.md](scripts/README.md)).
4. Sync any affected `README.md` in the same commit.
