#!/usr/bin/env python3
"""
PageForge (Windows edition) — a batch PDF/image workbench.

This is the Windows-native host, re-implemented on PySide6/Qt so it uses the
native Windows 11 theme (title bar, controls, accent colour, light/dark) instead
of translating GNOME's Adwaita look. It is a standalone Windows fork: the
tool contract is byte-for-byte identical to the GTK build, so every tools/*.py
runs unchanged.

Every tool is just a Python script in your scripts folder. The built-in tools
are seeded there on first run and are ordinary scripts you can edit, delete, or
replace. Drop in new scripts to add tools; see PLUGINS.md for the contract.

Runtime deps:  pip install PySide6 pymupdf pillow numpy   (+ per-tool extras on demand)
"""

import io
import os
import re
import sys
import json
import inspect
import hashlib
import subprocess
import importlib.util
import threading
import tempfile
import traceback
from pathlib import Path

from PySide6.QtCore import (Qt, QTimer, QSize, QRect, QRectF, QPoint, QPointF,
                            Signal, QObject, QMimeData, QThread)
from PySide6.QtGui import (QPainter, QPixmap, QImage, QColor, QPen, QBrush,
                           QCursor, QFont, QIcon, QAction, QDrag, QPalette,
                           QGuiApplication, QFontMetrics, QTextCursor)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QToolButton, QCheckBox, QRadioButton, QButtonGroup,
    QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit, QFrame, QScrollArea,
    QStackedWidget, QSizePolicy, QFileDialog, QColorDialog, QDialog,
    QDialogButtonBox, QProgressBar, QPlainTextEdit, QMessageBox, QLayout,
    QStyle, QSplitter)

import fitz   # PyMuPDF, used for source rendering in the preview
from PIL import Image
import numpy as np

_DEBUG = bool(os.environ.get("PAGEFORGE_DEBUG"))


def _dbg(msg):
    if _DEBUG:
        print("[pageforge] " + msg, file=sys.stderr, flush=True)


APP_ID = "io.local.PageForge"
APP_VERSION = "1.7.2"          # Windows edition (parity with GTK 1.7.x + split/extract fix)
PREVIEW_DPI = 110
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
RED, GREEN = "#c01c28", "#26a269"


# --------------------------------------------------------------------------- #
# Platform paths (Windows). Config lives in %APPDATA%\PageForge; the default
# scripts folder lives in %LOCALAPPDATA%\PageForge\tools. Falls back to the
# home directory if the env vars are missing (e.g. a stripped environment).
# --------------------------------------------------------------------------- #
def _appdata():
    base = os.environ.get("APPDATA")
    return Path(base) if base else (Path.home() / "AppData" / "Roaming")


def _localappdata():
    base = os.environ.get("LOCALAPPDATA")
    return Path(base) if base else (Path.home() / "AppData" / "Local")


CONFIG_DIR = _appdata() / "PageForge"
CONFIG_FILE = CONFIG_DIR / "config.json"
DEFAULT_TOOLS_DIR = _localappdata() / "PageForge" / "tools"


