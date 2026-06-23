# Makefile to build and install ppstool.

PREFIX ?= /usr/local
BINDIR ?= $(PREFIX)/bin

CC ?= cc
CPPFLAGS ?=
CFLAGS ?= -Wall -Wextra -O2
LDLIBS ?= -lrt

TARGET := ppstool
GUI := ppstool-gui.py

.PHONY: all check clean gui install install-gui uninstall

all: $(TARGET)

$(TARGET): ppstool.c ptp_clock.h
	$(CC) $(CPPFLAGS) $(CFLAGS) $< $(LDLIBS) -o $@

gui:
	python3 -m py_compile $(GUI)

check: $(TARGET) gui

install: $(TARGET)
	install -d $(DESTDIR)$(BINDIR)
	install -m 0755 $(TARGET) $(DESTDIR)$(BINDIR)/$(TARGET)

install-gui: install gui
	install -d $(DESTDIR)$(BINDIR)
	install -m 0755 $(GUI) $(DESTDIR)$(BINDIR)/ppstool-gui

uninstall:
	rm -f $(DESTDIR)$(BINDIR)/$(TARGET)
	rm -f $(DESTDIR)$(BINDIR)/ppstool-gui

clean:
	rm -f $(TARGET)
	rm -rf __pycache__
