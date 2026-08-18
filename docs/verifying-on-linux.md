# Verifying sysview on the Dell Linux box

The test suite passes on macOS, and its integration tests do exercise the real
server over a real socket — but every assertion checks shape rather than values,
and the collectors' `psutil` calls are mocked in the unit tests. So nothing so
far proves the numbers are right against real `/proc`, real mounts, or a real
Docker daemon. These steps are what does.

## 1. Get the code onto the box

All implementation is on the `feat/implement-sysview` branch. `main` holds only
docs, so pulling `main` would give you no code.

```sh
# first time
git clone https://github.com/irfan-khairul/linux-sysview.git
cd linux-sysview

# every time
git fetch origin
git checkout feat/implement-sysview
git pull

python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## 2. Run the suite on Linux

```sh
.venv/bin/python -m pytest -q
```

Expect all tests to pass. A failure here means a macOS-only assumption slipped
through — most likely a `psutil.virtual_memory()` field, which is why the code
reads only the five fields common to both platforms.

## 3. Start it

```sh
.venv/bin/python -m sysview
# -> sysview 0.1.0 serving on http://0.0.0.0:8080
```

Then open `http://<dell-ip>:8080` from your Mac.

## 4. Check each view against the real system

Run each command on the box and compare against the browser.

**System Resource**
```sh
free -h                  # compare memory used/total
df -h                    # compare the disk rows
nproc                    # compare the per-core bar count
uptime                   # compare uptime and load average
```
Per-core bars should move on their own. To check network rates, copy a large
file and watch the interface rates rise:
```sh
dd if=/dev/zero of=/tmp/blob bs=1M count=500 && rm /tmp/blob
```
Note: CPU reads 0.0% on the very first page load and populates a second later.
That is by design — percentages are deltas and need two samples.

**System Processes**
```sh
ps aux | wc -l           # compare against the "of N total" count
```
Click each column header to sort. Type in the filter box to narrow by name or
PID. Confirm there is no kill or signal button anywhere — this view is
deliberately read-only.

**Docker Processes**
```sh
docker ps -a             # compare the container list
```
Then, on a throwaway container, click Stop and confirm the row updates, then
Start. If Docker is not installed or the daemon is down, the view should say
"Docker not available" rather than showing an error.

**File Explorer**
- Double-click a folder to descend; the breadcrumb should update.
- Back should ascend, and be disabled at `/`.
- Clicking a file should do nothing at all.
- As a non-root user, open `/root` — expect "Permission denied" inline, with the
  view still usable.

## 5. Report back

Anything that misbehaves gets fixed with a normal test-first cycle. If it all
works, nothing needs committing for this step.
