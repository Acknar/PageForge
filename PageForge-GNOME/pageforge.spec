Name:           pageforge
Version:        1.3.0
Release:        1%{?dist}
Summary:        Batch PDF & image workbench (GTK4/Libadwaita)

License:        AGPL-3.0-only
URL:            https://example.local/pageforge
Source0:        %{name}-%{version}.tar.gz
BuildArch:      noarch

Requires:       python3-gobject
Requires:       gtk4
Requires:       libadwaita
Requires:       python3-pillow
Requires:       python3-numpy
Requires:       python3-pymupdf
# Optional, installed on demand from within the app when a tool needs them:
#   OCR:               tesseract, tesseract-langpack-*, python3-pytesseract
#   Remove background: python3 rembg, onnxruntime
#   OCR (EasyOCR):     easyocr (pulls in PyTorch)

%description
PageForge is a GTK4/Libadwaita desktop app for batch PDF and image tasks:
divide, crop, auto-crop, rename, background removal, convert, split, extract
and OCR. Every tool is a small, self-contained Python script kept in a user
scripts folder, so tools can be edited, removed, or added freely. Tools that
need extra Python or system packages install them on demand from the app.

%prep
%autosetup

%install
mkdir -p %{buildroot}%{_datadir}/pageforge/tools
install -m 0644 pageforge.py %{buildroot}%{_datadir}/pageforge/pageforge.py
install -m 0644 tools/*.py %{buildroot}%{_datadir}/pageforge/tools/
install -m 0644 tools/PLUGINS.md %{buildroot}%{_datadir}/pageforge/tools/PLUGINS.md
mkdir -p %{buildroot}%{_datadir}/pageforge/icons
install -m 0644 icons/*.svg %{buildroot}%{_datadir}/pageforge/icons/

mkdir -p %{buildroot}%{_bindir}
cat > %{buildroot}%{_bindir}/pageforge <<EOF
#!/bin/sh
exec python3 %{_datadir}/pageforge/pageforge.py "\$@"
EOF
chmod 0755 %{buildroot}%{_bindir}/pageforge

mkdir -p %{buildroot}%{_datadir}/applications
install -m 0644 io.github.acknar.PageForge.desktop \
    %{buildroot}%{_datadir}/applications/io.github.acknar.PageForge.desktop
# The shipped .desktop carries a placeholder Exec for manual (source) installs.
# For the packaged app, point it at the installed launcher so the menu icon works.
sed -i -e '/^# Edit Exec/d' -e 's|^Exec=.*|Exec=pageforge|' \
    %{buildroot}%{_datadir}/applications/io.github.acknar.PageForge.desktop

mkdir -p %{buildroot}%{_datadir}/icons/hicolor/scalable/apps
install -m 0644 io.github.acknar.PageForge.svg \
    %{buildroot}%{_datadir}/icons/hicolor/scalable/apps/io.github.acknar.PageForge.svg

mkdir -p %{buildroot}%{_metainfodir}
install -m 0644 io.github.acknar.PageForge.metainfo.xml \
    %{buildroot}%{_metainfodir}/io.github.acknar.PageForge.metainfo.xml

%files
%{_datadir}/pageforge/
%{_bindir}/pageforge
%{_datadir}/applications/io.github.acknar.PageForge.desktop
%{_datadir}/icons/hicolor/scalable/apps/io.github.acknar.PageForge.svg
%{_metainfodir}/io.github.acknar.PageForge.metainfo.xml

%post
touch --no-create %{_datadir}/icons/hicolor &>/dev/null || :
gtk-update-icon-cache %{_datadir}/icons/hicolor &>/dev/null || :
update-desktop-database &>/dev/null || :

%postun
gtk-update-icon-cache %{_datadir}/icons/hicolor &>/dev/null || :
update-desktop-database &>/dev/null || :

%changelog
* Sat Oct 24 2026 PageForge <noreply@example.local> - 1.3.0-1
- Auto-crop border: new Straighten option detects and corrects tilted pages,
- with a before and after preview.
- Generated previews refresh when an option changes instead of showing an
- earlier result.
- App ID is now io.github.acknar.PageForge (desktop file, icon and metainfo
- renamed to match).
- Fuller AppStream metadata: tool list, supported formats, links, branding.
- Flatpak: signed update repository on GitHub Pages; settings are copied from
- the old io.local.PageForge install on first launch.

* Fri Oct 23 2026 PageForge <noreply@example.local> - 1.1.0-1
- Renumbered to match the PageForge 1.1.0 release (previously 1.8.4).
- Flatpak packaging (flatpak/io.local.PageForge.yml), with Tesseract bundled.
- Inside the Flatpak, system packages are not installed with pkexec/dnf; the
- dependency dialog explains what the sandbox already includes instead.
- Packages installed with pip --user import without restarting the app.
- AppStream metadata: io.local.PageForge.metainfo.xml.
- License corrected to AGPL-3.0-only, matching the project's LICENSE file.

* Wed Jul 22 2026 PageForge <noreply@example.local> - 1.7.4-1
- Split bars: real fix for "can't move the bars / no bars in at-pages". Each grid
- page carried its own click gesture even in split modes (where the grid isn't
- selectable), and it grabbed the pointer away from the split-bar drag. The
- per-page click is now only wired when the grid is selectable, so in split modes
- the bar drag/tap owns the pointer. Reverted the 1.7.3 capture-phase and
- live-refresh attempts (they fought the symptom and blocked movement).
- Debug: run with PAGEFORGE_DEBUG=1 from a terminal to trace split gestures.

* Wed Jul 22 2026 PageForge <noreply@example.local> - 1.7.3-1
- Split / Extract: the split bars are now actually draggable. The grid's
- split-drag gesture runs in the capture phase so it claims the pointer before
- each page's selection click; previously a child click won the sequence, so
- dragged bars snapped back and tapping a gap in "at pages" added nothing.
- "Split — at pages": click a gap to drop a split bar, click a bar to remove it,
- drag to move it (all working now).
- Computed splits (into N / every N) now re-space live *during* the drag, so the
- grabbed bar snaps to its real achievable gap as you move it instead of jumping
- to the nearest one only on release (fixes small drags landing off-target).

* Wed Jul 22 2026 PageForge <noreply@example.local> - 1.7.2-1
- Split / Extract: every operation now previews on the page grid. "Into N files"
- and "every N pages" draw their evenly-spaced cuts as draggable split bars —
- drag any bar to change N and all the bars re-space to match. "One file per
- page" is gone; use "every N pages" and drag down to 1 instead.
- Host: after a split-bar edit the grid re-derives the bars from the values the
- tool wrote, so an even split re-spaces every bar (not just the dragged one).

* Wed Jul 22 2026 PageForge <noreply@example.local> - 1.7.1-1
- Language: changing the interface language re-renders the tool list, options
- panel, preview and settings dialog in place; the static window chrome
- (section titles, file/output buttons) applies on the next launch (noted in
- the Language row subtitle).
- French: shorter "Add files/folder" buttons ("Ajout de fichiers" / "Ajout de
- dossier") and the Upscale tool is now "Augmenter la résolution".

* Tue Jul 21 2026 PageForge <noreply@example.local> - 1.7.0-1
- New: interface language setting (Settings → Language: English / Français) with a
- full French translation of the whole app — every window, button, label, dialog
- and error message, plus every tool's title, option labels and dropdown choices.
- i18n is a single English-keyed catalog in the host with a tr() lookup applied at
- render time; tools stay pure-Python English and the host translates when it draws
- them. Choice options keep their English value as the logical value and translate
- only their displayed text, so tool logic is unaffected by the UI language.
- OCR (zones): the "Column width ratios" field is gone from the panel — set
- asymmetric columns by dragging the column dividers in the preview (unchanged).

* Tue Jul 21 2026 PageForge <noreply@example.local> - 1.6.0-1
- OCR (zones): a "Mode" selector (Columns / Draw zones) now shows only the options
- that matter for the chosen mode, so the panel is no longer cluttered with both
- sets at once. Columns mode hides the zone rows; Draw zones hides the column,
- margin, book and gutter rows and turns on zone drawing on the canvas.
- Host: new generic "visible_when" option seam (rows show/hide by another option's
- value, mirroring enabled_when); hiding a regions row also disables canvas drawing.

* Tue Jul 21 2026 PageForge <noreply@example.local> - 1.5.0-1
- Divide into grid: new "Gutter between cells" trims whitespace from between the
- tiles (half off each cut); the preview outlines each kept tile.
- Merge into PDF and Organize pages are now one tool, "Organize / Merge"
- (accepts images + PDFs). The old separate "Merge into PDF" script is retired —
- delete tools/merge.py or use Settings → Restore built-in tools.
- Grid fix: deleted pages now stay deleted instead of reappearing at the end.
- Grid fix: toggling "Show individual pages" no longer interleaves the first page
- of each document; pages keep their natural per-file order.

* Thu Jul 09 2026 PageForge <noreply@example.local> - 1.1.0-1
- Fix the menu icon: the packaged .desktop now runs the installed `pageforge`
- launcher instead of a placeholder ~/Apps path (the cause of "menu spins, no
- window" while the terminal launch worked).
- Docs: document the core framework principle (the app is only a host; tools own
- their options, deps, preview styling and preview-button behaviour) plus Ubuntu
- dependency names and an install-from-source.sh for any distro.

* Wed Jul 08 2026 PageForge <noreply@example.local> - 1.0.0-6
- Previews are now script-owned: tools declare their own overlay colour/fill/dash,
- zone colour and hint, so the app needs no change for a tool to restyle its preview.
- New needs_preview_button() hook: EasyOCR auto-previews instantly when a PDF text
- layer is present, and only shows Generate preview when the neural engine must run.
- OCR (zones) draws zones and column guides in blue. Click "Page X / N" to jump to a
- page. Restore built-in tools also re-seeds PLUGINS.md. On launch, new tools that
- need dependencies prompt the user to install them from Settings.

* Wed Jul 08 2026 PageForge <noreply@example.local> - 1.0.0-1
- Initial package: 11 built-in tools, per-page OCR zones, on-demand deps.

* Wed Jul 08 2026 PageForge <noreply@example.local> - 1.0.0-2
- Fix deprecation warnings (ColorDialogButton, PreferencesDialog) and settings group removal.

* Wed Jul 08 2026 PageForge <noreply@example.local> - 1.0.0-3
- OCR: drag inside an existing zone moves it instead of drawing a new one.

* Wed Jul 08 2026 PageForge <noreply@example.local> - 1.0.0-4
- Corner-resize zones; strict zone read order; EasyOCR replaces Dynamic OCR;

* Wed Jul 08 2026 PageForge <noreply@example.local> - 1.0.0-5
- SHIP the fixed tools (defaults, OCR crash, EasyOCR preview); reorder tools in
- Settings; cached preview render; multi-line errors; rename-safe tool sync.
- crop link icons + layout fix; legible errors; OCR column crash fixed.
