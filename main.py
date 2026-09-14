from __future__ import annotations

import os
import site
import sys
import sysconfig
from pathlib import Path


def configure_qt_environment() -> None:
    """Set the Qt plugin paths before PyQt is imported."""
    candidates: list[Path] = []

    for base in site.getsitepackages():
        candidates.append(Path(base))

    user_site = Path(site.USER_SITE) if getattr(site, "USER_SITE", None) else None
    if user_site and user_site.exists():
        candidates.append(user_site)

    candidates.append(Path(sys.prefix))
    candidates.append(Path(sysconfig.get_paths().get("purelib", sys.prefix)))
    candidates.append(Path(sysconfig.get_paths().get("platlib", sys.prefix)))

    pyqt_plugins = None
    for base in candidates:
        candidate = base / "PyQt6" / "Qt6" / "plugins"
        if candidate.exists():
            pyqt_plugins = candidate
            break

    if pyqt_plugins is not None:
        platform_dir = pyqt_plugins / "platforms"
        os.environ["QT_PLUGIN_PATH"] = str(pyqt_plugins)
        os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = str(platform_dir)

    if sys.platform == "darwin":
        # Prefer the native Cocoa backend for real macOS desktop sessions.
        # Only force offscreen when a headless CI/session is explicitly requested.
        if os.environ.get("QT_QPA_PLATFORM"):
            return
        if os.environ.get("CI") or os.environ.get("DISPLAY") == "" and os.environ.get("WAYLAND_DISPLAY") == "":
            os.environ["QT_QPA_PLATFORM"] = "offscreen"
        else:
            os.environ["QT_QPA_PLATFORM"] = "cocoa"
    else:
        if not os.environ.get("QT_QPA_PLATFORM"):
            os.environ["QT_QPA_PLATFORM"] = "offscreen"


configure_qt_environment()

from PyQt6.QtCore import Qt, QThread, pyqtSignal, QSize, QByteArray
from PyQt6.QtGui import QIcon, QPixmap, QPainter, QColor
from PyQt6.QtSvg import QSvgRenderer
import re
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
    QFrame,
)

try:
    from .downloader import download_all_videos, download_mp3, download_video
except ImportError:  # pragma: no cover
    from downloader import download_all_videos, download_mp3, download_video


class DownloadThread(QThread):
    progress = pyqtSignal(float, str)
    finished = pyqtSignal(bool, str)

    def __init__(self, url: str, option: str, output_dir: str):
        super().__init__()
        self.url = url
        self.option = option
        self.output_dir = output_dir
        self.cancel_event = None

    def stop(self):
        if self.cancel_event is not None:
            self.cancel_event.set()

    def run(self):
        self.cancel_event = __import__("threading").Event()
        try:
            download_video(
                self.url,
                option=self.option,
                output_dir=self.output_dir,
                progress_callback=lambda percent, message: self.progress.emit(percent, message),
                cancel_event=self.cancel_event,
            )
            self.finished.emit(True, "Download complete.")
        except Exception as exc:  # pragma: no cover - UI-level failure handling
            if self.cancel_event is not None and self.cancel_event.is_set():
                self.finished.emit(False, "Download stopped by user.")
            else:
                self.finished.emit(False, f"Download failed: {exc}")


class BulkDownloadThread(QThread):
    progress = pyqtSignal(float, str)
    finished = pyqtSignal(bool, str)

    def __init__(self, url: str, option: str, output_dir: str):
        super().__init__()
        self.url = url
        self.option = option
        self.output_dir = output_dir
        self.cancel_event = None

    def stop(self):
        if self.cancel_event is not None:
            self.cancel_event.set()

    def run(self):
        self.cancel_event = __import__("threading").Event()
        try:
            files = download_all_videos(
                self.url,
                option=self.option,
                output_dir=self.output_dir,
                progress_callback=lambda percent, message: self.progress.emit(percent, message),
                cancel_event=self.cancel_event,
            )
            if self.cancel_event.is_set():
                self.finished.emit(False, "Download stopped by user.")
            elif files:
                self.finished.emit(True, f"Download complete. {len(files)} videos saved.")
            else:
                self.finished.emit(False, "No videos found on this page or playlist.")
        except Exception as exc:  # pragma: no cover - UI-level failure handling
            if self.cancel_event is not None and self.cancel_event.is_set():
                self.finished.emit(False, "Download stopped by user.")
            else:
                self.finished.emit(False, f"Bulk download failed: {exc}")


