# Makefile to build and install ppstool.

PREFIX ?= /usr/local
BINDIR ?= $(PREFIX)/bin

CC ?= cc
CPPFLAGS ?=
CFLAGS ?= -Wall -Wextra -O2
LDLIBS ?= -lrt

TARGET := ppstool
GUI := ppstool-gui.py
GUI_APP := ppstool-gui.pyz
ZIPAPP_DIR := .ppstool-gui-zipapp
ZIPAPP_EMBED_CLI ?= 1

.PHONY: all check clean gui install install-gui uninstall zipapp

all: $(TARGET)

$(TARGET): ppstool.c ptp_clock.h
	$(CC) $(CPPFLAGS) $(CFLAGS) $< $(LDLIBS) -o $@

gui:
	python3 -m py_compile $(GUI)

zipapp: $(GUI_APP)

ifeq ($(ZIPAPP_EMBED_CLI),1)
$(GUI_APP): $(TARGET)
endif

$(GUI_APP): $(GUI)
	rm -rf $(ZIPAPP_DIR)
	mkdir -p $(ZIPAPP_DIR)
	cp $(GUI) $(ZIPAPP_DIR)/__main__.py
	if [ "$(ZIPAPP_EMBED_CLI)" = "1" ]; then cp $(TARGET) $(ZIPAPP_DIR)/$(TARGET); fi
	python3 -m zipapp $(ZIPAPP_DIR) --python "/usr/bin/env python3" --output $@
	chmod 0755 $@
	rm -rf $(ZIPAPP_DIR)

check: $(TARGET) gui zipapp

install: $(TARGET)
	install -d $(DESTDIR)$(BINDIR)
	install -m 0755 $(TARGET) $(DESTDIR)$(BINDIR)/$(TARGET)

install-gui: install $(GUI_APP)
	install -d $(DESTDIR)$(BINDIR)
	install -m 0755 $(GUI_APP) $(DESTDIR)$(BINDIR)/ppstool-gui

uninstall:
	rm -f $(DESTDIR)$(BINDIR)/$(TARGET)
	rm -f $(DESTDIR)$(BINDIR)/ppstool-gui

clean:
	rm -f $(TARGET) $(GUI_APP)
	rm -rf __pycache__ $(ZIPAPP_DIR)
