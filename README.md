# Linux System Resource Viewer

A lightweight web-based system monitor and process viewer for a remote Linux
machine. Runs on the Linux box, viewed from a browser on any machine on the
same network.

No build step, no `node_modules`, no framework. Python 3 plus one dependency
on the server; vanilla JavaScript in the browser.

> **Status:** working. Test suite passes on macOS; Linux verification pending.
> See [the design spec](docs/superpowers/specs/2026-08-19-linux-system-resource-design.md).

## Features

- **System Resource** — CPU (total and per-core), memory and swap, disk usage
  per mount, and live network throughput per interface.
- **System Processes** — sortable, filterable process table. Read-only.
- **Docker Processes** — container list with per-container CPU and memory, plus
  start, stop, and restart.
- **File Explorer** — browse directories by double-clicking, with a breadcrumb
  and a back button. View only; files are not opened, downloaded, or modified.

## Requirements

- Linux, Python 3.8 or newer
- [`psutil`](https://pypi.org/project/psutil/)
- Docker CLI on the host, if you want the Docker view

## Install

```sh
git clone https://github.com/irfan-khairul/linux-sysview.git
cd linux-sysview
pip install -r requirements.txt
```

## Usage

Run on the Linux machine you want to monitor:

```sh
python -m sysview                      # binds 0.0.0.0:8080
python -m sysview --port 9000          # different port
python -m sysview --host 127.0.0.1     # localhost only (see Security)
```

Then open `http://<linux-box-ip>:8080` in your browser.

| Flag | Default | Description |
|---|---|---|
| `--host` | `0.0.0.0` | Address to bind |
| `--port` | `8080` | Port to listen on |
| `--interval` | `2` | Initial UI refresh interval in seconds, applied when the page first loads; the user can still change it afterwards via the Refresh dropdown |

The process table and file explorer show whatever the user running the server
can see. Running as an unprivileged user is recommended and sufficient for
normal use.

## Security

**This tool ships with no authentication.** It is built for a trusted private
network. By default it binds to all interfaces, so anyone who can reach the port
can view your system's processes and files, and can start, stop, or restart your
Docker containers.

Do not expose it to the internet. If your network is not fully trusted, bind to
localhost and reach it through an SSH tunnel:

```sh
# on the Linux box
python -m sysview --host 127.0.0.1

# from your own machine
ssh -L 8080:127.0.0.1:8080 user@linux-box
```

Then browse to `http://localhost:8080`.

Process signals (kill, renice) and all file writes are deliberately absent: the
only state-changing operations are the three Docker container actions.

## Development

```sh
pip install -r requirements.txt pytest
pytest
```

Collectors in `sysview/` return plain dictionaries and do not touch HTTP, so
they can be tested directly without starting a server.

## License

MIT