class MP3DownloadThread(QThread):
    progress = pyqtSignal(float, str)
    finished = pyqtSignal(bool, str)

    def __init__(self, url: str, output_dir: str):
        super().__init__()
        self.url = url
        self.output_dir = output_dir
        self.cancel_event = None

    def stop(self):
        if self.cancel_event is not None:
            self.cancel_event.set()

    def run(self):
        self.cancel_event = __import__("threading").Event()
        try:
            download_mp3(
                self.url,
                output_dir=self.output_dir,
                progress_callback=lambda percent, message: self.progress.emit(percent, message),
                cancel_event=self.cancel_event,
            )
            self.finished.emit(True, "MP3 download complete.")
        except Exception as exc:  # pragma: no cover - UI-level failure handling
            if self.cancel_event is not None and self.cancel_event.is_set():
                self.finished.emit(False, "Download stopped by user.")
            else:
                self.finished.emit(False, f"MP3 download failed: {exc}")


class KhmerDubThread(QThread):
    progress = pyqtSignal(float, str)
    finished = pyqtSignal(bool, str)

    def __init__(self, url: str, output_dir: str):
        super().__init__()
        self.url = url
        self.output_dir = output_dir
        self.cancel_event = None

    def stop(self):
        if self.cancel_event is not None:
            self.cancel_event.set()

    def run(self):
        self.cancel_event = __import__("threading").Event()
        try:
            from downloader import process_video_subtitles_to_khmer_voxcp2
            process_video_subtitles_to_khmer_voxcp2(
                self.url,
                output_dir=self.output_dir,
                progress_callback=lambda percent, message: self.progress.emit(percent, message),
                cancel_event=self.cancel_event,
            )
            self.finished.emit(True, "Khmer dub completed.")
        except Exception as exc:  # pragma: no cover - UI-level failure handling
            if self.cancel_event is not None and self.cancel_event.is_set():
                self.finished.emit(False, "Download stopped by user.")
            else:
                message = str(exc)
                if not message.lower().startswith("khmer dub failed:"):
                    message = f"Khmer dub failed: {message}"
                self.finished.emit(False, message)


