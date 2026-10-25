#!/usr/bin/env python3
"""
PageForge — a batch PDF/image workbench for GNOME (GTK4 + Libadwaita).

Every tool is just a Python script in your scripts folder. The six starting
tools (divide, crop, autocrop, rename, removebg, convert) are seeded there on
first run and are ordinary scripts you can edit, delete, or replace. Drop in
new scripts to add tools; see PLUGINS.md for the contract.

Runtime deps:  pip install pymupdf pillow numpy    (+ per-tool extras on demand)
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

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, Gio, Gdk, GLib, Pango, GObject  # noqa: E402

import cairo  # noqa: E402
import fitz   # PyMuPDF, used for source rendering in the preview
from PIL import Image
import numpy as np

APP_ID = "io.github.acknar.PageForge"
APP_VERSION = "1.2.0"
PREVIEW_DPI = 110

# Set PAGEFORGE_DEBUG=1 to trace split-bar gestures on stderr (run from a terminal).
_DEBUG = bool(os.environ.get("PAGEFORGE_DEBUG"))
def _dbg(msg):
    if _DEBUG:
        print("[pageforge] " + msg, file=sys.stderr, flush=True)

# Inside the Flatpak the app runs sandboxed on the GNOME runtime: /app is
# read-only, there is no dnf/pkexec, and Tesseract is bundled. Tools' extra pip
# packages go to the per-app PYTHONUSERBASE set by the launcher.
IN_FLATPAK = os.path.exists("/.flatpak-info")

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
RED, GREEN = "#c01c28", "#26a269"
CONFIG_DIR = Path(GLib.get_user_config_dir()) / "PageForge"
CONFIG_FILE = CONFIG_DIR / "config.json"
DEFAULT_TOOLS_DIR = Path(GLib.get_user_data_dir()) / "PageForge" / "tools"

# The starting tools ship as real files in a `tools/` folder next to this script.
# They are copied into the user's scripts folder on first run and kept in sync
# thereafter; they are ordinary, user-editable scripts once copied.
APP_DIR = Path(__file__).resolve().parent
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


# Non-tool files shipped alongside the tools that belong in the scripts folder
# (docs the user may want beside their scripts). Seeded on first run and
# re-created by "Restore built-in tools".
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
# Helpers
# --------------------------------------------------------------------------- #
def is_pdf(p):
    return Path(p).suffix.lower() == ".pdf"


def is_image(p):
    return Path(p).suffix.lower() in IMAGE_EXTS


def kind_of(p):
    return "pdf" if is_pdf(p) else ("image" if is_image(p) else "other")


def reading_order(boxes):
    """Zone indices in reading order: strictly top-to-bottom, then left-to-right, so a
    box higher up always reads first (vertical position decides, not horizontal)."""
    return sorted(range(len(boxes)), key=lambda i: (boxes[i][1], boxes[i][0]))


# Framework defaults. These are only fallbacks: a tool may override any preview
# colour/style from its own script (see PLUGINS.md → Previews) so the app never
# needs updating for a tool to change how its preview looks.
OVERLAY_COLOR = RED       # cheap overlays (grid / rect / boxes)
DETECT_COLOR = "#1c70d1"  # heavy detected regions (preview_regions)
ZONE_COLOR = RED          # user-drawn zones (regions option)


def parse_rgb(value, fallback):
    """Parse '#rrggbb' (or any Gdk-parsable colour) to an (r, g, b) 0–1 tuple.
    `fallback` may be a hex string or an (r, g, b) tuple."""
    if isinstance(fallback, str):
        fb = Gdk.RGBA()
        fb.parse(fallback)
        fallback = (fb.red, fb.green, fb.blue)
    if not value:
        return fallback
    rgba = Gdk.RGBA()
    try:
        if rgba.parse(value):
            return (rgba.red, rgba.green, rgba.blue)
    except Exception:
        pass
    return fallback


def parse_dash(value, scale):
    """Normalise a `dash` style value to a cairo dash list (empty = solid).
    Accepts True (default dashes), False/None (solid), or an explicit list."""
    if value is True:
        return [6 / scale, 4 / scale]
    if isinstance(value, (list, tuple)) and value:
        return [float(v) / scale for v in value]
    return []


def find_spec_ok(mod):
    try:
        return importlib.util.find_spec(mod) is not None
    except Exception:
        return False


def pil_to_surface(img):
    buf = io.BytesIO()
    img.convert("RGBA").save(buf, "PNG")
    buf.seek(0)
    return cairo.ImageSurface.create_from_png(buf)


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
            cfg.update(json.loads(CONFIG_FILE.read_text()))
    except Exception:
        traceback.print_exc()
    return cfg


def save_config(cfg):
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps(cfg, indent=2))
    except Exception:
        traceback.print_exc()


# --------------------------------------------------------------------------- #
# i18n: a single English-keyed catalog. Every user-visible string (host UI and
# tool titles/labels/choices/hints) is looked up here at render time; a missing
# key falls back to the English text, so partial catalogs and new strings are
# always safe. Tools stay pure-Python and declare English only — the host
# translates when it renders. Choice options keep their English value as the
# logical value and only their *display* is translated.
# --------------------------------------------------------------------------- #
LANG = "en"
TRANSLATIONS = {"fr": {'Batch PDF & image workbench': 'Atelier PDF & images par lots', 'Settings': 'Paramètres', 'Choose scripts folder': 'Choisir le dossier de scripts', 'Add files': 'Ajout de fichiers', 'Add folder': 'Ajout de dossier', 'Clear': 'Effacer', '…or drop files here': '…ou déposez des fichiers ici', 'No files loaded': 'Aucun fichier chargé', 'No tools available.\nOpen Settings to add or enable tools.': 'Aucun outil disponible.\nOuvrez les Paramètres pour ajouter ou activer des outils.', 'No options for this tool.': 'Aucune option pour cet outil.', 'Link these two values': 'Lier ces deux valeurs', 'Clear page': 'Effacer la page', "Remove this page's zones": 'Supprimer les zones de cette page', 'Copy to all': 'Copier sur toutes', "Use this page's zones on every page": 'Appliquer les zones de cette page à toutes les pages', 'Choose': 'Choisir', 'none': 'aucun', 'Choose file': 'Choisir un fichier', 'Choose output folder': 'Choisir le dossier de sortie', "Default: an 'output' folder beside your files": 'Par défaut : un dossier « output » à côté de vos fichiers', 'Name': 'Nom', '[Original file name]': "[Nom du fichier d'origine]", "[Original file name] keeps each file's name (including what the Rename tool produced). Type a base name to replace it. Numbering is added on top.": "[Nom du fichier d'origine] conserve le nom de chaque fichier (y compris ce que l'outil Renommer a produit). Saisissez un nom de base pour le remplacer. La numérotation s'ajoute par-dessus.", 'Add numbering': 'Ajouter une numérotation', 'Style': 'Style', 'as prefix': 'en préfixe', 'as suffix': 'en suffixe', 'Overwrite originals': 'Écraser les originaux', 'Process files': 'Traiter les fichiers', 'Load files to see a preview': 'Chargez des fichiers pour voir un aperçu', 'Generate preview': "Générer l'aperçu", 'Working…': 'En cours…', 'Previous page': 'Page précédente', 'Next page': 'Page suivante', 'Click to jump to a page': 'Cliquer pour aller à une page', 'Go to page': 'Aller à la page', 'Go': 'Aller', 'Processing': 'Traitement', 'Starting…': 'Démarrage…', 'Cancel': 'Annuler', 'Load results as new files': 'Charger les résultats comme nouveaux fichiers', 'Close': 'Fermer', 'Error:': 'Erreur :', 'failed': 'échec', 'done': 'terminé', 'Cancelled after {n} file(s).': 'Annulé après {n} fichier(s).', 'Wrote {n} file(s).': '{n} fichier(s) écrit(s).', 'Scripts folder': 'Dossier de scripts', 'All tools are scripts in this folder.': 'Tous les outils sont des scripts dans ce dossier.', 'Location': 'Emplacement', 'not set': 'non défini', 'Open': 'Ouvrir', 'Change': 'Changer', 'Maintenance': 'Maintenance', 'Restore built-in tools': 'Restaurer les outils intégrés', 'Reload': 'Recharger', 'Tools': 'Outils', 'Reorder with ↑ ↓; enable, disable, or delete any tool.': 'Réordonner avec ↑ ↓ ; activer, désactiver ou supprimer un outil.', 'No tools found': 'Aucun outil trouvé', 'Add scripts to the folder, then Reload.': 'Ajoutez des scripts au dossier, puis Rechargez.', 'needs: ': 'nécessite : ', 'needs:': 'nécessite :', 'needs': 'nécessite', 'extra components': 'des composants supplémentaires', 'Install deps': 'Installer les dépendances', "Delete this tool's script": 'Supprimer le script de cet outil', 'About': 'À propos', 'Version': 'Version', 'Install': 'Installer', 'Installing…': 'Installation…', 'Dependencies': 'Dépendances', 'System packages will use pkexec (password prompt).\n': 'Les paquets système utiliseront pkexec (demande de mot de passe).\n', 'Language': 'Langue', 'Restart to fully apply.': 'Redémarrez pour appliquer entièrement.', 'New tools need setup': 'De nouveaux outils requièrent une configuration', "These tools were found but aren't enabled yet because they need extra components installed:": "Ces outils ont été trouvés mais ne sont pas encore activés car ils nécessitent l'installation de composants supplémentaires :", 'Open Settings to install their dependencies and enable them.': 'Ouvrez les Paramètres pour installer leurs dépendances et les activer.', 'Later': 'Plus tard', 'Open Settings': 'Ouvrir les Paramètres', '1  Files': '1  Fichiers', '2  Tools': '2  Outils', '3  Options': '3  Options', '4  Output': '4  Sortie', 'image': 'image', 'pdf': 'PDF', 'Zones are per-page · drag to add · right-click to delete · pages with no zones are skipped': 'Zones par page · glisser pour ajouter · clic droit pour supprimer · les pages sans zone sont ignorées', 'Some pages or images are too small for the margins you set. Lower the crop/margin values and try again.': 'Certaines pages ou images sont trop petites pour les marges définies. Réduisez les valeurs de rognage/marge et réessayez.', "This tool hit an internal error (a bug in the tool script). If it's a built-in, try Settings → Restore built-in tools. Detail: ": "Cet outil a rencontré une erreur interne (un bug dans le script). S'il est intégré, essayez Paramètres → Restaurer les outils intégrés. Détail : ", "Tesseract isn't installed. Open Settings, turn on an OCR tool, and let it install its dependencies (you'll be asked for your password).": "Tesseract n'est pas installé. Ouvrez les Paramètres, activez un outil OCR et laissez-le installer ses dépendances (votre mot de passe sera demandé).", "EasyOCR isn't ready. Enable OCR (EasyOCR) in Settings to install it — the first run also downloads models and needs an internet connection.": "EasyOCR n'est pas prêt. Activez OCR (EasyOCR) dans les Paramètres pour l'installer — le premier lancement télécharge aussi des modèles et nécessite une connexion internet.", 'A required library is missing for this tool. Enable it in Settings to install its dependencies. Detail: ': 'Une bibliothèque requise manque pour cet outil. Activez-le dans les Paramètres pour installer ses dépendances. Détail : ', 'Permission denied writing the output. Pick a different output folder.': "Permission refusée pour l'écriture. Choisissez un autre dossier de sortie.", "A file couldn't be found — it may have been moved or deleted.": 'Un fichier est introuvable — il a peut-être été déplacé ou supprimé.', 'Ran out of memory. Try a lower DPI, or process fewer files at once.': 'Mémoire insuffisante. Essayez un DPI plus bas ou traitez moins de fichiers à la fois.', "The tool couldn't finish. Detail: ": "L'outil n'a pas pu terminer. Détail : ", 'Auto-crop border': 'Rogner automatiquement la bordure', 'Convert (image ↔ PDF)': 'Convertir (image ↔ PDF)', 'Crop margins': 'Rogner les marges', 'Divide into grid': 'Diviser en grille', 'OCR (EasyOCR)': 'OCR (EasyOCR)', 'OCR (zones)': 'OCR (zones)', 'Organize / Merge': 'Organiser / Fusionner', 'Remove background': "Supprimer l'arrière-plan", 'Rename': 'Renommer', 'Split / Extract': 'Diviser / Extraire', 'Upscale': 'Augmenter la résolution', 'Border colour': 'Couleur de la bordure', 'Tolerance': 'Tolérance', 'Padding': 'Marge intérieure', 'Mode': 'Mode', 'Images → single PDF': 'Images → un seul PDF', 'Images → one PDF each': 'Images → un PDF chacune', 'PDF → images': 'PDF → images', 'Image format': "Format d'image", 'Render DPI': 'DPI de rendu', 'Top': 'Haut', 'Bottom': 'Bas', 'Left': 'Gauche', 'Right': 'Droite', 'Columns': 'Colonnes', 'Rows': 'Lignes', 'Rotate result': 'Pivoter le résultat', 'None': 'Aucune', '90° clockwise': '90° horaire', '90° counter-clockwise': '90° antihoraire', '180°': '180°', 'Gutter between cells': 'Espacement entre les cellules', 'Crop top': 'Rogner en haut', 'Crop bottom': 'Rogner en bas', 'Crop left': 'Rogner à gauche', 'Crop right': 'Rogner à droite', 'Use PDF text layer when present': 'Utiliser la couche de texte du PDF si présente', 'Merge pieces into blocks': 'Fusionner les morceaux en blocs', 'Detect columns / sections': 'Détecter les colonnes / sections', 'Join wrapped lines into paragraphs': 'Joindre les lignes coupées en paragraphes', 'Language code (e.g. en, fr)': 'Code de langue (ex. en, fr)', 'Render DPI (scans)': 'DPI de rendu (numérisations)', 'Output': 'Sortie', 'Text file': 'Fichier texte', 'CSV (blocks)': 'CSV (blocs)', 'Searchable PDF': 'PDF interrogeable', 'Columns splits each page into evenly spaced columns; Draw zones reads only the boxes you draw on each page.': 'Colonnes découpe chaque page en colonnes régulières ; Dessiner des zones ne lit que les cadres que vous dessinez sur chaque page.', 'Draw zones': 'Dessiner des zones', 'Text zones': 'Zones de texte', 'Zone order': 'Ordre des zones', 'Top → bottom, left → right': 'Haut → bas, gauche → droite', 'As drawn': "Dans l'ordre de dessin", 'Column spacing (px)': 'Espacement des colonnes (px)', 'Column width ratios': 'Proportions de largeur des colonnes', 'Margin top (px)': 'Marge haut (px)', 'Margin bottom (px)': 'Marge bas (px)', 'Margin left (px)': 'Marge gauche (px)', 'Margin right (px)': 'Marge droite (px)', 'Book pages?': 'Pages de livre ?', 'Symmetric pages': 'Pages symétriques', 'Left page start': 'Début page gauche', 'Right page start': 'Début page droite', 'Gutter shift (px, book scans)': 'Décalage de reliure (px, scans de livre)', 'Wrap lines': 'Renvoyer les lignes', 'Render DPI (PDF)': 'DPI de rendu (PDF)', 'CSV (zones as columns)': 'CSV (zones en colonnes)', 'Show individual pages': 'Afficher les pages individuelles', 'WebP quality': 'Qualité WebP', 'Lossless': 'Sans perte', 'Name list (.txt, optional)': 'Liste de noms (.txt, facultatif)', 'Remove existing numbering': 'Supprimer la numérotation existante', 'Find': 'Rechercher', 'Replace with': 'Remplacer par', 'Make lowercase': 'Mettre en minuscules', 'Spaces': 'Espaces', 'keep': 'conserver', '→ underscore _': '→ tiret bas _', '→ hyphen -': "→ trait d'union -", 'Operation': 'Opération', 'Extract — keep pages': 'Extraire — garder les pages', 'Extract — delete pages': 'Extraire — supprimer les pages', 'Split — into N files': 'Diviser — en N fichiers', 'Split — every N pages': 'Diviser — toutes les N pages', 'Split — at pages': 'Diviser — aux pages', 'Pages / split points (e.g. 1-5, 8, 11-13)': 'Pages / points de coupe (ex. 1-5, 8, 11-13)', 'N  (for split into / every N)': 'N  (pour diviser en / toutes les N)', 'Scale factor': "Facteur d'échelle", 'Target size': 'Taille cible', 'Scale factor (Scale factor mode)': "Facteur d'échelle (mode Facteur d'échelle)", 'Target size in px (Target size mode)': 'Taille cible en px (mode Taille cible)', 'Fit target to': 'Ajuster la cible à', 'Longest side': 'Plus grand côté', 'Shortest side': 'Plus petit côté', 'Width': 'Largeur', 'Height': 'Hauteur', 'Never shrink (upscale only)': 'Ne jamais réduire (agrandir seulement)', 'Resampling': 'Rééchantillonnage', 'Lanczos (best for photos)': 'Lanczos (idéal pour les photos)', 'Bicubic (smooth)': 'Bicubique (lisse)', 'Bilinear (fast)': 'Bilinéaire (rapide)', 'Nearest (pixel art)': 'Plus proche (pixel art)', 'Post-sharpen (counter softening)': 'Accentuation finale (compenser le flou)', 'Output format': 'Format de sortie', 'Same as input': "Comme l'entrée"}}


# Warning / "why did nothing happen?" system messages (mirrored from the
# Windows edition — see SYNC-Linux-Windows.md §3).
TRANSLATIONS["fr"].update({
    "Warning": "Avertissement",
    "This tool works on PDF files, but you've loaded images.":
        "Cet outil fonctionne sur des fichiers PDF, mais vous avez chargé des images.",
    "This tool works on images, but you've loaded PDF files.":
        "Cet outil fonctionne sur des images, mais vous avez chargé des fichiers PDF.",
    "Only PDF files will be processed — {n} image(s) will be skipped.":
        "Seuls les fichiers PDF seront traités — {n} image(s) ignorée(s).",
    "Only images will be processed — {n} PDF file(s) will be skipped.":
        "Seules les images seront traitées — {n} fichier(s) PDF ignoré(s).",
    "This mode converts PDFs to images, but you've loaded images.":
        "Ce mode convertit des PDF en images, mais vous avez chargé des images.",
    "This mode converts images to PDF, but you've loaded PDF files.":
        "Ce mode convertit des images en PDF, mais vous avez chargé des fichiers PDF.",
    "No pages selected — click pages in the grid, or type a page list like 1-5, 8.":
        "Aucune page sélectionnée — cliquez des pages dans la grille ou saisissez une liste comme 1-5, 8.",
    "The tool ran but produced no files. Check your settings — for example an empty "
    "page selection, values that change nothing, or files the tool couldn't use.":
        "L'outil s'est exécuté mais n'a produit aucun fichier. Vérifiez vos réglages — "
        "par exemple une sélection de pages vide, des valeurs sans effet, ou des fichiers "
        "que l'outil n'a pas pu utiliser.",
})


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
# Dependency installation
# --------------------------------------------------------------------------- #
def _pip_cmds(pkgs):
    exe = sys.executable
    base = [exe, "-m", "pip", "install"]
    in_venv = sys.prefix != sys.base_prefix
    if in_venv:
        return [base + list(pkgs)]
    return [base + ["--user"] + list(pkgs),
            base + ["--user", "--break-system-packages"] + list(pkgs)]


def _run_stream(cmd, log):
    log("$ " + " ".join(cmd) + "\n")
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    except FileNotFoundError:
        log(f"  (command not found: {cmd[0]})\n")
        return 1
    for line in proc.stdout:
        log(line)
    proc.wait()
    return proc.returncode


def _add_user_site():
    """pip --user installs into the per-user site-packages, which Python only adds
    to sys.path at startup if the folder already existed. Add it now so a package
    installed this session imports without a restart."""
    try:
        import site
        usp = site.getusersitepackages()
        if usp and os.path.isdir(usp) and usp not in sys.path:
            site.addsitedir(usp)
    except Exception:
        pass


def install_deps(requires, system_requires, log):
    ok = True
    if system_requires:
        if IN_FLATPAK:
            log("System packages can't be installed from inside the Flatpak:\n")
            for s in system_requires:
                log(f"  • {s}\n")
            log("The PageForge Flatpak already includes Tesseract. Other system "
                "components need the RPM or source install.\n\n")
        else:
            log("Installing system packages (a password prompt may appear)…\n")
            if _run_stream(["pkexec", "dnf", "install", "-y", *system_requires], log) != 0:
                ok = False
    if ok and requires:
        for cmd in _pip_cmds(requires):
            if _run_stream(cmd, log) == 0:
                break
        else:
            ok = False
    _add_user_site()
    importlib.invalidate_caches()
    return ok


# --------------------------------------------------------------------------- #
# Tool loading
# --------------------------------------------------------------------------- #
def grid_move(order, src, dst):
    """Reorder helper for the thumbnail grid: pull `src` out of the order list
    and re-insert it at the position `dst` currently occupies (i.e. drop src
    *onto* dst). Pure — unit-tested without a display."""
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
        # thumbnail-grid surface (Merge / Organize / Split-Extract select)
        self.has_preview_grid = hasattr(module, "preview_grid")
        self.preview_kind_default = str(getattr(module, "PREVIEW_KIND", "canvas"))
        self.has_needs_preview_button = hasattr(module, "needs_preview_button")
        self.preview_on_demand = bool(getattr(module, "PREVIEW_ON_DEMAND", False))
        # any preview_image tool may opt into the host's before/after compare
        # slider (source vs. generated result) with COMPARE_PREVIEW = True.
        self.has_compare = bool(getattr(module, "COMPARE_PREVIEW", False))
        # two optional, tool-facing host hooks (see SYNC-Linux-Windows.md §2):
        # warning(context, options) -> str|None ; suggest_options(context, options) -> dict
        self.has_warning = hasattr(module, "warning")
        self.has_suggest_options = hasattr(module, "suggest_options")

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
# Accordion section
# --------------------------------------------------------------------------- #
class Section(Gtk.Box):
    def __init__(self, title, on_activate):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.add_css_class("card")
        self.set_margin_bottom(8)
        self.header = Gtk.Button()
        self.header.add_css_class("flat")
        self.header.connect("clicked", lambda *_: on_activate(self))
        hb = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        for m in ("top", "bottom", "start", "end"):
            getattr(hb, f"set_margin_{m}")(10)
        self.chevron = Gtk.Image.new_from_icon_name("pan-end-symbolic")
        lab = Gtk.Label(label=title, xalign=0)
        lab.add_css_class("heading")
        lab.set_hexpand(True)
        hb.append(self.chevron)
        hb.append(lab)
        self.header.set_child(hb)
        self.append(self.header)
        self.revealer = Gtk.Revealer()
        self.revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.body.set_margin_bottom(12)
        self.body.set_margin_start(12)
        self.body.set_margin_end(12)
        self.body.set_margin_top(4)
        self.revealer.set_child(self.body)
        self.append(self.revealer)

    def set_expanded(self, e):
        self.revealer.set_reveal_child(e)
        self.chevron.set_from_icon_name("pan-down-symbolic" if e else "pan-end-symbolic")


# --------------------------------------------------------------------------- #
# Main window
# --------------------------------------------------------------------------- #
class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="PageForge")
        self.set_default_size(1240, 820)
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
        self._warn_msg = None
        self._opt_getters = {}
        self._file_opts = {}
        self._img_cache = {}
        self._base_cache = {}
        self._base_surface = None
        self._overlay = None
        self._overlay_style = {}
        self._handles = []
        self._cursor_name = None
        self._dragging_handle = None
        self._opt_setters = {}
        self._suppress_refresh = False
        self._detected_style = {}
        self._zone_style = {}
        self._unit_scale = 1.0
        self._surf_w = self._surf_h = 1
        self._settings_groups = []
        self._regions = {}
        self._drawing = None
        self._moving = None
        self._resizing = None
        self._regions_enabled = False
        self._last_scale, self._last_ox, self._last_oy = 1.0, 0.0, 0.0
        self._drag_start = (0, 0)
        self._detected = []
        # thumbnail-grid surface state
        self._thumb_dir = Path(tempfile.mkdtemp(prefix="pageforge-thumbs-"))
        self._thumb_cache = {}          # cache_key -> png path
        self._grid_items = []           # [{orig, kind, path, page, caption}]
        self._grid_order = []           # display order, in original-index space
        self._grid_selected = []        # selected original indices
        self._grid_deleted = set()      # keys of units deleted this session
        self._grid_children = {}        # orig index -> Gtk.FlowBoxChild
        self._grid_cells = {}           # orig index -> inner card Gtk.Box (for highlight)
        self._grid_spec = {}
        self._grid_selectable = False
        self._grid_reorderable = False
        self._grid_anchor = None        # anchor orig for shift-range selection
        self._grid_sig = None           # signature of last-rendered grid (skip rebuilds)
        self._grid_gen = 0              # bumps to cancel stale thumbnail workers

        self._build_window_content()

        self._select_section(self.sections[0])
        if not self.cfg.get("scripts_dir"):
            GLib.idle_add(self._first_run)
        else:
            self._sync_bundled(self.cfg["scripts_dir"])
            self._reload_tools()
            GLib.idle_add(self._prompt_new_dep_tools)
        self._refresh_preview()

    # ---- first run ---------------------------------------------------------
    def _first_run(self):
        d = Adw.MessageDialog(transient_for=self, heading="Welcome to PageForge",
                              body="Choose a folder to keep your tools in. The built-in "
                                   "tools will be placed there and loaded automatically. "
                                   "You can add your own scripts to the same folder later.")
        d.add_response("default", f"Use default folder")
        d.add_response("choose", "Choose folder…")
        d.set_response_appearance("default", Adw.ResponseAppearance.SUGGESTED)
        d.connect("response", self._on_first_run_response)
        d.present()
        return False

    def _on_first_run_response(self, dialog, response):
        if response == "choose":
            fd = Gtk.FileDialog()
            fd.set_title(tr("Choose scripts folder"))
            fd.select_folder(self, None, self._on_first_folder)
        else:
            self._setup_scripts_dir(DEFAULT_TOOLS_DIR)

    def _on_first_folder(self, fd, res):
        try:
            folder = fd.select_folder_finish(res)
        except GLib.Error:
            self._setup_scripts_dir(DEFAULT_TOOLS_DIR)
            return
        self._setup_scripts_dir(Path(folder.get_path()))

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
        GLib.idle_add(self._prompt_new_dep_tools)

    @staticmethod
    def _hash(text):
        return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]

    def _existing_tool_names(self, d):
        names = set()
        for p in Path(d).glob("*.py"):
            try:
                m = re.search(r'^NAME\s*=\s*["\'](.+?)["\']', p.read_text(encoding="utf-8"), re.M)
                if m:
                    names.add(m.group(1))
            except Exception:
                pass
        return names

    def _sync_bundled(self, d, force=False):
        """Seed/refresh the built-in tool scripts.

        - new bundled tools are written in
        - bundled tools you haven't edited are updated to the latest version
        - tools you edited or renamed are left alone; deleted ones stay deleted
        - force=True (Restore) overwrites everything and un-deletes
        """
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
                # don't create a duplicate if the same tool exists under another filename
                if tool_name and tool_name in present_names:
                    continue
                p.write_text(src, encoding="utf-8")
                seeded[name] = newh
            else:
                cur = self._hash(p.read_text(encoding="utf-8"))
                if seeded.get(name) == cur and cur != newh:
                    p.write_text(src, encoding="utf-8")   # unmodified → update
                    seeded[name] = newh
                else:
                    seeded.setdefault(name, cur)          # user-edited → leave alone
        # bundled docs (e.g. PLUGINS.md): seed if missing, overwrite on restore
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
        return self.cfg.get("order", {}).get(tid, self.tools[tid].order if tid in self.tools else 1000)

    def _sorted_ids(self, ids):
        return sorted(ids, key=lambda t: (self._effective_order(t), self.tools[t].name.lower()))

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
        """On launch, tell the user about newly-seen tools that can't enable because
        their dependencies aren't installed, and point them at Settings. Each tool is
        only announced once (tracked in config) so it doesn't nag on every launch."""
        prompted = set(self.cfg.setdefault("dep_prompted", []))
        disabled = self.cfg.get("disabled", {})
        new_ids = [tid for tid, t in self.tools.items()
                   if not t.deps_met and not disabled.get(tid, False) and tid not in prompted]
        if not new_ids:
            return False
        specs = [self.tools[tid] for tid in self._sorted_ids(new_ids)]
        lines = "\n".join(
            f"• {tr(s.name)} — {tr('needs')} {', '.join(s.requires + s.system_requires) or tr('extra components')}"
            for s in specs)
        d = Adw.MessageDialog(
            transient_for=self, heading=tr("New tools need setup"),
            body=(tr("These tools were found but aren't enabled yet because they need extra "
                     "components installed:") + f"\n\n{lines}\n\n" +
                  tr("Open Settings to install their dependencies and enable them.")))
        d.add_response("later", tr("Later"))
        d.add_response("settings", tr("Open Settings"))
        d.set_response_appearance("settings", Adw.ResponseAppearance.SUGGESTED)
        d.connect("response", lambda dlg, resp: resp == "settings" and self._open_settings())
        d.present()
        prompted.update(s.id for s in specs)
        self.cfg["dep_prompted"] = sorted(prompted)
        save_config(self.cfg)
        return False

    # ---- window content ----------------------------------------------------
    def _build_window_content(self):
        """Build (or rebuild) the whole window chrome: header, sidebar and
        preview. Called once at startup and again on a language change so the
        entire interface re-renders live in the newly chosen language."""
        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        header.set_title_widget(Adw.WindowTitle(title="PageForge",
                                                subtitle=tr("Batch PDF & image workbench")))
        gear = Gtk.Button.new_from_icon_name("emblem-system-symbolic")
        gear.set_tooltip_text(tr("Settings"))
        gear.connect("clicked", lambda *_: self._open_settings())
        header.pack_end(gear)
        # amber warning indicator (left of Settings) — shown only when the current
        # tool + loaded files have a caveat; clicking reveals the explanation.
        # Logic/messages mirror the Windows edition (SYNC-Linux-Windows.md §3);
        # the presentation is native GTK (a header button + popover, not a bubble).
        self.warn_btn = Gtk.MenuButton()
        self.warn_btn.set_icon_name("dialog-warning-symbolic")
        self.warn_btn.add_css_class("flat")
        self.warn_btn.add_css_class("warning")
        self.warn_btn.set_tooltip_text(tr("Warning"))
        self.warn_btn.set_visible(False)
        _wpop = Gtk.Popover()
        self.warn_label = Gtk.Label(label="")
        self.warn_label.set_wrap(True)
        self.warn_label.set_max_width_chars(40)
        self.warn_label.set_xalign(0)
        for _m in ("top", "bottom", "start", "end"):
            getattr(self.warn_label, f"set_margin_{_m}")(10)
        _wpop.set_child(self.warn_label)
        self.warn_btn.set_popover(_wpop)
        header.pack_end(self.warn_btn)
        toolbar.add_top_bar(header)

        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        paned.set_position(390)
        paned.set_resize_start_child(False)
        paned.set_shrink_start_child(False)
        toolbar.set_content(paned)
        self.set_content(toolbar)
        paned.set_start_child(self._build_sidebar())
        paned.set_end_child(self._build_preview())

    # ---- sidebar -----------------------------------------------------------
    def _build_sidebar(self):
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        for m in ("top", "bottom", "start", "end"):
            getattr(outer, f"set_margin_{m}")(12)
        scroller.set_child(outer)

        self.sec_files = Section(tr("1  Files"), self._select_section)
        self.sec_tools = Section(tr("2  Tools"), self._select_section)
        self.sec_options = Section(tr("3  Options"), self._select_section)
        self.sec_output = Section(tr("4  Output"), self._select_section)
        self.sections = [self.sec_files, self.sec_tools, self.sec_options, self.sec_output]

        self._build_files_section(self.sec_files.body)
        self.tools_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.sec_tools.body.append(self.tools_box)
        self.options_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.sec_options.body.append(self.options_container)
        self._build_output_section(self.sec_output.body)

        for s in self.sections:
            outer.append(s)
        return scroller

    def _build_files_section(self, body):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        b1 = Gtk.Button(label=tr("Add files")); b1.connect("clicked", self._choose_files)
        b2 = Gtk.Button(label=tr("Add folder")); b2.connect("clicked", self._choose_folder)
        b3 = Gtk.Button(label=tr("Clear")); b3.add_css_class("destructive-action")
        b3.connect("clicked", lambda *_: self._set_files([]))
        for b in (b1, b2, b3):
            row.append(b)
        body.append(row)
        hint = Gtk.Label(label=tr("…or drop files here"))
        hint.add_css_class("dim-label"); hint.set_margin_top(4); hint.set_margin_bottom(4)
        frame = Gtk.Frame(); frame.set_child(hint)
        drop = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        drop.connect("drop", self._on_drop)
        frame.add_controller(drop)
        body.append(frame)
        self.files_label = Gtk.Label(label=tr("No files loaded"), xalign=0)
        self.files_label.add_css_class("dim-label")
        body.append(self.files_label)

    def _rebuild_tools_section(self):
        c = self.tools_box.get_first_child()
        while c:
            self.tools_box.remove(c)
            c = self.tools_box.get_first_child()
        self.tool_buttons = {}
        if not self.active_ids:
            lab = Gtk.Label(label=tr("No tools available.\nOpen Settings to add or enable tools."),
                            xalign=0)
            lab.add_css_class("dim-label")
            self.tools_box.append(lab)
            return
        group = None
        for tid in self.active_ids:
            btn = Gtk.CheckButton(label=tr(self.tools[tid].name))
            if group is None:
                group = btn
            else:
                btn.set_group(group)
            btn.set_active(tid == self.tool)
            btn.connect("toggled", self._on_tool_toggled, tid)
            self.tool_buttons[tid] = btn
            self.tools_box.append(btn)

    # ---- generic options ---------------------------------------------------
    def _rebuild_options(self):
        c = self.options_container.get_first_child()
        while c:
            self.options_container.remove(c)
            c = self.options_container.get_first_child()
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
            lab = Gtk.Label(label=tr("No options for this tool."), xalign=0)
            lab.add_css_class("dim-label")
            self.options_container.append(lab)
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
        step = 1 if typ == "int" else 0.1
        sp = Gtk.SpinButton.new_with_range(opt.get("min", 0), opt.get("max", 100000), step)
        if typ == "float":
            sp.set_digits(2)
        sp.set_value(opt.get("default", 0))
        sp.connect("value-changed", lambda *_: self._refresh_preview())
        return sp

    def _update_link_icon(self, tgl):
        linked = tgl.get_active()
        svg = ICON_DIR / ("linked.svg" if linked else "unlinked.svg")
        if svg.exists():
            img = Gtk.Image.new_from_file(str(svg))
            img.set_pixel_size(22)
            tgl.set_child(img)
        else:
            tgl.set_icon_name("chain-link-symbolic" if linked else "chain-link-loose-symbolic")

    def _add_linked_pair(self, a, b):
        grid = Gtk.Grid(column_spacing=8, row_spacing=6)
        grid.set_margin_top(2); grid.set_margin_bottom(2)
        la = Gtk.Label(label=tr(a.get("label", a["key"])), xalign=0)
        la.set_hexpand(True); la.set_valign(Gtk.Align.CENTER)
        lb = Gtk.Label(label=tr(b.get("label", b["key"])), xalign=0)
        lb.set_hexpand(True); lb.set_valign(Gtk.Align.CENTER)
        sa, sb = self._make_spin(a), self._make_spin(b)
        sa.set_valign(Gtk.Align.CENTER); sb.set_valign(Gtk.Align.CENTER)
        grid.attach(la, 0, 0, 1, 1); grid.attach(sa, 1, 0, 1, 1)
        grid.attach(lb, 0, 1, 1, 1); grid.attach(sb, 1, 1, 1, 1)
        tgl = Gtk.ToggleButton()
        tgl.set_valign(Gtk.Align.FILL)     # spans the two rows' natural height
        tgl.set_vexpand(False)
        tgl.add_css_class("flat")
        tgl.set_tooltip_text(tr("Link these two values"))
        self._update_link_icon(tgl)
        tgl.connect("toggled", lambda t: (self._update_link_icon(t), self._refresh_preview()))
        grid.attach(tgl, 2, 0, 1, 2)
        self._opt_rows[a["key"]] = grid
        self._opt_rows[b["key"]] = grid
        for key, sp in ((a["key"], sa), (b["key"], sb)):
            self._opt_widgets[key] = sp
            self._opt_getters[key] = (lambda s=sp, o=(a if key == a["key"] else b):
                                      int(s.get_value()) if o.get("type", "int") == "int" else s.get_value())
            self._opt_setters[key] = (lambda v, s=sp: s.set_value(float(v)))
        self._wire_link(sa, sb, tgl)
        self.options_container.append(grid)

    def _rule_ok(self, rule):
        """Evaluate a condition {"key": <other>, "in"/"not_in"/"eq": …} against
        the current option values. Shared by enabled_when and visible_when."""
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
        """Grey out any option whose enabled_when condition is currently unmet.
        Generic host seam: a tool declares
        "enabled_when": {"key": <other>, "in": [...]}  (or "eq"/"not_in").
        Re-run whenever options change so the row tracks its controller live."""
        for key, rule in getattr(self, "_enable_rules", []):
            row = self._opt_rows.get(key)
            if row is not None:
                row.set_sensitive(bool(self._rule_ok(rule)))

    def _apply_visible_rules(self):
        """Show/hide an option row whose visible_when condition is (un)met —
        same schema as enabled_when. Lets a tool split its options into modes
        and only show the ones that apply. A hidden 'regions' option also turns
        canvas zone-drawing off, so a tool can switch cleanly between a
        zone-drawing mode and another (e.g. column) mode."""
        for key, rule in getattr(self, "_visible_rules", []):
            row = self._opt_rows.get(key)
            if row is not None:
                row.set_visible(bool(self._rule_ok(rule)))
        rk = getattr(self, "_regions_key", None)
        if rk is not None:
            rrow = self._opt_rows.get(rk)
            if rrow is not None:
                self._regions_enabled = bool(rrow.get_visible())

    def _add_hidden_option(self, opt):
        """A state-only option: no UI row, just a stored value that preview()
        can read and on_handle_drag can write. Lets a tool keep drag-driven
        state (e.g. Divide's kept-cell bounds) without cluttering the panel."""
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
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        lab = Gtk.Label(label=label, xalign=0); lab.set_hexpand(True)
        row.append(lab)
        if typ in ("int", "float"):
            sp = self._make_spin(opt)
            row.append(sp)
            self._opt_widgets[key] = sp
            self._opt_getters[key] = (lambda s=sp, t=typ:
                                      int(s.get_value()) if t == "int" else s.get_value())
            self._opt_setters[key] = (lambda v, s=sp: s.set_value(float(v)))
        elif typ == "bool":
            sw = Gtk.Switch(); sw.set_valign(Gtk.Align.CENTER)
            sw.set_active(bool(opt.get("default", False)))
            sw.connect("notify::active", lambda *_: self._refresh_preview())
            row.append(sw)
            self._opt_getters[key] = (lambda s=sw: s.get_active())
            self._opt_setters[key] = (lambda v, s=sw: s.set_active(bool(v)))
        elif typ == "choice":
            choices = list(opt.get("choices", []))
            dd = Gtk.DropDown.new_from_strings([tr(c) for c in choices])
            if opt.get("default") in choices:
                dd.set_selected(choices.index(opt["default"]))
            dd.connect("notify::selected", lambda *_: self._refresh_preview())
            row.append(dd)
            self._opt_getters[key] = (lambda d=dd, c=choices: c[d.get_selected()] if c else "")
            self._opt_setters[key] = (lambda v, d=dd, c=choices:
                                      d.set_selected(c.index(v)) if v in c else None)
        elif typ == "color":
            rgba = Gdk.RGBA()
            rgba.parse(opt.get("default", "#000000"))
            btn = Gtk.ColorDialogButton.new(Gtk.ColorDialog())
            btn.set_rgba(rgba)
            btn.set_valign(Gtk.Align.CENTER)
            btn.connect("notify::rgba", lambda *_: self._refresh_preview())
            row.append(btn)

            def _getcolor(b=btn):
                c = b.get_rgba()
                return "#%02x%02x%02x" % (int(c.red * 255), int(c.green * 255), int(c.blue * 255))
            self._opt_getters[key] = _getcolor

            def _setcolor(v, b=btn):
                rr = Gdk.RGBA()
                if rr.parse(str(v)):
                    b.set_rgba(rr)
            self._opt_setters[key] = _setcolor
        elif typ == "regions":
            self._regions_enabled = True
            self._regions_key = key
            self._zone_style = {"color": parse_rgb(opt.get("color"), ZONE_COLOR)}
            self._zone_hint_text = tr(opt.get("hint")) or tr(
                "Zones are per-page · drag to add · right-click to delete · "
                "pages with no zones are skipped")
            lab.set_text(label)
            clearb = Gtk.Button(label=tr("Clear page"))
            clearb.set_tooltip_text(tr("Remove this page's zones"))
            clearb.connect("clicked", lambda *_: (self._regions.pop(self.page_index, None),
                                                  self.canvas.queue_draw()))
            allb = Gtk.Button(label=tr("Copy to all"))
            allb.set_tooltip_text(tr("Use this page's zones on every page"))
            allb.connect("clicked", lambda *_: self._copy_zones_to_all())
            row.append(clearb); row.append(allb)
            self._opt_getters[key] = (lambda: {p: list(v) for p, v in self._regions.items() if v})
        elif typ == "file":
            self._file_opts[key] = opt.get("default", "") or ""
            fl = Gtk.Label(label=Path(self._file_opts[key]).name if self._file_opts[key] else tr("none"),
                           xalign=1)
            fl.add_css_class("dim-label"); fl.set_hexpand(True)
            btn = Gtk.Button(label=tr("Choose"))
            btn.connect("clicked", self._choose_file_option, key, fl)
            row.append(fl); row.append(btn)
            self._opt_getters[key] = (lambda k=key: self._file_opts.get(k, ""))

            def _setfile(v, k=key, lbl=fl):
                self._file_opts[k] = str(v or "")
                lbl.set_text(Path(self._file_opts[k]).name if self._file_opts[k] else tr("none"))
            self._opt_setters[key] = _setfile
        else:
            e = Gtk.Entry(); e.set_text(str(opt.get("default", ""))); e.set_hexpand(True)
            e.connect("changed", lambda *_: self._refresh_preview())
            row.append(e)
            self._opt_getters[key] = (lambda e=e: e.get_text())
            self._opt_setters[key] = (lambda v, e=e: e.set_text(str(v)))
        self._opt_rows[key] = row
        self.options_container.append(row)

    def _choose_file_option(self, btn, key, label):
        d = Gtk.FileDialog(); d.set_title(tr("Choose file"))
        d.open(self, None, lambda dd, r: self._on_file_option(dd, r, key, label))

    def _on_file_option(self, d, res, key, label):
        try:
            f = d.open_finish(res)
        except GLib.Error:
            return
        self._file_opts[key] = f.get_path()
        label.set_text(Path(f.get_path()).name)
        self._refresh_preview()

    def _read_opts(self):
        return {k: g() for k, g in self._opt_getters.items()}

    def _set_option(self, key, value):
        """Write a value back into an option widget (value → widget). Used by
        interactive previews (e.g. dragging a handle updates a number). Setting
        the widget fires its change signal, which normally refreshes the preview;
        callers that drive many writes per drag set self._suppress_refresh first."""
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
            if toggle.get_active() and not self._syncing:
                self._syncing = True
                dst.set_value(src.get_value())
                self._syncing = False
        a.connect("value-changed", lambda *_: sync(a, b))
        b.connect("value-changed", lambda *_: sync(b, a))
        toggle.connect("toggled", lambda *_: (sync(a, b), self._refresh_preview()))

    def _build_output_section(self, body):
        pick = Gtk.Button(label=tr("Choose output folder")); pick.connect("clicked", self._choose_output)
        self.output_label = Gtk.Label(label=tr("Default: an 'output' folder beside your files"),
                                      xalign=0, wrap=True)
        self.output_label.add_css_class("dim-label")

        # ---- naming ----
        name_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        name_row.append(Gtk.Label(label=tr("Name"), xalign=0))
        self.out_name_entry = Gtk.Entry()
        self.out_name_entry.set_hexpand(True)
        self.out_name_entry.set_text(tr("[Original file name]"))
        self.out_name_entry.set_tooltip_text(tr(
            "[Original file name] keeps each file's name (including what the Rename tool "
            "produced). Type a base name to replace it. Numbering is added on top."))
        self.out_name_entry.connect("changed", lambda *_: self._refresh_preview())
        name_row.append(self.out_name_entry)

        num_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        num_row.append(Gtk.Label(label=tr("Add numbering"), xalign=0, hexpand=True))
        self.out_number_switch = Gtk.Switch(); self.out_number_switch.set_valign(Gtk.Align.CENTER)
        self.out_number_switch.connect("notify::active", lambda *_: self._refresh_preview())
        num_row.append(self.out_number_switch)

        num_row2 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        num_row2.append(Gtk.Label(label=tr("Style"), xalign=0, hexpand=True))
        self.out_number_dd = Gtk.DropDown.new_from_strings(["1, 2, 3", "01, 02, 03", "001, 002, 003"])
        self.out_pos_dd = Gtk.DropDown.new_from_strings([tr("as prefix"), tr("as suffix")])
        self.out_number_dd.connect("notify::selected", lambda *_: self._refresh_preview())
        self.out_pos_dd.connect("notify::selected", lambda *_: self._refresh_preview())
        num_row2.append(self.out_number_dd); num_row2.append(self.out_pos_dd)

        orow = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        orow.append(Gtk.Label(label=tr("Overwrite originals"), xalign=0, hexpand=True))
        self.sw_overwrite = Gtk.Switch(); self.sw_overwrite.set_valign(Gtk.Align.CENTER)
        orow.append(self.sw_overwrite)

        self.process_btn = Gtk.Button(label=tr("Process files"))
        self.process_btn.add_css_class("suggested-action"); self.process_btn.add_css_class("pill")
        self.process_btn.set_margin_top(6)
        self.process_btn.connect("clicked", lambda *_: self._process())
        for w in (pick, self.output_label, name_row, num_row, num_row2, orow, self.process_btn):
            body.append(w)

    def _naming_config(self):
        raw = self.out_name_entry.get_text().strip()
        # Compare against both the English sentinel and its translated form: the
        # entry is pre-filled with tr("[Original file name]"), so in a non-English
        # UI `raw` holds the translated placeholder, not the English literal.
        keep = (not raw) or raw in ("[Original file name]", tr("[Original file name]"))
        add_number = self.out_number_switch.get_active()
        if keep and not add_number:
            return None
        name = re.sub(r'[/\\:*?<>|"]', "", raw).strip()
        return {"keep": keep, "name": name, "add_number": add_number,
                "pad": self.out_number_dd.get_selected() + 1,
                "prefix": self.out_pos_dd.get_selected() == 0}

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

    # ---- preview -----------------------------------------------------------
    def _build_preview(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_margin_top(12); box.set_margin_bottom(12)
        box.set_margin_start(6); box.set_margin_end(12)
        self.preview_stack = Gtk.Stack()
        self.preview_stack.set_vexpand(True); self.preview_stack.set_hexpand(True)
        self.canvas = Gtk.DrawingArea()
        self.canvas.set_draw_func(self._draw_canvas)
        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self._region_drag_begin)
        drag.connect("drag-update", self._region_drag_update)
        drag.connect("drag-end", self._region_drag_end)
        self.canvas.add_controller(drag)
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._on_canvas_motion)
        motion.connect("leave", lambda *_: self._set_canvas_cursor(None))
        self.canvas.add_controller(motion)
        rclick = Gtk.GestureClick()
        rclick.set_button(3)
        rclick.connect("pressed", self._region_right_click)
        self.canvas.add_controller(rclick)
        self.preview_stack.add_named(self.canvas, "image")
        # thumbnail-grid surface (Merge / Organize / Split-Extract page-select)
        self.grid_scroller = Gtk.ScrolledWindow()
        self.grid_scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.grid_flow = Gtk.FlowBox()
        self.grid_flow.set_valign(Gtk.Align.START)
        # selection is managed manually (SelectionMode.NONE) so we control
        # highlight, Ctrl/Shift-range and Delete ourselves — the native
        # FlowBox selection can't fill shift-ranges or show our highlight.
        self.grid_flow.set_selection_mode(Gtk.SelectionMode.NONE)
        self.grid_flow.set_max_children_per_line(6)
        self.grid_flow.set_min_children_per_line(2)
        self.grid_flow.set_row_spacing(8); self.grid_flow.set_column_spacing(8)
        self.grid_flow.set_homogeneous(True)
        gkey = Gtk.EventControllerKey()
        gkey.connect("key-pressed", self._grid_key)
        self.grid_flow.add_controller(gkey)
        # Overlay a non-interactive DrawingArea on top of the flow to paint the
        # themed insertion bar (reorder / file-drop) and split bars. It lets
        # pointer + drops fall through to the flow beneath (set_can_target False).
        self.grid_overlay = Gtk.Overlay()
        self.grid_overlay.set_child(self.grid_flow)
        self.grid_bar = Gtk.DrawingArea()
        self.grid_bar.set_can_target(False)
        self.grid_bar.set_draw_func(self._draw_grid_bar)
        self.grid_overlay.add_overlay(self.grid_bar)
        # Flow-level drop targets so motion coords are in flow space and the
        # insertion bar tracks a single drag across the whole grid.
        rtgt = Gtk.DropTarget.new(GObject.TYPE_INT, Gdk.DragAction.MOVE)
        rtgt.connect("motion", self._grid_reorder_motion)
        rtgt.connect("leave", lambda *_: self._grid_bar_clear())
        rtgt.connect("drop", self._grid_reorder_drop)
        self.grid_flow.add_controller(rtgt)
        ftgt = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        ftgt.connect("motion", self._grid_file_motion)
        ftgt.connect("leave", lambda *_: self._grid_bar_clear())
        ftgt.connect("drop", self._grid_file_drop)
        self.grid_flow.add_controller(ftgt)
        # Split-bar interaction (only live in split mode): tap a gap to add a
        # split, tap a bar to remove it, drag a bar to move it. A tap is just a
        # zero-distance drag, so one gesture handles all three.
        sdrag = Gtk.GestureDrag()
        sdrag.connect("drag-begin", self._grid_split_drag_begin)
        sdrag.connect("drag-update", self._grid_split_drag_update)
        sdrag.connect("drag-end", self._grid_split_drag_end)
        self.grid_flow.add_controller(sdrag)
        self.grid_scroller.set_child(self.grid_overlay)
        self.preview_stack.add_named(self.grid_scroller, "grid")
        self._grid_gap = None
        self._grid_splits = set()
        self._grid_split_mode = False
        self._split_drag = None
        self._install_grid_css()
        self.text_label = Gtk.Label()
        self.text_label.set_wrap(True); self.text_label.set_justify(Gtk.Justification.CENTER)
        self.text_label.set_vexpand(True)
        self.preview_stack.add_named(self.text_label, "text")
        self.empty_label = Gtk.Label(label=tr("Load files to see a preview"))
        self.empty_label.add_css_class("dim-label")
        self.preview_stack.add_named(self.empty_label, "empty")
        # ONE file-drop target on the always-visible stack, so a file dropped
        # anywhere on the preview lands regardless of which child (image / grid /
        # text / empty) is showing. It dispatches by tool kind: grid tools ADD,
        # everything else REPLACES.
        pdrop = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        pdrop.connect("drop", self._on_preview_drop)
        self.preview_stack.add_controller(pdrop)

        # zone-drawing hint: a banner row shown ABOVE the preview when a
        # region-drawing tool is active (not overlaid on the preview itself).
        self.zone_hint = Gtk.Label(label="")
        self.zone_hint.add_css_class("dim-label")
        self.zone_hint.add_css_class("caption")
        self.zone_hint.set_wrap(True)
        self.zone_hint.set_justify(Gtk.Justification.CENTER)
        self.zone_hint.set_visible(False)
        box.append(self.zone_hint)

        box.append(self.preview_stack)

        self.preview_btn = Gtk.Button(label=tr("Generate preview"))
        self.preview_btn.set_halign(Gtk.Align.CENTER)
        self.preview_btn.connect("clicked", lambda *_: self._run_image_preview())
        self.preview_btn.set_visible(False)
        box.append(self.preview_btn)

        self.page_nav = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.page_nav.set_halign(Gtk.Align.CENTER)
        pp = Gtk.Button.new_from_icon_name("go-up-symbolic")
        pp.set_tooltip_text(tr("Previous page")); pp.connect("clicked", lambda *_: self._step_page(-1))
        pn = Gtk.Button.new_from_icon_name("go-down-symbolic")
        pn.set_tooltip_text(tr("Next page")); pn.connect("clicked", lambda *_: self._step_page(1))
        self.page_label = Gtk.Button(label="Page 1 / 1")
        self.page_label.add_css_class("flat")
        self.page_label.set_tooltip_text(tr("Click to jump to a page"))
        self.page_jump_pop = Gtk.Popover()
        self.page_jump_pop.set_parent(self.page_label)
        jbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        for m in ("top", "bottom", "start", "end"):
            getattr(jbox, f"set_margin_{m}")(8)
        jbox.append(Gtk.Label(label=tr("Go to page")))
        self.page_jump_spin = Gtk.SpinButton.new_with_range(1, 1, 1)
        self.page_jump_spin.connect("activate", lambda *_: self._do_page_jump())
        go = Gtk.Button(label=tr("Go")); go.add_css_class("suggested-action")
        go.connect("clicked", lambda *_: self._do_page_jump())
        jbox.append(self.page_jump_spin); jbox.append(go)
        self.page_jump_pop.set_child(jbox)
        self.page_label.connect("clicked", lambda *_: self._open_page_jump())
        self.page_nav.append(pp); self.page_nav.append(self.page_label); self.page_nav.append(pn)
        box.append(self.page_nav)

        nav = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        nav.set_halign(Gtk.Align.CENTER)
        prev = Gtk.Button.new_from_icon_name("go-previous-symbolic")
        prev.connect("clicked", lambda *_: self._step_preview(-1))
        nxt = Gtk.Button.new_from_icon_name("go-next-symbolic")
        nxt.connect("clicked", lambda *_: self._step_preview(1))
        self.fname_label = Gtk.Label(label="")
        self.fname_label.set_use_markup(True)
        self.fname_label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        self.fname_label.set_max_width_chars(60)
        self.fname_label.set_hexpand(True)
        self.fname_label.set_justify(Gtk.Justification.CENTER)
        nav.append(prev); nav.append(self.fname_label); nav.append(nxt)
        box.append(nav)

        self.counter_label = Gtk.Label(label="0 / 0")
        self.counter_label.add_css_class("dim-label")
        self.counter_label.add_css_class("caption")
        box.append(self.counter_label)
        return box

    # ---- section nav -------------------------------------------------------
    def _select_section(self, section):
        for s in self.sections:
            s.set_expanded(s is section)

    # ---- files -------------------------------------------------------------
    def _choose_files(self, *_):
        d = Gtk.FileDialog(); d.set_title(tr("Add files"))
        d.open_multiple(self, None, self._on_files_chosen)

    def _on_files_chosen(self, d, res):
        try:
            model = d.open_multiple_finish(res)
        except GLib.Error:
            return
        self._set_files(self.files + [Path(model.get_item(i).get_path())
                                      for i in range(model.get_n_items())])

    def _choose_folder(self, *_):
        d = Gtk.FileDialog(); d.set_title(tr("Add folder"))
        d.select_folder(self, None, self._on_folder_chosen)

    def _on_folder_chosen(self, d, res):
        try:
            folder = d.select_folder_finish(res)
        except GLib.Error:
            return
        p = Path(folder.get_path())
        self._set_files(self.files + [q for q in sorted(p.iterdir()) if is_pdf(q) or is_image(q)])

    def _on_drop(self, target, value, x, y):
        paths = []
        for gfile in value.get_files():
            p = Path(gfile.get_path())
            if p.is_dir():
                paths += [q for q in sorted(p.iterdir()) if is_pdf(q) or is_image(q)]
            elif is_pdf(p) or is_image(p):
                paths.append(p)
        self._set_files(self.files + paths)
        return True

    def _on_preview_drop(self, target, value, x, y):
        """A file dropped anywhere on the preview panel. On a grid tool it is
        ADDED to the set (you're assembling a collection — merge/organize/split);
        on every other tool it REPLACES the file list and its options (you're
        switching subject, and stale margins/crops shouldn't carry over).
        Works from any preview state, including the empty placeholder."""
        paths = []
        for gfile in value.get_files():
            p = Path(gfile.get_path())
            if p.is_dir():
                paths += [q for q in sorted(p.iterdir()) if is_pdf(q) or is_image(q)]
            elif is_pdf(p) or is_image(p):
                paths.append(p)
        if not paths:
            return True
        spec = self.tools.get(self.tool)
        is_grid = (spec is not None and spec.has_preview_grid
                   and self._tool_preview_kind(spec, self._read_opts()) == "grid"
                   and self.files)
        if is_grid:
            self._set_files(self.files + paths)   # keep options → grid reconciles
        else:
            self._rebuild_options()
            self._set_files(paths)
        return True

    def _install_grid_css(self):
        """One accent-outline class for the manually-managed grid selection."""
        css = (".pf-sel{box-shadow:inset 0 0 0 3px @accent_bg_color;"
               "border-radius:12px;}")
        try:
            prov = Gtk.CssProvider()
            try:
                prov.load_from_string(css)          # GTK 4.12+
            except AttributeError:
                prov.load_from_data(css.encode(), -1)
            Gtk.StyleContext.add_provider_for_display(
                Gdk.Display.get_default(), prov,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        except Exception:
            traceback.print_exc()

    def _set_files(self, files):
        seen, uniq = set(), []
        for f in files:
            if f not in seen:
                seen.add(f); uniq.append(f)
        self.files = uniq
        self.preview_index = 0
        self.page_index = 0
        self._detected = []
        self._img_cache.clear()
        self._base_cache.clear()
        if not self.files:
            self.files_label.set_text("No files loaded")
        else:
            n_pdf = sum(1 for f in self.files if is_pdf(f))
            n_img = sum(1 for f in self.files if is_image(f))
            self.files_label.set_text(f"{len(self.files)} files  ({n_pdf} PDF, {n_img} image)")
        self._apply_suggested_options()
        self._refresh_preview()

    def _choose_output(self, *_):
        d = Gtk.FileDialog(); d.set_title(tr("Choose output folder"))
        d.select_folder(self, None, self._on_output_chosen)

    def _on_output_chosen(self, d, res):
        try:
            folder = d.select_folder_finish(res)
        except GLib.Error:
            return
        self.output_dir = Path(folder.get_path())
        self.output_label.set_text(str(self.output_dir))

    # ---- tool selection ----------------------------------------------------
    def _on_tool_toggled(self, btn, tid):
        if btn.get_active():
            self.tool = tid
            self.page_index = 0
            self._detected = []
            self._rebuild_options()
            self._apply_suggested_options()
            self._refresh_preview()

    # ---- preview logic -----------------------------------------------------
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
        n = max(self.page_count, 1)
        self.page_jump_spin.set_range(1, n)
        self.page_jump_spin.set_value(self.page_index + 1)
        self.page_jump_pop.popup()
        self.page_jump_spin.grab_focus()

    def _do_page_jump(self):
        target = int(self.page_jump_spin.get_value()) - 1
        self.page_jump_pop.popdown()
        target = max(0, min(target, self.page_count - 1))
        if target != self.page_index:
            self.page_index = target
            self._detected = []
            self._refresh_preview()

    def _page_dims(self, f):
        """Page size in the tool's native units: points for a PDF page, pixels
        for an image. Handles report drags in 0..1 fractions; a tool multiplies
        by these to recover its own units."""
        try:
            if is_pdf(f):
                R = fitz.open(f)[self.page_index].rect
                return float(R.width), float(R.height)
            w, h = Image.open(f).size
            return float(w), float(h)
        except Exception:
            return 1.0, 1.0

    # ---- warning / "why did nothing happen?" system -----------------------
    def _compute_warning(self):
        """Return a message explaining why the current tool doesn't fit the
        loaded files, or None. A tool may override with its own warning().
        Priority: strong ACCEPTS mismatch > tool warning() > partial-skip
        (mirrors the Windows edition — see SYNC-Linux-Windows.md §2/§3)."""
        spec = self.tools.get(self.tool)
        if spec is None or not self.files:
            return None
        acc = set(spec.accepts)
        relevant = [f for f in self.files if kind_of(f) in acc]
        # 1) Strongest: the tool can't use any of these files at all. This wins
        #    over a tool's own warning() — otherwise a PDF tool would try to read
        #    an image (PyMuPDF opens images as 1-page docs) and report the wrong
        #    thing.
        if not relevant:
            if acc == {"pdf"}:
                return tr("This tool works on PDF files, but you've loaded images.")
            if acc == {"image"}:
                return tr("This tool works on images, but you've loaded PDF files.")
            return None
        # 2) Types are fine — let the tool add finer guidance (extract with no
        #    pages selected, Convert's mode vs. file kind, …).
        fn = getattr(spec.module, "warning", None)
        if callable(fn):
            try:
                m = fn(self._preview_context(), self._read_opts())
                if m:
                    return tr(m)
            except Exception:
                traceback.print_exc()
        # 3) A mix where only some files apply — the rest will be skipped.
        if len(relevant) < len(self.files):
            skipped = len(self.files) - len(relevant)
            if acc == {"pdf"}:
                return tr("Only PDF files will be processed — {n} image(s) will be skipped.").format(n=skipped)
            if acc == {"image"}:
                return tr("Only images will be processed — {n} PDF file(s) will be skipped.").format(n=skipped)
        return None

    def _update_warning(self, msg):
        """Show/hide the amber header indicator for the given message (or None).
        Presentation is native GTK; the message set is shared with Windows."""
        if not hasattr(self, "warn_btn"):
            return
        self._warn_msg = msg
        self.warn_btn.set_visible(bool(msg))
        if msg:
            self.warn_label.set_text(msg)
            self.warn_btn.set_tooltip_text(msg)
        else:
            self.warn_label.set_text("")
            self.warn_btn.set_tooltip_text(tr("Warning"))
            pop = self.warn_btn.get_popover()
            if pop is not None and pop.get_visible():
                pop.popdown()

    def _apply_suggested_options(self):
        """When the file set or the tool changes, let the tool pick smarter option
        defaults for the new situation (e.g. Convert selects 'PDF → images' once a
        PDF is loaded). Runs ONLY on those transitions — never on a normal option
        tweak — so it never overrides a choice the user just made by hand."""
        spec = self.tools.get(self.tool)
        fn = getattr(spec.module, "suggest_options", None) if spec else None
        if not callable(fn):
            return
        try:
            writes = fn(self._preview_context(), self._read_opts()) or {}
        except Exception:
            traceback.print_exc()
            writes = {}
        if writes:
            self._suppress_refresh = True
            try:
                for k, v in writes.items():
                    self._set_option(k, v)
            finally:
                self._suppress_refresh = False

    def _preview_context(self, f=None):
        if f is None:
            f = self._current_file()
        pw, ph = self._page_dims(f) if f is not None else (1.0, 1.0)
        return {"files": [str(x) for x in self.files], "index": self.preview_index,
                "page_index": self.page_index, "page_w": pw, "page_h": ph}

    def _refresh_preview(self):
        if self._suppress_refresh:
            return
        self._apply_enable_rules()   # keep conditional (enabled_when) rows in sync
        self._apply_visible_rules()  # …and show/hide (visible_when) rows by mode
        self._update_warning(self._compute_warning())
        self._handles = []
        f = self._current_file()
        spec = self.tools.get(self.tool)
        if f is None or spec is None:
            self.preview_stack.set_visible_child_name("empty")
            self.fname_label.set_text(""); self.counter_label.set_text("0 / 0")
            self.page_nav.set_visible(False); self.preview_btn.set_visible(False)
            self.zone_hint.set_visible(False)
            return
        self.counter_label.set_text(f"{self.preview_index + 1} / {len(self.files)}")
        # page count
        if is_pdf(f):
            try:
                self.page_count = fitz.open(f).page_count
            except Exception:
                self.page_count = 1
        else:
            self.page_count = 1
        self.page_index = max(0, min(self.page_index, self.page_count - 1))
        self.page_nav.set_visible(self.page_count > 1)
        self.page_label.set_label(f"Page {self.page_index + 1} / {self.page_count}")

        opts = self._read_opts()
        context = self._preview_context(f)
        self._detected = getattr(self, "_detected", [])
        self._update_filename_label(f, spec, opts, context)
        self._compare_mode = False          # cleared on every refresh; re-armed
                                            # only when a compare preview applies

        # 0) thumbnail-grid surface (Merge / Organize / Split-Extract select)
        if self._tool_preview_kind(spec, opts) == "grid" and spec.has_preview_grid:
            self._show_grid(spec, opts, context)
            return

        self.zone_hint.set_visible(self._regions_enabled)
        if self._regions_enabled:
            self.zone_hint.set_text(self._zone_hint_text or
                                    "Zones are per-page · drag to add · right-click to delete · "
                                    "pages with no zones are skipped")

        # 1) cheap overlay/text preview
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
            self.preview_btn.set_visible(False)
            self.preview_stack.set_visible_child_name("text")
            self.text_label.set_markup(
                f"<span size='x-large' foreground='{RED}'>{GLib.markup_escape_text(str(old))}</span>\n"
                f"<span size='xx-large'>↓</span>\n"
                f"<span size='x-large' foreground='{GREEN}'>{GLib.markup_escape_text(str(new))}</span>")
            return

        has_heavy = spec.has_preview_image or spec.has_preview_regions
        show_button = has_heavy and self._wants_button(spec, f, opts)
        self.preview_btn.set_visible(show_button)

        # 2) image-replacing preview (e.g. background removal)
        # the options are part of the key: a result computed with other
        # settings is stale and must not be shown again
        key = (spec.id, str(f), self.page_index, self._opts_sig(opts))
        if spec.has_preview_image and key in self._img_cache:
            surf = self._img_cache[key]
            if spec.has_compare:
                self._render_source_base(f)          # "before"
                self._arm_compare(self._base_surface, surf)
            self._base_surface = surf
            self._overlay = None
            self.preview_stack.set_visible_child_name("image")
            self.canvas.queue_draw()
            return

        # render source as base, draw overlay(s)
        self.preview_stack.set_visible_child_name("image")
        self._render_source_base(f)
        self._overlay = self._overlay_from_spec(ov)
        self._handles = list((ov or {}).get("handles") or [])
        self.canvas.queue_draw()
        # auto-run any heavy preview the tool says is cheap enough not to gate
        if has_heavy and not show_button:
            self._run_image_preview()

    def _recompute_overlay_only(self):
        """Recompute overlay + handles from the tool's preview() and redraw,
        WITHOUT re-rendering the (cached) base surface. Cheap enough to run on
        every handle drag-update for a smooth live drag."""
        f = self._current_file()
        spec = self.tools.get(self.tool)
        if f is None or spec is None or not spec.has_preview:
            self.canvas.queue_draw()
            return
        try:
            ov = spec.module.preview(str(f), self.page_index,
                                     self._read_opts(), self._preview_context(f))
        except Exception:
            traceback.print_exc()
            ov = None
        self._overlay = self._overlay_from_spec(ov)
        self._handles = list((ov or {}).get("handles") or [])
        self.canvas.queue_draw()

    # ---- thumbnail-grid surface -------------------------------------------
    def _tool_preview_kind(self, spec, opts):
        """A tool may fix its surface with PREVIEW_KIND, or choose per-state via
        preview_kind(item, options). Default 'canvas'."""
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
        """Page count for a file (PDFs → real count, images → 1). Cheap and
        cached so flattening the whole grid across many files stays snappy."""
        key = str(fp)
        cache = getattr(self, "_pagecount_cache", None)
        if cache is None:
            cache = self._pagecount_cache = {}
        if key in cache:
            return cache[key]
        n = 1
        if is_pdf(fp):
            try:
                import fitz
                d = fitz.open(fp)
                n = d.page_count
                d.close()
            except Exception:
                n = 1
        cache[key] = n
        return n

    def _show_grid(self, spec, opts, context):
        self.preview_stack.set_visible_child_name("grid")
        self.page_nav.set_visible(False)
        self.zone_hint.set_visible(False)
        self.preview_btn.set_visible(False)
        try:
            gspec = spec.module.preview_grid(context, opts) or {}
        except Exception:
            traceback.print_exc()
            gspec = {}
        self._grid_spec = gspec
        source = gspec.get("source", "files")
        items = []
        if source == "pages":
            # Flatten EVERY page of EVERY file into one continuous sequence:
            # all of file 1's pages, then file 2's, …  No per-file arrows.
            for fi, fp in enumerate(self.files):
                pdf = is_pdf(fp)
                n = self._file_page_count(fp) if pdf else 1
                name = Path(fp).name
                for p in range(n):
                    cap = f"{name} · p{p + 1}" if pdf else name
                    items.append({"kind": "pdf-page" if pdf else "image",
                                  "path": str(fp), "page": p,
                                  "file": fi, "pageno": (p if pdf else None),
                                  "caption": cap, "key": (str(fp), p)})
        else:  # one thumbnail per loaded file
            for fi, fp in enumerate(self.files):
                # key uses page=None (NOT 0) so a whole-file unit never collides
                # with page 0 of the same file in pages view — that collision is
                # what made the files→pages toggle interleave first pages.
                items.append({"kind": "pdf" if is_pdf(fp) else "image",
                              "path": str(fp), "page": 0,
                              "file": fi, "pageno": None,
                              "caption": Path(fp).name, "key": (str(fp), None)})
        # Drop units the user deleted this session. Every refresh rebuilds the
        # grid from self.files, so without this the deleted pages would come
        # back (and, since they're no longer in the order, get appended at the
        # end). Prune stale keys whose file is no longer loaded so a fresh Load
        # starts clean; keep them across drag-add of more files.
        live = {str(fp) for fp in self.files}
        self._grid_deleted = {k for k in getattr(self, "_grid_deleted", set())
                              if k[0] in live}
        if self._grid_deleted:
            items = [it for it in items if it["key"] not in self._grid_deleted]
        for i, it in enumerate(items):
            it["orig"] = i                       # index-space id used elsewhere
        self._grid_items = items
        full = list(range(len(items)))
        key_to_idx = {it["key"]: i for i, it in enumerate(items)}
        prev_keys = getattr(self, "_grid_unit_keys", {}) or {}

        # Reconcile order/selection/anchor BY KEY, not index, so inserts,
        # removals and the pages/files toggle all keep a sensible order across
        # rebuilds. An explicit spec order (current index space) still wins.
        order = gspec.get("order")
        if isinstance(order, list) and sorted(order) == full:
            self._grid_order = list(order)
        else:
            seen, new_order = set(), []
            for o in self._grid_order:           # previous order, index space
                j = key_to_idx.get(prev_keys.get(o))
                if j is not None and j not in seen:
                    new_order.append(j); seen.add(j)
            for j in full:                        # append genuinely-new units
                if j not in seen:
                    new_order.append(j); seen.add(j)
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

        # split-bar mode: the tool supplies the current cut points; the host
        # paints persistent, draggable bars at those gaps and hands edits back
        # through on_split(). Tools decide what the bars mean (explicit cuts, or
        # an evenly-spaced split whose spacing the drag recomputes).
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

        # remember this build's index→key map for the next reconcile
        self._grid_unit_keys = {i: it["key"] for i, it in enumerate(items)}

        # Only rebuild the FlowBox when the displayed set/order actually changed;
        # a pure selection change updates the highlight in place. This is what
        # kills the flicker on every option tweak / selection click.
        sig = (source, tuple(self._grid_order),
               tuple(self._grid_items[o]["caption"] for o in self._grid_order))
        if sig == self._grid_sig:
            self._apply_grid_selection()
        else:
            self._grid_sig = sig
            self._render_grid_children()
        # split bars depend on child allocations → repaint after layout settles
        if self._grid_split_mode:
            GLib.idle_add(self._grid_bar_repaint_once)

    def _grid_bar_repaint_once(self):
        self.grid_bar.queue_draw()
        return False

    def _render_grid_children(self):
        fb = self.grid_flow
        # keep the scroll position across the rebuild (delete / reorder) instead
        # of snapping back to the top.
        vadj = self.grid_scroller.get_vadjustment()
        keep_scroll = vadj.get_value() if vadj else 0.0
        c = fb.get_first_child()
        while c:
            fb.remove(c); c = fb.get_first_child()
        self._grid_children = {}
        self._grid_cells = {}
        self._grid_gen += 1
        gen = self._grid_gen
        jobs = []
        for orig in self._grid_order:
            it = self._grid_items[orig]
            cell = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            cell.add_css_class("card")
            cell.set_margin_top(4); cell.set_margin_bottom(4)
            cell.set_margin_start(4); cell.set_margin_end(4)
            pic = Gtk.Picture()
            pic.set_size_request(120, 150)
            pic.set_content_fit(Gtk.ContentFit.CONTAIN)
            cap = Gtk.Label(label=it["caption"])
            cap.add_css_class("caption")
            cap.set_ellipsize(Pango.EllipsizeMode.MIDDLE); cap.set_max_width_chars(16)
            cell.append(pic); cell.append(cap)
            child = Gtk.FlowBoxChild()
            child.set_child(cell)
            child._orig = orig
            fb.append(child)
            self._grid_children[orig] = child
            self._grid_cells[orig] = cell
            # Only wire per-page click selection when the grid is actually
            # selectable (Extract / Organize). In split modes the grid isn't
            # selectable, and a child click gesture there would just grab the
            # pointer and starve the flow-level split-bar drag (tap-to-add and
            # bar dragging both stopped working because of this).
            if self._grid_selectable:
                gc = Gtk.GestureClick(); gc.set_button(1)
                gc.connect("pressed", self._grid_child_click, orig)
                child.add_controller(gc)
            if self._grid_reorderable:
                self._grid_wire_dnd(child)
            ck = f"{it['kind']}::{it['path']}::{it['page']}"
            png = self._thumb_cache.get(ck)
            if png and Path(png).exists():
                pic.set_filename(png)         # sync from cache → no empty flash
            else:
                jobs.append((pic, it["kind"], it["path"], it["page"], ck))
        self._apply_grid_selection()
        if jobs:
            self._start_thumb_worker(jobs, gen)
        if vadj and keep_scroll:
            # restore once layout has settled (children have a fixed size)
            GLib.idle_add(vadj.set_value, keep_scroll)

    def _apply_grid_selection(self):
        """Repaint the accent outline in place — no widget rebuild."""
        selset = set(self._grid_selected) if self._grid_selectable else set()
        for orig, cell in self._grid_cells.items():
            if orig in selset:
                cell.add_css_class("pf-sel")
            else:
                cell.remove_css_class("pf-sel")

    # -- selection (manual: plain / Ctrl-toggle / Shift-range) --
    def _grid_child_click(self, gesture, n_press, x, y, orig):
        if not self._grid_selectable:
            return
        try:
            state = gesture.get_current_event_state()
        except Exception:
            state = 0
        ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
        shift = bool(state & Gdk.ModifierType.SHIFT_MASK)
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
            # plain click on an item that's part of a multi-selection: keep the
            # whole selection so a drag can move the group. Collapsing to a
            # single item here is what dropped the other selected pages.
            self._grid_anchor = orig
        else:
            self._grid_selected = [orig]
            self._grid_anchor = orig
        self._apply_grid_selection()
        self._grid_fire_select()

    def _grid_fire_select(self):
        """If the tool cares about selection (Extract), hand it the picks so it
        can write its option (e.g. the 'pages' field)."""
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

    def _grid_key(self, controller, keyval, keycode, state):
        """Delete / Backspace removes the selected items from a reorderable
        grid (Organize / Merge). Non-reorderable grids ignore it."""
        if keyval not in (Gdk.KEY_Delete, Gdk.KEY_BackSpace):
            return False
        if not self._grid_reorderable or not self._grid_selected:
            return False
        remove = set(self._grid_selected)
        if len(remove) >= len(self._grid_order):
            return True                    # never remove the whole set
        # remember the deleted units BY KEY so they stay gone across the
        # rebuild-from-files that the next refresh performs.
        units = getattr(self, "_grid_items", [])
        rem_keys = {units[o]["key"] for o in remove if 0 <= o < len(units)}
        self._grid_deleted = getattr(self, "_grid_deleted", set()) | rem_keys
        self._grid_order = [o for o in self._grid_order if o not in remove]
        self._grid_selected = []
        self._grid_anchor = self._grid_order[0] if self._grid_order else None
        self._grid_sig = None              # order changed → force rebuild
        self._grid_fire_reorder()
        self._refresh_preview()
        return True

    # -- reorder (drag) : the drop + insertion bar are handled flow-level --
    def _grid_wire_dnd(self, child):
        src = Gtk.DragSource(); src.set_actions(Gdk.DragAction.MOVE)
        src.connect("prepare", self._grid_drag_prepare, child)
        child.add_controller(src)

    def _grid_drag_prepare(self, src, x, y, child):
        return Gdk.ContentProvider.new_for_value(GObject.Value(GObject.TYPE_INT, child._orig))

    # -- insertion bar: hit-test the gap between thumbnails in a wrapping flow --
    def _grid_compute_gap(self, px, py):
        """Given a pointer in grid_flow coords, return the insertion slot as
        {"index": pos_in_display_order, "x","y0","y1"} (bar geometry), or None."""
        order = self._grid_order
        if not order:
            return {"index": 0, "x": 6.0, "y0": 2.0, "y1": 150.0}
        rects = []
        for pos, orig in enumerate(order):
            child = self._grid_children.get(orig)
            if child is None:
                continue
            ok, r = child.compute_bounds(self.grid_flow)
            if ok:
                rects.append((pos, r.origin.x, r.origin.y, r.size.width, r.size.height))
        if not rects:
            return None
        rows = []
        for it in rects:
            for row in rows:
                if abs(row["y"] - it[2]) < 8:
                    row["items"].append(it); break
            else:
                rows.append({"y": it[2], "items": [it]})
        rows.sort(key=lambda r: r["y"])
        row = None
        for rw in rows:
            top = min(i[2] for i in rw["items"])
            bot = max(i[2] + i[4] for i in rw["items"])
            if top - 6 <= py <= bot + 6:
                row = rw; break
        if row is None:
            row = rows[-1] if py > rows[-1]["y"] else rows[0]
        items = sorted(row["items"], key=lambda i: i[1])
        top = min(i[2] for i in items); bot = max(i[2] + i[4] for i in items)
        for (pos, x, y, w, h) in items:
            if px < x + w / 2:
                return {"index": pos, "x": max(1.0, x - 4), "y0": top, "y1": bot}
        last = items[-1]
        return {"index": last[0] + 1, "x": last[1] + last[3] + 4, "y0": top, "y1": bot}

    def _grid_bar_clear(self):
        if self._grid_gap is not None:
            self._grid_gap = None
            self.grid_bar.queue_draw()

    def _grid_reorder_motion(self, target, x, y):
        if not self._grid_reorderable:
            return 0
        self._grid_gap = self._grid_compute_gap(x, y)
        self.grid_bar.queue_draw()
        return Gdk.DragAction.MOVE

    def _grid_reorder_drop(self, target, value, x, y):
        if not self._grid_reorderable:
            return False
        try:
            src_orig = int(value)
        except (TypeError, ValueError):
            self._grid_bar_clear(); return False
        gap = self._grid_compute_gap(x, y)
        self._grid_bar_clear()
        if gap is None or src_orig not in self._grid_order:
            return False
        # If the dragged page is part of a multi-selection, move the whole
        # selected group (in display order); otherwise just the dragged page.
        sel = set(self._grid_selected)
        if src_orig in sel and len(sel) > 1:
            moving = [o for o in self._grid_order if o in sel]
        else:
            moving = [src_orig]
        move_set = set(moving)
        pos = gap["index"]
        order = list(self._grid_order)
        before = sum(1 for o in order[:pos] if o in move_set)   # shift for gaps left
        rest = [o for o in order if o not in move_set]
        pos = max(0, min(len(rest), pos - before))
        self._grid_order = rest[:pos] + moving + rest[pos:]
        self._grid_selected = [o for o in self._grid_order if o in move_set]
        self._grid_anchor = moving[0]
        self._grid_sig = None              # order changed → force rebuild
        self._grid_fire_reorder()
        self._refresh_preview()
        return True

    def _grid_file_motion(self, target, x, y):
        if not self._grid_reorderable or not self.files:
            return 0
        self._grid_gap = self._grid_compute_gap(x, y)
        self.grid_bar.queue_draw()
        return Gdk.DragAction.COPY

    def _grid_file_drop(self, target, value, x, y):
        paths = []
        for gfile in value.get_files():
            p = Path(gfile.get_path())
            if p.is_dir():
                paths += [q for q in sorted(p.iterdir()) if is_pdf(q) or is_image(q)]
            elif is_pdf(p) or is_image(p):
                paths.append(p)
        gap = self._grid_compute_gap(x, y)
        pos = gap["index"] if gap else len(self._grid_order)
        self._grid_bar_clear()
        if not paths:
            return False
        prev_keys = set((getattr(self, "_grid_unit_keys", {}) or {}).values())
        self._set_files(self.files + paths)          # rebuild; new units appended
        new_units = [i for i in self._grid_order
                     if (getattr(self, "_grid_unit_keys", {}) or {}).get(i) not in prev_keys]
        if new_units:
            rest = [o for o in self._grid_order if o not in set(new_units)]
            pos = max(0, min(len(rest), pos))
            self._grid_order = rest[:pos] + new_units + rest[pos:]
            self._grid_sig = None
            self._grid_fire_reorder()
            self._refresh_preview()
        return True

    def _draw_grid_bar(self, area, cr, w, h):
        r, g, b = self._accent_rgb()
        # split bars (persistent, split mode) …
        if self._grid_split_mode:
            for pos in sorted(self._grid_splits):
                geo = self._grid_gap_geometry(pos)
                if geo:
                    self._paint_bar(cr, geo, (r, g, b), 1.0)
        # … and the live insertion bar (during a drag)
        if self._grid_gap is not None:
            self._paint_bar(cr, self._grid_gap, (r, g, b), 1.0)

    @staticmethod
    def _paint_bar(cr, geo, rgb, alpha):
        r, g, b = rgb
        x, y0, y1 = geo["x"], geo["y0"], geo["y1"]
        cr.set_source_rgba(r, g, b, alpha)
        cr.set_line_width(4)
        cr.move_to(x, y0 + 2); cr.line_to(x, y1 - 2); cr.stroke()
        for yy in (y0 + 2, y1 - 2):
            cr.arc(x, yy, 4, 0, 6.2832); cr.fill()

    # -- split bars (Split — at pages) --------------------------------------
    def _grid_gap_geometry(self, pos):
        """Bar geometry for the gap BEFORE display position `pos` (or after the
        last item when pos == len). Used to paint persistent split bars."""
        order = self._grid_order
        if not order:
            return None
        def bounds(orig):
            child = self._grid_children.get(orig)
            if child is None:
                return None
            ok, r = child.compute_bounds(self.grid_flow)
            return (r.origin.x, r.origin.y, r.size.width, r.size.height) if ok else None
        if pos <= 0:
            b = bounds(order[0])
            return {"x": max(1.0, b[0] - 4), "y0": b[1], "y1": b[1] + b[3]} if b else None
        if pos >= len(order):
            b = bounds(order[-1])
            return {"x": b[0] + b[2] + 4, "y0": b[1], "y1": b[1] + b[3]} if b else None
        b = bounds(order[pos])
        bp = bounds(order[pos - 1])
        if b is None:
            return None
        # if the previous thumb is on an earlier row, sit the bar left of this one
        y0, y1 = b[1], b[1] + b[3]
        if bp and abs(bp[1] - b[1]) < 8:
            x = (bp[0] + bp[2] + b[0]) / 2
        else:
            x = max(1.0, b[0] - 4)
        return {"x": x, "y0": y0, "y1": y1}

    def _nearest_split(self, px, py, threshold=18):
        """Return the split position whose bar is within `threshold` px of the
        pointer, or None."""
        best, bestd = None, threshold
        for pos in self._grid_splits:
            geo = self._grid_gap_geometry(pos)
            if geo and geo["y0"] - 8 <= py <= geo["y1"] + 8:
                d = abs(geo["x"] - px)
                if d < bestd:
                    best, bestd = pos, d
        return best

    _SPLIT_TAP_PX = 6                      # movement under this = a tap, not a drag

    def _grid_split_drag_begin(self, gesture, sx, sy):
        self._split_drag = None            # position of the bar being dragged
        self._split_candidate = None       # a bar under the initial press
        self._split_moved = False
        if not self._grid_split_mode:
            return
        self._split_candidate = self._nearest_split(sx, sy)
        _dbg("split drag-begin at (%.0f,%.0f) mode=%s candidate=%s splits=%s"
             % (sx, sy, self._grid_split_mode, self._split_candidate,
                sorted(self._grid_splits)))
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)

    def _grid_split_drag_update(self, gesture, ox, oy):
        if not self._grid_split_mode:
            return
        if abs(ox) < self._SPLIT_TAP_PX and abs(oy) < self._SPLIT_TAP_PX:
            return                         # still within tap tolerance
        if self._split_candidate is None:
            return                         # started on empty space → not a move
        if self._split_drag is None:
            self._split_drag = self._split_candidate
        ok, sx, sy = gesture.get_start_point()
        gap = self._grid_compute_gap(sx + ox, sy + oy)
        if gap and 0 < gap["index"] < len(self._grid_order):
            new = gap["index"]
            if new != self._split_drag:
                self._grid_splits.discard(self._split_drag)
                self._grid_splits.add(new)
                self._split_drag = new
                self._split_moved = True
                _dbg("split drag-update -> bar moved to %d, splits=%s"
                     % (new, sorted(self._grid_splits)))
                self.grid_bar.queue_draw()

    def _grid_split_drag_end(self, gesture, ox, oy):
        if not self._grid_split_mode:
            return
        moved = abs(ox) >= self._SPLIT_TAP_PX or abs(oy) >= self._SPLIT_TAP_PX
        if moved and self._split_drag is not None:
            pass                                   # bar already moved live
        elif self._split_candidate is not None:    # tap on a bar → remove it
            self._grid_splits.discard(self._split_candidate)
        else:                                      # tap on a gap → add a split
            ok, sx, sy = gesture.get_start_point()
            gap = self._grid_compute_gap(sx, sy)
            if gap and 0 < gap["index"] < len(self._grid_order):
                self._grid_splits.add(gap["index"])
        _dbg("split drag-end moved=%s splits=%s" % (moved, sorted(self._grid_splits)))
        self._split_drag = self._split_candidate = None
        self._split_moved = False
        self.grid_bar.queue_draw()
        self._grid_fire_split()

    def _grid_fire_split(self):
        """Hand the current split points back to the tool via on_split(points)."""
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

    def _grid_fire_reorder(self):
        spec = self.tools.get(self.tool)
        fn = getattr(spec.module, "on_reorder", None) if spec else None
        if callable(fn):
            try:
                fn(list(self._grid_order), self._read_opts(), self._preview_context())
            except Exception:
                traceback.print_exc()

    # -- thumbnails (rendered off the main thread, cached to disk) --
    def _start_thumb_worker(self, jobs, gen):
        def work():
            for pic, kind, path, page, ck in jobs:
                if gen != self._grid_gen:
                    return
                png = self._thumb_cache.get(ck)
                if not png or not Path(png).exists():
                    png = self._render_thumb_png(kind, path, page, ck)
                if png and gen == self._grid_gen:
                    GLib.idle_add(self._set_thumb, pic, png, gen)
        threading.Thread(target=work, daemon=True).start()

    def _set_thumb(self, pic, png, gen):
        if gen == self._grid_gen:
            pic.set_filename(png)
        return False

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
                im = Image.open(path); im.thumbnail((320, 400))
                im.convert("RGB").save(str(out))
            self._thumb_cache[ck] = str(out)
            return str(out)
        except Exception:
            traceback.print_exc()
            return None

    def _wants_button(self, spec, f, opts):
        """Whether the Generate-preview button should be shown (True) or the heavy
        preview should just run automatically (False). The tool decides per file
        via needs_preview_button(); otherwise fall back to PREVIEW_ON_DEMAND."""
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
            self.fname_label.set_markup(
                f"<span foreground='{RED}'>{GLib.markup_escape_text(old)}</span>"
                f"  →  <span foreground='{GREEN}'>{GLib.markup_escape_text(new)}</span>")
        else:
            self.fname_label.set_markup(GLib.markup_escape_text(old))

    def _predicted_name(self, f, spec, opts, context):
        """Best-effort final name: tool's rename (via its text preview) then Output naming."""
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
            self._base_surface, self._unit_scale, self._surf_w, self._surf_h = cached
            return
        self._base_surface = None
        self._unit_scale = 1.0
        try:
            if is_pdf(f):
                page = fitz.open(f)[self.page_index]
                zoom = PREVIEW_DPI / 72
                pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
                img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
                self._unit_scale = zoom
            else:
                src = Image.open(f); src.load()
                orig_w = src.width
                # A source image with transparency must be shown on a checkerboard,
                # not flattened with .convert("RGB"): flattening reveals the black
                # RGB stored under fully-transparent pixels (a black frame), and the
                # thumbnail resample then bleeds that black into the subject's edges
                # as a speckled fringe. Compositing onto the checker first makes the
                # image opaque, so neither artefact can appear.
                if src.mode in ("RGBA", "LA") or (src.mode == "P" and "transparency" in src.info):
                    img = composite_on_checker(src.convert("RGBA"))
                else:
                    img = src.convert("RGB")
                img.thumbnail((1600, 1600))
                self._unit_scale = img.width / orig_w
            self._surf_w, self._surf_h = img.width, img.height
            self._base_surface = pil_to_surface(img)
            self._base_cache[key] = (self._base_surface, self._unit_scale, self._surf_w, self._surf_h)
            if len(self._base_cache) > 8:
                self._base_cache.pop(next(iter(self._base_cache)))
        except Exception:
            traceback.print_exc()

    def _overlay_from_spec(self, ov):
        """Return the overlay-kind tuple and record its style. A tool may set
        `color`, `fill` and `dash` on its preview() dict; the app only supplies
        defaults, so appearance lives entirely in the script."""
        self._overlay_style = {}
        if not ov:
            return None
        # dashed by default for multi-box overlays, solid otherwise — but the
        # tool can override with an explicit `dash`.
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
                return (x0 * self._surf_w, y0 * self._surf_h, x1 * self._surf_w, y1 * self._surf_h)
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

    def _accent_rgb(self):
        """The OS/theme accent colour as an (r,g,b) 0..1 tuple, so overlays like
        the compare divider match the desktop. Falls back to GNOME blue."""
        try:                                    # libadwaita 1.6+ explicit API
            mgr = Adw.StyleManager.get_default()
            rgba = mgr.get_accent_color().to_standalone_rgba(mgr.get_dark())
            return (rgba.red, rgba.green, rgba.blue)
        except Exception:
            pass
        try:                                    # theme CSS colour lookup
            ok, rgba = self.get_style_context().lookup_color("accent_bg_color")
            if ok:
                return (rgba.red, rgba.green, rgba.blue)
        except Exception:
            pass
        return parse_rgb("#3584e4", OVERLAY_COLOR)

    def _draw_compare(self, cr, width, height):
        """Before/after split view: the generated 'after' fills the frame; the
        source 'before' is clipped to the left of a draggable vertical divider.
        Both are stretched to the same on-screen rect so features line up even
        when the after surface is a different resolution (e.g. Upscale)."""
        before, after = self._compare_before, self._compare_after
        aw, ah = after.get_width(), after.get_height()
        scale = min(width / aw, height / ah) * 0.96
        dw, dh = aw * scale, ah * scale
        ox, oy = (width - dw) / 2, (height - dh) / 2
        self._compare_rect = (ox, oy, dw, dh)
        div = max(0.0, min(1.0, getattr(self, "_compare_x", 0.5)))
        divx = ox + dw * div

        cr.save(); cr.translate(ox, oy); cr.scale(scale, scale)
        cr.set_source_surface(after, 0, 0); cr.paint(); cr.restore()

        if dw * div > 0.5:
            cr.save()
            cr.rectangle(ox, oy, dw * div, dh); cr.clip()
            cr.translate(ox, oy)
            cr.scale(dw / before.get_width(), dh / before.get_height())
            cr.set_source_surface(before, 0, 0); cr.paint()
            cr.restore()

        r, g, b = self._accent_rgb()
        cr.set_source_rgba(1, 1, 1, 0.9); cr.set_line_width(3)
        cr.move_to(divx, oy); cr.line_to(divx, oy + dh); cr.stroke()
        cr.set_source_rgba(r, g, b, 0.95); cr.set_line_width(1.5)
        cr.move_to(divx, oy); cr.line_to(divx, oy + dh); cr.stroke()
        cy = oy + dh / 2
        cr.arc(divx, cy, 10, 0, 6.2832)
        cr.set_source_rgba(1, 1, 1, 0.95); cr.fill()
        cr.arc(divx, cy, 10, 0, 6.2832)
        cr.set_source_rgba(r, g, b, 0.95); cr.set_line_width(2); cr.stroke()
        for dxp in (-3, 3):
            cr.move_to(divx + dxp, cy - 4); cr.line_to(divx + dxp, cy + 4)
        cr.stroke()

        cr.select_font_face("Sans"); cr.set_font_size(13)
        for label, tx, anchor_right in (("Before", ox + 8, False),
                                        ("After", ox + dw - 8, True)):
            ext = cr.text_extents(label)
            px = tx - ext.width if anchor_right else tx
            cr.set_source_rgba(0, 0, 0, 0.55)
            cr.rectangle(px - 4, oy + 6, ext.width + 8, 20); cr.fill()
            cr.set_source_rgba(1, 1, 1, 0.95)
            cr.move_to(px, oy + 21); cr.show_text(label)

    def _draw_canvas(self, area, cr, width, height):
        if (getattr(self, "_compare_mode", False)
                and getattr(self, "_compare_before", None)
                and getattr(self, "_compare_after", None)):
            self._draw_compare(cr, width, height)
            return
        surface = self._base_surface
        if surface is None:
            return
        sw, sh = surface.get_width(), surface.get_height()
        self._surf_w, self._surf_h = sw, sh
        scale = min(width / sw, height / sh) * 0.96
        ox, oy = (width - sw * scale) / 2, (height - sh * scale) / 2
        self._last_scale, self._last_ox, self._last_oy = scale, ox, oy
        cr.save(); cr.translate(ox, oy); cr.scale(scale, scale)
        cr.set_source_surface(surface, 0, 0); cr.paint(); cr.restore()

        if self._overlay:
            cr.save(); cr.translate(ox, oy); cr.scale(scale, scale)
            style = self._overlay_style or {}
            r, g, b = style.get("color") or parse_rgb(None, OVERLAY_COLOR)
            cr.set_line_width(2 / scale)
            kind = self._overlay[0]
            if kind == "grid":
                cr.set_source_rgba(r, g, b, 0.95)
                _, cols, rows = self._overlay
                for c in range(1, cols):
                    cr.move_to(sw * c / cols, 0); cr.line_to(sw * c / cols, sh)
                for row in range(1, rows):
                    cr.move_to(0, sh * row / rows); cr.line_to(sw, sh * row / rows)
                cr.stroke()
            elif kind == "cropgrid":
                # The kept tiles are outlined; everything trimmed away — the
                # outer margin AND, when a gutter is set, the strips between the
                # cells — is washed and hatched the same way.
                _, x0, y0, x1, y1, cols, rows, gut = self._overlay
                cw, ch = (x1 - x0) / cols, (y1 - y0) / rows
                if gut > 0:
                    ins = gut / 2.0
                    tiles = []
                    for ri in range(rows):
                        for ci in range(cols):
                            tx0 = x0 + ci * cw + ins; ty0 = y0 + ri * ch + ins
                            tx1 = x0 + (ci + 1) * cw - ins; ty1 = y0 + (ri + 1) * ch - ins
                            if tx1 > tx0 and ty1 > ty0:
                                tiles.append((tx0, ty0, tx1, ty1))
                else:
                    tiles = [(x0, y0, x1, y1)]
                # wash + hatch the whole surface minus the kept tiles
                cr.save()
                cr.rectangle(0, 0, sw, sh)
                for (tx0, ty0, tx1, ty1) in tiles:
                    cr.rectangle(tx0, ty0, tx1 - tx0, ty1 - ty0)
                cr.set_fill_rule(cairo.FILL_RULE_EVEN_ODD)
                cr.clip()
                cr.set_source_rgba(r, g, b, 0.12)
                cr.rectangle(0, 0, sw, sh); cr.fill()
                cr.set_source_rgba(r, g, b, 0.55)
                cr.set_line_width(1 / scale)
                step = 12 / scale
                hx = -sh
                while hx < sw:
                    cr.move_to(hx, 0); cr.line_to(hx + sh, sh); cr.stroke()
                    hx += step
                cr.restore()
                # outline the kept region and each kept tile
                cr.set_source_rgba(r, g, b, 0.95)
                cr.set_line_width(2 / scale)
                cr.rectangle(x0, y0, x1 - x0, y1 - y0); cr.stroke()
                if gut > 0:
                    cr.set_line_width(1.5 / scale)
                    for (tx0, ty0, tx1, ty1) in tiles:
                        cr.rectangle(tx0, ty0, tx1 - tx0, ty1 - ty0)
                    cr.stroke()
                else:
                    for c in range(1, cols):
                        gx = x0 + (x1 - x0) * c / cols
                        cr.move_to(gx, y0); cr.line_to(gx, y1)
                    for row in range(1, rows):
                        gy = y0 + (y1 - y0) * row / rows
                        cr.move_to(x0, gy); cr.line_to(x1, gy)
                    cr.stroke()
            elif kind == "rect":
                cr.set_source_rgba(r, g, b, 0.95)
                _, x0, y0, x1, y1 = self._overlay
                cr.rectangle(x0, y0, x1 - x0, y1 - y0); cr.stroke()
            elif kind == "boxes":
                cr.set_dash(parse_dash(style.get("dash", True), scale))
                fill = style.get("fill")
                for (x0, y0, x1, y1) in self._overlay[1]:
                    if fill:
                        cr.set_source_rgba(fill[0], fill[1], fill[2], 0.12)
                        cr.rectangle(x0, y0, x1 - x0, y1 - y0); cr.fill()
                    cr.set_source_rgba(r, g, b, 0.95)
                    cr.rectangle(x0, y0, x1 - x0, y1 - y0); cr.stroke()
                cr.set_dash([])
            cr.restore()

        # draggable handles declared by the tool's preview() (fractions 0..1).
        # No grab tabs — the cursor changes to a resize arrow on hover instead.
        if getattr(self, "_handles", None):
            cr.save(); cr.translate(ox, oy); cr.scale(scale, scale)
            cr.set_dash([])
            for h in self._handles:
                hr, hg, hb = parse_rgb(h.get("color"), OVERLAY_COLOR)
                cr.set_source_rgba(hr, hg, hb, 0.95)
                cr.set_line_width(2 / scale)
                cr.set_dash(parse_dash(True, scale) if h.get("dash") else [])
                kind = h.get("kind")
                if kind == "vline":
                    x = float(h.get("x", 0.0)) * sw
                    cr.move_to(x, 0); cr.line_to(x, sh); cr.stroke()
                elif kind == "hline":
                    y = float(h.get("y", 0.0)) * sh
                    cr.move_to(0, y); cr.line_to(sw, y); cr.stroke()
                elif kind == "point":
                    x = float(h.get("x", 0.0)) * sw
                    y = float(h.get("y", 0.0)) * sh
                    if h.get("dash"):
                        cr.arc(x, y, 6 / scale, 0, 6.2832); cr.stroke()
                    else:
                        cr.arc(x, y, 6 / scale, 0, 6.2832); cr.fill()
            cr.set_dash([])
            cr.restore()

        # detected layout (heavy preview); colour comes from the tool, default blue
        if getattr(self, "_detected", None):
            cr.save(); cr.translate(ox, oy); cr.scale(scale, scale)
            style = self._detected_style or {}
            r, g, b = style.get("color") or parse_rgb(None, DETECT_COLOR)
            dash = parse_dash(style.get("dash", False), scale)
            cr.set_line_width(2 / scale)
            for (x0, y0, x1, y1) in self._detected:
                cr.set_dash([])
                cr.set_source_rgba(r, g, b, 0.12)
                cr.rectangle(x0 * sw, y0 * sh, (x1 - x0) * sw, (y1 - y0) * sh); cr.fill()
                cr.set_dash(dash)
                cr.set_source_rgba(r, g, b, 0.95)
                cr.rectangle(x0 * sw, y0 * sh, (x1 - x0) * sw, (y1 - y0) * sh); cr.stroke()
            cr.set_dash([])
            cr.restore()

        if self._regions_enabled:
            cr.save(); cr.translate(ox, oy); cr.scale(scale, scale)
            zr, zg, zb = (self._zone_style or {}).get("color") or parse_rgb(None, ZONE_COLOR)
            cr.set_line_width(2 / scale)
            boxes = list(self._regions.get(self.page_index, []))
            order = self._zone_order(boxes)
            if self._drawing:
                boxes = boxes + [tuple(self._drawing)]
            for i, (x0, y0, x1, y1) in enumerate(boxes):
                rx, ry = min(x0, x1) * sw, min(y0, y1) * sh
                rw, rh = abs(x1 - x0) * sw, abs(y1 - y0) * sh
                cr.set_source_rgba(zr, zg, zb, 0.15)
                cr.rectangle(rx, ry, rw, rh); cr.fill()
                cr.set_source_rgba(zr, zg, zb, 0.95)
                cr.rectangle(rx, ry, rw, rh); cr.stroke()
                # no drawn grab squares — the cursor changes over edges/corners
                if i < len(order):
                    cr.set_font_size(16 / scale)
                    label = str(order[i])
                    cr.move_to(rx + rw - 14 / scale, ry + 16 / scale)
                    cr.show_text(label)
            cr.restore()

    def _zone_order(self, boxes):
        """Return the processing-order badge number for each zone (in draw order)."""
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

    # ---- zone drawing ------------------------------------------------------
    def _widget_to_frac(self, wx, wy):
        sc = self._last_scale or 1.0
        fx = (wx - self._last_ox) / (self._surf_w * sc)
        fy = (wy - self._last_oy) / (self._surf_h * sc)
        return max(0.0, min(1.0, fx)), max(0.0, min(1.0, fy))

    def _zone_at(self, fx, fy):
        page = self._regions.get(self.page_index, [])
        for i in range(len(page) - 1, -1, -1):
            x0, y0, x1, y1 = page[i]
            if min(x0, x1) <= fx <= max(x0, x1) and min(y0, y1) <= fy <= max(y0, y1):
                return i
        return None

    def _corner_at(self, box, fx, fy):
        sc = self._last_scale or 1.0
        tol_x = 16 / (self._surf_w * sc)
        tol_y = 16 / (self._surf_h * sc)
        x0, y0, x1, y1 = box
        x0, x1 = sorted((x0, x1)); y0, y1 = sorted((y0, y1))
        corners = {"nw": (x0, y0), "ne": (x1, y0), "sw": (x0, y1), "se": (x1, y1)}
        for name, (cx, cy) in corners.items():
            if abs(fx - cx) <= tol_x and abs(fy - cy) <= tol_y:
                return name
        return None

    def _zone_hit(self, box, fx, fy):
        """Classify a point against a zone: a corner (nw/ne/sw/se), a side
        (n/s/e/w), the body (move), or None. Corners beat sides beat body, so a
        near-corner point resizes diagonally rather than along one edge."""
        sc = self._last_scale or 1.0
        tol_x = 16 / max(1.0, self._surf_w * sc)
        tol_y = 16 / max(1.0, self._surf_h * sc)
        x0, y0, x1, y1 = box
        x0, x1 = sorted((x0, x1)); y0, y1 = sorted((y0, y1))
        if not (x0 - tol_x <= fx <= x1 + tol_x and y0 - tol_y <= fy <= y1 + tol_y):
            return None
        near_l, near_r = abs(fx - x0) <= tol_x, abs(fx - x1) <= tol_x
        near_t, near_b = abs(fy - y0) <= tol_y, abs(fy - y1) <= tol_y
        if near_t and near_l: return "nw"
        if near_t and near_r: return "ne"
        if near_b and near_l: return "sw"
        if near_b and near_r: return "se"
        if near_t: return "n"
        if near_b: return "s"
        if near_l: return "w"
        if near_r: return "e"
        if x0 <= fx <= x1 and y0 <= fy <= y1:
            return "move"
        return None

    def _handle_at(self, fx, fy):
        """Nearest declared handle within grab tolerance, or None. Handle
        positions are 0..1 fractions; vline cares about x, hline about y."""
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

    _CURSOR_FOR_KIND = {"vline": "ew-resize", "hline": "ns-resize", "point": "move"}
    _ZONE_CURSOR = {"nw": "nwse-resize", "se": "nwse-resize",
                    "ne": "nesw-resize", "sw": "nesw-resize",
                    "n": "ns-resize", "s": "ns-resize",
                    "e": "ew-resize", "w": "ew-resize", "move": "move"}

    def _on_canvas_motion(self, _ctrl, wx, wy):
        """Hovering a draggable handle — or a zone edge/corner/body — swaps the
        pointer to the matching resize cursor; no visible grab tab is drawn."""
        if (self._dragging_handle is not None or self._base_surface is None
                or self._resizing is not None or self._moving is not None
                or self._drawing is not None):
            return
        fx, fy = self._widget_to_frac(wx, wy)
        h = self._handle_at(fx, fy)
        name = self._CURSOR_FOR_KIND.get(h.get("kind")) if h else None
        if name is None and self._regions_enabled:
            name = self._zone_cursor(fx, fy)
        self._set_canvas_cursor(name)

    def _zone_cursor(self, fx, fy):
        page = self._regions.get(self.page_index, [])
        for i in range(len(page) - 1, -1, -1):
            mode = self._zone_hit(tuple(page[i]), fx, fy)
            if mode:
                return self._ZONE_CURSOR.get(mode)
        return None

    def _set_canvas_cursor(self, name):
        if name == self._cursor_name:
            return
        self._cursor_name = name
        cur = Gdk.Cursor.new_from_name(name, None) if name else None
        self.canvas.set_cursor(cur)

    def _apply_handle_drag(self, handle_id, fx, fy):
        """Route a handle drag to the tool's on_handle_drag(), apply the option
        writes it returns, and live-redraw the overlay without a base re-render."""
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
        rect = getattr(self, "_compare_rect", None)
        if not rect:
            return
        ox, _oy, dw, _dh = rect
        self._compare_x = max(0.0, min(1.0, (wx - ox) / dw)) if dw else 0.5
        self.canvas.queue_draw()

    def _region_drag_begin(self, gesture, sx, sy):
        if self._base_surface is None:
            return
        self._drag_start = (sx, sy)
        if getattr(self, "_compare_mode", False):
            self._compare_drag = True
            self._update_compare_x(sx)
            return
        self._compare_drag = False
        fx, fy = self._widget_to_frac(sx, sy)
        # handles take priority over zones, and work even when zones are off
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
        self.canvas.queue_draw()

    def _region_drag_update(self, gesture, ox, oy):
        sx, sy = self._drag_start
        if getattr(self, "_compare_drag", False):
            self._update_compare_x(sx + ox)
            return
        fx, fy = self._widget_to_frac(sx + ox, sy + oy)
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
            ox0, ox1 = sorted((ox0, ox1)); oy0, oy1 = sorted((oy0, oy1))
            mode = self._resizing["mode"]
            nx0, ny0, nx1, ny1 = ox0, oy0, ox1, oy1
            if "w" in mode: nx0 = fx
            if "e" in mode: nx1 = fx
            if "n" in mode: ny0 = fy
            if "s" in mode: ny1 = fy
            page[i] = (nx0, ny0, nx1, ny1)
            self.canvas.queue_draw()
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
            self.canvas.queue_draw()
            return
        if self._drawing is None:
            return
        self._drawing[2], self._drawing[3] = fx, fy
        self.canvas.queue_draw()

    def _region_drag_end(self, gesture, ox, oy):
        if getattr(self, "_compare_drag", False):
            self._compare_drag = False
            return
        if self._dragging_handle is not None:
            self._dragging_handle = None
            self._refresh_preview()   # full reconcile after the drag
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
            self.canvas.queue_draw()
            return
        if self._moving is not None:
            self._moving = None
            self.canvas.queue_draw()
            return
        if self._drawing is None:
            return
        x0, y0, x1, y1 = self._drawing
        self._drawing = None
        x0, x1 = sorted((x0, x1))
        y0, y1 = sorted((y0, y1))
        if (x1 - x0) > 0.02 and (y1 - y0) > 0.02:
            self._regions.setdefault(self.page_index, []).append((x0, y0, x1, y1))
        self.canvas.queue_draw()

    def _region_right_click(self, gesture, n_press, wx, wy):
        page = self._regions.get(self.page_index)
        if not self._regions_enabled or not page:
            return
        fx, fy = self._widget_to_frac(wx, wy)
        for i in range(len(page) - 1, -1, -1):
            x0, y0, x1, y1 = page[i]
            if min(x0, x1) <= fx <= max(x0, x1) and min(y0, y1) <= fy <= max(y0, y1):
                del page[i]
                self.canvas.queue_draw()
                return

    def _copy_zones_to_all(self):
        cur = self._regions.get(self.page_index, [])
        if not cur:
            return
        n = max(self.page_count, 1)
        for p in range(n):
            self._regions[p] = list(cur)
        self.canvas.queue_draw()

    def _run_image_preview(self):
        f = self._current_file()
        spec = self.tools.get(self.tool)
        if f is None or spec is None:
            return
        page_index = self.page_index
        opts = self._read_opts()
        self.preview_btn.set_label(tr("Working…")); self.preview_btn.set_sensitive(False)

        def work():
            try:
                if spec.has_preview_regions:
                    result = spec.module.preview_regions(str(f), page_index, opts)
                    boxes, style = self._parse_detected(result)
                    GLib.idle_add(self._apply_detected, (spec.id, str(f), page_index), boxes, style)
                elif spec.has_preview_image:
                    img = spec.module.preview_image(str(f), page_index, opts)
                    if img.mode == "RGBA":
                        img = composite_on_checker(img)
                    surf = pil_to_surface(img)
                    GLib.idle_add(self._apply_image_preview, (spec.id, str(f), page_index, self._opts_sig(opts)), surf)
                else:
                    GLib.idle_add(self._image_preview_error, "This tool has no preview to generate.")
            except Exception as e:
                traceback.print_exc()
                GLib.idle_add(self._image_preview_error, str(e))

        threading.Thread(target=work, daemon=True).start()

    @staticmethod
    def _parse_detected(result):
        """preview_regions may return a plain list of boxes (default styling) or a
        dict {"boxes": [...], "color": .., "fill": .., "dash": ..} so a tool can
        style its detected overlay itself."""
        if isinstance(result, dict):
            boxes = result.get("boxes") or []
            style = {
                "color": parse_rgb(result.get("color"), DETECT_COLOR),
                "dash": result.get("dash", False),
            }
            return boxes, style
        return (result or []), {}

    def _apply_detected(self, key, boxes, style=None):
        self.preview_btn.set_label(tr("Generate preview")); self.preview_btn.set_sensitive(True)
        cur = self._current_file()
        if cur and key == (self.tool, str(cur), self.page_index):
            self._detected = boxes
            self._detected_style = style or {}
            self.canvas.queue_draw()
        return False

    @staticmethod
    def _opts_sig(opts):
        """Hashable fingerprint of a tool's option values (image-preview cache key)."""
        return json.dumps(opts, sort_keys=True, default=str)

    def _apply_image_preview(self, key, surf):
        self._img_cache[key] = surf
        self.preview_btn.set_label(tr("Generate preview")); self.preview_btn.set_sensitive(True)
        cur = self._current_file()
        if cur and key == (self.tool, str(cur), self.page_index, self._opts_sig(self._read_opts())):
            spec = self.tools.get(self.tool)
            if spec and spec.has_compare:
                base = self._base_cache.get((str(cur), self.page_index))
                before = base[0] if base else self._base_surface
                self._arm_compare(before, surf)
            self._base_surface = surf; self._overlay = None
            self.preview_stack.set_visible_child_name("image")
            self.canvas.queue_draw()
        return False

    def _arm_compare(self, before, after):
        """Enter before/after compare mode with a draggable divider (generic —
        used by any COMPARE_PREVIEW tool, e.g. Upscale / Remove background)."""
        if before is None or after is None:
            self._compare_mode = False
            return
        self._compare_before = before
        self._compare_after = after
        self._compare_mode = True
        if not hasattr(self, "_compare_x"):
            self._compare_x = 0.5

    def _image_preview_error(self, msg):
        self.preview_btn.set_label(tr("Generate preview")); self.preview_btn.set_sensitive(True)
        self._toast(f"Preview failed: {msg}")
        return False

    # ---- processing --------------------------------------------------------
    def _resolve_output_dir(self):
        if self.output_dir is not None:
            return self.output_dir
        base = self.files[0].parent if self.files else Path.cwd()
        return base / "output"

    def _relevant_files(self, spec):
        return [f for f in self.files if kind_of(f) in spec.accepts]

    def _process(self):
        spec = self.tools.get(self.tool)
        if not self.files or spec is None:
            self._toast("Load files and pick a tool first.")
            return
        targets = self._relevant_files(spec)
        if not targets:
            self._toast(f"No compatible files for “{spec.name}”.")
            return
        overwrite = self.sw_overwrite.get_active()
        out_dir = self._resolve_output_dir()
        if not overwrite:
            out_dir.mkdir(parents=True, exist_ok=True)
        opts = self._read_opts()
        if self._tool_preview_kind(spec, opts) == "grid":
            # hand the live grid order/selection to process() under reserved keys
            opts["_grid_order"] = list(getattr(self, "_grid_order", []))
            opts["_grid_selected"] = sorted(getattr(self, "_grid_selected", []))
            # resolved, flattened sequence so tools don't reimplement flattening:
            # ordered (file_index, pageno_or_None) tuples across ALL files.
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

        dlg, bar, status = self._progress_dialog()
        state = {"cancel": False, "written": []}

        def run():
            try:
                if spec.batch:
                    def prog(done, total):
                        GLib.idle_add(self._set_progress, bar, status,
                                      (done / total) if total else 0.0,
                                      f"{done} / {total}", "Working…")
                    kw = {"progress": prog} if accepts_progress else {}
                    res = spec.module.process(strs, str(out_dir), opts, overwrite, **kw) or []
                    state["written"] += [Path(x) for x in res]
                    GLib.idle_add(self._set_progress, bar, status, 1.0, "done", "Done")
                else:
                    n = len(strs)
                    for i, f in enumerate(strs, start=1):
                        if state["cancel"]:
                            break
                        name = Path(f).name

                        def prog(done, total, i=i, name=name):
                            frac = ((i - 1) + (done / total if total else 0)) / n
                            sub = f"{i}/{n}" + (f" — page {done}/{total}" if total > 1 else "")
                            GLib.idle_add(self._set_progress, bar, status, frac, sub, name)
                        GLib.idle_add(self._set_progress, bar, status, (i - 1) / n, f"{i}/{n}", name)
                        kw = {"progress": prog} if accepts_progress else {}
                        res = spec.module.process(f, str(out_dir), opts, overwrite, **kw) or []
                        state["written"] += [Path(x) for x in res]
                        GLib.idle_add(self._set_progress, bar, status, i / n, f"{i}/{n}", name)
                if not state["cancel"]:
                    state["written"] = [Path(x) for x in
                                        self._apply_naming([str(p) for p in state["written"]], naming)]
                GLib.idle_add(self._finish_dialog, dlg, state, None)
            except Exception as e:
                traceback.print_exc()
                GLib.idle_add(self._finish_dialog, dlg, state, str(e))

        dlg.cancel_cb = lambda: state.__setitem__("cancel", True)
        threading.Thread(target=run, daemon=True).start()

    def _set_progress(self, bar, status, frac, text, name):
        bar.set_fraction(max(0.0, min(1.0, frac)))
        bar.set_text(text)
        status.set_text(name)
        return False

    def _tick(self, bar, status, done, total, name):
        bar.set_fraction(done / total if total else 0.0)
        bar.set_text(f"{done} / {total}")
        status.set_text(name)
        return False

    def _progress_dialog(self):
        win = Gtk.Window(transient_for=self, modal=True, title=tr("Processing"))
        win.set_default_size(480, 190)
        win.set_resizable(True)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        for m in ("top", "bottom", "start", "end"):
            getattr(box, f"set_margin_{m}")(20)
        bar = Gtk.ProgressBar(show_text=True)
        status = Gtk.Label(label=tr("Starting…"), xalign=0)
        status.set_wrap(True)
        status.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        status.set_max_width_chars(52)
        status.set_xalign(0)
        status.set_justify(Gtk.Justification.LEFT)
        status.add_css_class("dim-label")
        btnrow = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        btnrow.set_halign(Gtk.Align.END)
        cancel = Gtk.Button(label=tr("Cancel"))
        cancel.connect("clicked", lambda *_: getattr(win, "cancel_cb", lambda: None)())
        btnrow.append(cancel)
        for w in (bar, status, btnrow):
            box.append(w)
        win.set_child(box)
        win._bar, win._status, win._btnrow = bar, status, btnrow
        win.present()
        return win, bar, status

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
        if error:
            dlg._status.set_text(f"{tr('Error:')} {error}"); dlg._bar.set_text(tr("failed"))
        elif state["cancel"]:
            dlg._status.set_text(tr("Cancelled after {n} file(s).").format(n=n))
        else:
            dlg._bar.set_fraction(1.0); dlg._bar.set_text(tr("done"))
            if n == 0:
                dlg._status.set_text(tr(
                    "The tool ran but produced no files. Check your settings — for "
                    "example an empty page selection, values that change nothing, or "
                    "files the tool couldn't use."))
            else:
                dlg._status.set_text(tr("Wrote {n} file(s).").format(n=n))
        c = dlg._btnrow.get_first_child()
        while c:
            dlg._btnrow.remove(c)
            c = dlg._btnrow.get_first_child()
        if state["written"] and not error:
            load = Gtk.Button(label=tr("Load results as new files"))
            # reset options first: a crop/divide result is a different page size,
            # so old margin/crop values would fall outside it and break the preview
            load.connect("clicked", lambda *_: (self._rebuild_options(),
                                                self._set_files(state["written"]), dlg.close()))
            dlg._btnrow.append(load)
        close = Gtk.Button(label=tr("Close")); close.add_css_class("suggested-action")
        close.connect("clicked", lambda *_: dlg.close())
        dlg._btnrow.append(close)
        return False

    def _toast(self, msg):
        d = Adw.MessageDialog(transient_for=self, heading="PageForge", body=msg)
        d.add_response("ok", "OK")
        d.present()

    # ---- settings ----------------------------------------------------------
    def _open_settings(self):
        self._settings_win = Adw.PreferencesDialog()
        self._settings_win.set_title("Settings")
        self._settings_page = Adw.PreferencesPage()
        self._settings_win.add(self._settings_page)
        self._settings_groups = []
        self._populate_settings()
        self._settings_win.present(self)

    def _populate_settings(self):
        for g in self._settings_groups:
            try:
                self._settings_page.remove(g)
            except Exception:
                pass
        self._settings_groups = []

        lang_group = Adw.PreferencesGroup(title=tr("Language"))
        self._settings_page.add(lang_group); self._settings_groups.append(lang_group)
        lang_row = Adw.ActionRow(title=tr("Language"),
                                 subtitle=tr("Restart to fully apply."))
        _codes = ["en", "fr"]
        lang_dd = Gtk.DropDown.new_from_strings(["English", "Français"])
        lang_dd.set_valign(Gtk.Align.CENTER)
        _cur = self.cfg.get("language", "en")
        lang_dd.set_selected(_codes.index(_cur) if _cur in _codes else 0)
        lang_dd.connect("notify::selected",
                        lambda d, *_: self._on_language_changed(_codes[d.get_selected()]))
        lang_row.add_suffix(lang_dd)
        lang_group.add(lang_row)

        folder_group = Adw.PreferencesGroup(title=tr("Scripts folder"),
                                            description=tr("All tools are scripts in this folder."))
        self._settings_page.add(folder_group); self._settings_groups.append(folder_group)
        frow = Adw.ActionRow(title=tr("Location"),
                             subtitle=self.cfg.get("scripts_dir") or tr("not set"))
        openb = Gtk.Button(label=tr("Open")); openb.set_valign(Gtk.Align.CENTER)
        openb.connect("clicked", lambda *_: self._open_scripts_folder())
        changeb = Gtk.Button(label=tr("Change")); changeb.set_valign(Gtk.Align.CENTER)
        changeb.connect("clicked", lambda *_: self._change_scripts_folder())
        frow.add_suffix(openb); frow.add_suffix(changeb)
        folder_group.add(frow)
        actions = Adw.ActionRow(title=tr("Maintenance"))
        restore = Gtk.Button(label=tr("Restore built-in tools")); restore.set_valign(Gtk.Align.CENTER)
        restore.connect("clicked", lambda *_: self._restore_builtins())
        reload_b = Gtk.Button(label=tr("Reload")); reload_b.set_valign(Gtk.Align.CENTER)
        reload_b.connect("clicked", lambda *_: (self._reload_tools(), self._populate_settings()))
        actions.add_suffix(restore); actions.add_suffix(reload_b)
        folder_group.add(actions)

        tools_group = Adw.PreferencesGroup(title=tr("Tools"),
                                           description=tr("Reorder with ↑ ↓; enable, disable, or delete any tool."))
        self._settings_page.add(tools_group); self._settings_groups.append(tools_group)
        disabled = self.cfg.get("disabled", {})
        if not self.tools:
            tools_group.add(Adw.ActionRow(title=tr("No tools found"),
                                          subtitle=tr("Add scripts to the folder, then Reload.")))
        ordered = self._sorted_ids(list(self.tools))
        for pos, tid in enumerate(ordered):
            spec = self.tools[tid]
            met = spec.deps_met
            sub = "  •  ".join(tr(a) for a in sorted(spec.accepts))
            if not met:
                sub = tr("needs: ") + ", ".join(spec.requires + spec.system_requires)
            row = Adw.ActionRow(title=tr(spec.name), subtitle=sub)
            up = Gtk.Button.new_from_icon_name("go-up-symbolic")
            up.set_valign(Gtk.Align.CENTER); up.add_css_class("flat")
            up.set_sensitive(pos > 0)
            up.connect("clicked", lambda b, t=tid: self._move_tool(t, -1))
            down = Gtk.Button.new_from_icon_name("go-down-symbolic")
            down.set_valign(Gtk.Align.CENTER); down.add_css_class("flat")
            down.set_sensitive(pos < len(ordered) - 1)
            down.connect("clicked", lambda b, t=tid: self._move_tool(t, 1))
            row.add_prefix(up); row.add_prefix(down)
            sw = Gtk.Switch(); sw.set_valign(Gtk.Align.CENTER)
            sw.set_active(met and not disabled.get(tid, False))
            sw.connect("notify::active", self._on_tool_switch, tid, spec)
            if not met:
                inst = Gtk.Button(label=tr("Install deps")); inst.set_valign(Gtk.Align.CENTER)
                inst.add_css_class("suggested-action")
                inst.connect("clicked", lambda b, s=spec: self._install_flow(s))
                row.add_suffix(inst)
            delb = Gtk.Button.new_from_icon_name("user-trash-symbolic")
            delb.set_valign(Gtk.Align.CENTER); delb.add_css_class("flat")
            delb.set_tooltip_text(tr("Delete this tool's script"))
            delb.connect("clicked", lambda b, s=spec: self._delete_tool(s))
            row.add_suffix(sw); row.add_suffix(delb)
            tools_group.add(row)

        about_group = Adw.PreferencesGroup(title=tr("About"))
        self._settings_page.add(about_group); self._settings_groups.append(about_group)
        about_group.add(Adw.ActionRow(title="PageForge", subtitle=f"{tr('Version')} {APP_VERSION}"))

    def _on_language_changed(self, code):
        """Persist the chosen UI language and apply it to the parts that rebuild
        on demand (tool list, options, preview, settings). The static window
        chrome (section titles, file/output buttons) picks up the new language
        on the next launch."""
        if code == self.cfg.get("language", "en"):
            return
        self.cfg["language"] = code
        save_config(self.cfg)
        set_language(code)
        self._rebuild_tools_section()
        self._rebuild_options()
        self._refresh_preview()
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

    def _on_tool_switch(self, switch, _p, tid, spec):
        want = switch.get_active()
        if want and not spec.deps_met:
            switch.set_active(False)
            self._install_flow(spec)
            return
        self.cfg.setdefault("disabled", {})[tid] = (not want)
        save_config(self.cfg)
        self._reload_tools()

    def _open_scripts_folder(self):
        d = self.cfg.get("scripts_dir")
        if d:
            Gio.AppInfo.launch_default_for_uri(Path(d).as_uri(), None)

    def _change_scripts_folder(self):
        fd = Gtk.FileDialog(); fd.set_title(tr("Choose scripts folder"))
        fd.select_folder(self, None, self._on_change_folder)

    def _on_change_folder(self, fd, res):
        try:
            folder = fd.select_folder_finish(res)
        except GLib.Error:
            return
        self.cfg["scripts_dir"] = folder.get_path()
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
        d = Adw.MessageDialog(transient_for=self, heading="Delete tool?",
                              body=f"Permanently delete the script for “{spec.name}”?\n{spec.path}")
        d.add_response("cancel", "Cancel")
        d.add_response("delete", "Delete")
        d.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        d.connect("response", self._on_delete_response, spec)
        d.present()

    def _on_delete_response(self, dialog, response, spec):
        if response != "delete":
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
        win = Gtk.Window(transient_for=self,
                         modal=True, title=f"{tr('Dependencies')} · {tr(spec.name)}")
        win.set_default_size(560, 420)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        for m in ("top", "bottom", "start", "end"):
            getattr(box, f"set_margin_{m}")(16)
        pkgs = spec.requires + spec.system_requires
        head = Gtk.Label(xalign=0, wrap=True)
        head.set_markup(f"<b>{tr(spec.name)}</b> {tr('needs:')} {', '.join(pkgs)}\n"
                        + (tr("System packages will use pkexec (password prompt).\n")
                           if spec.system_requires and not IN_FLATPAK else ""))
        box.append(head)
        sw = Gtk.ScrolledWindow(); sw.set_vexpand(True)
        tv = Gtk.TextView(editable=False, monospace=True)
        tv.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        sw.set_child(tv); box.append(sw)
        buf = tv.get_buffer()
        btnrow = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        btnrow.set_halign(Gtk.Align.END)
        install = Gtk.Button(label=tr("Install")); install.add_css_class("suggested-action")
        close = Gtk.Button(label=tr("Close"))
        close.connect("clicked", lambda *_: win.close())
        btnrow.append(close); btnrow.append(install)
        box.append(btnrow)
        win.set_child(box)

        def log(line):
            GLib.idle_add(self._append_text, buf, line)

        def do_install(*_):
            install.set_sensitive(False); install.set_label(tr("Installing…"))

            def work():
                ok = install_deps(spec.requires, spec.system_requires, log)
                GLib.idle_add(finish, ok)
            threading.Thread(target=work, daemon=True).start()

        def finish(ok):
            install.set_label(tr("Install"))
            if ok:
                self.cfg.setdefault("disabled", {})[spec.id] = False
                save_config(self.cfg)
                self._reload_tools()
                if hasattr(self, "_settings_page"):
                    self._populate_settings()
                self._append_text(buf, "\n✓ Done. Tool enabled.\n")
            else:
                install.set_sensitive(True)
                self._append_text(buf, "\n✗ Installation failed. See log above.\n")
            return False

        install.connect("clicked", do_install)
        win.present()

    def _append_text(self, buf, line):
        buf.insert(buf.get_end_iter(), line)
        return False


class PageForgeApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.FLAGS_NONE)

    def do_activate(self):
        # Adopt any legacy GtkSettings dark preference through the supported
        # libadwaita API so we don't rely on the deprecated GtkSettings path.
        try:
            settings = Gtk.Settings.get_default()
            if settings and settings.get_property("gtk-application-prefer-dark-theme"):
                Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)
        except Exception:
            pass
        (self.props.active_window or MainWindow(self)).present()


def main():
    return PageForgeApp().run(sys.argv)


if __name__ == "__main__":
    raise SystemExit(main())
