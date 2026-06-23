# ppstool

`ppstool` is a small Linux utility for inspecting and configuring PTP Hardware
Clock devices such as `/dev/ptp0`. It is useful for NIC PPS, external timestamp,
periodic output, pin configuration, PHC time reads, and offset measurements.

The project now includes:

- `ppstool`: the command line tool, with stricter argument validation.
- `ppstool-gui.py`: an optional Tkinter desktop frontend that runs the same CLI.

## Requirements

- Linux with PTP clock support.
- C compiler, `make`, libc development headers, and Linux UAPI headers.
- Optional GUI: Python 3 with Tkinter, usually packaged as `python3-tk`.

On Debian/Ubuntu-style systems:

```sh
sudo apt install build-essential linux-libc-dev python3-tk
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

Check the optional GUI syntax without building the C tool:

```sh
make gui
```

## Install

Install the CLI:

```sh
sudo make install
```

Install both the CLI and GUI:

```sh
sudo make install-gui
```

The default prefix is `/usr/local`. Override it when needed:

```sh
sudo make PREFIX=/usr install-gui
```

## GUI

From the source checkout:

```sh
./ppstool-gui.py
```

After `sudo make install-gui`:

```sh
ppstool-gui
```

The GUI command field defaults to a local `./ppstool` binary when one exists,
otherwise it uses `ppstool` from `PATH`. Some operations require elevated
permissions; run the GUI with suitable privileges or set the command field to a
privilege helper such as `pkexec /path/to/ppstool`.

The GUI uses a menu-driven layout for common workflows: device status, PPS
input, PPS output, pin functions, clock/time adjustment, and advanced raw
arguments. The common setup screen includes quick actions for PPS input capture,
1 Hz PPS output, system PPS, and periodic output disable.

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