# The starting tools ship as real files in a `tools/` folder next to this
# script (or inside the PyInstaller bundle). They are copied into the user's
# scripts folder on first run and kept in sync thereafter; they are ordinary,
# user-editable scripts once copied.
def _app_dir():
    """Directory that holds bundled resources (tools/, icons/). Under a frozen
    PyInstaller build this is sys._MEIPASS; otherwise the script's folder."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent


APP_DIR = _app_dir()
SHIPPED_TOOLS = APP_DIR / "tools"
ICON_DIR = APP_DIR / "icons"


def bundled_sources():
    out = {}
    if SHIPPED_TOOLS.is_dir():
        for f in sorted(SHIPPED_TOOLS.glob("*.py")):
            if f.name.startswith("_"):
                continue
            try:
                out[f.name] = f.read_text(encoding="utf-8")
            except Exception:
                pass
    return out


# Non-tool files shipped alongside the tools that belong in the scripts folder.
BUNDLED_DOCS = ("PLUGINS.md",)


def bundled_docs():
    out = {}
    for name in BUNDLED_DOCS:
        src = SHIPPED_TOOLS / name
        if src.is_file():
            try:
                out[name] = src.read_text(encoding="utf-8")
            except Exception:
                pass
    return out


# --------------------------------------------------------------------------- #
# Helpers (pure — shared verbatim with the GTK build where possible)
# --------------------------------------------------------------------------- #
def is_pdf(p):
    return Path(p).suffix.lower() == ".pdf"


def is_image(p):
    return Path(p).suffix.lower() in IMAGE_EXTS


def kind_of(p):
    return "pdf" if is_pdf(p) else ("image" if is_image(p) else "other")


def reading_order(boxes):
    """Zone indices in reading order: strictly top-to-bottom, then left-to-right."""
    return sorted(range(len(boxes)), key=lambda i: (boxes[i][1], boxes[i][0]))


def _esc(s):
    """Escape a string for safe use inside Qt rich-text (HTML) labels."""
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


def _make_gear_icon(color, size=20):
    """A themed cogwheel icon (a filled disc with teeth and a punched centre),
    painted in `color` so it reads on both light and dark title bars."""
    import math
    from PySide6.QtGui import QPainterPath  # noqa: F401
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    c = QColor(color)
    cx = cy = size / 2.0
    r_in = size * 0.30
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(c))
    p.drawEllipse(QPointF(cx, cy), r_in, r_in)
    tw, th = size * 0.16, size * 0.20
    for i in range(8):
        p.save()
        p.translate(cx, cy)
        p.rotate(i * 45.0)
        p.drawRoundedRect(QRectF(-tw / 2, -(r_in + th * 0.55), tw, th),
                          size * 0.05, size * 0.05)
        p.restore()
    # punch the centre hole
    p.setCompositionMode(QPainter.CompositionMode_Clear)
    p.setBrush(QBrush(QColor(0, 0, 0)))
    p.drawEllipse(QPointF(cx, cy), size * 0.13, size * 0.13)
    p.end()
    return QIcon(pm)


# Framework preview-colour defaults. A tool may override any of these from its
# own script (see PLUGINS.md → Previews).
OVERLAY_COLOR = RED       # cheap overlays (grid / rect / boxes)
DETECT_COLOR = "#1c70d1"  # heavy detected regions (preview_regions)
ZONE_COLOR = RED          # user-drawn zones (regions option)


def parse_rgb(value, fallback):
    """Parse '#rrggbb' (or any Qt-parsable colour) to an (r, g, b) 0-1 tuple.
    `fallback` may be a hex string or an (r, g, b) tuple."""
    if isinstance(fallback, str):
        c = QColor(fallback)
        fallback = (c.redF(), c.greenF(), c.blueF())
    if not value:
        return fallback
    try:
        c = QColor(value)
        if c.isValid():
            return (c.redF(), c.greenF(), c.blueF())
    except Exception:
        pass
    return fallback


def parse_dash(value):
    """Normalise a `dash` style to a Qt dash pattern list (in pen-width units),
    or None for a solid line. Accepts True (default dashes), False/None (solid),
    or an explicit list of numbers."""
    if value is True:
        return [6, 4]
    if isinstance(value, (list, tuple)) and value:
        return [float(v) for v in value]
    return None


def find_spec_ok(mod):
    try:
        return importlib.util.find_spec(mod) is not None
    except Exception:
        return False


def pil_to_qimage(img):
    """Convert a PIL image to a QImage (RGBA8888), keeping a copy of the bytes
    so the buffer outlives the numpy temporary."""
    img = img.convert("RGBA")
    data = img.tobytes("raw", "RGBA")
    qimg = QImage(data, img.width, img.height, QImage.Format_RGBA8888)
    return qimg.copy()


def checkerboard(w, h, sq=12):
    a = np.full((h, w, 3), 255, dtype=np.uint8)
    yy, xx = np.mgrid[0:h, 0:w]
    a[((xx // sq + yy // sq) % 2).astype(bool)] = 204
    return Image.fromarray(a, "RGB")


def composite_on_checker(img):
    img = img.convert("RGBA")
    board = checkerboard(img.width, img.height).convert("RGBA")
    board.alpha_composite(img)
    return board


def load_config():
    cfg = {"scripts_dir": None, "disabled": {}, "seeded": False}
    try:
        if CONFIG_FILE.exists():
            cfg.update(json.loads(CONFIG_FILE.read_text(encoding="utf-8")))
    except Exception:
        traceback.print_exc()
    return cfg


def save_config(cfg):
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    except Exception:
        traceback.print_exc()


# --------------------------------------------------------------------------- #
# i18n: a single English-keyed catalog. Identical mechanism to the GTK build —
# every user-visible string (host UI and tool titles/labels/choices/hints) is
# looked up here at render time; a missing key falls back to the English text.
# Choice options keep their English value as the logical value and only their
# *display* is translated.
# --------------------------------------------------------------------------- #
LANG = "en"
TRANSLATIONS = {"fr": {'Batch PDF & image workbench': 'Atelier PDF & images par lots', 'Settings': 'Paramètres', 'Choose scripts folder': 'Choisir le dossier de scripts', 'Add files': 'Ajout de fichiers', 'Add folder': 'Ajout de dossier', 'Clear': 'Effacer', '…or drop files here': '…ou déposez des fichiers ici', 'No files loaded': 'Aucun fichier chargé', 'No tools available.\nOpen Settings to add or enable tools.': 'Aucun outil disponible.\nOuvrez les Paramètres pour ajouter ou activer des outils.', 'No options for this tool.': 'Aucune option pour cet outil.', 'Link these two values': 'Lier ces deux valeurs', 'Clear page': 'Effacer la page', "Remove this page's zones": 'Supprimer les zones de cette page', 'Copy to all': 'Copier sur toutes', "Use this page's zones on every page": 'Appliquer les zones de cette page à toutes les pages', 'Choose': 'Choisir', 'none': 'aucun', 'Choose file': 'Choisir un fichier', 'Choose output folder': 'Choisir le dossier de sortie', "Default: an 'output' folder beside your files": 'Par défaut : un dossier « output » à côté de vos fichiers', 'Name': 'Nom', '[Original file name]': "[Nom du fichier d'origine]", "[Original file name] keeps each file's name (including what the Rename tool produced). Type a base name to replace it. Numbering is added on top.": "[Nom du fichier d'origine] conserve le nom de chaque fichier (y compris ce que l'outil Renommer a produit). Saisissez un nom de base pour le remplacer. La numérotation s'ajoute par-dessus.", 'Add numbering': 'Ajouter une numérotation', 'Style': 'Style', 'as prefix': 'en préfixe', 'as suffix': 'en suffixe', 'Overwrite originals': 'Écraser les originaux', 'Process files': 'Traiter les fichiers', 'Load files to see a preview': 'Chargez des fichiers pour voir un aperçu', 'Generate preview': "Générer l'aperçu", 'Working…': 'En cours…', 'Previous page': 'Page précédente', 'Next page': 'Page suivante', 'Click to jump to a page': 'Cliquer pour aller à une page', 'Go to page': 'Aller à la page', 'Go': 'Aller', 'Processing': 'Traitement', 'Starting…': 'Démarrage…', 'Cancel': 'Annuler', 'Load results as new files': 'Charger les résultats comme nouveaux fichiers', 'Close': 'Fermer', 'Error:': 'Erreur :', 'failed': 'échec', 'done': 'terminé', 'Cancelled after {n} file(s).': 'Annulé après {n} fichier(s).', 'Wrote {n} file(s).': '{n} fichier(s) écrit(s).', 'Scripts folder': 'Dossier de scripts', 'All tools are scripts in this folder.': 'Tous les outils sont des scripts dans ce dossier.', 'Location': 'Emplacement', 'not set': 'non défini', 'Open': 'Ouvrir', 'Change': 'Changer', 'Maintenance': 'Maintenance', 'Restore built-in tools': 'Restaurer les outils intégrés', 'Reload': 'Recharger', 'Tools': 'Outils', 'Reorder with ↑ ↓; enable, disable, or delete any tool.': 'Réordonner avec ↑ ↓ ; activer, désactiver ou supprimer un outil.', 'No tools found': 'Aucun outil trouvé', 'Add scripts to the folder, then Reload.': 'Ajoutez des scripts au dossier, puis Rechargez.', 'needs: ': 'nécessite : ', 'needs:': 'nécessite :', 'needs': 'nécessite', 'extra components': 'des composants supplémentaires', 'Install deps': 'Installer les dépendances', "Delete this tool's script": 'Supprimer le script de cet outil', 'About': 'À propos', 'Version': 'Version', 'Install': 'Installer', 'Installing…': 'Installation…', 'Dependencies': 'Dépendances', 'System packages will use pkexec (password prompt).\n': 'Les paquets système utiliseront pkexec (demande de mot de passe).\n', 'Language': 'Langue', 'Restart to fully apply.': 'Redémarrez pour appliquer entièrement.', 'New tools need setup': 'De nouveaux outils requièrent une configuration', "These tools were found but aren't enabled yet because they need extra components installed:": "Ces outils ont été trouvés mais ne sont pas encore activés car ils nécessitent l'installation de composants supplémentaires :", 'Open Settings to install their dependencies and enable them.': 'Ouvrez les Paramètres pour installer leurs dépendances et les activer.', 'Later': 'Plus tard', 'Open Settings': 'Ouvrir les Paramètres', '1  Files': '1  Fichiers', '2  Tools': '2  Outils', '3  Options': '3  Options', '4  Output': '4  Sortie', 'image': 'image', 'pdf': 'PDF', 'Zones are per-page · drag to add · right-click to delete · pages with no zones are skipped': 'Zones par page · glisser pour ajouter · clic droit pour supprimer · les pages sans zone sont ignorées', 'Some pages or images are too small for the margins you set. Lower the crop/margin values and try again.': 'Certaines pages ou images sont trop petites pour les marges définies. Réduisez les valeurs de rognage/marge et réessayez.', "This tool hit an internal error (a bug in the tool script). If it's a built-in, try Settings → Restore built-in tools. Detail: ": "Cet outil a rencontré une erreur interne (un bug dans le script). S'il est intégré, essayez Paramètres → Restaurer les outils intégrés. Détail : ", "Tesseract isn't installed. Open Settings, turn on an OCR tool, and let it install its dependencies (you'll be asked for your password).": "Tesseract n'est pas installé. Ouvrez les Paramètres, activez un outil OCR et laissez-le installer ses dépendances (votre mot de passe sera demandé).", "EasyOCR isn't ready. Enable OCR (EasyOCR) in Settings to install it — the first run also downloads models and needs an internet connection.": "EasyOCR n'est pas prêt. Activez OCR (EasyOCR) dans les Paramètres pour l'installer — le premier lancement télécharge aussi des modèles et nécessite une connexion internet.", 'A required library is missing for this tool. Enable it in Settings to install its dependencies. Detail: ': 'Une bibliothèque requise manque pour cet outil. Activez-le dans les Paramètres pour installer ses dépendances. Détail : ', 'Permission denied writing the output. Pick a different output folder.': "Permission refusée pour l'écriture. Choisissez un autre dossier de sortie.", "A file couldn't be found — it may have been moved or deleted.": 'Un fichier est introuvable — il a peut-être été déplacé ou supprimé.', 'Ran out of memory. Try a lower DPI, or process fewer files at once.': 'Mémoire insuffisante. Essayez un DPI plus bas ou traitez moins de fichiers à la fois.', "The tool couldn't finish. Detail: ": "L'outil n'a pas pu terminer. Détail : ", 'Auto-crop border': 'Rogner automatiquement la bordure', 'Convert (image ↔ PDF)': 'Convertir (image ↔ PDF)', 'Crop margins': 'Rogner les marges', 'Divide into grid': 'Diviser en grille', 'OCR (EasyOCR)': 'OCR (EasyOCR)', 'OCR (zones)': 'OCR (zones)', 'Organize / Merge': 'Organiser / Fusionner', 'Remove background': "Supprimer l'arrière-plan", 'Rename': 'Renommer', 'Split / Extract': 'Diviser / Extraire', 'Upscale': 'Augmenter la résolution', 'Border colour': 'Couleur de la bordure', 'Tolerance': 'Tolérance', 'Padding': 'Marge intérieure', 'Mode': 'Mode', 'Images → single PDF': 'Images → un seul PDF', 'Images → one PDF each': 'Images → un PDF chacune', 'PDF → images': 'PDF → images', 'Image format': "Format d'image", 'Render DPI': 'DPI de rendu', 'Top': 'Haut', 'Bottom': 'Bas', 'Left': 'Gauche', 'Right': 'Droite', 'Columns': 'Colonnes', 'Rows': 'Lignes', 'Rotate result': 'Pivoter le résultat', 'None': 'Aucune', '90° clockwise': '90° horaire', '90° counter-clockwise': '90° antihoraire', '180°': '180°', 'Gutter between cells': 'Espacement entre les cellules', 'Crop top': 'Rogner en haut', 'Crop bottom': 'Rogner en bas', 'Crop left': 'Rogner à gauche', 'Crop right': 'Rogner à droite', 'Use PDF text layer when present': 'Utiliser la couche de texte du PDF si présente', 'Merge pieces into blocks': 'Fusionner les morceaux en blocs', 'Detect columns / sections': 'Détecter les colonnes / sections', 'Join wrapped lines into paragraphs': 'Joindre les lignes coupées en paragraphes', 'Language code (e.g. en, fr)': 'Code de langue (ex. en, fr)', 'Render DPI (scans)': 'DPI de rendu (numérisations)', 'Output': 'Sortie', 'Text file': 'Fichier texte', 'CSV (blocks)': 'CSV (blocs)', 'Searchable PDF': 'PDF interrogeable', 'Columns splits each page into evenly spaced columns; Draw zones reads only the boxes you draw on each page.': 'Colonnes découpe chaque page en colonnes régulières ; Dessiner des zones ne lit que les cadres que vous dessinez sur chaque page.', 'Draw zones': 'Dessiner des zones', 'Text zones': 'Zones de texte', 'Zone order': 'Ordre des zones', 'Top → bottom, left → right': 'Haut → bas, gauche → droite', 'As drawn': "Dans l'ordre de dessin", 'Column spacing (px)': 'Espacement des colonnes (px)', 'Column width ratios': 'Proportions de largeur des colonnes', 'Margin top (px)': 'Marge haut (px)', 'Margin bottom (px)': 'Marge bas (px)', 'Margin left (px)': 'Marge gauche (px)', 'Margin right (px)': 'Marge droite (px)', 'Book pages?': 'Pages de livre ?', 'Symmetric pages': 'Pages symétriques', 'Left page start': 'Début page gauche', 'Right page start': 'Début page droite', 'Gutter shift (px, book scans)': 'Décalage de reliure (px, scans de livre)', 'Wrap lines': 'Renvoyer les lignes', 'Render DPI (PDF)': 'DPI de rendu (PDF)', 'CSV (zones as columns)': 'CSV (zones en colonnes)', 'Show individual pages': 'Afficher les pages individuelles', 'WebP quality': 'Qualité WebP', 'Lossless': 'Sans perte', 'Name list (.txt, optional)': 'Liste de noms (.txt, facultatif)', 'Remove existing numbering': 'Supprimer la numérotation existante', 'Find': 'Rechercher', 'Replace with': 'Remplacer par', 'Make lowercase': 'Mettre en minuscules', 'Spaces': 'Espaces', 'keep': 'conserver', '→ underscore _': '→ tiret bas _', '→ hyphen -': "→ trait d'union -", 'Operation': 'Opération', 'Extract — keep pages': 'Extraire — garder les pages', 'Extract — delete pages': 'Extraire — supprimer les pages', 'Split — into N files': 'Diviser — en N fichiers', 'Split — every N pages': 'Diviser — toutes les N pages', 'Split — one file per page': 'Diviser — un fichier par page', 'Split — at pages': 'Diviser — aux pages', 'Pages / split points (e.g. 1-5, 8, 11-13)': 'Pages / points de coupe (ex. 1-5, 8, 11-13)', 'N  (for split into / every N)': 'N  (pour diviser en / toutes les N)', 'Scale factor': "Facteur d'échelle", 'Target size': 'Taille cible', 'Scale factor (Scale factor mode)': "Facteur d'échelle (mode Facteur d'échelle)", 'Target size in px (Target size mode)': 'Taille cible en px (mode Taille cible)', 'Fit target to': 'Ajuster la cible à', 'Longest side': 'Plus grand côté', 'Shortest side': 'Plus petit côté', 'Width': 'Largeur', 'Height': 'Hauteur', 'Never shrink (upscale only)': 'Ne jamais réduire (agrandir seulement)', 'Resampling': 'Rééchantillonnage', 'Lanczos (best for photos)': 'Lanczos (idéal pour les photos)', 'Bicubic (smooth)': 'Bicubique (lisse)', 'Bilinear (fast)': 'Bilinéaire (rapide)', 'Nearest (pixel art)': 'Plus proche (pixel art)', 'Post-sharpen (counter softening)': 'Accentuation finale (compenser le flou)', 'Output format': 'Format de sortie', 'Same as input': "Comme l'entrée"}}


def set_language(lang):
    global LANG
    LANG = lang if (lang == "en" or lang in TRANSLATIONS) else "en"


def tr(s):
    """Translate a UI string to the current language, falling back to English.
    Non-string / empty values pass through untouched."""
    if not isinstance(s, str) or not s:
        return s
    return TRANSLATIONS.get(LANG, {}).get(s, s)


# --------------------------------------------------------------------------- #
# Dependency installation (Windows: pip only — no pkexec/dnf). System packages
# such as tesseract are handled outside this app (bundled with the installer or
# installed by the user); SYSTEM_REQUIRES is surfaced as guidance, not run.
# --------------------------------------------------------------------------- #
def _pip_cmds(pkgs):
    """Build the pip command(s) to install `pkgs` into THIS interpreter's env.
    In the packaged Windows build the app runs on a bundled, writable standalone
    Python, so `sys.executable -m pip` installs into the same environment the app
    imports from — on-demand tool deps work exactly like running from source."""
    exe = sys.executable
    # pythonw.exe has no console; use the sibling python.exe so pip output streams.
    if os.name == "nt" and exe.lower().endswith("pythonw.exe"):
        cand = Path(exe).with_name("python.exe")
        if cand.exists():
            exe = str(cand)
    in_venv = sys.prefix != sys.base_prefix
    base = [exe, "-m", "pip", "install"] + ([] if in_venv else ["--user"])
    return [base + list(pkgs)]


def _run_stream(cmd, log):
    log("$ " + " ".join(cmd) + "\n")
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True)
    except FileNotFoundError:
        log(f"  (command not found: {cmd[0]})\n")
        return 1
    for line in proc.stdout:
        log(line)
    proc.wait()
    return proc.returncode


def install_deps(requires, system_requires, log):
    ok = True
    if system_requires:
        log("This tool needs system components that PageForge can't install "
            "automatically on Windows:\n")
        for s in system_requires:
            log(f"  • {s}\n")
        log("Install them separately (or use a build that bundles them), "
            "then reload.\n\n")
    if requires:
        for cmd in _pip_cmds(requires):
            if _run_stream(cmd, log) == 0:
                break
        else:
            ok = False
    importlib.invalidate_caches()
    return ok


# --------------------------------------------------------------------------- #
# Tool loading (pure — identical contract to the GTK build)
# --------------------------------------------------------------------------- #
def grid_move(order, src, dst):
    """Reorder helper for the thumbnail grid: pull `src` out of the order list
    and re-insert it at the position `dst` currently occupies (drop src onto
    dst). Pure — unit-tested without a display."""
    order = [o for o in order if o != src]
    if dst == src or dst not in order:
        order.append(src)
    else:
        order.insert(order.index(dst), src)
    return order


class ToolSpec:
    def __init__(self, path, module):
        self.path = Path(path)
        self.module = module
        self.id = self.path.stem
        self.name = str(getattr(module, "NAME", self.id))
        self.accepts = set(getattr(module, "ACCEPTS", ("image", "pdf")))
        self.batch = bool(getattr(module, "BATCH", False))
        self.order = int(getattr(module, "ORDER", 1000))
        self.options_meta = list(getattr(module, "OPTIONS", []))
        self.requires = list(getattr(module, "REQUIRES", []))
        self.system_requires = list(getattr(module, "SYSTEM_REQUIRES", []))
        self.modules = list(getattr(module, "MODULES", []))
        self.has_preview = hasattr(module, "preview")
        self.has_preview_image = hasattr(module, "preview_image")
        self.has_preview_regions = hasattr(module, "preview_regions")
        self.has_preview_grid = hasattr(module, "preview_grid")
        self.preview_kind_default = str(getattr(module, "PREVIEW_KIND", "canvas"))
        self.has_needs_preview_button = hasattr(module, "needs_preview_button")
        self.preview_on_demand = bool(getattr(module, "PREVIEW_ON_DEMAND", False))
        self.has_compare = bool(getattr(module, "COMPARE_PREVIEW", False))

    @property
    def deps_met(self):
        return all(find_spec_ok(m) for m in self.modules)


def load_tool_from_file(path):
    path = Path(path)
    spec = importlib.util.spec_from_file_location(f"pf_tool_{path.stem}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if not hasattr(mod, "NAME") or not hasattr(mod, "process"):
        return None
    return ToolSpec(path, mod)


# --------------------------------------------------------------------------- #
# Small reusable Qt widgets
# --------------------------------------------------------------------------- #
class FlowLayout(QLayout):
    """A wrapping flow layout (left→right, wraps to next row). Standard Qt
    pattern, used for the thumbnail grid."""

    def __init__(self, parent=None, margin=0, spacing=8):
        super().__init__(parent)
        self._items = []
        self._spacing = spacing
        self.setContentsMargins(margin, margin, margin, margin)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientations(Qt.Orientation(0))

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._do_layout(QRect(0, 0, width, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        size += QSize(m.left() + m.right(), m.top() + m.bottom())
        return size

    def _do_layout(self, rect, test_only):
        m = self.contentsMargins()
        x = rect.x() + m.left()
        y = rect.y() + m.top()
        line_height = 0
        right = rect.right() - m.right()
        for item in self._items:
            w = item.sizeHint().width()
            h = item.sizeHint().height()
            next_x = x + w + self._spacing
            if next_x - self._spacing > right and line_height > 0:
                x = rect.x() + m.left()
                y = y + line_height + self._spacing
                next_x = x + w + self._spacing
                line_height = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), QSize(w, h)))
            x = next_x
            line_height = max(line_height, h)
        return y + line_height + m.bottom() - rect.y()


class Section(QWidget):
    """A collapsible accordion card: a header button that reveals a body.
    Selecting one collapses the others (handled by the owner via set_expanded)."""

    def __init__(self, title, on_activate):
        super().__init__()
        self._on_activate = on_activate
        self.setObjectName("pfSection")
        # let the card background/border from the stylesheet actually paint
        self.setAttribute(Qt.WA_StyledBackground, True)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 8)
        outer.setSpacing(0)
        self.header = QToolButton()
        self.header.setText("  " + title)
        self.header.setCheckable(True)
        self.header.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.header.setArrowType(Qt.RightArrow)
        self.header.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.header.setAutoRaise(True)
        f = self.header.font()
        f.setBold(True)
        self.header.setFont(f)
        self.header.clicked.connect(lambda: self._on_activate(self))
        outer.addWidget(self.header)
        self.body = QWidget()
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(12, 6, 12, 10)
        self.body_layout.setSpacing(8)
        outer.addWidget(self.body)
        self.body.setVisible(False)

    def set_expanded(self, e):
        self.header.setChecked(e)
        self.header.setArrowType(Qt.DownArrow if e else Qt.RightArrow)
        self.body.setVisible(e)


class CanvasWidget(QWidget):
    """Single-page preview surface: base raster + overlays + handles + zones +
    before/after compare. All drawing math lives in the window (mirrors the GTK
    draw func); this widget just forwards paint + pointer events."""

    def __init__(self, win):
        super().__init__()
        self.win = win
        self.setMouseTracking(True)
        self.setMinimumSize(320, 320)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._press = None

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        try:
            self.win._paint_canvas(p, self.width(), self.height())
        finally:
            p.end()

    def mousePressEvent(self, e):
        pos = e.position()
        if e.button() == Qt.RightButton:
            self.win._canvas_right_click(pos.x(), pos.y())
            return
        if e.button() == Qt.LeftButton:
            self._press = (pos.x(), pos.y())
            self.win._canvas_press(pos.x(), pos.y())

    def mouseMoveEvent(self, e):
        pos = e.position()
        if e.buttons() & Qt.LeftButton and self._press is not None:
            self.win._canvas_drag(pos.x(), pos.y())
        else:
            self.win._canvas_hover(pos.x(), pos.y())

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self._press is not None:
            pos = e.position()
            self._press = None
            self.win._canvas_release(pos.x(), pos.y())


class GridCell(QFrame):
    """One thumbnail card in the grid. Draws its own card background and, when
    selected, an accent outline (native highlight colour)."""

    def __init__(self, grid, orig, caption):
        super().__init__()
        self.grid = grid
        self.orig = orig
        self.selected = False
        self.setObjectName("pfCell")
        self.setFixedSize(140, 190)
        self.setAcceptDrops(False)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(4)
        self.pic = QLabel()
        self.pic.setAlignment(Qt.AlignCenter)
        self.pic.setFixedSize(128, 150)
        self.pic.setScaledContents(False)
        lay.addWidget(self.pic, 0, Qt.AlignHCenter)
        self.cap = QLabel(caption)
        self.cap.setAlignment(Qt.AlignHCenter)
        self.cap.setWordWrap(False)
        fm = QFontMetrics(self.cap.font())
        self.cap.setText(fm.elidedText(caption, Qt.ElideMiddle, 128))
        f = self.cap.font()
        f.setPointSizeF(max(7.0, f.pointSizeF() - 1))
        self.cap.setFont(f)
        lay.addWidget(self.cap, 0, Qt.AlignHCenter)
        self._press_pos = None

    def set_pixmap(self, pm):
        if pm and not pm.isNull():
            self.pic.setPixmap(pm.scaled(128, 150, Qt.KeepAspectRatio,
                                         Qt.SmoothTransformation))

    def set_selected(self, sel):
        if sel != self.selected:
            self.selected = sel
            self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(1, 1, self.width() - 2, self.height() - 2)
        pal = self.palette()
        base = pal.color(QPalette.Base)
        p.setBrush(QBrush(base))
        border = pal.color(QPalette.Mid)
        p.setPen(QPen(border, 1))
        p.drawRoundedRect(rect, 8, 8)
        if self.selected:
            acc = pal.color(QPalette.Highlight)
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(acc, 3))
            p.drawRoundedRect(QRectF(2, 2, self.width() - 4, self.height() - 4), 8, 8)
        p.end()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._press_pos = e.position()
            self.grid._cell_clicked(self.orig, e.modifiers())

    def mouseMoveEvent(self, e):
        if not (e.buttons() & Qt.LeftButton) or self._press_pos is None:
            return
        if (e.position() - self._press_pos).manhattanLength() < 8:
            return
        if not self.grid.reorderable:
            return
        drag = QDrag(self)
        mime = QMimeData()
        mime.setText(f"pf-orig:{self.orig}")
        drag.setMimeData(mime)
        pm = self.grab()
        drag.setPixmap(pm.scaled(pm.size() * 0.6, Qt.KeepAspectRatio,
                                 Qt.SmoothTransformation))
        drag.exec(Qt.MoveAction)


class BarOverlay(QWidget):
    """Transparent, click-through overlay painted on top of the grid to draw the
    themed insertion bar (reorder / file-drop) and persistent split bars."""

    def __init__(self, win):
        super().__init__()
        self.win = win
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        try:
            self.win._paint_grid_bars(p)
        finally:
            p.end()


class GridContainer(QWidget):
    """Holds the FlowLayout of cells and accepts internal-reorder + file drops,
    driving the insertion bar. Coordinates are in this widget's space."""

    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.reorderable = False
        self.selectable = False
        self.setAcceptDrops(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.flow = FlowLayout(self, margin=8, spacing=10)

    # selection dispatch from a cell click
    def _cell_clicked(self, orig, modifiers):
        self.win._grid_cell_click(orig, modifiers)

    def dragEnterEvent(self, e):
        md = e.mimeData()
        if md.hasText() and md.text().startswith("pf-orig:"):
            if self.reorderable:
                e.acceptProposedAction()
        elif md.hasUrls():
            if self.reorderable:
                e.acceptProposedAction()

    def dragMoveEvent(self, e):
        pos = e.position()
        md = e.mimeData()
        if md.hasText() and md.text().startswith("pf-orig:"):
            self.win._grid_bar_motion(pos.x(), pos.y())
            e.acceptProposedAction()
        elif md.hasUrls():
            self.win._grid_bar_motion(pos.x(), pos.y())
            e.acceptProposedAction()

    def dragLeaveEvent(self, _e):
        self.win._grid_bar_clear()

    def dropEvent(self, e):
        pos = e.position()
        md = e.mimeData()
        if md.hasText() and md.text().startswith("pf-orig:"):
            try:
                src = int(md.text().split(":", 1)[1])
            except ValueError:
                return
            self.win._grid_reorder_drop(src, pos.x(), pos.y())
            e.acceptProposedAction()
        elif md.hasUrls():
            paths = [Path(u.toLocalFile()) for u in md.urls() if u.isLocalFile()]
            self.win._grid_file_drop(paths, pos.x(), pos.y())
            e.acceptProposedAction()

    # split-bar interaction (only meaningful in split mode) — press/drag/release
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            pos = e.position()
            self.win._grid_split_press(pos.x(), pos.y())

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.LeftButton:
            pos = e.position()
            self.win._grid_split_move(pos.x(), pos.y())

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            pos = e.position()
            self.win._grid_split_release(pos.x(), pos.y())

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            if self.win._grid_delete_selected():
                return
        super().keyPressEvent(e)


def _clear_layout(layout):
    """Remove and delete every item (widgets and nested layouts) from a layout."""
    while layout.count():
        item = layout.takeAt(0)
        w = item.widget()
        if w is not None:
            w.setParent(None)
            w.deleteLater()
        else:
            child = item.layout()
            if child is not None:
                _clear_layout(child)
                child.deleteLater()


class _DropFrame(QFrame):
    """A frame that accepts file/folder drops and forwards resolved paths."""

    def __init__(self, on_paths):
        super().__init__()
        self._on_paths = on_paths
        self.setAcceptDrops(True)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        paths = [Path(u.toLocalFile()) for u in e.mimeData().urls() if u.isLocalFile()]
        if paths:
            self._on_paths(paths)
            e.acceptProposedAction()


class _Bridge(QObject):
    """Marshals a callable from a worker thread onto the GUI thread. Emitting the
    signal (queued, since the bridge lives on the main thread) is the Qt
    equivalent of GTK's GLib.idle_add."""
    call = Signal(object)

    def __init__(self):
        super().__init__()
        self.call.connect(self._run)

    @staticmethod
    def _run(fn):
        try:
            fn()
        except Exception:
            traceback.print_exc()


class PreviewBox(QWidget):
    """Outer preview container. A file dropped anywhere on the preview lands
    here (unless a child that accepts drops — the grid — consumes it first)."""

    def __init__(self, win):
        super().__init__()
        self.win = win
        self.setAcceptDrops(True)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        paths = [Path(u.toLocalFile()) for u in e.mimeData().urls() if u.isLocalFile()]
        if paths:
            self.win._on_preview_drop(paths)
            e.acceptProposedAction()


# --------------------------------------------------------------------------- #
# Main window
# --------------------------------------------------------------------------- #
class MainWindow(QMainWindow):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.setWindowTitle("PageForge")
        self.resize(1240, 820)
        self.cfg = load_config()
        set_language(self.cfg.get("language", "en"))
        self.files = []
        self.preview_index = 0
        self.page_index = 0
        self.page_count = 1
        self.output_dir = None
        self.tools = {}
        self.active_ids = []
        self.tool = None
        self._opt_getters = {}
        self._opt_setters = {}
        self._opt_widgets = {}
        self._opt_rows = {}
        self._file_opts = {}
        self._enable_rules = []
        self._visible_rules = []
        self._hidden_vals = {}
        self._regions_key = None
        # base/preview state
        self._base_qimage = None
        self._unit_scale = 1.0
        self._surf_w = self._surf_h = 1
        self._base_cache = {}
        self._img_cache = {}
        self._overlay = None
        self._overlay_style = {}
        self._handles = []
        self._detected = []
        self._detected_style = {}
        self._cursor_name = None
        self._last_scale, self._last_ox, self._last_oy = 1.0, 0.0, 0.0
        # zone / region interaction
        self._regions = {}
        self._regions_enabled = False
        self._zone_style = {}
        self._zone_hint_text = ""
        self._drawing = None
        self._moving = None
        self._resizing = None
        self._dragging_handle = None
        self._suppress_refresh = False
        # compare
        self._compare_mode = False
        self._compare_before = None
        self._compare_after = None
        self._compare_x = 0.5
        self._compare_rect = None
        self._compare_drag = False
        # thumbnail-grid state
        self._thumb_dir = Path(tempfile.mkdtemp(prefix="pageforge-thumbs-"))
        self._thumb_cache = {}
        self._grid_items = []
        self._grid_order = []
        self._grid_selected = []
        self._grid_deleted = set()
        self._grid_cells = {}
        self._grid_spec = {}
        self._grid_selectable = False
        self._grid_reorderable = False
        self._grid_anchor = None
        self._grid_sig = None
        self._grid_gen = 0
        self._grid_unit_keys = {}
        self._pagecount_cache = {}
        self._grid_gap = None
        self._grid_splits = set()
        self._grid_split_mode = False
        self._split_candidate = None
        self._split_drag = None
        self._split_press = None
        self._bridge = _Bridge()

        self._build_window_content()
        self._select_section(self.sections[0])
        if not self.cfg.get("scripts_dir"):
            QTimer.singleShot(0, self._first_run)
        else:
            self._sync_bundled(self.cfg["scripts_dir"])
            self._reload_tools()
            QTimer.singleShot(0, self._prompt_new_dep_tools)
        self._refresh_preview()

    # ---- first run ---------------------------------------------------------
    def _first_run(self):
        box = QMessageBox(self)
        box.setWindowTitle("Welcome to PageForge")
        box.setText("Choose a folder to keep your tools in. The built-in tools "
                    "will be placed there and loaded automatically. You can add "
                    "your own scripts to the same folder later.")
        use_default = box.addButton("Use default folder", QMessageBox.AcceptRole)
        choose = box.addButton("Choose folder…", QMessageBox.ActionRole)
        box.exec()
        if box.clickedButton() is choose:
            d = QFileDialog.getExistingDirectory(self, tr("Choose scripts folder"))
            self._setup_scripts_dir(Path(d) if d else DEFAULT_TOOLS_DIR)
        else:
            self._setup_scripts_dir(DEFAULT_TOOLS_DIR)

    def _setup_scripts_dir(self, d):
        d = Path(d)
        try:
            d.mkdir(parents=True, exist_ok=True)
            self._sync_bundled(d)
        except Exception as e:
            self._toast(f"Could not set up folder: {e}")
            return
        self.cfg["scripts_dir"] = str(d)
        self.cfg["seeded"] = True
        save_config(self.cfg)
        self._reload_tools()
        self._refresh_preview()
        QTimer.singleShot(0, self._prompt_new_dep_tools)

    @staticmethod
    def _hash(text):
        return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]

    def _existing_tool_names(self, d):
        names = set()
        for p in Path(d).glob("*.py"):
            try:
                m = re.search(r'^NAME\s*=\s*["\'](.+?)["\']',
                              p.read_text(encoding="utf-8"), re.M)
                if m:
                    names.add(m.group(1))
            except Exception:
                pass
        return names

    def _sync_bundled(self, d, force=False):
        """Seed/refresh the built-in tool scripts (seed copies → user's editable
        scripts folder). Identical policy to the GTK build: new bundled tools are
        written in; unedited bundled tools are updated; user-edited/renamed tools
        are left alone; deleted ones stay deleted; force=True (Restore)
        overwrites everything and un-deletes."""
        d = Path(d)
        seeded = self.cfg.setdefault("seeded_hashes", {})
        deleted = set(self.cfg.setdefault("deleted", []))
        present_names = self._existing_tool_names(d)
        for name, src in bundled_sources().items():
            p = d / name
            newh = self._hash(src)
            tool_name = None
            mm = re.search(r'^NAME\s*=\s*["\'](.+?)["\']', src, re.M)
            if mm:
                tool_name = mm.group(1)
            if force:
                p.write_text(src, encoding="utf-8")
                seeded[name] = newh
                deleted.discard(name)
            elif name in deleted:
                continue
            elif not p.exists():
                if tool_name and tool_name in present_names:
                    continue
                p.write_text(src, encoding="utf-8")
                seeded[name] = newh
            else:
                cur = self._hash(p.read_text(encoding="utf-8"))
                if seeded.get(name) == cur and cur != newh:
                    p.write_text(src, encoding="utf-8")
                    seeded[name] = newh
                else:
                    seeded.setdefault(name, cur)
        for name, src in bundled_docs().items():
            p = d / name
            if force or not p.exists():
                try:
                    p.write_text(src, encoding="utf-8")
                except Exception:
                    pass
        self.cfg["deleted"] = sorted(deleted)
        save_config(self.cfg)

    # ---- tool loading ------------------------------------------------------
    def _effective_order(self, tid):
        return self.cfg.get("order", {}).get(
            tid, self.tools[tid].order if tid in self.tools else 1000)

    def _sorted_ids(self, ids):
        return sorted(ids, key=lambda t: (self._effective_order(t),
                                          self.tools[t].name.lower()))

    def _reload_tools(self):
        importlib.invalidate_caches()
        self.tools = {}
        d = self.cfg.get("scripts_dir")
        if d and Path(d).is_dir():
            for f in sorted(Path(d).glob("*.py")):
                if f.name.startswith("_"):
                    continue
                try:
                    t = load_tool_from_file(f)
                    if t:
                        self.tools[t.id] = t
                except Exception:
                    print(f"[PageForge] failed to load {f}")
                    traceback.print_exc()
        disabled = self.cfg.get("disabled", {})
        active = [tid for tid, t in self.tools.items()
                  if not disabled.get(tid, False) and t.deps_met]
        self.active_ids = self._sorted_ids(active)
        if self.tool not in self.active_ids:
            self.tool = self.active_ids[0] if self.active_ids else None
        self._rebuild_tools_section()
        self._rebuild_options()

    def _prompt_new_dep_tools(self):
        prompted = set(self.cfg.setdefault("dep_prompted", []))
        disabled = self.cfg.get("disabled", {})
        new_ids = [tid for tid, t in self.tools.items()
                   if not t.deps_met and not disabled.get(tid, False)
                   and tid not in prompted]
        if not new_ids:
            return
        specs = [self.tools[tid] for tid in self._sorted_ids(new_ids)]
        lines = "\n".join(
            f"• {tr(s.name)} — {tr('needs')} "
            f"{', '.join(s.requires + s.system_requires) or tr('extra components')}"
            for s in specs)
        box = QMessageBox(self)
        box.setWindowTitle(tr("New tools need setup"))
        box.setText(tr("These tools were found but aren't enabled yet because "
                       "they need extra components installed:")
                    + f"\n\n{lines}\n\n"
                    + tr("Open Settings to install their dependencies and enable them."))
        later = box.addButton(tr("Later"), QMessageBox.RejectRole)
        settings = box.addButton(tr("Open Settings"), QMessageBox.AcceptRole)
        box.exec()
        if box.clickedButton() is settings:
            self._open_settings()
        prompted.update(s.id for s in specs)
        self.cfg["dep_prompted"] = sorted(prompted)
        save_config(self.cfg)

    # ---- window content ----------------------------------------------------
    def _build_window_content(self):
        """Build (or rebuild) the whole window: header, sidebar, preview. Called
        at startup and again on a language change so the interface re-renders
        live in the newly chosen language."""
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Slim top strip: just a Settings cogwheel at the top-right, sitting up
        # by the window's caption buttons. No title text, no separator line.
        topbar = QHBoxLayout()
        topbar.setContentsMargins(6, 4, 8, 0)
        topbar.addStretch(1)
        gear = QToolButton()
        gear.setAutoRaise(True)
        gear.setToolTip(tr("Settings"))
        gear.setIcon(_make_gear_icon(self.palette().color(QPalette.WindowText)))
        gear.setIconSize(QSize(20, 20))
        gear.setFixedSize(30, 28)
        gear.clicked.connect(self._open_settings)
        topbar.addWidget(gear)
        root.addLayout(topbar)

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self._build_sidebar())
        splitter.addWidget(self._build_preview())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([400, 840])
        root.addWidget(splitter, 1)
        self.setCentralWidget(central)
        self._apply_section_style()

    def _apply_section_style(self):
        """Give the sidebar sections a discreet rounded card frame, theme-aware,
        in the spirit of the Windows 11 Settings cards."""
        win = self.palette().color(QPalette.Window)
        dark = win.lightness() < 128
        if dark:
            bg, border = "rgba(255,255,255,0.05)", "rgba(255,255,255,0.12)"
        else:
            bg, border = "rgba(0,0,0,0.02)", "rgba(0,0,0,0.13)"
        css = (f"#pfSection {{ background:{bg}; border:1px solid {border};"
               f" border-radius:8px; }}")
        for s in getattr(self, "sections", []):
            s.setStyleSheet(css)

    # ---- sidebar -----------------------------------------------------------
    def _build_sidebar(self):
        scroller = QScrollArea()
        scroller.setWidgetResizable(True)
        scroller.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroller.setMinimumWidth(340)
        outer = QWidget()
        ol = QVBoxLayout(outer)
        ol.setContentsMargins(12, 12, 12, 12)
        ol.setSpacing(0)

        self.sec_files = Section(tr("1  Files"), self._select_section)
        self.sec_tools = Section(tr("2  Tools"), self._select_section)
        self.sec_options = Section(tr("3  Options"), self._select_section)
        self.sec_output = Section(tr("4  Output"), self._select_section)
        self.sections = [self.sec_files, self.sec_tools, self.sec_options, self.sec_output]

        self._build_files_section(self.sec_files.body_layout)
        self.tools_box = QVBoxLayout()
        self.tools_box.setSpacing(4)
        self.sec_tools.body_layout.addLayout(self.tools_box)
        self.options_container = QVBoxLayout()
        self.options_container.setSpacing(6)
        self.sec_options.body_layout.addLayout(self.options_container)
        self._build_output_section(self.sec_output.body_layout)

        for s in self.sections:
            ol.addWidget(s)
        ol.addStretch(1)
        scroller.setWidget(outer)
        return scroller

    def _build_files_section(self, body):
        row = QHBoxLayout()
        b1 = QPushButton(tr("Add files"))
        b1.clicked.connect(self._choose_files)
        b2 = QPushButton(tr("Add folder"))
        b2.clicked.connect(self._choose_folder)
        b3 = QPushButton(tr("Clear"))
        b3.clicked.connect(lambda: self._set_files([]))
        for b in (b1, b2, b3):
            row.addWidget(b)
        body.addLayout(row)
        drop = _DropFrame(self._drop_paths)
        drop.setFrameShape(QFrame.StyledPanel)
        dl = QVBoxLayout(drop)
        hint = QLabel(tr("…or drop files here"))
        hint.setAlignment(Qt.AlignCenter)
        hint.setEnabled(False)
        dl.addWidget(hint)
        body.addWidget(drop)
        self.files_label = QLabel(tr("No files loaded"))
        self.files_label.setEnabled(False)
        body.addWidget(self.files_label)

    def _rebuild_tools_section(self):
        _clear_layout(self.tools_box)
        self.tool_buttons = {}
        if not self.active_ids:
            lab = QLabel(tr("No tools available.\nOpen Settings to add or enable tools."))
            lab.setEnabled(False)
            self.tools_box.addWidget(lab)
            return
        self._tool_group = QButtonGroup(self)
        self._tool_group.setExclusive(True)
        for tid in self.active_ids:
            btn = QRadioButton(tr(self.tools[tid].name))
            btn.setChecked(tid == self.tool)
            btn.toggled.connect(lambda checked, t=tid: self._on_tool_toggled(checked, t))
            self._tool_group.addButton(btn)
            self.tool_buttons[tid] = btn
            self.tools_box.addWidget(btn)

    def _on_tool_toggled(self, checked, tid):
        if checked:
            self.tool = tid
            self.page_index = 0
            self._detected = []
            self._rebuild_options()
            self._refresh_preview()

    # ---- section nav -------------------------------------------------------
    def _select_section(self, section):
        for s in self.sections:
            s.set_expanded(s is section)

    # ---- generic options ---------------------------------------------------
    def _rebuild_options(self):
        _clear_layout(self.options_container)
        self._opt_getters = {}
        self._opt_setters = {}
        self._opt_widgets = {}
        self._opt_rows = {}
        self._enable_rules = []
        self._visible_rules = []
        self._regions_key = None
        self._hidden_vals = {}
        self._grid_order = []
        self._grid_selected = []
        self._regions_enabled = False
        self._zone_style = {}
        self._zone_hint_text = ""
        self._regions = {}
        spec = self.tools.get(self.tool)
        if not spec:
            return
        if not spec.options_meta:
            lab = QLabel(tr("No options for this tool."))
            lab.setEnabled(False)
            self.options_container.addWidget(lab)
            return
        meta = spec.options_meta
        consumed = set()
        for opt in meta:
            if opt["key"] in consumed:
                continue
            if opt.get("hidden"):
                self._add_hidden_option(opt)
                continue
            if opt.get("enabled_when"):
                self._enable_rules.append((opt["key"], opt["enabled_when"]))
            if opt.get("visible_when"):
                self._visible_rules.append((opt["key"], opt["visible_when"]))
            partner_key = opt.get("link_with")
            partner = next((o for o in meta if o["key"] == partner_key), None) if partner_key else None
            if partner:
                if partner.get("enabled_when"):
                    self._enable_rules.append((partner["key"], partner["enabled_when"]))
                if partner.get("visible_when"):
                    self._visible_rules.append((partner["key"], partner["visible_when"]))
                self._add_linked_pair(opt, partner)
                consumed.add(partner_key)
            else:
                self._add_option_row(opt)
        self._apply_enable_rules()
        self._apply_visible_rules()

    def _make_spin(self, opt):
        typ = opt.get("type", "int")
        if typ == "float":
            sp = QDoubleSpinBox()
            sp.setDecimals(2)
            sp.setSingleStep(0.1)
        else:
            sp = QSpinBox()
            sp.setSingleStep(1)
        sp.setRange(opt.get("min", 0), opt.get("max", 100000))
        sp.setValue(opt.get("default", 0))
        sp.valueChanged.connect(lambda *_: self._refresh_preview())
        return sp

    def _add_linked_pair(self, a, b):
        row = QWidget()
        grid = QGridLayout(row)
        grid.setContentsMargins(0, 2, 0, 2)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)
        la = QLabel(tr(a.get("label", a["key"])))
        lb = QLabel(tr(b.get("label", b["key"])))
        sa, sb = self._make_spin(a), self._make_spin(b)
        grid.addWidget(la, 0, 0)
        grid.addWidget(sa, 0, 1)
        grid.addWidget(lb, 1, 0)
        grid.addWidget(sb, 1, 1)
        grid.setColumnStretch(0, 1)
        tgl = QToolButton()
        tgl.setCheckable(True)
        tgl.setAutoRaise(True)
        tgl.setToolTip(tr("Link these two values"))
        # a tall, narrow vertical rectangle spanning both spin rows
        tgl.setFixedWidth(28)
        tgl.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        self._update_link_icon(tgl)
        grid.addWidget(tgl, 0, 2, 2, 1)
        self._opt_rows[a["key"]] = row
        self._opt_rows[b["key"]] = row
        for key, sp, o in ((a["key"], sa, a), (b["key"], sb, b)):
            self._opt_widgets[key] = sp
            self._opt_getters[key] = (lambda s=sp, oo=o:
                                      int(s.value()) if oo.get("type", "int") == "int"
                                      else float(s.value()))
            self._opt_setters[key] = (lambda v, s=sp: s.setValue(float(v)))
        self._wire_link(sa, sb, tgl)
        tgl.toggled.connect(lambda *_: (self._update_link_icon(tgl), self._refresh_preview()))
        self.options_container.addWidget(row)

    def _update_link_icon(self, tgl):
        pm = self._link_pixmap(tgl.isChecked())
        tgl.setIcon(QIcon(pm))
        tgl.setIconSize(pm.size())
        tgl.setText("")

    def _link_pixmap(self, linked, w=20, h=36):
        """A themed chain glyph: two links joined (linked) or pulled apart with a
        gap (unlinked). Painted in the palette text colour so it reads in any theme."""
        color = self.palette().color(QPalette.WindowText)
        pm = QPixmap(w, h)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing, True)
        pen = QPen(color)
        pen.setWidthF(1.8)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        cx, lw, lh = w / 2.0, 11.0, 15.0
        if linked:
            p.drawRoundedRect(QRectF(cx - lw / 2, h / 2 - lh, lw, lh), 5, 5)
            p.drawRoundedRect(QRectF(cx - lw / 2, h / 2, lw, lh), 5, 5)
        else:
            p.drawRoundedRect(QRectF(cx - lw / 2, h / 2 - lh - 3, lw, lh), 5, 5)
            p.drawRoundedRect(QRectF(cx - lw / 2, h / 2 + 3, lw, lh), 5, 5)
        p.end()
        return pm

    def _rule_ok(self, rule):
        getter = self._opt_getters.get(rule.get("key"))
        if getter is None:
            return True
        val = getter()
        if "in" in rule:
            return val in rule["in"]
        if "not_in" in rule:
            return val not in rule["not_in"]
        if "eq" in rule:
            return val == rule["eq"]
        return True

    def _apply_enable_rules(self):
        for key, rule in getattr(self, "_enable_rules", []):
            row = self._opt_rows.get(key)
            if row is not None:
                row.setEnabled(bool(self._rule_ok(rule)))

    def _apply_visible_rules(self):
        for key, rule in getattr(self, "_visible_rules", []):
            row = self._opt_rows.get(key)
            if row is not None:
                row.setVisible(bool(self._rule_ok(rule)))
        rk = getattr(self, "_regions_key", None)
        if rk is not None:
            rrow = self._opt_rows.get(rk)
            if rrow is not None:
                # isHidden() reflects the explicit hide flag independent of whether
                # an ancestor is mapped yet — the Qt equivalent of GTK get_visible().
                self._regions_enabled = not rrow.isHidden()

    def _add_hidden_option(self, opt):
        key = opt["key"]
        self._hidden_vals[key] = opt.get("default")
        self._opt_getters[key] = (lambda k=key: self._hidden_vals.get(k))

        def _set(v, k=key):
            self._hidden_vals[k] = v
        self._opt_setters[key] = _set

    def _add_option_row(self, opt):
        key = opt["key"]
        typ = opt.get("type", "text")
        label = tr(opt.get("label", key))
        row = QWidget()
        hb = QHBoxLayout(row)
        hb.setContentsMargins(0, 0, 0, 0)
        hb.setSpacing(8)
        lab = QLabel(label)
        lab.setWordWrap(True)
        hb.addWidget(lab, 1)

        if typ in ("int", "float"):
            sp = self._make_spin(opt)
            hb.addWidget(sp)
            self._opt_widgets[key] = sp
            self._opt_getters[key] = (lambda s=sp, t=typ:
                                      int(s.value()) if t == "int" else float(s.value()))
            self._opt_setters[key] = (lambda v, s=sp: s.setValue(float(v)))
        elif typ == "bool":
            sw = QCheckBox()
            sw.setChecked(bool(opt.get("default", False)))
            sw.toggled.connect(lambda *_: self._refresh_preview())
            hb.addWidget(sw)
            self._opt_getters[key] = (lambda s=sw: s.isChecked())
            self._opt_setters[key] = (lambda v, s=sw: s.setChecked(bool(v)))
        elif typ == "choice":
            choices = list(opt.get("choices", []))
            dd = QComboBox()
            dd.addItems([tr(c) for c in choices])
            if opt.get("default") in choices:
                dd.setCurrentIndex(choices.index(opt["default"]))
            dd.currentIndexChanged.connect(lambda *_: self._refresh_preview())
            hb.addWidget(dd)
            self._opt_getters[key] = (lambda d=dd, c=choices:
                                      c[d.currentIndex()] if c and d.currentIndex() >= 0 else "")
            self._opt_setters[key] = (lambda v, d=dd, c=choices:
                                      d.setCurrentIndex(c.index(v)) if v in c else None)
        elif typ == "color":
            btn = QPushButton()
            btn.setFixedWidth(48)
            state = {"hex": str(opt.get("default", "#000000"))}
            self._paint_color_btn(btn, state["hex"])

            def _pick(_=None, b=btn, st=state, k=key):
                c = QColorDialog.getColor(QColor(st["hex"]), self, tr("Choose"))
                if c.isValid():
                    st["hex"] = c.name()
                    self._paint_color_btn(b, st["hex"])
                    self._refresh_preview()
            btn.clicked.connect(_pick)
            hb.addWidget(btn)
            self._opt_getters[key] = (lambda st=state: st["hex"])

            def _setcolor(v, b=btn, st=state):
                c = QColor(str(v))
                if c.isValid():
                    st["hex"] = c.name()
                    self._paint_color_btn(b, st["hex"])
            self._opt_setters[key] = _setcolor
        elif typ == "regions":
            self._regions_enabled = True
            self._regions_key = key
            self._zone_style = {"color": parse_rgb(opt.get("color"), ZONE_COLOR)}
            self._zone_hint_text = tr(opt.get("hint")) or tr(
                "Zones are per-page · drag to add · right-click to delete · "
                "pages with no zones are skipped")
            clearb = QPushButton(tr("Clear page"))
            clearb.setToolTip(tr("Remove this page's zones"))
            clearb.clicked.connect(lambda: (self._regions.pop(self.page_index, None),
                                            self.canvas.update()))
            allb = QPushButton(tr("Copy to all"))
            allb.setToolTip(tr("Use this page's zones on every page"))
            allb.clicked.connect(lambda: self._copy_zones_to_all())
            hb.addWidget(clearb)
            hb.addWidget(allb)
            self._opt_getters[key] = (lambda: {p: list(v) for p, v in self._regions.items() if v})
        elif typ == "file":
            self._file_opts[key] = opt.get("default", "") or ""
            fl = QLabel(Path(self._file_opts[key]).name if self._file_opts[key] else tr("none"))
            fl.setEnabled(False)
            fl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            btn = QPushButton(tr("Choose"))
            btn.clicked.connect(lambda _=None, k=key, l=fl: self._choose_file_option(k, l))
            hb.addWidget(fl, 1)
            hb.addWidget(btn)
            self._opt_getters[key] = (lambda k=key: self._file_opts.get(k, ""))

            def _setfile(v, k=key, lbl=fl):
                self._file_opts[k] = str(v or "")
                lbl.setText(Path(self._file_opts[k]).name if self._file_opts[k] else tr("none"))
            self._opt_setters[key] = _setfile
        else:
            e = QLineEdit()
            e.setText(str(opt.get("default", "")))
            e.textChanged.connect(lambda *_: self._refresh_preview())
            hb.addWidget(e, 1)
            self._opt_getters[key] = (lambda ed=e: ed.text())
            self._opt_setters[key] = (lambda v, ed=e: ed.setText(str(v)))
        self._opt_rows[key] = row
        self.options_container.addWidget(row)

    @staticmethod
    def _paint_color_btn(btn, hexstr):
        c = QColor(hexstr)
        tc = "#000000" if c.lightnessF() > 0.5 else "#ffffff"
        btn.setStyleSheet(f"QPushButton {{ background:{c.name()}; color:{tc};"
                          f" border:1px solid palette(mid); border-radius:4px; }}")
        btn.setText(c.name())

    def _choose_file_option(self, key, label):
        f, _ = QFileDialog.getOpenFileName(self, tr("Choose file"))
        if f:
            self._file_opts[key] = f
            label.setText(Path(f).name)
            self._refresh_preview()

    def _read_opts(self):
        return {k: g() for k, g in self._opt_getters.items()}

    def _set_option(self, key, value):
        setter = self._opt_setters.get(key)
        if setter is not None:
            try:
                setter(value)
            except Exception:
                traceback.print_exc()

    def _wire_link(self, a, b, toggle):
        if toggle is None:
            return
        self._syncing = False

        def sync(src, dst):
            if toggle.isChecked() and not self._syncing:
                self._syncing = True
                dst.setValue(src.value())
                self._syncing = False
        a.valueChanged.connect(lambda *_: sync(a, b))
        b.valueChanged.connect(lambda *_: sync(b, a))

    # ---- output section ----------------------------------------------------
    def _build_output_section(self, body):
        pick = QPushButton(tr("Choose output folder"))
        pick.clicked.connect(self._choose_output)
        body.addWidget(pick)
        self.output_label = QLabel(tr("Default: an 'output' folder beside your files"))
        self.output_label.setWordWrap(True)
        self.output_label.setEnabled(False)
        body.addWidget(self.output_label)

        name_row = QHBoxLayout()
        name_row.addWidget(QLabel(tr("Name")))
        self.out_name_entry = QLineEdit(tr("[Original file name]"))
        self.out_name_entry.setToolTip(tr(
            "[Original file name] keeps each file's name (including what the Rename tool "
            "produced). Type a base name to replace it. Numbering is added on top."))
        self.out_name_entry.textChanged.connect(lambda *_: self._refresh_preview())
        name_row.addWidget(self.out_name_entry, 1)
        body.addLayout(name_row)

        num_row = QHBoxLayout()
        num_row.addWidget(QLabel(tr("Add numbering")), 1)
        self.out_number_switch = QCheckBox()
        self.out_number_switch.toggled.connect(lambda *_: self._refresh_preview())
        num_row.addWidget(self.out_number_switch)
        body.addLayout(num_row)

        num_row2 = QHBoxLayout()
        num_row2.addWidget(QLabel(tr("Style")), 1)
        self.out_number_dd = QComboBox()
        self.out_number_dd.addItems(["1, 2, 3", "01, 02, 03", "001, 002, 003"])
        self.out_pos_dd = QComboBox()
        self.out_pos_dd.addItems([tr("as prefix"), tr("as suffix")])
        self.out_number_dd.currentIndexChanged.connect(lambda *_: self._refresh_preview())
        self.out_pos_dd.currentIndexChanged.connect(lambda *_: self._refresh_preview())
        num_row2.addWidget(self.out_number_dd)
        num_row2.addWidget(self.out_pos_dd)
        body.addLayout(num_row2)

        orow = QHBoxLayout()
        orow.addWidget(QLabel(tr("Overwrite originals")), 1)
        self.sw_overwrite = QCheckBox()
        orow.addWidget(self.sw_overwrite)
        body.addLayout(orow)

        self.process_btn = QPushButton(tr("Process files"))
        self.process_btn.setDefault(True)
        self.process_btn.clicked.connect(self._process)
        body.addWidget(self.process_btn)

    def _naming_config(self):
        raw = self.out_name_entry.text().strip()
        keep = (not raw) or raw == "[Original file name]" or raw == tr("[Original file name]")
        add_number = self.out_number_switch.isChecked()
        if keep and not add_number:
            return None
        name = re.sub(r'[/\\:*?<>|"]', "", raw).strip()
        return {"keep": keep, "name": name, "add_number": add_number,
                "pad": self.out_number_dd.currentIndex() + 1,
                "prefix": self.out_pos_dd.currentIndex() == 0}

    def _name_with_number(self, stem, index, naming):
        base = stem if naming["keep"] else (naming["name"] or stem)
        if not naming["add_number"]:
            return base
        num = str(index).zfill(naming["pad"])
        return f"{num} - {base}" if naming["prefix"] else f"{base} - {num}"

    def _apply_naming(self, written, naming):
        if not naming or not written:
            return written
        naming = dict(naming)
        naming["pad"] = max(naming["pad"], len(str(len(written))))
        out = []
        for i, p in enumerate(written, start=1):
            p = Path(p)
            stem = self._name_with_number(p.stem, i, naming)
            dest = p.with_name(f"{stem}{p.suffix}")
            k = 2
            while dest.exists() and dest != p:
                dest = p.with_name(f"{stem} ({k}){p.suffix}")
                k += 1
            try:
                p.rename(dest)
                out.append(str(dest))
            except Exception:
                out.append(str(p))
        return out

    def _resolve_output_dir(self):
        if self.output_dir is not None:
            return self.output_dir
        base = self.files[0].parent if self.files else Path.cwd()
        return base / "output"

    # ---- files -------------------------------------------------------------
    def _choose_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, tr("Add files"))
        if files:
            self._set_files(self.files + [Path(f) for f in files])

    def _choose_folder(self):
        d = QFileDialog.getExistingDirectory(self, tr("Add folder"))
        if d:
            p = Path(d)
            self._set_files(self.files + [q for q in sorted(p.iterdir())
                                          if is_pdf(q) or is_image(q)])

    def _drop_paths(self, paths):
        out = []
        for p in paths:
            if p.is_dir():
                out += [q for q in sorted(p.iterdir()) if is_pdf(q) or is_image(q)]
            elif is_pdf(p) or is_image(p):
                out.append(p)
        self._set_files(self.files + out)

    def _set_files(self, files):
        seen, uniq = set(), []
        for f in files:
            f = Path(f)
            if f not in seen:
                seen.add(f)
                uniq.append(f)
        self.files = uniq
        self.preview_index = 0
        self.page_index = 0
        self._detected = []
        self._img_cache.clear()
        self._base_cache.clear()
        if not self.files:
            self.files_label.setText(tr("No files loaded"))
        else:
            n_pdf = sum(1 for f in self.files if is_pdf(f))
            n_img = sum(1 for f in self.files if is_image(f))
            self.files_label.setText(f"{len(self.files)} files  ({n_pdf} PDF, {n_img} image)")
        self._refresh_preview()

    def _choose_output(self):
        d = QFileDialog.getExistingDirectory(self, tr("Choose output folder"))
        if d:
            self.output_dir = Path(d)
            self.output_label.setText(str(self.output_dir))

    def _relevant_files(self, spec):
        return [f for f in self.files if kind_of(f) in spec.accepts]

    # ---- preview build -----------------------------------------------------
    def _build_preview(self):
        box = PreviewBox(self)
        v = QVBoxLayout(box)
        v.setContentsMargins(6, 12, 12, 12)
        v.setSpacing(6)

        self.preview_stack = QStackedWidget()
        self.preview_stack.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        # canvas surface
        self.canvas = CanvasWidget(self)
        self._canvas_index = self.preview_stack.addWidget(self.canvas)

        # grid surface: a scroll area over the flow container, with a bar overlay
        self.grid_scroller = QScrollArea()
        self.grid_scroller.setWidgetResizable(True)
        self.grid_scroller.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.grid_container = GridContainer(self)
        self.grid_bar = BarOverlay(self)
        self.grid_bar.setParent(self.grid_container)
        self.grid_container.installEventFilter(self)   # keep the overlay sized
        self.grid_scroller.setWidget(self.grid_container)
        self._grid_index = self.preview_stack.addWidget(self.grid_scroller)

        # text (rename) surface
        self.text_label = QLabel()
        self.text_label.setAlignment(Qt.AlignCenter)
        self.text_label.setWordWrap(True)
        self.text_label.setTextFormat(Qt.RichText)
        self._text_index = self.preview_stack.addWidget(self.text_label)

        # empty placeholder
        self.empty_label = QLabel(tr("Load files to see a preview"))
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.empty_label.setEnabled(False)
        self._empty_index = self.preview_stack.addWidget(self.empty_label)

        v.addWidget(self.preview_stack, 1)

        self.preview_btn = QPushButton(tr("Generate preview"))
        self.preview_btn.setVisible(False)
        self.preview_btn.clicked.connect(lambda: self._run_image_preview())
        pbrow = QHBoxLayout()
        pbrow.addStretch(1)
        pbrow.addWidget(self.preview_btn)
        pbrow.addStretch(1)
        v.addLayout(pbrow)

        # page navigation (multi-page PDFs)
        self.page_nav = QWidget()
        pn = QHBoxLayout(self.page_nav)
        pn.setContentsMargins(0, 0, 0, 0)
        pn.addStretch(1)
        pp = QToolButton()
        pp.setArrowType(Qt.UpArrow)
        pp.setToolTip(tr("Previous page"))
        pp.clicked.connect(lambda: self._step_page(-1))
        nx = QToolButton()
        nx.setArrowType(Qt.DownArrow)
        nx.setToolTip(tr("Next page"))
        nx.clicked.connect(lambda: self._step_page(1))
        self.page_label = QPushButton("Page 1 / 1")
        self.page_label.setFlat(True)
        self.page_label.setToolTip(tr("Click to jump to a page"))
        self.page_label.clicked.connect(self._open_page_jump)
        pn.addWidget(pp)
        pn.addWidget(self.page_label)
        pn.addWidget(nx)
        pn.addStretch(1)
        v.addWidget(self.page_nav)

        self.zone_hint = QLabel("")
        self.zone_hint.setAlignment(Qt.AlignCenter)
        self.zone_hint.setEnabled(False)
        self.zone_hint.setVisible(False)
        v.addWidget(self.zone_hint)

        # file navigation — a centered compact group, matching the page-nav row
        nav = QHBoxLayout()
        nav.addStretch(1)
        prev = QToolButton()
        prev.setArrowType(Qt.LeftArrow)
        prev.clicked.connect(lambda: self._step_preview(-1))
        nxt = QToolButton()
        nxt.setArrowType(Qt.RightArrow)
        nxt.clicked.connect(lambda: self._step_preview(1))
        self.fname_label = QLabel("")
        self.fname_label.setTextFormat(Qt.RichText)
        self.fname_label.setAlignment(Qt.AlignCenter)
        self.fname_label.setMaximumWidth(560)
        nav.addWidget(prev)
        nav.addWidget(self.fname_label)
        nav.addWidget(nxt)
        nav.addStretch(1)
        v.addLayout(nav)

        self.counter_label = QLabel("0 / 0")
        self.counter_label.setAlignment(Qt.AlignCenter)
        self.counter_label.setEnabled(False)
        v.addWidget(self.counter_label)
        return box

    def eventFilter(self, obj, event):
        # keep the click-through bar overlay covering the whole grid container
        if obj is getattr(self, "grid_container", None) and event.type() == event.Type.Resize:
            self.grid_bar.setGeometry(0, 0, self.grid_container.width(),
                                      self.grid_container.height())
            self.grid_bar.raise_()
        return super().eventFilter(obj, event)

    def _show_stack(self, name):
        idx = {"image": self._canvas_index, "grid": self._grid_index,
               "text": self._text_index, "empty": self._empty_index}[name]
        self.preview_stack.setCurrentIndex(idx)

    def _invoke(self, fn):
        self._bridge.call.emit(fn)

    # ---- preview navigation ------------------------------------------------
    def _current_file(self):
        if not self.files:
            return None
        self.preview_index = max(0, min(self.preview_index, len(self.files) - 1))
        return self.files[self.preview_index]

    def _step_preview(self, delta):
        if not self.files:
            return
        self.preview_index = (self.preview_index + delta) % len(self.files)
        self.page_index = 0
        self._detected = []
        self._refresh_preview()

    def _step_page(self, delta):
        if self.page_count <= 1:
            return
        self.page_index = (self.page_index + delta) % self.page_count
        self._detected = []
        self._refresh_preview()

    def _open_page_jump(self):
        from PySide6.QtWidgets import QInputDialog
        n = max(self.page_count, 1)
        val, ok = QInputDialog.getInt(self, tr("Go to page"), tr("Go to page"),
                                      self.page_index + 1, 1, n, 1)
        if ok:
            target = max(0, min(val - 1, self.page_count - 1))
            if target != self.page_index:
                self.page_index = target
                self._detected = []
                self._refresh_preview()

    def _page_dims(self, f):
        try:
            if is_pdf(f):
                R = fitz.open(f)[self.page_index].rect
                return float(R.width), float(R.height)
            w, h = Image.open(f).size
            return float(w), float(h)
        except Exception:
            return 1.0, 1.0

    def _preview_context(self, f=None):
        if f is None:
            f = self._current_file()
        pw, ph = self._page_dims(f) if f is not None else (1.0, 1.0)
        return {"files": [str(x) for x in self.files], "index": self.preview_index,
                "page_index": self.page_index, "page_w": pw, "page_h": ph}

    def _refresh_preview(self):
        if self._suppress_refresh:
            return
        self._apply_enable_rules()
        self._apply_visible_rules()
        self._handles = []
        f = self._current_file()
        spec = self.tools.get(self.tool)
        if f is None or spec is None:
            self._show_stack("empty")
            self.fname_label.setText("")
            self.counter_label.setText("0 / 0")
            self.page_nav.setVisible(False)
            self.preview_btn.setVisible(False)
            self.zone_hint.setVisible(False)
            return
        self.counter_label.setText(f"{self.preview_index + 1} / {len(self.files)}")
        if is_pdf(f):
            try:
                self.page_count = fitz.open(f).page_count
            except Exception:
                self.page_count = 1
        else:
            self.page_count = 1
        self.page_index = max(0, min(self.page_index, self.page_count - 1))
        self.page_nav.setVisible(self.page_count > 1)
        self.page_label.setText(f"Page {self.page_index + 1} / {self.page_count}")

        opts = self._read_opts()
        context = self._preview_context(f)
        self._detected = getattr(self, "_detected", [])
        self._update_filename_label(f, spec, opts, context)
        self._compare_mode = False

        # 0) thumbnail-grid surface
        if self._tool_preview_kind(spec, opts) == "grid" and spec.has_preview_grid:
            self._show_grid(spec, opts, context)
            return

        self.zone_hint.setVisible(self._regions_enabled)
        if self._regions_enabled:
            self.zone_hint.setText(self._zone_hint_text or
                                   "Zones are per-page · drag to add · right-click to delete · "
                                   "pages with no zones are skipped")

        # 1) cheap overlay / text preview
        ov = None
        if spec.has_preview:
            try:
                ov = spec.module.preview(str(f), self.page_index, opts, context)
            except Exception:
                traceback.print_exc()
                ov = None
        if ov and "text" in ov:
            old = f.name
            new = self._predicted_name(f, spec, opts, context)
            self.preview_btn.setVisible(False)
            self._show_stack("text")
            self.text_label.setText(
                f"<div style='font-size:18px;color:{RED}'>{_esc(str(old))}</div>"
                f"<div style='font-size:22px'>↓</div>"
                f"<div style='font-size:18px;color:{GREEN}'>{_esc(str(new))}</div>")
            return

        has_heavy = spec.has_preview_image or spec.has_preview_regions
        show_button = has_heavy and self._wants_button(spec, f, opts)
        self.preview_btn.setVisible(show_button)

        # 2) image-replacing preview cached
        key = (spec.id, str(f), self.page_index)
        if spec.has_preview_image and key in self._img_cache:
            surf = self._img_cache[key]
            if spec.has_compare:
                self._render_source_base(f)
                self._arm_compare(self._base_qimage, surf)
            self._base_qimage = surf
            self._overlay = None
            self._show_stack("image")
            self.canvas.update()
            return

        self._show_stack("image")
        self._render_source_base(f)
        self._overlay = self._overlay_from_spec(ov)
        self._handles = list((ov or {}).get("handles") or [])
        self.canvas.update()
        if has_heavy and not show_button:
            self._run_image_preview()

    def _recompute_overlay_only(self):
        f = self._current_file()
        spec = self.tools.get(self.tool)
        if f is None or spec is None or not spec.has_preview:
            self.canvas.update()
            return
        try:
            ov = spec.module.preview(str(f), self.page_index,
                                     self._read_opts(), self._preview_context(f))
        except Exception:
            traceback.print_exc()
            ov = None
        self._overlay = self._overlay_from_spec(ov)
        self._handles = list((ov or {}).get("handles") or [])
        self.canvas.update()

    def _tool_preview_kind(self, spec, opts):
        if spec is None:
            return "canvas"
        fn = getattr(spec.module, "preview_kind", None)
        if callable(fn):
            f = self._current_file()
            try:
                return fn(str(f) if f is not None else None, opts) or "canvas"
            except Exception:
                traceback.print_exc()
                return "canvas"
        return spec.preview_kind_default

    def _file_page_count(self, fp):
        key = str(fp)
        if key in self._pagecount_cache:
            return self._pagecount_cache[key]
        n = 1
        if is_pdf(fp):
            try:
                d = fitz.open(fp)
                n = d.page_count
                d.close()
            except Exception:
                n = 1
        self._pagecount_cache[key] = n
        return n

    def _wants_button(self, spec, f, opts):
        if spec.has_needs_preview_button:
            try:
                return bool(spec.module.needs_preview_button(str(f), self.page_index, opts))
            except Exception:
                traceback.print_exc()
        return spec.preview_on_demand

    def _update_filename_label(self, f, spec, opts, context):
        new = self._predicted_name(f, spec, opts, context)
        old = f.name
        if new and new != old:
            self.fname_label.setText(
                f"<span style='color:{RED}'>{_esc(old)}</span>"
                f"  →  <span style='color:{GREEN}'>{_esc(new)}</span>")
        else:
            self.fname_label.setText(_esc(old))

    def _predicted_name(self, f, spec, opts, context):
        stem, suffix = f.stem, f.suffix
        try:
            if spec.has_preview:
                ov = spec.module.preview(str(f), self.page_index, opts, context)
                if ov and "text" in ov:
                    p = Path(str(ov["text"][1]))
                    stem, suffix = p.stem, (p.suffix or suffix)
        except Exception:
            pass
        naming = self._naming_config()
        if naming:
            stem = self._name_with_number(stem, self.preview_index + 1, naming)
        return f"{stem}{suffix}"

    def _render_source_base(self, f):
        key = (str(f), self.page_index)
        cached = self._base_cache.get(key)
        if cached is not None:
            self._base_qimage, self._unit_scale, self._surf_w, self._surf_h = cached
            return
        self._base_qimage = None
        self._unit_scale = 1.0
        try:
            if is_pdf(f):
                page = fitz.open(f)[self.page_index]
                zoom = PREVIEW_DPI / 72
                pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
                img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
                self._unit_scale = zoom
            else:
                orig = Image.open(f).convert("RGB")
                img = orig.copy()
                img.thumbnail((1600, 1600))
                self._unit_scale = img.width / orig.width
            self._surf_w, self._surf_h = img.width, img.height
            self._base_qimage = pil_to_qimage(img)
            self._base_cache[key] = (self._base_qimage, self._unit_scale,
                                     self._surf_w, self._surf_h)
            if len(self._base_cache) > 8:
                self._base_cache.pop(next(iter(self._base_cache)))
        except Exception:
            traceback.print_exc()

    def _overlay_from_spec(self, ov):
        self._overlay_style = {}
        if not ov:
            return None
        default_dash = True if "boxes" in ov else False
        self._overlay_style = {
            "color": parse_rgb(ov.get("color"), OVERLAY_COLOR),
            "fill": parse_rgb(ov["fill"], OVERLAY_COLOR) if ov.get("fill") else None,
            "dash": ov.get("dash", default_dash),
        }
        if "grid" in ov:
            c, r = ov["grid"]
            return ("grid", int(c), int(r))

        def to_px(x0, y0, x1, y1, space):
            if space == "fraction":
                return (x0 * self._surf_w, y0 * self._surf_h,
                        x1 * self._surf_w, y1 * self._surf_h)
            s = self._unit_scale
            return (x0 * s, y0 * s, x1 * s, y1 * s)

        if "cropgrid" in ov:
            cg = ov["cropgrid"]
            space = ov.get("space", "pixels")
            x0, y0, x1, y1 = to_px(*cg["rect"], space)
            gut = float(cg.get("gutter", 0) or 0)
            gut *= self._surf_w if space == "fraction" else self._unit_scale
            return ("cropgrid", x0, y0, x1, y1, int(cg["cols"]), int(cg["rows"]), gut)
        if "rect" in ov:
            return ("rect",) + to_px(*ov["rect"], ov.get("space", "pixels"))
        if "boxes" in ov:
            space = ov.get("space", "fraction")
            return ("boxes", [to_px(*b, space) for b in ov["boxes"]])
        return None

    def _zone_order(self, boxes):
        spec = self.tools.get(self.tool)
        order_key = None
        if spec:
            for o in spec.options_meta:
                if o.get("type") == "regions" and o.get("order_key"):
                    order_key = o["order_key"]
        as_drawn = True
        if order_key:
            val = str(self._read_opts().get(order_key, "")).lower()
            as_drawn = "drawn" in val
        if as_drawn or not boxes:
            return list(range(1, len(boxes) + 1))
        indexed = reading_order(boxes)
        rank = [0] * len(boxes)
        for pos, idx in enumerate(indexed, start=1):
            rank[idx] = pos
        return rank

    # ---- canvas rendering --------------------------------------------------
    def _accent_rgb(self):
        """The native OS/theme accent colour (Windows highlight) as an (r,g,b)
        0..1 tuple, so overlays like the compare divider match the desktop."""
        try:
            c = self.palette().color(QPalette.Highlight)
            return (c.redF(), c.greenF(), c.blueF())
        except Exception:
            return parse_rgb("#3584e4", OVERLAY_COLOR)

    @staticmethod
    def _qcol(rgb, a=1.0):
        r, g, b = rgb
        c = QColor(int(r * 255), int(g * 255), int(b * 255))
        c.setAlphaF(a)
        return c

    def _paint_canvas(self, p, width, height):
        if (self._compare_mode and self._compare_before is not None
                and self._compare_after is not None):
            self._draw_compare(p, width, height)
            return
        qimg = self._base_qimage
        if qimg is None:
            return
        sw, sh = qimg.width(), qimg.height()
        self._surf_w, self._surf_h = sw, sh
        scale = min(width / sw, height / sh) * 0.96
        ox = (width - sw * scale) / 2
        oy = (height - sh * scale) / 2
        self._last_scale, self._last_ox, self._last_oy = scale, ox, oy
        p.drawImage(QRectF(ox, oy, sw * scale, sh * scale), qimg)
        inv = (1.0 / scale) if scale else 1.0

        def cos_pen(rgb, w=2.0, a=0.95, dash=False):
            pen = QPen(self._qcol(rgb, a))
            pen.setWidthF(w)
            pen.setCosmetic(True)
            pen.setStyle(Qt.DashLine if dash else Qt.SolidLine)
            return pen

        p.save()
        p.translate(ox, oy)
        p.scale(scale, scale)

        if self._overlay:
            style = self._overlay_style or {}
            rgb = style.get("color") or parse_rgb(None, OVERLAY_COLOR)
            kind = self._overlay[0]
            if kind == "grid":
                p.setPen(cos_pen(rgb, 2))
                _, cols, rows = self._overlay
                for c in range(1, cols):
                    p.drawLine(QPointF(sw * c / cols, 0), QPointF(sw * c / cols, sh))
                for row in range(1, rows):
                    p.drawLine(QPointF(0, sh * row / rows), QPointF(sw, sh * row / rows))
            elif kind == "cropgrid":
                _, x0, y0, x1, y1, cols, rows, gut = self._overlay
                cw, ch = (x1 - x0) / cols, (y1 - y0) / rows
                if gut > 0:
                    ins = gut / 2.0
                    tiles = []
                    for ri in range(rows):
                        for ci in range(cols):
                            tx0 = x0 + ci * cw + ins
                            ty0 = y0 + ri * ch + ins
                            tx1 = x0 + (ci + 1) * cw - ins
                            ty1 = y0 + (ri + 1) * ch - ins
                            if tx1 > tx0 and ty1 > ty0:
                                tiles.append((tx0, ty0, tx1, ty1))
                else:
                    tiles = [(x0, y0, x1, y1)]
                # wash + hatch everything except the kept tiles (even-odd clip)
                from PySide6.QtGui import QPainterPath
                outer = QPainterPath()
                outer.addRect(QRectF(0, 0, sw, sh))
                for (tx0, ty0, tx1, ty1) in tiles:
                    inner = QPainterPath()
                    inner.addRect(QRectF(tx0, ty0, tx1 - tx0, ty1 - ty0))
                    outer = outer.subtracted(inner)
                p.save()
                p.setClipPath(outer)
                p.fillRect(QRectF(0, 0, sw, sh), self._qcol(rgb, 0.12))
                hp = QPen(self._qcol(rgb, 0.55))
                hp.setWidthF(1)
                hp.setCosmetic(True)
                p.setPen(hp)
                step = 12 * inv
                hx = -sh
                while hx < sw:
                    p.drawLine(QPointF(hx, 0), QPointF(hx + sh, sh))
                    hx += step
                p.restore()
                p.setPen(cos_pen(rgb, 2))
                p.drawRect(QRectF(x0, y0, x1 - x0, y1 - y0))
                if gut > 0:
                    p.setPen(cos_pen(rgb, 1.5))
                    for (tx0, ty0, tx1, ty1) in tiles:
                        p.drawRect(QRectF(tx0, ty0, tx1 - tx0, ty1 - ty0))
                else:
                    p.setPen(cos_pen(rgb, 2))
                    for c in range(1, cols):
                        gx = x0 + (x1 - x0) * c / cols
                        p.drawLine(QPointF(gx, y0), QPointF(gx, y1))
                    for row in range(1, rows):
                        gy = y0 + (y1 - y0) * row / rows
                        p.drawLine(QPointF(x0, gy), QPointF(x1, gy))
            elif kind == "rect":
                p.setPen(cos_pen(rgb, 2))
                _, x0, y0, x1, y1 = self._overlay
                p.drawRect(QRectF(x0, y0, x1 - x0, y1 - y0))
            elif kind == "boxes":
                dash = bool(style.get("dash", True))
                fill = style.get("fill")
                for (x0, y0, x1, y1) in self._overlay[1]:
                    if fill:
                        p.fillRect(QRectF(x0, y0, x1 - x0, y1 - y0), self._qcol(fill, 0.12))
                    p.setPen(cos_pen(rgb, 2, dash=dash))
                    p.drawRect(QRectF(x0, y0, x1 - x0, y1 - y0))

        # draggable handles
        for h in getattr(self, "_handles", None) or []:
            hrgb = parse_rgb(h.get("color"), OVERLAY_COLOR)
            dash = bool(h.get("dash"))
            p.setPen(cos_pen(hrgb, 2, dash=dash))
            kind = h.get("kind")
            if kind == "vline":
                x = float(h.get("x", 0.0)) * sw
                p.drawLine(QPointF(x, 0), QPointF(x, sh))
            elif kind == "hline":
                y = float(h.get("y", 0.0)) * sh
                p.drawLine(QPointF(0, y), QPointF(sw, y))
            elif kind == "point":
                x = float(h.get("x", 0.0)) * sw
                y = float(h.get("y", 0.0)) * sh
                r = 6 * inv
                if dash:
                    p.setBrush(Qt.NoBrush)
                else:
                    p.setBrush(QBrush(self._qcol(hrgb, 0.95)))
                p.drawEllipse(QPointF(x, y), r, r)
                p.setBrush(Qt.NoBrush)

        # detected regions (heavy preview)
        if getattr(self, "_detected", None):
            style = self._detected_style or {}
            rgb = style.get("color") or parse_rgb(None, DETECT_COLOR)
            dash = bool(style.get("dash", False))
            for (x0, y0, x1, y1) in self._detected:
                p.fillRect(QRectF(x0 * sw, y0 * sh, (x1 - x0) * sw, (y1 - y0) * sh),
                           self._qcol(rgb, 0.12))
                p.setPen(cos_pen(rgb, 2, dash=dash))
                p.drawRect(QRectF(x0 * sw, y0 * sh, (x1 - x0) * sw, (y1 - y0) * sh))

        # user-drawn zones
        if self._regions_enabled:
            zrgb = (self._zone_style or {}).get("color") or parse_rgb(None, ZONE_COLOR)
            boxes = list(self._regions.get(self.page_index, []))
            order = self._zone_order(boxes)
            if self._drawing:
                boxes = boxes + [tuple(self._drawing)]
            for i, (x0, y0, x1, y1) in enumerate(boxes):
                rx, ry = min(x0, x1) * sw, min(y0, y1) * sh
                rw, rh = abs(x1 - x0) * sw, abs(y1 - y0) * sh
                p.fillRect(QRectF(rx, ry, rw, rh), self._qcol(zrgb, 0.15))
                p.setPen(cos_pen(zrgb, 2))
                p.drawRect(QRectF(rx, ry, rw, rh))
                if i < len(order):
                    p.save()
                    p.resetTransform()
                    p.setPen(self._qcol(zrgb, 0.95))
                    fnt = QFont()
                    fnt.setPixelSize(15)
                    p.setFont(fnt)
                    dx = ox + (rx + rw) * scale - 14
                    dy = oy + ry * scale + 16
                    p.drawText(QPointF(dx, dy), str(order[i]))
                    p.restore()
        p.restore()

    def _draw_compare(self, p, width, height):
        before, after = self._compare_before, self._compare_after
        aw, ah = after.width(), after.height()
        scale = min(width / aw, height / ah) * 0.96
        dw, dh = aw * scale, ah * scale
        ox, oy = (width - dw) / 2, (height - dh) / 2
        self._compare_rect = (ox, oy, dw, dh)
        div = max(0.0, min(1.0, self._compare_x))
        divx = ox + dw * div

        p.drawImage(QRectF(ox, oy, dw, dh), after)
        if dw * div > 0.5:
            p.save()
            p.setClipRect(QRectF(ox, oy, dw * div, dh))
            p.drawImage(QRectF(ox, oy, dw, dh), before)
            p.restore()

        acc = self._accent_rgb()
        pen = QPen(QColor(255, 255, 255, 230))
        pen.setWidthF(3)
        p.setPen(pen)
        p.drawLine(QPointF(divx, oy), QPointF(divx, oy + dh))
        pen2 = QPen(self._qcol(acc, 0.95))
        pen2.setWidthF(1.5)
        p.setPen(pen2)
        p.drawLine(QPointF(divx, oy), QPointF(divx, oy + dh))
        cy = oy + dh / 2
        p.setBrush(QColor(255, 255, 255, 242))
        p.setPen(Qt.NoPen)
        p.drawEllipse(QPointF(divx, cy), 10, 10)
        p.setBrush(Qt.NoBrush)
        pen3 = QPen(self._qcol(acc, 0.95))
        pen3.setWidthF(2)
        p.setPen(pen3)
        p.drawEllipse(QPointF(divx, cy), 10, 10)

        p.setFont(QFont("Sans", 10))
        for label, tx, right in (("Before", ox + 8, False), ("After", ox + dw - 8, True)):
            fm = QFontMetrics(p.font())
            w = fm.horizontalAdvance(label)
            px = tx - w if right else tx
            p.fillRect(QRectF(px - 4, oy + 6, w + 8, 20), QColor(0, 0, 0, 140))
            p.setPen(QColor(255, 255, 255, 242))
            p.drawText(QPointF(px, oy + 21), label)

    # ---- canvas coordinate + hit-testing ----------------------------------
    def _widget_to_frac(self, wx, wy):
        sc = self._last_scale or 1.0
        fx = (wx - self._last_ox) / (self._surf_w * sc) if self._surf_w else 0
        fy = (wy - self._last_oy) / (self._surf_h * sc) if self._surf_h else 0
        return max(0.0, min(1.0, fx)), max(0.0, min(1.0, fy))

    def _corner_at(self, box, fx, fy):
        return self._zone_hit(box, fx, fy)

    def _zone_hit(self, box, fx, fy):
        sc = self._last_scale or 1.0
        tol_x = 16 / max(1.0, self._surf_w * sc)
        tol_y = 16 / max(1.0, self._surf_h * sc)
        x0, y0, x1, y1 = box
        x0, x1 = sorted((x0, x1))
        y0, y1 = sorted((y0, y1))
        if not (x0 - tol_x <= fx <= x1 + tol_x and y0 - tol_y <= fy <= y1 + tol_y):
            return None
        near_l, near_r = abs(fx - x0) <= tol_x, abs(fx - x1) <= tol_x
        near_t, near_b = abs(fy - y0) <= tol_y, abs(fy - y1) <= tol_y
        if near_t and near_l:
            return "nw"
        if near_t and near_r:
            return "ne"
        if near_b and near_l:
            return "sw"
        if near_b and near_r:
            return "se"
        if near_t:
            return "n"
        if near_b:
            return "s"
        if near_l:
            return "w"
        if near_r:
            return "e"
        if x0 <= fx <= x1 and y0 <= fy <= y1:
            return "move"
        return None

    def _handle_at(self, fx, fy):
        handles = getattr(self, "_handles", None)
        if not handles:
            return None
        sc = self._last_scale or 1.0
        tol_x = 12 / max(1.0, self._surf_w * sc)
        tol_y = 12 / max(1.0, self._surf_h * sc)
        best, best_d = None, 1e9
        for h in handles:
            kind = h.get("kind")
            if kind == "vline":
                d, tol = abs(fx - float(h.get("x", 0.0))), tol_x
            elif kind == "hline":
                d, tol = abs(fy - float(h.get("y", 0.0))), tol_y
            elif kind == "point":
                d = max(abs(fx - float(h.get("x", 0.0))), abs(fy - float(h.get("y", 0.0))))
                tol = max(tol_x, tol_y)
            else:
                continue
            if d <= tol and d < best_d:
                best, best_d = h, d
        return best

    _CURSOR_FOR_KIND = {"vline": Qt.SizeHorCursor, "hline": Qt.SizeVerCursor,
                        "point": Qt.SizeAllCursor}
    _ZONE_CURSOR = {"nw": Qt.SizeFDiagCursor, "se": Qt.SizeFDiagCursor,
                    "ne": Qt.SizeBDiagCursor, "sw": Qt.SizeBDiagCursor,
                    "n": Qt.SizeVerCursor, "s": Qt.SizeVerCursor,
                    "e": Qt.SizeHorCursor, "w": Qt.SizeHorCursor,
                    "move": Qt.SizeAllCursor}

    def _set_canvas_cursor(self, shape):
        if shape == self._cursor_name:
            return
        self._cursor_name = shape
        if shape is None:
            self.canvas.unsetCursor()
        else:
            self.canvas.setCursor(QCursor(shape))

    def _zone_cursor(self, fx, fy):
        page = self._regions.get(self.page_index, [])
        for i in range(len(page) - 1, -1, -1):
            mode = self._zone_hit(tuple(page[i]), fx, fy)
            if mode:
                return self._ZONE_CURSOR.get(mode)
        return None

    # ---- canvas pointer events --------------------------------------------
    def _canvas_hover(self, wx, wy):
        if (self._dragging_handle is not None or self._base_qimage is None
                or self._resizing is not None or self._moving is not None
                or self._drawing is not None):
            return
        fx, fy = self._widget_to_frac(wx, wy)
        h = self._handle_at(fx, fy)
        shape = self._CURSOR_FOR_KIND.get(h.get("kind")) if h else None
        if shape is None and self._regions_enabled:
            shape = self._zone_cursor(fx, fy)
        self._set_canvas_cursor(shape)

    def _canvas_press(self, wx, wy):
        if self._compare_mode:
            self._compare_drag = True
            self._update_compare_x(wx)
            return
        if self._base_qimage is None:
            return
        self._compare_drag = False
        fx, fy = self._widget_to_frac(wx, wy)
        self._dragging_handle = None
        h = self._handle_at(fx, fy)
        if h is not None:
            self._dragging_handle = h.get("id")
            self._moving = self._resizing = self._drawing = None
            return
        if not self._regions_enabled:
            return
        self._moving = self._resizing = self._drawing = None
        page = self._regions.get(self.page_index, [])
        found = None
        for i in range(len(page) - 1, -1, -1):
            mode = self._zone_hit(tuple(page[i]), fx, fy)
            if mode:
                found = (i, mode)
                break
        if found is not None:
            i, mode = found
            box = tuple(page[i])
            if mode == "move":
                self._moving = {"index": i, "orig": box, "sx": fx, "sy": fy}
            else:
                self._resizing = {"index": i, "mode": mode, "orig": box}
        else:
            self._drawing = [fx, fy, fx, fy]
        self.canvas.update()

    def _canvas_drag(self, wx, wy):
        if self._compare_drag:
            self._update_compare_x(wx)
            return
        fx, fy = self._widget_to_frac(wx, wy)
        if self._dragging_handle is not None:
            self._apply_handle_drag(self._dragging_handle, fx, fy)
            return
        if not self._regions_enabled:
            return
        page = self._regions.get(self.page_index, [])
        if self._resizing is not None:
            i = self._resizing["index"]
            if not (0 <= i < len(page)):
                return
            ox0, oy0, ox1, oy1 = self._resizing["orig"]
            ox0, ox1 = sorted((ox0, ox1))
            oy0, oy1 = sorted((oy0, oy1))
            mode = self._resizing["mode"]
            nx0, ny0, nx1, ny1 = ox0, oy0, ox1, oy1
            if "w" in mode:
                nx0 = fx
            if "e" in mode:
                nx1 = fx
            if "n" in mode:
                ny0 = fy
            if "s" in mode:
                ny1 = fy
            page[i] = (nx0, ny0, nx1, ny1)
            self.canvas.update()
            return
        if self._moving is not None:
            i = self._moving["index"]
            if not (0 <= i < len(page)):
                return
            ox0, oy0, ox1, oy1 = self._moving["orig"]
            w, h = ox1 - ox0, oy1 - oy0
            nx0 = min(max(0.0, ox0 + (fx - self._moving["sx"])), max(0.0, 1.0 - w))
            ny0 = min(max(0.0, oy0 + (fy - self._moving["sy"])), max(0.0, 1.0 - h))
            page[i] = (nx0, ny0, nx0 + w, ny0 + h)
            self.canvas.update()
            return
        if self._drawing is None:
            return
        self._drawing[2], self._drawing[3] = fx, fy
        self.canvas.update()

    def _canvas_release(self, wx, wy):
        if self._compare_drag:
            self._compare_drag = False
            return
        if self._dragging_handle is not None:
            self._dragging_handle = None
            self._refresh_preview()
            return
        if not self._regions_enabled:
            return
        page = self._regions.get(self.page_index, [])
        if self._resizing is not None:
            i = self._resizing["index"]
            if 0 <= i < len(page):
                x0, y0, x1, y1 = page[i]
                x0, x1 = sorted((max(0.0, min(1.0, x0)), max(0.0, min(1.0, x1))))
                y0, y1 = sorted((max(0.0, min(1.0, y0)), max(0.0, min(1.0, y1))))
                if (x1 - x0) > 0.02 and (y1 - y0) > 0.02:
                    page[i] = (x0, y0, x1, y1)
                else:
                    page[i] = self._resizing["orig"]
            self._resizing = None
            self.canvas.update()
            return
        if self._moving is not None:
            self._moving = None
            self.canvas.update()
            return
        if self._drawing is None:
            return
        x0, y0, x1, y1 = self._drawing
        self._drawing = None
        x0, x1 = sorted((x0, x1))
        y0, y1 = sorted((y0, y1))
        if (x1 - x0) > 0.02 and (y1 - y0) > 0.02:
            self._regions.setdefault(self.page_index, []).append((x0, y0, x1, y1))
        self.canvas.update()

    def _canvas_right_click(self, wx, wy):
        page = self._regions.get(self.page_index)
        if not self._regions_enabled or not page:
            return
        fx, fy = self._widget_to_frac(wx, wy)
        for i in range(len(page) - 1, -1, -1):
            x0, y0, x1, y1 = page[i]
            if min(x0, x1) <= fx <= max(x0, x1) and min(y0, y1) <= fy <= max(y0, y1):
                del page[i]
                self.canvas.update()
                return

    def _apply_handle_drag(self, handle_id, fx, fy):
        spec = self.tools.get(self.tool)
        fn = getattr(spec.module, "on_handle_drag", None) if spec else None
        if fn is None:
            return
        try:
            writes = fn(handle_id, fx, fy, self._read_opts(), self._preview_context()) or {}
        except Exception:
            traceback.print_exc()
            return
        self._suppress_refresh = True
        try:
            for k, v in writes.items():
                self._set_option(k, v)
        finally:
            self._suppress_refresh = False
        self._recompute_overlay_only()

    def _update_compare_x(self, wx):
        rect = self._compare_rect
        if not rect:
            return
        ox, _oy, dw, _dh = rect
        self._compare_x = max(0.0, min(1.0, (wx - ox) / dw)) if dw else 0.5
        self.canvas.update()

    def _arm_compare(self, before, after):
        if before is None or after is None:
            self._compare_mode = False
            return
        self._compare_before = before
        self._compare_after = after
        self._compare_mode = True

    def _copy_zones_to_all(self):
        cur = self._regions.get(self.page_index, [])
        if not cur:
            return
        n = max(self.page_count, 1)
        for pg in range(n):
            self._regions[pg] = list(cur)
        self.canvas.update()

    # ---- heavy image / region preview (background thread) ------------------
    def _run_image_preview(self):
        f = self._current_file()
        spec = self.tools.get(self.tool)
        if f is None or spec is None:
            return
        page_index = self.page_index
        opts = self._read_opts()
        self.preview_btn.setText(tr("Working…"))
        self.preview_btn.setEnabled(False)

        def work():
            try:
                if spec.has_preview_regions:
                    result = spec.module.preview_regions(str(f), page_index, opts)
                    boxes, style = self._parse_detected(result)
                    self._invoke(lambda: self._apply_detected(
                        (spec.id, str(f), page_index), boxes, style))
                elif spec.has_preview_image:
                    img = spec.module.preview_image(str(f), page_index, opts)
                    if img.mode == "RGBA":
                        img = composite_on_checker(img)
                    surf = pil_to_qimage(img)
                    self._invoke(lambda: self._apply_image_preview(
                        (spec.id, str(f), page_index), surf))
                else:
                    self._invoke(lambda: self._image_preview_error(
                        "This tool has no preview to generate."))
            except Exception as e:
                traceback.print_exc()
                self._invoke(lambda e=e: self._image_preview_error(str(e)))

        threading.Thread(target=work, daemon=True).start()

    @staticmethod
    def _parse_detected(result):
        if isinstance(result, dict):
            boxes = result.get("boxes") or []
            style = {"color": parse_rgb(result.get("color"), DETECT_COLOR),
                     "dash": result.get("dash", False)}
            return boxes, style
        return (result or []), {}

    def _apply_detected(self, key, boxes, style=None):
        self.preview_btn.setText(tr("Generate preview"))
        self.preview_btn.setEnabled(True)
        cur = self._current_file()
        if cur and key == (self.tool, str(cur), self.page_index):
            self._detected = boxes
            self._detected_style = style or {}
            self.canvas.update()

    def _apply_image_preview(self, key, surf):
        self._img_cache[key] = surf
        self.preview_btn.setText(tr("Generate preview"))
        self.preview_btn.setEnabled(True)
        cur = self._current_file()
        if cur and key == (self.tool, str(cur), self.page_index):
            spec = self.tools.get(self.tool)
            if spec and spec.has_compare:
                base = self._base_cache.get((str(cur), self.page_index))
                before = base[0] if base else self._base_qimage
                self._arm_compare(before, surf)
            self._base_qimage = surf
            self._overlay = None
            self._show_stack("image")
            self.canvas.update()

    def _image_preview_error(self, msg):
        self.preview_btn.setText(tr("Generate preview"))
        self.preview_btn.setEnabled(True)
        self._toast(f"Preview failed: {msg}")

    # ---- thumbnail-grid surface -------------------------------------------
    def _show_grid(self, spec, opts, context):
        self._show_stack("grid")
        self.page_nav.setVisible(False)
        self.zone_hint.setVisible(False)
        self.preview_btn.setVisible(False)
        try:
            gspec = spec.module.preview_grid(context, opts) or {}
        except Exception:
            traceback.print_exc()
            gspec = {}
        self._grid_spec = gspec
        source = gspec.get("source", "files")
        items = []
        if source == "pages":
            for fi, fp in enumerate(self.files):
                pdf = is_pdf(fp)
                n = self._file_page_count(fp) if pdf else 1
                name = Path(fp).name
                for pg in range(n):
                    cap = f"{name} · p{pg + 1}" if pdf else name
                    items.append({"kind": "pdf-page" if pdf else "image",
                                  "path": str(fp), "page": pg,
                                  "file": fi, "pageno": (pg if pdf else None),
                                  "caption": cap, "key": (str(fp), pg)})
        else:
            for fi, fp in enumerate(self.files):
                items.append({"kind": "pdf" if is_pdf(fp) else "image",
                              "path": str(fp), "page": 0,
                              "file": fi, "pageno": None,
                              "caption": Path(fp).name, "key": (str(fp), None)})
        live = {str(fp) for fp in self.files}
        self._grid_deleted = {k for k in getattr(self, "_grid_deleted", set())
                              if k[0] in live}
        if self._grid_deleted:
            items = [it for it in items if it["key"] not in self._grid_deleted]
        for i, it in enumerate(items):
            it["orig"] = i
        self._grid_items = items
        full = list(range(len(items)))
        key_to_idx = {it["key"]: i for i, it in enumerate(items)}
        prev_keys = getattr(self, "_grid_unit_keys", {}) or {}

        order = gspec.get("order")
        if isinstance(order, list) and sorted(order) == full:
            self._grid_order = list(order)
        else:
            seen, new_order = set(), []
            for o in self._grid_order:
                j = key_to_idx.get(prev_keys.get(o))
                if j is not None and j not in seen:
                    new_order.append(j)
                    seen.add(j)
            for j in full:
                if j not in seen:
                    new_order.append(j)
                    seen.add(j)
            self._grid_order = new_order or full

        sel = gspec.get("selected")
        if isinstance(sel, list):
            selset = {i for i in sel if i in full}
        else:
            selset = set()
            for o in self._grid_selected:
                j = key_to_idx.get(prev_keys.get(o))
                if j is not None:
                    selset.add(j)
        self._grid_selected = [o for o in self._grid_order if o in selset]
        self._grid_selectable = bool(gspec.get("selectable"))
        self._grid_reorderable = bool(gspec.get("reorderable"))
        self.grid_container.selectable = self._grid_selectable
        self.grid_container.reorderable = self._grid_reorderable

        self._grid_split_mode = bool(gspec.get("split_bars"))
        if self._grid_split_mode:
            sp = gspec.get("splits")
            self._grid_splits = {int(p) for p in sp} if isinstance(sp, list) else set()
        else:
            self._grid_splits = set()
        self._grid_gap = None
        if self._grid_split_mode:
            _dbg("grid render: split_mode=True selectable=%s splits=%s pages=%d"
                 % (bool(gspec.get("selectable")), sorted(self._grid_splits),
                    len(self._grid_order)))

        self._grid_anchor = key_to_idx.get(prev_keys.get(self._grid_anchor))
        if self._grid_anchor not in self._grid_order:
            self._grid_anchor = self._grid_order[0] if self._grid_order else None

        self._grid_unit_keys = {i: it["key"] for i, it in enumerate(items)}

        sig = (source, tuple(self._grid_order),
               tuple(self._grid_items[o]["caption"] for o in self._grid_order))
        if sig == self._grid_sig:
            self._apply_grid_selection()
        else:
            self._grid_sig = sig
            self._render_grid_children()
        if self._grid_split_mode:
            QTimer.singleShot(0, self.grid_bar.update)

    def _render_grid_children(self):
        vbar = self.grid_scroller.verticalScrollBar()
        keep_scroll = vbar.value() if vbar else 0
        _clear_layout(self.grid_container.flow)
        self._grid_cells = {}
        self._grid_gen += 1
        gen = self._grid_gen
        jobs = []
        for orig in self._grid_order:
            it = self._grid_items[orig]
            cell = GridCell(self.grid_container, orig, it["caption"])
            self.grid_container.flow.addWidget(cell)
            self._grid_cells[orig] = cell
            ck = f"{it['kind']}::{it['path']}::{it['page']}"
            png = self._thumb_cache.get(ck)
            if png and Path(png).exists():
                cell.set_pixmap(QPixmap(png))
            else:
                jobs.append((orig, it["kind"], it["path"], it["page"], ck, gen))
        self._apply_grid_selection()
        self.grid_bar.raise_()
        if jobs:
            self._start_thumb_worker(jobs, gen)
        if keep_scroll:
            QTimer.singleShot(0, lambda: vbar.setValue(keep_scroll))

    def _apply_grid_selection(self):
        selset = set(self._grid_selected) if self._grid_selectable else set()
        for orig, cell in self._grid_cells.items():
            cell.set_selected(orig in selset)

    # -- selection (plain / Ctrl-toggle / Shift-range) --
    def _grid_cell_click(self, orig, modifiers):
        if not self._grid_selectable:
            return
        ctrl = bool(modifiers & Qt.ControlModifier)
        shift = bool(modifiers & Qt.ShiftModifier)
        order = self._grid_order
        if shift and self._grid_anchor in order:
            a, b = order.index(self._grid_anchor), order.index(orig)
            lo, hi = sorted((a, b))
            rng = order[lo:hi + 1]
            if ctrl:
                keep = set(self._grid_selected) | set(rng)
                self._grid_selected = [o for o in order if o in keep]
            else:
                self._grid_selected = rng
        elif ctrl:
            keep = set(self._grid_selected)
            keep.discard(orig) if orig in keep else keep.add(orig)
            self._grid_selected = [o for o in order if o in keep]
            self._grid_anchor = orig
        elif orig in self._grid_selected and len(self._grid_selected) > 1:
            self._grid_anchor = orig
        else:
            self._grid_selected = [orig]
            self._grid_anchor = orig
        self._apply_grid_selection()
        self._grid_fire_select()

    def _grid_fire_select(self):
        spec = self.tools.get(self.tool)
        fn = getattr(spec.module, "on_select", None) if spec else None
        if not callable(fn):
            return
        try:
            writes = fn(list(self._grid_selected), self._read_opts(),
                        self._preview_context()) or {}
        except Exception:
            traceback.print_exc()
            writes = {}
        if writes:
            self._suppress_refresh = True
            for k, v in writes.items():
                self._set_option(k, v)
            self._suppress_refresh = False

    def _grid_delete_selected(self):
        if not self._grid_reorderable or not self._grid_selected:
            return False
        remove = set(self._grid_selected)
        if len(remove) >= len(self._grid_order):
            return True
        units = getattr(self, "_grid_items", [])
        rem_keys = {units[o]["key"] for o in remove if 0 <= o < len(units)}
        self._grid_deleted = getattr(self, "_grid_deleted", set()) | rem_keys
        self._grid_order = [o for o in self._grid_order if o not in remove]
        self._grid_selected = []
        self._grid_anchor = self._grid_order[0] if self._grid_order else None
        self._grid_sig = None
        self._grid_fire_reorder()
        self._refresh_preview()
        return True

    def _grid_fire_reorder(self):
        spec = self.tools.get(self.tool)
        fn = getattr(spec.module, "on_reorder", None) if spec else None
        if callable(fn):
            try:
                fn(list(self._grid_order), self._read_opts(), self._preview_context())
            except Exception:
                traceback.print_exc()

    # -- insertion-bar geometry (hit-test the gap in a wrapping flow) --
    def _grid_compute_gap(self, px, py):
        order = self._grid_order
        if not order:
            return {"index": 0, "x": 6.0, "y0": 2.0, "y1": 150.0}
        rects = []
        for pos, orig in enumerate(order):
            cell = self._grid_cells.get(orig)
            if cell is None:
                continue
            r = cell.geometry()
            rects.append((pos, r.x(), r.y(), r.width(), r.height()))
        if not rects:
            return None
        rows = []
        for it in rects:
            for row in rows:
                if abs(row["y"] - it[2]) < 8:
                    row["items"].append(it)
                    break
            else:
                rows.append({"y": it[2], "items": [it]})
        rows.sort(key=lambda r: r["y"])
        row = None
        for rw in rows:
            top = min(i[2] for i in rw["items"])
            bot = max(i[2] + i[4] for i in rw["items"])
            if top - 6 <= py <= bot + 6:
                row = rw
                break
        if row is None:
            row = rows[-1] if py > rows[-1]["y"] else rows[0]
        items = sorted(row["items"], key=lambda i: i[1])
        top = min(i[2] for i in items)
        bot = max(i[2] + i[4] for i in items)
        for (pos, x, y, w, h) in items:
            if px < x + w / 2:
                return {"index": pos, "x": max(1.0, x - 5), "y0": top, "y1": bot}
        last = items[-1]
        return {"index": last[0] + 1, "x": last[1] + last[3] + 5, "y0": top, "y1": bot}

    def _grid_bar_clear(self):
        if self._grid_gap is not None:
            self._grid_gap = None
            self.grid_bar.update()

    def _grid_bar_motion(self, x, y):
        if not self._grid_reorderable:
            return
        self._grid_gap = self._grid_compute_gap(x, y)
        self.grid_bar.update()

    def _grid_reorder_drop(self, src_orig, x, y):
        if not self._grid_reorderable:
            return
        gap = self._grid_compute_gap(x, y)
        self._grid_bar_clear()
        if gap is None or src_orig not in self._grid_order:
            return
        sel = set(self._grid_selected)
        if src_orig in sel and len(sel) > 1:
            moving = [o for o in self._grid_order if o in sel]
        else:
            moving = [src_orig]
        move_set = set(moving)
        pos = gap["index"]
        order = list(self._grid_order)
        before = sum(1 for o in order[:pos] if o in move_set)
        rest = [o for o in order if o not in move_set]
        pos = max(0, min(len(rest), pos - before))
        self._grid_order = rest[:pos] + moving + rest[pos:]
        self._grid_selected = [o for o in self._grid_order if o in move_set]
        self._grid_anchor = moving[0]
        self._grid_sig = None
        self._grid_fire_reorder()
        self._refresh_preview()

    def _grid_file_drop(self, paths, x, y):
        resolved = []
        for p in paths:
            if p.is_dir():
                resolved += [q for q in sorted(p.iterdir()) if is_pdf(q) or is_image(q)]
            elif is_pdf(p) or is_image(p):
                resolved.append(p)
        gap = self._grid_compute_gap(x, y)
        pos = gap["index"] if gap else len(self._grid_order)
        self._grid_bar_clear()
        if not resolved:
            return
        prev_keys = set((getattr(self, "_grid_unit_keys", {}) or {}).values())
        self._set_files(self.files + resolved)
        new_units = [i for i in self._grid_order
                     if (getattr(self, "_grid_unit_keys", {}) or {}).get(i) not in prev_keys]
        if new_units:
            rest = [o for o in self._grid_order if o not in set(new_units)]
            pos = max(0, min(len(rest), pos))
            self._grid_order = rest[:pos] + new_units + rest[pos:]
            self._grid_sig = None
            self._grid_fire_reorder()
            self._refresh_preview()

    # -- bar painting (insertion + persistent split bars) --
    def _paint_grid_bars(self, p):
        acc = self._accent_rgb()
        if self._grid_split_mode:
            for pos in sorted(self._grid_splits):
                geo = self._grid_gap_geometry(pos)
                if geo:
                    self._paint_bar(p, geo, acc)
        if self._grid_gap is not None:
            self._paint_bar(p, self._grid_gap, acc)

    def _paint_bar(self, p, geo, rgb):
        col = self._qcol(rgb, 1.0)
        x, y0, y1 = geo["x"], geo["y0"], geo["y1"]
        pen = QPen(col)
        pen.setWidthF(4)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        p.drawLine(QPointF(x, y0 + 2), QPointF(x, y1 - 2))
        p.setBrush(QBrush(col))
        p.setPen(Qt.NoPen)
        for yy in (y0 + 2, y1 - 2):
            p.drawEllipse(QPointF(x, yy), 4, 4)

    # -- split bars (Split — at pages) --------------------------------------
    def _grid_gap_geometry(self, pos):
        order = self._grid_order
        if not order:
            return None

        def bounds(orig):
            cell = self._grid_cells.get(orig)
            if cell is None:
                return None
            r = cell.geometry()
            return (r.x(), r.y(), r.width(), r.height())
        if pos <= 0:
            b = bounds(order[0])
            return {"x": max(1.0, b[0] - 5), "y0": b[1], "y1": b[1] + b[3]} if b else None
        if pos >= len(order):
            b = bounds(order[-1])
            return {"x": b[0] + b[2] + 5, "y0": b[1], "y1": b[1] + b[3]} if b else None
        b = bounds(order[pos])
        bp = bounds(order[pos - 1])
        if b is None:
            return None
        y0, y1 = b[1], b[1] + b[3]
        if bp and abs(bp[1] - b[1]) < 8:
            x = (bp[0] + bp[2] + b[0]) / 2
        else:
            x = max(1.0, b[0] - 5)
        return {"x": x, "y0": y0, "y1": y1}

    def _nearest_split(self, px, py, threshold=18):
        best, bestd = None, threshold
        for pos in self._grid_splits:
            geo = self._grid_gap_geometry(pos)
            if geo and geo["y0"] - 8 <= py <= geo["y1"] + 8:
                d = abs(geo["x"] - px)
                if d < bestd:
                    best, bestd = pos, d
        return best

    _SPLIT_TAP_PX = 6

    def _grid_split_press(self, sx, sy):
        self._split_press = (sx, sy)
        self._split_drag = None
        self._split_candidate = None
        self._split_moved = False
        if not self._grid_split_mode:
            return
        self._split_candidate = self._nearest_split(sx, sy)
        _dbg("split press at (%.0f,%.0f) mode=%s candidate=%s splits=%s"
             % (sx, sy, self._grid_split_mode, self._split_candidate,
                sorted(self._grid_splits)))

    def _grid_split_move(self, x, y):
        if not self._grid_split_mode or self._split_press is None:
            return
        sx, sy = self._split_press
        ox, oy = x - sx, y - sy
        if abs(ox) < self._SPLIT_TAP_PX and abs(oy) < self._SPLIT_TAP_PX:
            return
        if self._split_candidate is None:
            return
        if self._split_drag is None:
            self._split_drag = self._split_candidate
        gap = self._grid_compute_gap(x, y)
        if gap and 0 < gap["index"] < len(self._grid_order):
            new = gap["index"]
            if new != self._split_drag:
                self._grid_splits.discard(self._split_drag)
                self._grid_splits.add(new)
                self._split_drag = new
                self._split_moved = True
                _dbg("split move -> bar moved to %d, splits=%s"
                     % (new, sorted(self._grid_splits)))
                self.grid_bar.update()

    def _grid_split_release(self, x, y):
        if not self._grid_split_mode or self._split_press is None:
            self._split_press = None
            return
        sx, sy = self._split_press
        self._split_press = None
        moved = abs(x - sx) >= self._SPLIT_TAP_PX or abs(y - sy) >= self._SPLIT_TAP_PX
        if moved and self._split_drag is not None:
            pass
        elif self._split_candidate is not None:
            self._grid_splits.discard(self._split_candidate)
        else:
            gap = self._grid_compute_gap(sx, sy)
            if gap and 0 < gap["index"] < len(self._grid_order):
                self._grid_splits.add(gap["index"])
        _dbg("split release moved=%s splits=%s" % (moved, sorted(self._grid_splits)))
        self._split_drag = self._split_candidate = None
        self._split_moved = False
        self.grid_bar.update()
        self._grid_fire_split()

    def _grid_fire_split(self):
        spec = self.tools.get(self.tool)
        fn = getattr(spec.module, "on_split", None) if spec else None
        if not callable(fn):
            return
        try:
            writes = fn(sorted(self._grid_splits), self._read_opts(),
                        self._preview_context()) or {}
        except Exception:
            traceback.print_exc()
            writes = {}
        _dbg("on_split(%s) -> %s" % (sorted(self._grid_splits), writes))
        if writes:
            self._suppress_refresh = True
            for k, v in writes.items():
                self._set_option(k, v)
            self._suppress_refresh = False
            # Re-derive the bars from the values the tool just wrote, so an
            # evenly-spaced split (where dragging one bar changes N) re-spaces
            # all the other bars. For explicit-cut tools this repaints the same
            # bars (grid signature unchanged → no thumbnail rebuild).
            self._refresh_preview()

    # -- thumbnails (rendered off-thread to disk, loaded on the GUI thread) --
    def _start_thumb_worker(self, jobs, gen):
        def work():
            for (orig, kind, path, page, ck, g) in jobs:
                if gen != self._grid_gen:
                    return
                png = self._thumb_cache.get(ck)
                if not png or not Path(png).exists():
                    png = self._render_thumb_png(kind, path, page, ck)
                if png and gen == self._grid_gen:
                    self._invoke(lambda o=orig, pp=png, gg=gen: self._set_thumb(o, pp, gg))
        threading.Thread(target=work, daemon=True).start()

    def _set_thumb(self, orig, png, gen):
        if gen == self._grid_gen:
            cell = self._grid_cells.get(orig)
            if cell is not None:
                cell.set_pixmap(QPixmap(png))

    def _render_thumb_png(self, kind, path, page, ck):
        try:
            out = self._thumb_dir / (hashlib.md5(ck.encode()).hexdigest() + ".png")
            if out.exists():
                self._thumb_cache[ck] = str(out)
                return str(out)
            if kind in ("pdf", "pdf-page"):
                doc = fitz.open(path)
                pg = doc[min(page, doc.page_count - 1)]
                pix = pg.get_pixmap(matrix=fitz.Matrix(0.35, 0.35), alpha=False)
                pix.save(str(out))
                doc.close()
            else:
                im = Image.open(path)
                im.thumbnail((320, 400))
                im.convert("RGB").save(str(out))
            self._thumb_cache[ck] = str(out)
            return str(out)
        except Exception:
            traceback.print_exc()
            return None

    # ---- preview drop dispatch --------------------------------------------
    def _on_preview_drop(self, paths):
        resolved = []
        for p in paths:
            if p.is_dir():
                resolved += [q for q in sorted(p.iterdir()) if is_pdf(q) or is_image(q)]
            elif is_pdf(p) or is_image(p):
                resolved.append(p)
        if not resolved:
            return
        spec = self.tools.get(self.tool)
        is_grid = (spec is not None and spec.has_preview_grid
                   and self._tool_preview_kind(spec, self._read_opts()) == "grid"
                   and self.files)
        if is_grid:
            self._set_files(self.files + resolved)
        else:
            self._rebuild_options()
            self._set_files(resolved)

    # ---- processing --------------------------------------------------------
    def _process(self):
        spec = self.tools.get(self.tool)
        if not self.files or spec is None:
            self._toast("Load files and pick a tool first.")
            return
        targets = self._relevant_files(spec)
        if not targets:
            self._toast(f"No compatible files for “{spec.name}”.")
            return
        overwrite = self.sw_overwrite.isChecked()
        out_dir = self._resolve_output_dir()
        if not overwrite:
            out_dir.mkdir(parents=True, exist_ok=True)
        opts = self._read_opts()
        if self._tool_preview_kind(spec, opts) == "grid":
            opts["_grid_order"] = list(getattr(self, "_grid_order", []))
            opts["_grid_selected"] = sorted(getattr(self, "_grid_selected", []))
            units = getattr(self, "_grid_items", [])
            opts["_grid_sequence"] = [
                (units[i].get("file"), units[i].get("pageno"))
                for i in self._grid_order if 0 <= i < len(units)]
        strs = [str(t) for t in targets]
        naming = None if overwrite else self._naming_config()
        try:
            accepts_progress = "progress" in inspect.signature(spec.module.process).parameters
        except (TypeError, ValueError):
            accepts_progress = False

        dlg = self._progress_dialog()
        state = {"cancel": False, "written": []}
        dlg._cancel_cb = lambda: state.__setitem__("cancel", True)

        def sp(frac, text, name):
            self._invoke(lambda: self._set_progress(dlg, frac, text, name))

        def run():
            try:
                if spec.batch:
                    def prog(done, total):
                        sp((done / total) if total else 0.0, f"{done} / {total}", "Working…")
                    kw = {"progress": prog} if accepts_progress else {}
                    res = spec.module.process(strs, str(out_dir), opts, overwrite, **kw) or []
                    state["written"] += [Path(x) for x in res]
                    sp(1.0, "done", "Done")
                else:
                    n = len(strs)
                    for i, f in enumerate(strs, start=1):
                        if state["cancel"]:
                            break
                        name = Path(f).name

                        def prog(done, total, i=i, name=name):
                            frac = ((i - 1) + (done / total if total else 0)) / n
                            sub = f"{i}/{n}" + (f" — page {done}/{total}" if total > 1 else "")
                            sp(frac, sub, name)
                        sp((i - 1) / n, f"{i}/{n}", name)
                        kw = {"progress": prog} if accepts_progress else {}
                        res = spec.module.process(f, str(out_dir), opts, overwrite, **kw) or []
                        state["written"] += [Path(x) for x in res]
                        sp(i / n, f"{i}/{n}", name)
                if not state["cancel"]:
                    state["written"] = [Path(x) for x in
                                        self._apply_naming([str(p) for p in state["written"]], naming)]
                self._invoke(lambda: self._finish_dialog(dlg, state, None))
            except Exception as e:
                traceback.print_exc()
                self._invoke(lambda e=e: self._finish_dialog(dlg, state, str(e)))

        threading.Thread(target=run, daemon=True).start()

    def _set_progress(self, dlg, frac, text, name):
        dlg._bar.setValue(int(max(0.0, min(1.0, frac)) * 100))
        dlg._bar.setFormat(text)
        dlg._status.setText(name)

    def _progress_dialog(self):
        dlg = QDialog(self)
        dlg.setWindowTitle(tr("Processing"))
        dlg.setModal(True)
        dlg.resize(480, 190)
        v = QVBoxLayout(dlg)
        v.setContentsMargins(20, 20, 20, 20)
        v.setSpacing(12)
        bar = QProgressBar()
        bar.setRange(0, 100)
        bar.setTextVisible(True)
        status = QLabel(tr("Starting…"))
        status.setWordWrap(True)
        status.setEnabled(False)
        btnrow = QHBoxLayout()
        btnrow.addStretch(1)
        cancel = QPushButton(tr("Cancel"))
        cancel.clicked.connect(lambda: getattr(dlg, "_cancel_cb", lambda: None)())
        btnrow.addWidget(cancel)
        v.addWidget(bar)
        v.addWidget(status)
        v.addLayout(btnrow)
        dlg._bar, dlg._status, dlg._btnrow = bar, status, btnrow
        dlg.show()
        return dlg

    def _humanize_error(self, msg):
        m = (msg or "").lower()
        if "clip must be finite" in m:
            return tr("Some pages or images are too small for the margins you set. "
                      "Lower the crop/margin values and try again.")
        if "cannot access local variable" in m or "referenced before assignment" in m:
            return (tr("This tool hit an internal error (a bug in the tool script). If it's a "
                       "built-in, try Settings → Restore built-in tools. Detail: ") + msg)
        if "tesseract" in m and ("not installed" in m or "not found" in m or "no such file" in m):
            return tr("Tesseract isn't installed. Open Settings, turn on an OCR tool, and let it "
                      "install its dependencies (you'll be asked for your password).")
        if "easyocr" in m or "torch" in m:
            return tr("EasyOCR isn't ready. Enable OCR (EasyOCR) in Settings to install it — the "
                      "first run also downloads models and needs an internet connection.")
        if "no module named" in m:
            return (tr("A required library is missing for this tool. Enable it in Settings to "
                       "install its dependencies. Detail: ") + msg)
        if "permission denied" in m:
            return tr("Permission denied writing the output. Pick a different output folder.")
        if "no such file" in m or "cannot find" in m:
            return tr("A file couldn't be found — it may have been moved or deleted.")
        if "memoryerror" in m or "not enough memory" in m or "cannot allocate" in m:
            return tr("Ran out of memory. Try a lower DPI, or process fewer files at once.")
        return tr("The tool couldn't finish. Detail: ") + msg

    def _finish_dialog(self, dlg, state, error):
        n = len(state["written"])
        if error:
            error = self._humanize_error(error)
            dlg._status.setText(f"{tr('Error:')} {error}")
            dlg._bar.setFormat(tr("failed"))
        elif state["cancel"]:
            dlg._status.setText(tr("Cancelled after {n} file(s).").format(n=n))
        else:
            dlg._bar.setValue(100)
            dlg._bar.setFormat(tr("done"))
            dlg._status.setText(tr("Wrote {n} file(s).").format(n=n))
        _clear_layout(dlg._btnrow)
        dlg._btnrow.addStretch(1)
        if state["written"] and not error:
            load = QPushButton(tr("Load results as new files"))

            def _load():
                self._rebuild_options()
                self._set_files(state["written"])
                dlg.close()
            load.clicked.connect(_load)
            dlg._btnrow.addWidget(load)
        close = QPushButton(tr("Close"))
        close.setDefault(True)
        close.clicked.connect(dlg.close)
        dlg._btnrow.addWidget(close)

    def _toast(self, msg):
        QMessageBox.information(self, "PageForge", msg)

    # ---- settings ----------------------------------------------------------
    def _open_settings(self):
        dlg = QDialog(self)
        self._settings_win = dlg
        dlg.setWindowTitle(tr("Settings"))
        dlg.resize(640, 720)
        outer = QVBoxLayout(dlg)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        self._settings_layout = QVBoxLayout(inner)
        self._settings_layout.setContentsMargins(16, 16, 16, 16)
        self._settings_layout.setSpacing(14)
        scroll.setWidget(inner)
        outer.addWidget(scroll)
        self._populate_settings()
        dlg.show()

    def _settings_group(self, title, description=None):
        from PySide6.QtWidgets import QGroupBox
        gb = QGroupBox(title)
        lay = QVBoxLayout(gb)
        lay.setSpacing(6)
        if description:
            d = QLabel(description)
            d.setWordWrap(True)
            d.setEnabled(False)
            lay.addWidget(d)
        self._settings_layout.addWidget(gb)
        return lay

    def _populate_settings(self):
        _clear_layout(self._settings_layout)

        # Language
        lang_lay = self._settings_group(tr("Language"))
        row = QHBoxLayout()
        lab = QLabel(tr("Language"))
        sub = QLabel(tr("Restart to fully apply."))
        sub.setEnabled(False)
        col = QVBoxLayout()
        col.addWidget(lab)
        col.addWidget(sub)
        row.addLayout(col, 1)
        codes = ["en", "fr"]
        dd = QComboBox()
        dd.addItems(["English", "Français"])
        cur = self.cfg.get("language", "en")
        dd.setCurrentIndex(codes.index(cur) if cur in codes else 0)
        dd.currentIndexChanged.connect(lambda i: self._on_language_changed(codes[i]))
        row.addWidget(dd)
        lang_lay.addLayout(row)

        # Scripts folder
        fol_lay = self._settings_group(tr("Scripts folder"),
                                       tr("All tools are scripts in this folder."))
        frow = QHBoxLayout()
        loc = QVBoxLayout()
        loc.addWidget(QLabel(tr("Location")))
        locsub = QLabel(self.cfg.get("scripts_dir") or tr("not set"))
        locsub.setEnabled(False)
        locsub.setWordWrap(True)
        loc.addWidget(locsub)
        frow.addLayout(loc, 1)
        openb = QPushButton(tr("Open"))
        openb.clicked.connect(self._open_scripts_folder)
        changeb = QPushButton(tr("Change"))
        changeb.clicked.connect(self._change_scripts_folder)
        frow.addWidget(openb)
        frow.addWidget(changeb)
        fol_lay.addLayout(frow)
        mrow = QHBoxLayout()
        mrow.addWidget(QLabel(tr("Maintenance")), 1)
        restore = QPushButton(tr("Restore built-in tools"))
        restore.clicked.connect(self._restore_builtins)
        reloadb = QPushButton(tr("Reload"))
        reloadb.clicked.connect(lambda: (self._reload_tools(), self._populate_settings()))
        mrow.addWidget(restore)
        mrow.addWidget(reloadb)
        fol_lay.addLayout(mrow)

        # Tools
        tools_lay = self._settings_group(
            tr("Tools"), tr("Reorder with ↑ ↓; enable, disable, or delete any tool."))
        disabled = self.cfg.get("disabled", {})
        if not self.tools:
            empty = QLabel(tr("No tools found") + " — " + tr("Add scripts to the folder, then Reload."))
            empty.setWordWrap(True)
            tools_lay.addWidget(empty)
        ordered = self._sorted_ids(list(self.tools))
        for pos, tid in enumerate(ordered):
            spec = self.tools[tid]
            met = spec.deps_met
            if met:
                sub = "  •  ".join(tr(a) for a in sorted(spec.accepts))
            else:
                sub = tr("needs: ") + ", ".join(spec.requires + spec.system_requires)
            roww = QFrame()
            roww.setFrameShape(QFrame.StyledPanel)
            rl = QHBoxLayout(roww)
            up = QToolButton()
            up.setArrowType(Qt.UpArrow)
            up.setEnabled(pos > 0)
            up.clicked.connect(lambda _=None, t=tid: self._move_tool(t, -1))
            down = QToolButton()
            down.setArrowType(Qt.DownArrow)
            down.setEnabled(pos < len(ordered) - 1)
            down.clicked.connect(lambda _=None, t=tid: self._move_tool(t, 1))
            rl.addWidget(up)
            rl.addWidget(down)
            info = QVBoxLayout()
            tl = QLabel(tr(spec.name))
            tlf = tl.font()
            tlf.setBold(True)
            tl.setFont(tlf)
            info.addWidget(tl)
            sl = QLabel(sub)
            sl.setEnabled(False)
            sl.setWordWrap(True)
            info.addWidget(sl)
            rl.addLayout(info, 1)
            if not met:
                inst = QPushButton(tr("Install deps"))
                inst.clicked.connect(lambda _=None, s=spec: self._install_flow(s))
                rl.addWidget(inst)
            sw = QCheckBox()
            sw.setChecked(met and not disabled.get(tid, False))
            sw.toggled.connect(lambda checked, t=tid, s=spec: self._on_tool_switch(checked, t, s))
            rl.addWidget(sw)
            delb = QToolButton()
            delb.setText("🗑")
            delb.setToolTip(tr("Delete this tool's script"))
            delb.clicked.connect(lambda _=None, s=spec: self._delete_tool(s))
            rl.addWidget(delb)
            tools_lay.addWidget(roww)

        # About
        about_lay = self._settings_group(tr("About"))
        about_lay.addWidget(QLabel(f"PageForge (Windows) — {tr('Version')} {APP_VERSION}"))

    def _on_language_changed(self, code):
        if code == self.cfg.get("language", "en"):
            return
        self.cfg["language"] = code
        save_config(self.cfg)
        set_language(code)
        # Qt can rebuild the whole window chrome live, so the language switch is
        # immediate everywhere — no restart needed.
        self._build_window_content()
        self._select_section(self.sections[2] if self.tool else self.sections[0])
        self._rebuild_tools_section()
        self._rebuild_options()
        self._refresh_preview()
        if getattr(self, "_settings_win", None) is not None and self._settings_win.isVisible():
            self._populate_settings()

    def _move_tool(self, tid, delta):
        order = self._sorted_ids(list(self.tools))
        i = order.index(tid)
        j = i + delta
        if 0 <= j < len(order):
            order[i], order[j] = order[j], order[i]
            self.cfg["order"] = {t: k for k, t in enumerate(order)}
            save_config(self.cfg)
            self._reload_tools()
            self._populate_settings()

    def _on_tool_switch(self, want, tid, spec):
        if want and not spec.deps_met:
            self._install_flow(spec)
            self._populate_settings()
            return
        self.cfg.setdefault("disabled", {})[tid] = (not want)
        save_config(self.cfg)
        self._reload_tools()

    def _open_scripts_folder(self):
        d = self.cfg.get("scripts_dir")
        if not d:
            return
        try:
            if os.name == "nt":
                os.startfile(d)  # noqa: type: ignore[attr-defined]
            else:
                subprocess.Popen(["xdg-open", str(d)])
        except Exception:
            traceback.print_exc()

    def _change_scripts_folder(self):
        d = QFileDialog.getExistingDirectory(self, tr("Choose scripts folder"))
        if d:
            self.cfg["scripts_dir"] = d
            self._sync_bundled(Path(d))
            save_config(self.cfg)
            self._reload_tools()
            self._populate_settings()

    def _restore_builtins(self):
        d = self.cfg.get("scripts_dir")
        if not d:
            return
        try:
            self._sync_bundled(Path(d), force=True)
        except Exception as e:
            self._toast(f"Restore failed: {e}")
            return
        self._reload_tools()
        self._populate_settings()

    def _delete_tool(self, spec):
        ret = QMessageBox.question(
            self, "Delete tool?",
            f"Permanently delete the script for “{spec.name}”?\n{spec.path}",
            QMessageBox.Cancel | QMessageBox.Yes, QMessageBox.Cancel)
        if ret != QMessageBox.Yes:
            return
        try:
            spec.path.unlink()
        except Exception as e:
            self._toast(f"Could not delete: {e}")
            return
        self.cfg.get("disabled", {}).pop(spec.id, None)
        self.cfg.setdefault("seeded_hashes", {}).pop(spec.path.name, None)
        if (SHIPPED_TOOLS / spec.path.name).exists():
            dset = set(self.cfg.setdefault("deleted", []))
            dset.add(spec.path.name)
            self.cfg["deleted"] = sorted(dset)
        save_config(self.cfg)
        self._reload_tools()
        self._populate_settings()

    def _install_flow(self, spec):
        dlg = QDialog(self)
        dlg.setWindowTitle(f"{tr('Dependencies')} · {tr(spec.name)}")
        dlg.resize(600, 440)
        v = QVBoxLayout(dlg)
        v.setContentsMargins(16, 16, 16, 16)
        v.setSpacing(10)
        pkgs = spec.requires + spec.system_requires
        head = QLabel()
        head.setWordWrap(True)
        head.setText(f"<b>{_esc(tr(spec.name))}</b> {tr('needs:')} {_esc(', '.join(pkgs))}")
        v.addWidget(head)
        if spec.system_requires:
            note = QLabel(tr("System packages will use pkexec (password prompt).\n"))
            note.setWordWrap(True)
            note.setEnabled(False)
            # On Windows there is no pkexec; the install flow surfaces guidance.
            note.setText("System components can't be auto-installed on Windows; "
                         "install them separately (or use a bundled build).")
            v.addWidget(note)
        log = QPlainTextEdit()
        log.setReadOnly(True)
        f = QFont("Consolas")
        f.setStyleHint(QFont.Monospace)
        log.setFont(f)
        v.addWidget(log, 1)
        btnrow = QHBoxLayout()
        btnrow.addStretch(1)
        close = QPushButton(tr("Close"))
        close.clicked.connect(dlg.close)
        install = QPushButton(tr("Install"))
        install.setDefault(True)
        btnrow.addWidget(close)
        btnrow.addWidget(install)
        v.addLayout(btnrow)

        def append(line):
            log.moveCursor(QTextCursor.End)
            log.insertPlainText(line)

        def logline(line):
            self._invoke(lambda: append(line))

        def do_install():
            install.setEnabled(False)
            install.setText(tr("Installing…"))

            def work():
                ok = install_deps(spec.requires, spec.system_requires, logline)
                self._invoke(lambda: finish(ok))
            threading.Thread(target=work, daemon=True).start()

        def finish(ok):
            install.setText(tr("Install"))
            if ok:
                self.cfg.setdefault("disabled", {})[spec.id] = False
                save_config(self.cfg)
                self._reload_tools()
                if hasattr(self, "_settings_layout"):
                    self._populate_settings()
                append("\n✓ Done. Tool enabled.\n")
            else:
                install.setEnabled(True)
                append("\n✗ Installation failed. See log above.\n")

        install.clicked.connect(do_install)
        dlg.show()