class MediaDownloaderApp(QWidget):
    def __init__(self):
        super().__init__()
        self.thread = None
        self.init_ui()
        self.apply_stylesheet()

    def _load_tinted_icon(self, svg_path: str | Path, size: int = 18, color: str = "#ffffff") -> QIcon:
        """Load an SVG, replace fills/strokes with `color`, render to a QPixmap and return a QIcon.

        This avoids permanently modifying asset files while allowing tinting.
        """
        try:
            p = Path(svg_path)
            if not p.exists():
                return QIcon()
            svg_text = p.read_text(encoding="utf-8")
            # Basic replacements for common fill/stroke attributes
            svg_text = re.sub(r'fill\s*=\s*"#[0-9a-fA-F]{3,6}"', f'fill="{color}"', svg_text)
            svg_text = re.sub(r'stroke\s*=\s*"#[0-9a-fA-F]{3,6}"', f'stroke="{color}"', svg_text)
            svg_text = svg_text.replace('fill="currentColor"', f'fill="{color}"')
            # Render SVG into an intermediate pixmap
            ba = QByteArray(svg_text.encode('utf-8'))
            renderer = QSvgRenderer(ba)

            # Render the icon content into its own pixmap
            icon_pix = QPixmap(size, size)
            icon_pix.fill(Qt.GlobalColor.transparent)
            pnt = QPainter(icon_pix)
            pnt.setRenderHint(QPainter.RenderHint.Antialiasing)
            renderer.render(pnt)
            pnt.end()

            return QIcon(icon_pix)
        except Exception:
            return QIcon()

    def _load_tinted_icon_with_frame(self, svg_path: str | Path, outer_size: int = 28, icon_ratio: float = 0.6, icon_color: str = "#ffffff", frame_color: str = "#0f172a") -> QIcon:
        """Load an SVG, tint it and draw it centered on a circular frame background.

        - `outer_size`: total pixel size of resulting icon
        - `icon_ratio`: proportion of outer to use for the inner icon
        """
        try:
            p = Path(svg_path)
            if not p.exists():
                return QIcon()
            # Render inner icon
            inner_size = max(8, int(outer_size * icon_ratio))
            inner_icon = self._load_tinted_icon(p, size=inner_size, color=icon_color)
            # Create outer pixmap and draw circular background
            outer = QPixmap(outer_size, outer_size)
            outer.fill(Qt.GlobalColor.transparent)
            painter = QPainter(outer)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(frame_color))
            painter.drawEllipse(0, 0, outer_size, outer_size)
            # Draw inner tinted icon centered
            inner_pix = inner_icon.pixmap(inner_size, inner_size)
            x = (outer_size - inner_size) // 2
            y = (outer_size - inner_size) // 2
            painter.drawPixmap(x, y, inner_pix)
            painter.end()
            return QIcon(outer)
        except Exception:
            return QIcon()

    def apply_stylesheet(self):
        self.setStyleSheet(
            """
            /* Dark dashboard theme */
            QWidget { background: #0b1220; color: #e6eef8; font-family: -apple-system, 'Helvetica Neue', 'Segoe UI', sans-serif; }
            /* Sidebar */
            #sidebar { background: #0f172a; border-right: 1px solid #111827; }
            QPushButton.navButton { background: transparent; color: #ffffff; border: none; padding: 10px 12px 10px 14px; text-align: left; font-weight: 600; border-radius: 12px; }
            QPushButton.navButton:hover { background: rgba(255,255,255,0.02); color: #ffffff; }
            QPushButton[selected="true"] { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #044b36, stop:1 #0b7a59); color: #dffcf2; }
            QPushButton.navButton::icon { margin-right: 10px; }
            /* Cards */
            .statCard { background: linear-gradient(180deg, #0e1723, #0b1320); border: 1px solid #16202b; border-radius: 12px; padding: 18px; }
            .statNumber { color: #e6f9fb; font-size: 20px; font-weight: 800; }
            .statLabel { color: #9fb0c8; font-size: 12px; }
            /* Main panel */
            QGroupBox { background: transparent; border: none; }
            .panel { background: #0e1624; border: 1px solid #17242f; border-radius: 14px; padding: 18px; }
            QLineEdit, QComboBox { background: #0b1724; border: 1px solid #172a36; border-radius: 8px; padding: 10px; color: #dff3ff; }
            QLabel#titleLabel { font-size: 26px; font-weight: 800; color: #f8fafc; }
            QLabel#subtitleLabel { color: #98a8b9; font-size: 13px; }
            QPushButton.action { background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #06b6d4, stop:1 #22c55e); color: #ffffff; border-radius: 10px; padding: 10px 16px; font-weight: 700; }
            QPushButton[accent="blue"] { background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #1e90ff, stop:1 #0ea5ff); color: #ffffff; }
            QPushButton.ghost { background: transparent; color: #ffffff; border: 1px solid #172a36; border-radius: 10px; padding: 10px 16px; }
            QProgressBar { background: #071019; border: 1px solid #10202a; border-radius: 10px; height: 16px; }
            QProgressBar::chunk { background: #06b6d4; border-radius: 10px; }
            """
        )

    def init_ui(self):
        self.setWindowTitle("Media Downloader Dashboard")
        self.resize(1120, 760)

        # Left sidebar
        sidebar = QFrame(self)
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(220)
        sb_layout = QVBoxLayout()
        sb_layout.setContentsMargins(18, 18, 18, 18)
        # App header with icon
        app_header = QFrame()
        ah_layout = QHBoxLayout()
        ah_layout.setContentsMargins(0, 0, 0, 0)
        icon_lbl = QLabel()
        icon_path = Path(__file__).resolve().parent / 'assets' / 'icon_dashboard.svg'
        if icon_path.exists():
            # Use framed header icon
            header_icon = self._load_tinted_icon_with_frame(str(icon_path), outer_size=36, icon_ratio=0.7, icon_color="#ffffff", frame_color="#07202a")
            pix = header_icon.pixmap(36, 36)
            icon_lbl.setPixmap(pix)
        text_lbl = QLabel("MediaDL")
        text_lbl.setStyleSheet("font-size:16px; font-weight:800; color:#9fe6d8; margin-left:8px;")
        ah_layout.addWidget(icon_lbl)
        ah_layout.addWidget(text_lbl)
        ah_layout.addStretch()
        app_header.setLayout(ah_layout)
        sb_layout.addWidget(app_header)
        sb_layout.addSpacing(12)
        btn_dashboard = QPushButton("Dashboard")
        btn_dashboard.setProperty("class", "navButton")
        btn_queue = QPushButton("Queue")
        btn_queue.setProperty("class", "navButton")
        btn_history = QPushButton("History")
        btn_history.setProperty("class", "navButton")
        btn_settings = QPushButton("Settings")
        btn_settings.setProperty("class", "navButton")
        assets = Path(__file__).resolve().parent / "assets"

        def resolve_icon(name_hint: str) -> str:
            """Return a path to an icon file. Try exact match first, then search assets/icon for a matching hint."""
            candidate = assets / name_hint
            if candidate.exists():
                return str(candidate)
            icon_dir = assets / "icon"
            if icon_dir.exists() and icon_dir.is_dir():
                for f in icon_dir.iterdir():
                    if name_hint.lower() in f.name.lower():
                        return str(f)
                # fallback: return first svg
                for f in icon_dir.iterdir():
                    if f.suffix.lower() == ".svg":
                        return str(f)
            # final fallback: assets root svg
            for f in assets.iterdir():
                if f.suffix.lower() == ".svg":
                    return str(f)
            return ""

        icons = {
            'dashboard': resolve_icon('dashboard-square-02.svg'),
            'queue': resolve_icon('menu-01.svg'),
            'history': resolve_icon('clock-02.svg'),
            'settings': resolve_icon('settings-01.svg'),
            'light': resolve_icon('sun-dim.svg'),
        }

        for b, key in ((btn_dashboard, 'dashboard'), (btn_queue, 'queue'), (btn_history, 'history'), (btn_settings, 'settings')):
            b.setFixedHeight(40)
            b.setProperty("class", "navButton")
            path = icons.get(key) or ''
            if path:
                # Use a circular framed icon for sidebar buttons
                framed = self._load_tinted_icon_with_frame(path, outer_size=28, icon_ratio=0.6, icon_color="#ffffff", frame_color="#0f172a")
                b.setIcon(framed)
                b.setIconSize(QSize(22, 22))
            sb_layout.addWidget(b)

        # mark Dashboard selected by default
        btn_dashboard.setProperty("selected", True)
        sb_layout.addStretch()
        light_mode = QPushButton("Light Mode")
        light_mode.setProperty("class", "navButton")
        light_mode.setIcon(self._load_tinted_icon_with_frame(icons['light'], outer_size=22, icon_ratio=0.6, icon_color="#ffffff", frame_color="#0f172a"))
        light_mode.setIconSize(QSize(18, 18))
        sb_layout.addWidget(light_mode)
        sidebar.setLayout(sb_layout)

        # Right content
        content = QWidget()
        content_layout = QVBoxLayout()
        content_layout.setSpacing(16)

        # Header
        header_row = QHBoxLayout()
        title = QLabel("Media Download Dashboard")
        title.setObjectName("titleLabel")
        subtitle = QLabel("Paste a link and choose the quality you want to save.")
        subtitle.setObjectName("subtitleLabel")
        title_col = QVBoxLayout()
        title_col.addWidget(title)
        title_col.addWidget(subtitle)
        header_row.addLayout(title_col)
        header_row.addStretch()

        # Stats
        stats_row = QHBoxLayout()
        def stat_card(number, label):
            w = QFrame()
            w.setProperty("class", "statCard")
            v = QVBoxLayout()
            lbl_num = QLabel(str(number))
            lbl_num.setProperty("class", "statNumber")
            lbl_lbl = QLabel(label)
            lbl_lbl.setProperty("class", "statLabel")
            v.addWidget(lbl_num)
            v.addWidget(lbl_lbl)
            w.setLayout(v)
            return w

        stats_row.addWidget(stat_card("1,248", "Total Downloads"))
        stats_row.addWidget(stat_card("3", "Active"))
        stats_row.addWidget(stat_card("2", "Failed"))
        stats_row.addWidget(stat_card("24.6 GB", "Storage Used"))

        # Header and stats will be added after the quick-start panel so URL is top-most
        # content_layout.addLayout(header_row)
        # content_layout.addLayout(stats_row)

        # Download panel
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("https://")
        # Focus URL input on startup for quick paste & start
        self.url_input.setFocus()
        try:
            self.url_input.selectAll()
        except Exception:
            pass
        self.format_combo = QComboBox()
        self.format_combo.addItems(["Best quality", "MP4 (best)", "MKV (best)", "Audio only"])
        project_download_dir = Path(__file__).resolve().parent / "downloads"
        self.output_dir_input = QLineEdit()
        self.output_dir_input.setText(str(project_download_dir))
        browse_button = QPushButton("Browse")
        browse_button.clicked.connect(self.browse_directory)
        # Try adding icons to inputs and browse button using resolved icons
        link_icon = resolve_icon('link-04.svg')
        folder_icon = resolve_icon('folder-02.svg')
        try:
            if link_icon:
                self.url_input.addAction(self._load_tinted_icon(link_icon, size=14, color="#ffffff"), QLineEdit.ActionPosition.LeadingPosition)
        except Exception:
            pass
        try:
            if folder_icon:
                self.output_dir_input.addAction(self._load_tinted_icon(folder_icon, size=14, color="#ffffff"), QLineEdit.ActionPosition.LeadingPosition)
                # Use framed browse icon
                browse_button.setIcon(self._load_tinted_icon_with_frame(folder_icon, outer_size=20, icon_ratio=0.6, icon_color="#ffffff", frame_color="#0f172a"))
                browse_button.setIconSize(QSize(16,16))
        except Exception:
            pass

        panel = QFrame()
        panel.setProperty("class", "panel")
        panel_layout = QVBoxLayout()
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.addRow("Media URL", self.url_input)
        form.addRow("Format", self.format_combo)
        folder_row = QHBoxLayout()
        folder_row.addWidget(self.output_dir_input)
        folder_row.addWidget(browse_button)
        form.addRow("Save to", folder_row)

        panel_layout.addLayout(form)
        actions = QHBoxLayout()
        self.download_button = QPushButton("Start Download")
        self.download_button.setProperty("class", "action")
        dl_icon = resolve_icon('download-01.svg')
        mp3_icon = resolve_icon('file-x.svg')
        ai_icon = resolve_icon('ai-generative.svg')
        hist_icon = resolve_icon('clock-02.svg')

        if dl_icon:
            self.download_button.setIcon(self._load_tinted_icon(dl_icon, size=18, color="#ffffff"))
            self.download_button.setIconSize(QSize(18,18))

        self.download_mp3_button = QPushButton("Download MP3")
        self.download_mp3_button.setProperty("class", "action")
        if mp3_icon:
            self.download_mp3_button.setIcon(self._load_tinted_icon(mp3_icon, size=18, color="#ffffff"))
            self.download_mp3_button.setIconSize(QSize(18,18))

        self.khmer_dub_button = QPushButton("Khmer Dub")
        self.khmer_dub_button.setProperty("class", "action")
        # make Khmer button blue accent to match design
        self.khmer_dub_button.setProperty("accent", "blue")
        if ai_icon:
            self.khmer_dub_button.setIcon(self._load_tinted_icon(ai_icon, size=18, color="#ffffff"))
            self.khmer_dub_button.setIconSize(QSize(18,18))

        self.download_all_button = QPushButton("Download All")
        self.download_all_button.setProperty("class", "action")
        if dl_icon:
            self.download_all_button.setIcon(self._load_tinted_icon(dl_icon, size=18, color="#ffffff"))
            self.download_all_button.setIconSize(QSize(18,18))

        self.stop_button = QPushButton("Stop")
        self.stop_button.setProperty("class", "ghost")
        if hist_icon:
            self.stop_button.setIcon(self._load_tinted_icon(hist_icon, size=16, color="#ffffff"))
            self.stop_button.setIconSize(QSize(16,16))
        self.stop_button.setEnabled(False)
        actions.addWidget(self.download_button)
        actions.addWidget(self.download_mp3_button)
        actions.addWidget(self.khmer_dub_button)
        actions.addWidget(self.download_all_button)
        actions.addWidget(self.stop_button)
        panel_layout.addLayout(actions)
        panel_layout.addSpacing(6)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        panel_layout.addWidget(self.progress_bar)
        self.status_label = QLabel("Ready to download")
        self.status_label.setStyleSheet("padding:12px; border-radius:10px; background:#07101a; color:#9fb0c8;")
        panel_layout.addWidget(self.status_label)
        panel.setLayout(panel_layout)

        # Place header and stats first so title and cards are at the top
        content_layout.addLayout(header_row)
        content_layout.addLayout(stats_row)
        # Then add the quick-start download panel below
        content_layout.addWidget(panel)
        content.setLayout(content_layout)

        # Wire existing signals
        self.download_button.clicked.connect(self.start_download)
        self.download_mp3_button.clicked.connect(self.start_mp3_download)
        self.khmer_dub_button.clicked.connect(self.start_khmer_dub)
        self.download_all_button.clicked.connect(self.start_bulk_download)
        self.stop_button.clicked.connect(self.stop_download)

        # Main layout
        main_h = QHBoxLayout()
        main_h.addWidget(sidebar)
        main_h.addWidget(content)
        self.setLayout(main_h)
        # Window icon
        win_icon = assets / 'icon_dashboard.svg'
        if win_icon.exists():
            self.setWindowIcon(self._load_tinted_icon(str(win_icon), size=32, color="#ffffff"))

    def browse_directory(self):
        directory = QFileDialog.getExistingDirectory(self, "Choose output folder")
        if directory:
            self.output_dir_input.setText(directory)

    def start_download(self):
        url = self.url_input.text().strip()
        if not url:
            self.status_label.setText("Please enter a valid URL.")
            return

        output_dir = self.output_dir_input.text().strip() or os.getcwd()
        Path(output_dir).mkdir(parents=True, exist_ok=True)

        self._set_download_state_busy()
        media_folder = 'mp3' if self.format_combo.currentText() == 'Audio only' else 'video'
        self.status_label.setText(f"Saving to: {Path(output_dir) / media_folder}")
        self.progress_bar.setValue(0)

        self.thread = DownloadThread(url, self.format_combo.currentText(), output_dir)
        self.thread.progress.connect(self.update_progress)
        self.thread.finished.connect(self.handle_finished)
        self.thread.start()

    def start_mp3_download(self):
        url = self.url_input.text().strip()
        if not url:
            self.status_label.setText("Please enter a valid URL.")
            return

        output_dir = self.output_dir_input.text().strip() or os.getcwd()
        Path(output_dir).mkdir(parents=True, exist_ok=True)

        self._set_download_state_busy()
        self.status_label.setText(f"Saving to: {Path(output_dir) / 'mp3'}")
        self.progress_bar.setValue(0)

        self.thread = MP3DownloadThread(url, output_dir)
        self.thread.progress.connect(self.update_progress)
        self.thread.finished.connect(self.handle_finished)
        self.thread.start()

    def start_khmer_dub(self):
        url = self.url_input.text().strip()
        if not url:
            self.status_label.setText("Please enter a valid URL.")
            return

        output_dir = self.output_dir_input.text().strip() or os.getcwd()
        Path(output_dir).mkdir(parents=True, exist_ok=True)

        self._set_download_state_busy()
        self.status_label.setText(f"Khmer dub output: {Path(output_dir) / 'video'}")
        self.progress_bar.setValue(0)

        self.thread = KhmerDubThread(url, output_dir)
        self.thread.progress.connect(self.update_progress)
        self.thread.finished.connect(self.handle_finished)
        self.thread.start()

    def start_bulk_download(self):
        url = self.url_input.text().strip()
        if not url:
            self.status_label.setText("Please enter a valid URL.")
            return

        output_dir = self.output_dir_input.text().strip() or os.getcwd()
        Path(output_dir).mkdir(parents=True, exist_ok=True)

        self._set_download_state_busy()
        media_folder = 'mp3' if self.format_combo.currentText() == 'Audio only' else 'video'
        self.status_label.setText(f"Saving to: {Path(output_dir) / media_folder}")
        self.progress_bar.setValue(0)

        self.thread = BulkDownloadThread(url, self.format_combo.currentText(), output_dir)
        self.thread.progress.connect(self.update_progress)
        self.thread.finished.connect(self.handle_finished)
        self.thread.start()

    def _set_download_state_busy(self):
        self.download_button.setEnabled(False)
        self.download_mp3_button.setEnabled(False)
        self.khmer_dub_button.setEnabled(False)
        self.download_all_button.setEnabled(False)
        self.stop_button.setEnabled(True)

    def stop_download(self):
        if self.thread is not None:
            self.thread.stop()
            self.status_label.setText("Stopping download...")
            self.stop_button.setEnabled(False)

    def update_progress(self, value: float, message: str):
        self.progress_bar.setValue(int(value))
        self.status_label.setText(message)

    def show_download_message(self, message: str, success: bool):
        self.status_label.setText(message)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("")
        if success:
            QMessageBox.information(self, "Download complete", message)
        else:
            QMessageBox.critical(self, "Download failed", message)

    def handle_finished(self, success: bool, message: str):
        self.download_button.setEnabled(True)
        self.download_mp3_button.setEnabled(True)
        self.khmer_dub_button.setEnabled(True)
        self.download_all_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        if success:
            self.show_download_message(message, success=True)
        else:
            self.show_download_message(message, success=False)
        self.thread = None


if __name__ == "__main__":
    configure_qt_environment()

    app = QApplication(sys.argv)
    window = MediaDownloaderApp()
    window.show()
    sys.exit(app.exec())
