PREFIX ?= /usr
DESTDIR ?=

PKG_DIR = pulseforge
QML_DIR = $(PKG_DIR)/qml
QML_CMP = $(QML_DIR)/components
BACKEND_DIR = $(PKG_DIR)/backend
ICON = Icon.png
DESKTOP = pulseforge.desktop

# Detect Python site-packages
PYTHON_SITE = $(shell python3 -c "import site; print(site.getsitepackages()[0])")

.PHONY: install uninstall

install:
	# Install Python package
	install -d $(DESTDIR)$(PYTHON_SITE)/pulseforge
	install -d $(DESTDIR)$(PYTHON_SITE)/pulseforge/backend
	install -d $(DESTDIR)$(PYTHON_SITE)/pulseforge/qml
	install -d $(DESTDIR)$(PYTHON_SITE)/pulseforge/qml/components
	install -m644 $(PKG_DIR)/__init__.py $(DESTDIR)$(PYTHON_SITE)/pulseforge/
	install -m644 $(PKG_DIR)/main.py $(DESTDIR)$(PYTHON_SITE)/pulseforge/
	install -m644 $(PKG_DIR)/Icon.png $(DESTDIR)$(PYTHON_SITE)/pulseforge/
	install -m644 $(BACKEND_DIR)/*.py $(DESTDIR)$(PYTHON_SITE)/pulseforge/backend/
	install -m644 $(QML_DIR)/*.qml $(DESTDIR)$(PYTHON_SITE)/pulseforge/qml/
	install -m644 $(QML_DIR)/*.png $(DESTDIR)$(PYTHON_SITE)/pulseforge/qml/
	install -m644 $(QML_CMP)/*.qml $(DESTDIR)$(PYTHON_SITE)/pulseforge/qml/components/
	install -m644 $(QML_DIR)/__init__.py $(DESTDIR)$(PYTHON_SITE)/pulseforge/qml/
	install -m644 $(QML_CMP)/__init__.py $(DESTDIR)$(PYTHON_SITE)/pulseforge/qml/components/
	install -m644 $(PKG_DIR)/launch.py $(DESTDIR)$(PYTHON_SITE)/pulseforge/

	# Install executable wrapper
	install -d $(DESTDIR)$(PREFIX)/bin
	@echo '#!/bin/bash' > $(DESTDIR)$(PREFIX)/bin/pulseforge
	@echo 'exec python3 -u -m pulseforge.main "$$@"' >> $(DESTDIR)$(PREFIX)/bin/pulseforge
	@chmod +x $(DESTDIR)$(PREFIX)/bin/pulseforge

	# Install icon
	install -d $(DESTDIR)$(PREFIX)/share/icons/hicolor/256x256/apps
	install -d $(DESTDIR)$(PREFIX)/share/icons/hicolor/512x512/apps
	install -m644 $(ICON) $(DESTDIR)$(PREFIX)/share/icons/hicolor/256x256/apps/pulseforge.png
	@python3 -c "from PIL import Image; img=Image.open('$(ICON)'); img=img.resize((512,512),Image.LANCZOS); img.save('$(DESTDIR)$(PREFIX)/share/icons/hicolor/512x512/apps/pulseforge.png')"
	gtk-update-icon-cache $(DESTDIR)$(PREFIX)/share/icons/hicolor/ 2>/dev/null || true

	# Install .desktop file
	install -d $(DESTDIR)$(PREFIX)/share/applications
	@echo '[Desktop Entry]' > $(DESTDIR)$(PREFIX)/share/applications/$(DESKTOP)
	@echo 'Name=PulseForge' >> $(DESTDIR)$(PREFIX)/share/applications/$(DESKTOP)
	@echo 'Comment=Virtual audio mixer with DSP mic processing for PipeWire' >> $(DESTDIR)$(PREFIX)/share/applications/$(DESKTOP)
	@echo 'Exec=pulseforge' >> $(DESTDIR)$(PREFIX)/share/applications/$(DESKTOP)
	@echo 'Icon=pulseforge' >> $(DESTDIR)$(PREFIX)/share/applications/$(DESKTOP)
	@echo 'Terminal=false' >> $(DESTDIR)$(PREFIX)/share/applications/$(DESKTOP)
	@echo 'Type=Application' >> $(DESTDIR)$(PREFIX)/share/applications/$(DESKTOP)
	@echo 'Categories=AudioVideo;Audio;Mixer;' >> $(DESTDIR)$(PREFIX)/share/applications/$(DESKTOP)
	@echo 'Keywords=audio;mixer;pipewire;sound;microphone;' >> $(DESTDIR)$(PREFIX)/share/applications/$(DESKTOP)
	@echo 'StartupNotify=true' >> $(DESTDIR)$(PREFIX)/share/applications/$(DESKTOP)
	@echo 'StartupWMClass=pulseforge' >> $(DESTDIR)$(PREFIX)/share/applications/$(DESKTOP)
	update-desktop-database $(DESTDIR)$(PREFIX)/share/applications/ 2>/dev/null || true

	@echo "PulseForge installed to $(DESTDIR)$(PREFIX)"

uninstall:
	rm -rf $(DESTDIR)$(PYTHON_SITE)/pulseforge
	rm -f $(DESTDIR)$(PREFIX)/bin/pulseforge
	rm -f $(DESTDIR)$(PREFIX)/share/icons/hicolor/*/apps/pulseforge.png
	rm -f $(DESTDIR)$(PREFIX)/share/applications/$(DESKTOP)
	gtk-update-icon-cache $(DESTDIR)$(PREFIX)/share/icons/hicolor/ 2>/dev/null || true
	update-desktop-database $(DESTDIR)$(PREFIX)/share/applications/ 2>/dev/null || true
	@echo "PulseForge uninstalled"