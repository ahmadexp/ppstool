# ppstool

`ppstool` is a small Linux utility for inspecting and configuring PTP Hardware
Clock devices such as `/dev/ptp0`. It is useful for NIC PPS, external timestamp,
periodic output, pin configuration, PHC time reads, and offset measurements.

The project now includes:

- `ppstool`: the command line tool, with stricter argument validation.
- `ppstool-gui.py`: a curses terminal UI that runs the same CLI.

## Requirements

- Linux with PTP clock support.
- C compiler, `make`, libc development headers, and Linux UAPI headers.
- Terminal UI: Python 3 with curses support.

On Debian/Ubuntu-style systems:

```sh
sudo apt install build-essential linux-libc-dev python3
```

## Build

```sh
make
```

The default linker flags keep `-lrt` for older systems. On modern systems where
`librt` is not needed, this also works:

```sh
make LDLIBS=
```

Check the terminal UI syntax without building the C tool:

```sh
make ui
```

Build the terminal UI as a self-contained Python zipapp:

```sh
make zipapp
./ppstool-gui.pyz
```

By default, `make zipapp` embeds the compiled `ppstool` binary in the archive,
so the resulting `.pyz` only needs a compatible Linux system and Python 3 to
launch. To build a UI-only archive that expects `ppstool` beside the archive or
in `PATH`, run:

```sh
make ZIPAPP_EMBED_CLI=0 zipapp
```

## Install

Install the CLI:

```sh
sudo make install
```

Install both the CLI and terminal UI:

```sh
sudo make install-ui
```

The UI is installed from the generated zipapp as `ppstool-gui`.

The default prefix is `/usr/local`. Override it when needed:

```sh
sudo make PREFIX=/usr install-ui
```

## Terminal UI

From the source checkout:

```sh
./ppstool-gui.py
```

After `sudo make install-ui`:

```sh
ppstool-gui
```

The terminal UI defaults to an embedded `ppstool` binary when one exists in the
zipapp, then to a local `./ppstool` binary when one exists, otherwise to
`ppstool` from `PATH`. Some operations require elevated permissions; run the UI
with suitable privileges or set the command field to a privilege helper such as
`pkexec /path/to/ppstool`.

The UI uses a curses layout with section navigation, action selection, editable
settings, and a live output pane. Use arrow keys or `hjkl` to move, Enter to
run or edit, `s` for settings, `x` to stop a running command, `c` to clear
output, and `q` to quit.

## Common CLI Examples

Find the PTP device for a NIC:

```sh
ptp_dev=$(ethtool -T eth0 | awk '/PTP Hardware Clock:/ {print $4}')
device="/dev/ptp${ptp_dev}"
```

Query capabilities and current pin configuration:

```sh
sudo ./ppstool -d "$device" -c
sudo ./ppstool -d "$device" -l
```

Configure pin 0 for external timestamp input on channel 0:

```sh
sudo ./ppstool -d "$device" -i 0 -L 0,1
```

Read external timestamp events until interrupted:

```sh
sudo ./ppstool -d "$device" -i 0 -e -1
```

Configure pin 0 for periodic output on channel 0, then enable a 1 Hz output:

```sh
sudo ./ppstool -d "$device" -i 0 -L 0,2
sudo ./ppstool -d "$device" -i 0 -p 1000000000
```

Disable the periodic output:

```sh
sudo ./ppstool -d "$device" -i 0 -p 0
```