def _wire_bundled_tesseract():
    """If a `tesseract/` folder is bundled beside the app (frozen build), put it on
    PATH and point pytesseract/TESSDATA at it, so the OCR tools find Tesseract with
    no system install. Host-level only — tool scripts are untouched."""
    tdir = APP_DIR / "tesseract"
    if not tdir.is_dir():
        return
    os.environ["PATH"] = str(tdir) + os.pathsep + os.environ.get("PATH", "")
    tessdata = tdir / "tessdata"
    if tessdata.is_dir():
        os.environ.setdefault("TESSDATA_PREFIX", str(tessdata))
    exe = tdir / "tesseract.exe"
    if exe.exists():
        try:
            import pytesseract
            pytesseract.pytesseract.tesseract_cmd = str(exe)
        except Exception:
            pass


def main():
    _wire_bundled_tesseract()
    # High-DPI is on by default in Qt6. Use the native platform style so the app
    # picks up the Windows 11 theme, accent colour and light/dark automatically.
    QApplication.setApplicationName("PageForge")
    QApplication.setOrganizationName("PageForge")
    app = QApplication(sys.argv)
    app.setApplicationDisplayName("PageForge")
    if ICON_DIR and (ICON_DIR / "pageforge.ico").exists():
        app.setWindowIcon(QIcon(str(ICON_DIR / "pageforge.ico")))
    win = MainWindow(app)
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())




