# Existing compiled-image entry points

These files preserve the live pilot's Python entry points as reconciled on 2026-11-11. They are deployment compatibility snapshots, not replacements for the normal source entry points in `server/` and `executor/`.

| Snapshot | Container destination |
| --- | --- |
| `server-main.py` | `/usr/lib/python3/dist-packages/server/__main__.py` |
| `executor-celery.py` | `/usr/lib/python3/dist-packages/executor/celery.py` |

Base image ID: `sha256:2cf585fcc628a5d1b1212a1cc4bc62fcfbe81002a542c6ba31e1860ad4d2b36f` (the running `firerpa/pigeon:1.0` image). The image includes compiled upstream modules; matching BK extension source does not mean those binaries were rebuilt from this repository.

The extra package path loads persistent BK extensions from `/user/phone-pilot/bk-server`. Supervisor already uses `ops/run_bk_server.py` and `ops/run_bk_worker.py`; preserve these launchers and the mounted single-phone configuration. Importing the worker guard creates its receipt table in the live environment, so do not execute these snapshots as a syntax check. Compile or parse them without execution.

No entry point needs changing or restarting for this reconciliation. Before any future restoration, compare the pinned image and live hashes, preserve the current files and validate the intended service only. Do not overwrite compiled modules or restart the whole supervisor group to make source directories look identical.
