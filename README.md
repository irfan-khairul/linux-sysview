# sysview

A lightweight web-based system monitor for a Linux machine. It runs on the
Linux box and you view it from a browser on any machine on the same network.

No build step, no `node_modules`, no framework. Python 3 plus one dependency on
the server; vanilla JavaScript in the browser.

> **Status:** in use and working. The test suite runs on macOS and Linux, but
> it mocks the system calls, so see the note under Development about what that
> does and does not prove.

## Features

- **System Resource** — CPU total and per-core, memory and swap, disk usage per
  mount, live network throughput, uptime and load average, plus CPU
  temperature, fan speed, and battery where the hardware reports them. Recent
  history is drawn as sparklines.
- **System Processes** — sortable, filterable process table. Sorting and
  filtering happen on the server across every process, so sorting by memory
  finds the real top consumers rather than re-ordering a slice. Read-only:
  there is no kill or signal.
- **Docker Processes** — containers grouped by their Compose project, each
  group collapsible with a running count, summed CPU, and Start / Stop /
  Restart for the whole project. Individual containers have the same three
  actions.
- **File Explorer** — click a folder to open it, with an editable path field
  and a back button. View only: files are never opened, downloaded, or
  modified.

## Requirements

- Linux, Python 3.8 or newer
- Docker CLI 17.06 or newer, only if you want the Docker view

Everything else is installed in step 2 below.

## Installation

Run these on the Linux machine you want to monitor.

**1. Make sure Python can create virtual environments.** Debian and Ubuntu
split this into a separate package, and leaving it out is the most common
first-run failure:

```sh
sudo apt install python3-venv          # Debian/Ubuntu only
```

**2. Clone the repository and set it up:**

```sh
git clone https://github.com/irfan-khairul/linux-sysview.git
cd linux-sysview
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

The virtualenv matters: recent Debian and Ubuntu refuse a plain
`pip install` outside one (`error: externally-managed-environment`).

**3. Start it:**

```sh
./run.sh
```

You should see `sysview 0.1.0 serving on http://0.0.0.0:8080`.

**4. Open it** from any machine on the network:

```
http://<linux-box-ip>:8080
```

Find the IP with `hostname -I` on the Linux box. Press Ctrl+C to stop.

### If something goes wrong

| Symptom | Cause and fix |
|---|---|
| `ensurepip is not available` | Step 1 was skipped. Install `python3-venv`, delete the half-made `.venv`, and redo step 2. |
| `No such file or directory: 'requirements.txt'` | You are not in the repo directory, or the clone did not complete. |
| `No interpreter at .../.venv/bin/python` | Step 2 was skipped or failed. |
| `Cannot bind 0.0.0.0:8080 — Address already in use` | Something else has the port. Use `./run.sh --port 9000`. |
| Page loads but looks stale after an update | The browser cached the old JavaScript. Hard-refresh with Ctrl+Shift+R. |
| Docker tab says "Docker not available" | The daemon is not running, or your user is not in the `docker` group. |

## Usage

```sh
./run.sh                    # foreground on port 8080; Ctrl+C stops it
./run.sh start              # background, survives closing the terminal
./run.sh stop               # stop the background instance
./run.sh status             # is it running, and on which port
./run.sh restart            # stop, then start
```

Flags pass straight through, and `PORT` works too:

```sh
./run.sh --port 9000
./run.sh start --port 9000
PORT=9000 ./run.sh start
```

| Flag | Default | Description |
|---|---|---|
| `--host` | `0.0.0.0` | Address to bind. Use `127.0.0.1` for localhost only |
| `--port` | `8080` | Port to listen on |
| `--interval` | `2` | Initial refresh interval in seconds; changeable in the UI afterwards |

`run.sh` uses this repo's `.venv`, so you never have to activate it. Set
`SYSVIEW_PYTHON` to use a different interpreter. `start` detaches with `nohup`,
writes its PID to `.sysview.pid` and output to `sysview.log` (both
git-ignored), and reports a failure rather than dying quietly.

To run it from anywhere without the `./`, link it onto your `PATH`:

```sh
ln -s "$PWD/run.sh" ~/.local/bin/sysview
sysview start
```

You can also invoke the module directly, bypassing `run.sh`:

```sh
.venv/bin/python -m sysview --port 9000
```

## Security

**There is no authentication.** This is built for a trusted private network.
It binds to all interfaces by default, so anyone who can reach the port can
read your process list and browse your filesystem, and can start, stop, and
restart your Docker containers.

Do not expose it to the internet. If your network is not fully trusted, bind to
localhost and reach it over an SSH tunnel:

```sh
# on the Linux box
./run.sh --host 127.0.0.1

# from your own machine
ssh -L 8080:127.0.0.1:8080 user@linux-box
```

Then browse to `http://localhost:8080`.

The process view and file explorer see exactly what the user running the server
sees, so run it as an unprivileged user. Process signals and every kind of file
write are deliberately absent: the only state-changing operations in the whole
application are the three Docker container actions.

## A note on Docker

`docker compose down` **removes** containers rather than stopping them, so a
project torn down that way disappears from this view entirely — there is
nothing left to list. Use `docker compose stop` if you want a project to stay
visible and restartable from the browser, and `docker compose up -d` to bring
back one that was taken down.

Grouping comes from the `com.docker.compose.project` label on each container,
not from the compose file, so it keeps working even if you delete the project
directory.

## Development

```sh
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest
```

105 tests. Collectors in `sysview/` return plain dictionaries and never touch
HTTP, so they can be tested without starting a server. The suite mocks `psutil`
and `subprocess` throughout, which means it passes on macOS as well as Linux —
but that also means passing tests do not prove the numbers are right on real
hardware. See [docs/verifying-on-linux.md](docs/verifying-on-linux.md) for the
manual checks that do.

The [design spec](docs/superpowers/specs/2026-08-19-linux-system-resource-design.md)
records the original decisions and the reasoning behind them.

## License

MIT
