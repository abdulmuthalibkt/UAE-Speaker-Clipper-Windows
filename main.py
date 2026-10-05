import sys
import os
import re
import csv
import json
import shutil
import subprocess
from pathlib import Path

from PySide6.QtCore import Qt, QUrl, QTimer, Signal, QPointF, QThread, QObject
from PySide6.QtGui import QColor, QPainter, QPen, QBrush, QFont
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QLineEdit, QPushButton, QComboBox, QCheckBox, QSlider,
    QScrollArea, QTableWidget, QTableWidgetItem, QMessageBox, QFileDialog,
    QPlainTextEdit, QSpinBox, QDoubleSpinBox, QFrame, QSizePolicy, QTreeWidget, QTreeWidgetItem, QAbstractItemView, QProgressBar
)
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput


# ============================================================
# PATHS
# ============================================================

BASE_DIR = Path.home() / "UAE_Speaker_Collection"
DOWNLOAD_DIR = BASE_DIR / "Downloads"
PROJECT_DIR = BASE_DIR / "Projects"
EXPORT_DIR = BASE_DIR / "Exports"

for p in (DOWNLOAD_DIR, PROJECT_DIR, EXPORT_DIR):
    p.mkdir(parents=True, exist_ok=True)


def resource_path(name: str) -> str:
    """Return a bundled resource path when frozen, otherwise a system executable."""
    # Windows bundles use .exe; macOS/Linux bundles use the original names.
    if sys.platform == "win32" and not name.lower().endswith(".exe"):
        name = name + ".exe"

    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidate = Path(meipass) / name
        if candidate.exists():
            return str(candidate)

    found = shutil.which(name)
    return found or name


FFMPEG_BIN = resource_path("bin/ffmpeg")
YTDLP_BIN = resource_path("bin/yt-dlp")


# ============================================================
# HELPERS
# ============================================================

def run_cmd(cmd, cwd=None):
    return subprocess.run(
        cmd,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False
    )


def ffprobe_duration(path):
    # Use bundled FFmpeg directly so the standalone app does not need ffprobe.
    r = subprocess.run([FFMPEG_BIN, "-i", str(path)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
    text = r.stderr or r.stdout or ""
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", text)
    if not m:
        return 0.0
    h, mi, sec = int(m.group(1)), int(m.group(2)), float(m.group(3))
    return h * 3600 + mi * 60 + sec


def fmt_time(sec, milliseconds=False):
    sec = max(0.0, float(sec))
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = int(sec % 60)
    if milliseconds:
        ms = int(round((sec - int(sec)) * 1000))
        if ms >= 1000:
            s += 1
            ms = 0
        return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"
    return f"{h:02d}:{m:02d}:{s:02d}"


def parse_vtt(path):
    """Return [(start, end, text), ...]."""
    if not path or not Path(path).exists():
        return []

    text = Path(path).read_text(encoding="utf-8", errors="ignore")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    blocks = re.split(r"\n\s*\n", text)
    out = []

    def to_sec(x):
        x = x.strip().replace(",", ".")
        parts = x.split(":")
        try:
            if len(parts) == 3:
                return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
            return int(parts[0]) * 60 + float(parts[1])
        except Exception:
            return 0.0

    for block in blocks:
        lines = [x.strip() for x in block.split("\n") if x.strip()]
        if not lines:
            continue
        timing_index = -1
        for i, line in enumerate(lines):
            if "-->" in line:
                timing_index = i
                break
        if timing_index < 0:
            continue

        timing = lines[timing_index]
        left, right = timing.split("-->", 1)
        right = right.split()[0]

        start = to_sec(left)
        end = to_sec(right)

        caption_lines = lines[timing_index + 1:]
        clean = " ".join(caption_lines)
        clean = re.sub(r"<[^>]+>", "", clean)
        clean = re.sub(r"\{\\.*?\}", "", clean).strip()

        if clean and end > start:
            out.append((start, end, clean))

    return out


def find_caption_files(folder):
    files = list(Path(folder).glob("*.vtt"))
    english = []
    arabic = []

    for f in files:
        n = f.name.lower()
        if re.search(r"(^|[._-])(en|en-us|en-gb)([._-]|$)", n):
            english.append(f)
        elif re.search(r"(^|[._-])(ar|ar-sa)([._-]|$)", n):
            arabic.append(f)

    return english, arabic


# ============================================================
# SPEAKER COLORS
# ============================================================

SPEAKER_COLORS = [
    "#ff3b30",  # Speaker 1 - bright red
    "#34c759",  # Speaker 2 - bright green
    "#ffd60a",  # Speaker 3 - bright yellow
    "#0a84ff",  # Speaker 4 - bright blue
    "#bf5af2",  # Speaker 5 - bright purple
    "#ff9f0a",  # Speaker 6 - bright orange
    "#64d2ff",  # Speaker 7 - cyan
    "#ff375f",  # Speaker 8 - pink
    "#30d158",  # Speaker 9 - vivid green
    "#5e5ce6",  # Speaker 10 - indigo
    "#ff453a",  # Speaker 11
    "#32d74b",  # Speaker 12
    "#ffcc00",  # Speaker 13
    "#64d2ff",  # Speaker 14
    "#ac8e68"   # Speaker 15
]


# ============================================================
# PRECISION WAVEFORM
# ============================================================

class WaveformWidget(QWidget):
    selectionChanged = Signal(float, float)
    cursorChanged = Signal(float)
    segmentSelected = Signal(object)

    def __init__(self):
        super().__init__()
        self.setMinimumHeight(250)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)

        self.duration = 0.0
        self.zoom = 1.0
        self.samples = []
        self.segments = []

        self.start = 0.0
        self.end = 0.0
        self.drag_mode = None
        self.drag_anchor = 0.0
        self.drag_segment = None
        self.drag_segment_start = 0.0
        self.drag_segment_end = 0.0
        self.cursor = 0.0

        self.setMinimumWidth(1800)

    def set_duration(self, duration):
        self.duration = max(0.0, duration)
        if self.duration:
            self.end = min(self.duration, max(self.end, 1.0))
        self.update_geometry()
        self.update()

    def set_samples(self, samples):
        self.samples = samples or []
        self.update()

    def set_zoom(self, zoom):
        self.zoom = max(1.0, min(200.0, float(zoom)))
        self.update_geometry()
        self.update()

    def set_segments(self, segments):
        self.segments = segments
        self.update()

    def update_geometry(self):
        # Keep a large working surface. Long recordings scroll horizontally.
        px_per_second = max(4.0, self.zoom * 22.0)
        width = max(1800, int(self.duration * px_per_second))
        self.setMinimumWidth(width)
        self.resize(width, max(250, self.parentWidget().height() if self.parentWidget() else 250))

    def x_to_time(self, x):
        if self.duration <= 0:
            return 0.0
        px_per_second = max(4.0, self.zoom * 22.0)
        return max(0.0, min(self.duration, x / px_per_second))

    def time_to_x(self, t):
        px_per_second = max(4.0, self.zoom * 22.0)
        return t * px_per_second

    def _sample_range(self, x1, x2):
        if not self.samples:
            return 0.0
        n = len(self.samples)
        if self.width() <= 0:
            return 0.0
        a = int(max(0, min(n - 1, (x1 / self.width()) * n)))
        b = int(max(a + 1, min(n, (x2 / self.width()) * n)))
        vals = self.samples[a:b]
        return max(vals) if vals else 0.0

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#0f172a"))

        if self.duration <= 0:
            p.setPen(QColor("#94a3b8"))
            p.drawText(20, 40, "Load an audio file to begin")
            return

        # Background grid: seconds / 5 sec / 10 sec depending zoom.
        px_per_second = max(4.0, self.zoom * 22.0)
        if self.zoom >= 30:
            major = 1
        elif self.zoom >= 12:
            major = 5
        else:
            major = 10

        first = int(self.x_to_time(0) // major) * major
        t = first
        while t <= self.duration:
            x = self.time_to_x(t)
            p.setPen(QPen(QColor("#334155"), 1))
            p.drawLine(int(x), 0, int(x), self.height())
            p.setPen(QColor("#64748b"))
            p.drawText(int(x) + 3, 18, fmt_time(t))
            t += major

        # Saved speaker segments: bright, highly visible colors.
        for seg in self.segments:
            x1 = self.time_to_x(seg["start"])
            x2 = self.time_to_x(seg["end"])
            color = QColor(seg.get("color", "#0a84ff"))
            if seg.get("type") == "background_noise":
                color = QColor("#ff453a")
            fill = QColor(color)
            fill.setAlpha(155)
            p.fillRect(
                int(x1), 25, max(3, int(x2 - x1)),
                self.height() - 45, fill
            )
            p.setPen(QPen(color, 3))
            p.drawLine(int(x1), 25, int(x1), self.height() - 20)
            p.drawLine(int(x2), 25, int(x2), self.height() - 20)

            # Segment label inside the segment.
            if x2 - x1 > 55:
                p.setPen(QColor("#ffffff"))
                p.setFont(QFont("Arial", 10, QFont.Bold))
                if seg.get("type") == "background_noise":
                    label = f"BACKGROUND NOISE • {seg.get('segment_number', '')}"
                else:
                    label = f"{seg.get('speaker', 'Speaker')} • Segment {seg.get('segment_number', '')}"
                p.drawText(int(x1) + 5, 42, label)

        # Selection.
        sx = self.time_to_x(self.start)
        ex = self.time_to_x(self.end)
        if self.end > self.start:
            p.fillRect(
                int(sx), 25, max(2, int(ex - sx)), self.height() - 45,
                QColor(37, 99, 235, 70)
            )

        # Waveform.
        center = self.height() // 2
        wave_top = 35
        wave_bottom = self.height() - 30

        if self.samples:
            n = len(self.samples)
            step = max(1, n // max(1, self.width() // 2))
            p.setPen(QPen(QColor("#38bdf8"), 1))

            x = 0
            i = 0
            while i < n and x < self.width():
                j = min(n, i + step)
                chunk = self.samples[i:j]
                amp = max(chunk) if chunk else 0.0
                y = max(2, int(amp * (wave_bottom - wave_top) * 0.48))
                p.drawLine(x, center - y, x, center + y)
                x += 2
                i = j
        else:
            # fallback if peak extraction failed
            p.setPen(QPen(QColor("#38bdf8"), 1))
            for x in range(0, self.width(), 4):
                y = int(18 * abs(__import__("math").sin(x * 0.025)))
                p.drawLine(x, center - y, x, center + y)

        # Handles.
        p.setPen(QPen(QColor("#f8fafc"), 2))
        p.setBrush(QBrush(QColor("#f8fafc")))
        p.drawRect(int(sx) - 3, 20, 6, self.height() - 35)
        p.drawRect(int(ex) - 3, 20, 6, self.height() - 35)

        # Cursor.
        cx = self.time_to_x(self.cursor)
        p.setPen(QPen(QColor("#facc15"), 2))
        p.drawLine(int(cx), 0, int(cx), self.height())

        # Selection labels.
        p.setPen(QColor("#f8fafc"))
        p.drawText(int(sx) + 5, self.height() - 8, fmt_time(self.start, True))
        p.drawText(int(ex) - 95, self.height() - 8, fmt_time(self.end, True))
        p.end()

    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton or self.duration <= 0:
            return

        x = e.position().x()
        t = self.x_to_time(x)
        sx = self.time_to_x(self.start)
        ex = self.time_to_x(self.end)

        # 1) Try to grab an existing saved segment.
        for seg in reversed(self.segments):
            seg_x1 = self.time_to_x(seg["start"])
            seg_x2 = self.time_to_x(seg["end"])

            if seg_x1 <= x <= seg_x2:
                # Near left/right edge = resize.
                if abs(x - seg_x1) <= 9:
                    self.drag_mode = "segment_start"
                elif abs(x - seg_x2) <= 9:
                    self.drag_mode = "segment_end"
                else:
                    # Inside segment = MOVE whole segment.
                    self.drag_mode = "segment_move"

                self.drag_segment = seg
                self.segmentSelected.emit(seg)
                self.drag_anchor = t
                self.drag_segment_start = seg["start"]
                self.drag_segment_end = seg["end"]
                self.start = seg["start"]
                self.end = seg["end"]
                self.selectionChanged.emit(self.start, self.end)
                self.update()
                return

        # 2) Current unsaved selection handles.
        if abs(x - sx) <= 10 and self.end > self.start:
            self.drag_mode = "start"
        elif abs(x - ex) <= 10 and self.end > self.start:
            self.drag_mode = "end"
        else:
            # New selection starts exactly where mouse is pressed.
            self.drag_mode = "new"
            self.segmentSelected.emit(None)
            self.start = t
            self.end = t

        self.drag_anchor = t
        self.cursor = t
        self.selectionChanged.emit(self.start, self.end)
        self.update()

    def mouseMoveEvent(self, e):
        t = self.x_to_time(e.position().x())
        self.cursor = t
        self.cursorChanged.emit(t)

        if self.drag_mode == "new":
            self.end = max(self.drag_anchor, t)

        elif self.drag_mode == "start":
            self.start = min(t, self.end)

        elif self.drag_mode == "end":
            self.end = max(t, self.start)

        elif self.drag_mode == "segment_move" and self.drag_segment is not None:
            seg = self.drag_segment
            length = self.drag_segment_end - self.drag_segment_start
            delta = t - self.drag_anchor

            new_start = self.drag_segment_start + delta
            new_end = new_start + length

            if new_start < 0:
                new_start = 0
                new_end = length
            if new_end > self.duration:
                new_end = self.duration
                new_start = max(0, new_end - length)

            seg["start"] = new_start
            seg["end"] = new_end
            self.start = new_start
            self.end = new_end

        elif self.drag_mode == "segment_start" and self.drag_segment is not None:
            seg = self.drag_segment
            seg["start"] = max(0.0, min(t, seg["end"] - 0.001))
            self.start = seg["start"]
            self.end = seg["end"]

        elif self.drag_mode == "segment_end" and self.drag_segment is not None:
            seg = self.drag_segment
            seg["end"] = min(self.duration, max(t, seg["start"] + 0.001))
            self.start = seg["start"]
            self.end = seg["end"]

        if self.drag_mode:
            self.selectionChanged.emit(self.start, self.end)
            self.update()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            if self.drag_mode == "new" and self.end < self.start:
                self.end = self.start

            self.drag_mode = None
            self.drag_segment = None
            self.selectionChanged.emit(self.start, self.end)
            self.update()

    def set_selection(self, start, end):
        self.start = max(0.0, min(self.duration, start))
        self.end = max(self.start, min(self.duration, end))
        self.update()
        self.selectionChanged.emit(self.start, self.end)


# ============================================================

# ============================================================
# ASYNC DOWNLOAD WORKER
# ============================================================

class DownloadWorker(QObject):
    progress = Signal(str)
    finished = Signal(str, str, str)   # mp3, video_id, title
    error = Signal(str)

    def __init__(self, url, file_id, fmt, write_auto_subs=False):
        super().__init__()
        self.url = url
        self.file_id = file_id
        self.fmt = fmt
        self.write_auto_subs = write_auto_subs

    def _run(self, cmd):
        return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              text=True, check=False, bufsize=1)

    def run(self):
        try:
            self.progress.emit("Reading YouTube video information…")
            meta = self._run([YTDLP_BIN, "--print", "%(id)s|%(title)s", "--skip-download", "--no-playlist", self.url])
            if meta.returncode != 0 or not meta.stdout.strip():
                raise RuntimeError(meta.stdout[-3000:] or "yt-dlp could not read this URL.")
            first = meta.stdout.strip().splitlines()[0]
            if "|" not in first:
                raise RuntimeError("Could not determine the YouTube video ID.")
            video_id, title = first.split("|", 1)
            safe_title = re.sub(r"[^A-Za-z0-9._-]+", "_", title).strip("_")
            base = DOWNLOAD_DIR / f"{self.file_id}_{video_id}"
            source_template = str(base) + ".%(ext)s"

            self.progress.emit(f"Downloading new source: {video_id} …")
            cmd = [YTDLP_BIN, "--no-playlist", "--no-cache-dir", "--force-overwrites",
                   "--no-continue", "--no-part", "--newline", "-f", self.fmt,
                   "-o", source_template, self.url]
            r = self._run(cmd)
            if r.returncode != 0:
                raise RuntimeError(r.stdout[-5000:] or "Audio download failed.")

            candidates = [p for p in DOWNLOAD_DIR.glob(f"{self.file_id}_{video_id}.*")
                          if p.is_file() and p.suffix.lower() not in (".mp3", ".vtt", ".part", ".ytdl")]
            if not candidates:
                raise RuntimeError(f"Downloaded media for video {video_id} was not found.")
            candidates.sort(key=lambda x: x.stat().st_mtime, reverse=True)
            source = candidates[0]
            mp3 = DOWNLOAD_DIR / f"{self.file_id}_{video_id}.mp3"

            self.progress.emit("Converting audio to MP3 · mono · 48 kHz · 128 kbps…")
            ff = self._run([FFMPEG_BIN, "-y", "-i", str(source), "-vn", "-ac", "1",
                            "-ar", "48000", "-b:a", "128k", str(mp3)])
            if ff.returncode != 0:
                raise RuntimeError(ff.stdout[-5000:] or "FFmpeg conversion failed.")

            self.progress.emit("Downloading available subtitles…")
            caption_cmd = [YTDLP_BIN, "--skip-download", "--no-playlist", "--no-cache-dir",
                           "--write-subs", "--sub-langs", "en,en-US,en-GB,ar,ar-SA",
                           "--sub-format", "vtt", "-o", str(base) + ".%(ext)s", self.url]
            if self.write_auto_subs:
                caption_cmd.insert(3, "--write-auto-subs")
            self._run(caption_cmd)

            self.progress.emit("Download complete — loading new audio…")
            self.finished.emit(str(mp3), video_id, safe_title)
        except Exception as e:
            self.error.emit(str(e))


# MAIN AUDIO ANNOTATOR
# ============================================================

class AudioAnnotator(QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle("UAE Speaker Clipper — Audio Annotation")
        self.resize(1500, 950)

        self.audio_path = None
        self.annotation_source = None
        self.file_id = ""
        self.duration = 0.0
        self.captions_en = []
        self.captions_ar = []
        self.current_caption = ""
        self.segments = []
        self.active_segment = None
        self.playing_segment = None
        self.playing_segment_button = None
        self.speaker_names = [f"Speaker {i}" for i in range(1, 16)]

        self.player = None
        self.audio_output = None
        self._create_media_engine()

        self.stop_timer = QTimer(self)
        self.stop_timer.setInterval(50)
        self.stop_timer.timeout.connect(self.check_selection_end)

        self.build_ui()
        self.apply_style()
        self.update_speaker_combo_style()

    # --------------------------------------------------------
    # Media engine lifecycle
    # --------------------------------------------------------

    def _create_media_engine(self):
        """Create a completely fresh Qt media engine."""
        self.player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.player.setAudioOutput(self.audio_output)

        self.player.positionChanged.connect(self.position_changed)
        self.player.durationChanged.connect(self.duration_changed)
        self.player.playbackStateChanged.connect(self.playback_state_changed)

    def _reset_media_engine(self):
        """Destroy the previous media source/player and create a fresh one.

        This is intentionally stronger than player.setSource(QUrl()). Qt Multimedia
        can keep the previous backend/source alive for an event-loop cycle. Replacing
        the QMediaPlayer guarantees that the next URL/audio starts from a clean source.
        """
        old_player = self.player
        old_output = self.audio_output

        try:
            if old_player is not None:
                old_player.stop()
                old_player.setSource(QUrl())
                old_player.setPosition(0)
        except Exception:
            pass

        self.player = None
        self.audio_output = None

        if old_player is not None:
            old_player.deleteLater()
        if old_output is not None:
            old_output.deleteLater()

        QApplication.processEvents()
        self._create_media_engine()
        QApplication.processEvents()

    # --------------------------------------------------------
    # UI
    # --------------------------------------------------------

    def build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        main = QVBoxLayout(root)
        main.setSpacing(8)

        # Top controls
        top = QGridLayout()
        top.addWidget(QLabel("File ID"), 0, 0)
        self.file_id_input = QLineEdit()
        self.file_id_input.setPlaceholderText("Example: UAE000001")
        top.addWidget(self.file_id_input, 0, 1)

        top.addWidget(QLabel("YouTube URL"), 0, 2)
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("Paste YouTube URL")
        top.addWidget(self.url_input, 0, 3, 1, 4)

        self.quality = QComboBox()
        self.quality.addItems([
            "Audio Only — FASTEST",
            "360p",
            "480p",
            "720p",
            "Best available"
        ])
        top.addWidget(self.quality, 0, 7)

        self.load_btn = QPushButton("DOWNLOAD AUDIO")
        self.load_btn.clicked.connect(self.download_audio)
        top.addWidget(self.load_btn, 0, 8)

        self.open_btn = QPushButton("OPEN AUDIO")
        self.open_btn.clicked.connect(self.open_audio_file)
        top.addWidget(self.open_btn, 0, 9)

        self.clear_source_btn = QPushButton("CLEAR / RESET")
        self.clear_source_btn.setToolTip("Release the old audio and delete its app-downloaded source files. Saved project clips are kept.")
        self.clear_source_btn.clicked.connect(self.clear_source)
        top.addWidget(self.clear_source_btn, 0, 10)

        self.delete_audio_btn = QPushButton("DELETE SOURCE AUDIO")
        self.delete_audio_btn.clicked.connect(self.delete_current_audio)
        top.addWidget(self.delete_audio_btn, 0, 11)

        main.addLayout(top)

        self.download_progress = QProgressBar()
        self.download_progress.setRange(0, 0)
        self.download_progress.setTextVisible(False)
        self.download_progress.setFixedHeight(6)
        self.download_progress.setVisible(False)
        main.addWidget(self.download_progress)

        # Player
        player_box = QHBoxLayout()

        self.play_btn = QPushButton("▶ Play")
        self.play_btn.clicked.connect(self.toggle_play)
        player_box.addWidget(self.play_btn)

        self.stop_btn = QPushButton("■ Stop")
        self.stop_btn.clicked.connect(self.stop_audio)
        player_box.addWidget(self.stop_btn)

        self.position_slider = QSlider(Qt.Horizontal)
        self.position_slider.setRange(0, 0)
        self.position_slider.sliderMoved.connect(self.seek_audio)
        player_box.addWidget(self.position_slider, 1)

        self.position_label = QLabel("00:00:00.000")
        player_box.addWidget(self.position_label)

        self.duration_label = QLabel("/ 00:00:00")
        player_box.addWidget(self.duration_label)

        player_box.addWidget(QLabel("Speed"))
        self.speed_combo = QComboBox()
        self.speed_combo.addItems([
            "0.25x", "0.5x", "0.75x", "1.0x",
            "1.25x", "1.5x", "1.75x", "2.0x", "3.0x"
        ])
        self.speed_combo.setCurrentText("1.0x")
        self.speed_combo.currentTextChanged.connect(self.change_speed)
        player_box.addWidget(self.speed_combo)

        main.addLayout(player_box)

        # Waveform toolbar
        wave_toolbar = QHBoxLayout()
        wave_toolbar.addWidget(QLabel("WAVEFORM"))

        self.zoom_out = QPushButton("−")
        self.zoom_out.clicked.connect(lambda: self.change_zoom(-1))
        wave_toolbar.addWidget(self.zoom_out)

        self.zoom_slider = QSlider(Qt.Horizontal)
        self.zoom_slider.setRange(1, 200)
        self.zoom_slider.setValue(5)
        self.zoom_slider.setFixedWidth(220)
        self.zoom_slider.valueChanged.connect(self.set_zoom)
        wave_toolbar.addWidget(self.zoom_slider)

        self.zoom_in = QPushButton("+")
        self.zoom_in.clicked.connect(lambda: self.change_zoom(1))
        wave_toolbar.addWidget(self.zoom_in)

        self.zoom_label = QLabel("Zoom 5x")
        wave_toolbar.addWidget(self.zoom_label)

        self.selection_label = QLabel("Selection: 00:00:00.000 → 00:00:00.000 | 0.000 sec")
        wave_toolbar.addWidget(self.selection_label)

        self.cursor_time_label = QLabel("Cursor: 00:00:00.000")
        self.cursor_time_label.setStyleSheet("color: #ffd60a; font-weight: bold;")
        wave_toolbar.addWidget(self.cursor_time_label)

        wave_toolbar.addStretch()
        main.addLayout(wave_toolbar)

        instruction = QLabel(
            "Drag LEFT → RIGHT on the waveform: mouse-down = Start, mouse-up = End. "
            "Use the handles for fine adjustment. Playback always starts at Start."
        )
        instruction.setStyleSheet("color: #94a3b8; padding: 2px 4px;")
        main.addWidget(instruction)

        # Scrollable waveform
        self.wave_scroll = QScrollArea()
        self.wave_scroll.setWidgetResizable(False)
        self.wave_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.wave_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.wave_scroll.setFixedHeight(310)

        self.waveform = WaveformWidget()
        self.wave_scroll.setWidget(self.waveform)
        main.addWidget(self.wave_scroll, 1)

        self.waveform.selectionChanged.connect(self.selection_changed)
        self.waveform.cursorChanged.connect(self.cursor_changed)
        self.waveform.segmentSelected.connect(self.on_segment_selected)

        # Annotation controls
        controls = QGridLayout()

        controls.addWidget(QLabel("Speaker"), 0, 0)
        self.speaker_combo = QComboBox()
        self.speaker_combo.addItems(self.speaker_names)
        self.speaker_combo.currentIndexChanged.connect(self.update_speaker_combo_style)
        controls.addWidget(self.speaker_combo, 0, 1)

        controls.addWidget(QLabel("Speaker ID"), 0, 2)
        self.speaker_id_input = QLineEdit()
        self.speaker_id_input.setPlaceholderText("Example: LNID000501")
        controls.addWidget(self.speaker_id_input, 0, 3)

        controls.addWidget(QLabel("Start"), 1, 0)
        self.start_input = QDoubleSpinBox()
        self.start_input.setRange(0.0, 9999999.0)
        self.start_input.setDecimals(3)
        self.start_input.setSingleStep(0.010)
        self.start_input.setSuffix(" sec")
        self.start_input.valueChanged.connect(self.manual_selection_changed)
        controls.addWidget(self.start_input, 1, 1)

        controls.addWidget(QLabel("End"), 1, 2)
        self.end_input = QDoubleSpinBox()
        self.end_input.setRange(0.0, 9999999.0)
        self.end_input.setDecimals(3)
        self.end_input.setSingleStep(0.010)
        self.end_input.setSuffix(" sec")
        self.end_input.valueChanged.connect(self.manual_selection_changed)
        controls.addWidget(self.end_input, 1, 3)

        self.set_selection_btn = QPushButton("SET TIMESTAMPS")
        self.set_selection_btn.clicked.connect(self.apply_manual_selection)
        controls.addWidget(self.set_selection_btn, 1, 4)

        self.play_selection_btn = QPushButton("▶ PLAY SELECTION")
        self.play_selection_btn.clicked.connect(self.play_selection)
        controls.addWidget(self.play_selection_btn, 0, 4)

        self.save_btn = QPushButton("SAVE SEGMENT")
        self.save_btn.clicked.connect(self.save_segment)
        controls.addWidget(self.save_btn, 0, 5)

        self.clear_btn = QPushButton("CLEAR SELECTION")
        self.clear_btn.clicked.connect(self.clear_selection)
        controls.addWidget(self.clear_btn, 0, 6)

        self.noise_btn = QPushButton("TAG BACKGROUND NOISE")
        self.noise_btn.setToolTip("Mark this exact time range as background noise. It will be excluded from saved speaker audio.")
        self.noise_btn.clicked.connect(self.tag_background_noise)
        controls.addWidget(self.noise_btn, 1, 7)

        self.delete_selected_btn = QPushButton("DELETE SELECTED")
        self.delete_selected_btn.clicked.connect(self.delete_active_segment)
        controls.addWidget(self.delete_selected_btn, 0, 7)

        self.update_segment_btn = QPushButton("UPDATE SELECTED SEGMENT")
        self.update_segment_btn.clicked.connect(self.update_selected_segment)
        controls.addWidget(self.update_segment_btn, 1, 5, 1, 2)

        main.addLayout(controls)

        # Speaker segments table
        main.addWidget(QLabel("SPEAKER SEGMENTS"))

        self.table = QTableWidget(0, 11)
        self.table.setHorizontalHeaderLabels([
            "Type", "Segment", "Speaker", "Speaker ID", "Start", "End", "Duration",
            "Play", "Edit", "Delete", "File"
        ])
        self.table.setAlternatingRowColors(True)
        self.table.setMinimumHeight(260)
        self.table.setMaximumHeight(430)
        self.table.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setDefaultSectionSize(34)
        self.table.horizontalHeader().setStretchLastSection(True)
        main.addWidget(self.table)

        # Bottom buttons
        bottom = QHBoxLayout()
        self.export_btn = QPushButton("EXPORT CSV")
        self.export_btn.clicked.connect(self.export_csv)
        bottom.addWidget(self.export_btn)

        self.folder_btn = QPushButton("OPEN PROJECT FOLDER")
        self.folder_btn.clicked.connect(self.open_project_folder)
        bottom.addWidget(self.folder_btn)

        bottom.addStretch()
        main.addLayout(bottom)

        # Project folder tree
        project_header = QHBoxLayout()
        project_header.addWidget(QLabel("PROJECT FILES / FOLDER TREE"))
        project_header.addStretch()

        self.refresh_projects_btn = QPushButton("↻ REFRESH")
        self.refresh_projects_btn.clicked.connect(self.refresh_project_tree)
        project_header.addWidget(self.refresh_projects_btn)

        self.show_all_folders_btn = QPushButton("SHOW ALL FOLDERS")
        self.show_all_folders_btn.clicked.connect(self.show_all_folders)
        project_header.addWidget(self.show_all_folders_btn)

        self.play_tree_file_btn = QPushButton("▶ PLAY SELECTED FILE")
        self.play_tree_file_btn.clicked.connect(self.play_selected_tree_file)
        project_header.addWidget(self.play_tree_file_btn)

        self.open_tree_folder_btn = QPushButton("OPEN SELECTED FOLDER")
        self.open_tree_folder_btn.clicked.connect(self.open_selected_tree_folder)
        project_header.addWidget(self.open_tree_folder_btn)
        main.addLayout(project_header)

        self.project_tree = QTreeWidget()
        self.project_tree.setHeaderLabels(["Folder / File", "Type", "Size"])
        self.project_tree.setMinimumHeight(190)
        self.project_tree.setMaximumHeight(260)
        self.project_tree.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.project_tree.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.project_tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self.project_tree.itemDoubleClicked.connect(self.project_tree_double_click)
        main.addWidget(self.project_tree)

        self.refresh_project_tree()

        # Signals
        self.player.positionChanged.connect(self.position_changed)
        self.player.durationChanged.connect(self.duration_changed)
        self.player.playbackStateChanged.connect(self.playback_state_changed)

    # --------------------------------------------------------
    # Styling
    # --------------------------------------------------------

    def apply_style(self):
        self.setStyleSheet("""
            QWidget {
                background: #0b1220;
                color: #e5e7eb;
                font-size: 13px;
            }
            QLineEdit, QComboBox, QPlainTextEdit, QSpinBox, QDoubleSpinBox {
                background: #111827;
                border: 1px solid #334155;
                border-radius: 5px;
                padding: 6px;
                color: #f8fafc;
            }
            QPushButton {
                background: #1e293b;
                border: 1px solid #475569;
                border-radius: 5px;
                padding: 7px 12px;
            }
            QPushButton:hover {
                background: #334155;
            }
            QTableWidget {
                background: #0f172a;
                gridline-color: #334155;
            }
            QHeaderView::section {
                background: #1e293b;
                padding: 6px;
                border: 1px solid #334155;
            }
            QSlider::groove:horizontal {
                height: 6px;
                background: #334155;
                border-radius: 3px;
            }
            QSlider::handle:horizontal {
                width: 14px;
                margin: -5px 0;
                border-radius: 7px;
                background: #38bdf8;
            }
        """)

    # --------------------------------------------------------
    # Download
    # --------------------------------------------------------

    def download_audio(self):
        url = self.url_input.text().strip()
        if not url:
            QMessageBox.warning(self, "Missing URL", "Paste a YouTube URL.")
            return

        self.file_id = self.file_id_input.text().strip()
        if not self.file_id:
            QMessageBox.warning(self, "Missing File ID", "Enter a File ID first.")
            return

        self.load_btn.setEnabled(False)
        self.open_btn.setEnabled(False)
        self.clear_source_btn.setEnabled(False)
        self.status_label.setText("Preparing new download…")
        if hasattr(self, "download_progress"):
            self.download_progress.setRange(0, 0)
            self.download_progress.setVisible(True)
        QApplication.processEvents()

        # Release the old media engine BEFORE starting the new download.
        self.stop_timer.stop()
        self._reset_media_engine()
        self.audio_path = None
        self.annotation_source = None
        self.duration = 0.0
        self.captions_en = []
        self.captions_ar = []
        self.current_caption = ""
        self.segments = []
        self.active_segment = None
        self.playing_segment = None
        self.waveform.set_samples([])
        self.waveform.set_segments([])
        self.waveform.set_duration(0)
        self.waveform.set_selection(0, 0)
        self.table.setRowCount(0)

        if self.quality.currentIndex() == 0:
            fmt = "bestaudio[ext=m4a]/bestaudio/best"
        else:
            q = self.quality.currentText().split("p")[0]
            if q == "Best available":
                fmt = "bestvideo+bestaudio/best"
            else:
                fmt = f"bestvideo[height<={q}]+bestaudio/best[height<={q}]/best"

        self.download_thread = QThread(self)
        self.download_worker = DownloadWorker(
            url, self.file_id, fmt,
            self.en_auto_check.isChecked() or self.ar_auto_check.isChecked()
        )
        self.download_worker.moveToThread(self.download_thread)
        self.download_thread.started.connect(self.download_worker.run)
        self.download_worker.progress.connect(self.download_progress_update)
        self.download_worker.finished.connect(self.download_finished)
        self.download_worker.error.connect(self.download_failed)
        self.download_worker.finished.connect(self.download_thread.quit)
        self.download_worker.error.connect(self.download_thread.quit)
        self.download_thread.finished.connect(self.download_worker.deleteLater)
        self.download_thread.finished.connect(self.download_thread.deleteLater)
        self.download_thread.start()

    def download_progress_update(self, message):
        self.status_label.setText(message)

    def download_finished(self, mp3_path, video_id, title):
        try:
            self.load_audio(Path(mp3_path))
            self.captions_en, self.captions_ar = self._load_caption_lists(self.file_id, video_id)
            self.status_label.setText(f"Ready — {fmt_time(self.duration)} — {video_id}")
        except Exception as e:
            self.download_failed(str(e))
            return
        finally:
            self.load_btn.setEnabled(True)
            self.open_btn.setEnabled(True)
            self.clear_source_btn.setEnabled(True)
            if hasattr(self, "download_progress"):
                self.download_progress.setVisible(False)

    def download_failed(self, message):
        self.status_label.setText("Download failed")
        QMessageBox.critical(self, "Download Error", message)
        self.load_btn.setEnabled(True)
        self.open_btn.setEnabled(True)
        self.clear_source_btn.setEnabled(True)
        if hasattr(self, "download_progress"):
            self.download_progress.setVisible(False)

    def _load_caption_lists(self, file_id, video_id):
        folder = DOWNLOAD_DIR
        en = []
        ar = []

        for p in folder.glob(f"{file_id}_{video_id}*.vtt"):
            n = p.name.lower()
            if ".en" in n or ".en-us" in n or ".en-gb" in n:
                en.append(p)
            elif ".ar" in n or ".ar-sa" in n:
                ar.append(p)

        # fallback if naming differs
        if not en or not ar:
            e2, a2 = find_caption_files(folder)
            if not en:
                en = e2[-5:]
            if not ar:
                ar = a2[-5:]

        return en, ar

    # --------------------------------------------------------
    # Open local audio
    # --------------------------------------------------------

    def open_audio_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open audio",
            str(DOWNLOAD_DIR),
            "Audio (*.mp3 *.m4a *.wav *.flac *.ogg *.opus)"
        )
        if path:
            self.load_audio(Path(path))

    def load_audio(self, path):
        path = Path(path)

        if not path.exists():
            raise FileNotFoundError(f"Audio file not found: {path}")

        # Always replace the media engine before loading a different file.
        # This prevents Qt from retaining the previously loaded audio source.
        self.stop_timer.stop()
        self._reset_media_engine()

        self.audio_path = path
        self.annotation_source = path
        self.duration = ffprobe_duration(path)

        self.player.setSource(QUrl.fromLocalFile(str(path)))

        self.position_slider.setRange(0, int(self.duration * 1000))
        self.duration_label.setText(f"/ {fmt_time(self.duration)}")

        self.start_input.setRange(0.0, self.duration)
        self.end_input.setRange(0.0, self.duration)
        self.start_input.setValue(0.0)
        self.end_input.setValue(min(self.duration, 1.0))

        self.waveform.set_duration(self.duration)
        self.waveform.set_selection(0, min(self.duration, 1.0))

        self.load_waveform_peaks()
        self.status_label.setText(f"Loaded: {self.audio_path.name}")

        # Try captions next to audio.
        stem = self.audio_path.stem
        self.captions_en = []
        self.captions_ar = []
        for p in self.audio_path.parent.glob(f"{stem}*.vtt"):
            n = p.name.lower()
            if ".en" in n:
                self.captions_en.append(p)
            if ".ar" in n:
                self.captions_ar.append(p)


    def _parse_caption_lists(self):
        self.captions_en_data = []
        self.captions_ar_data = []

        if self.captions_en:
            self.captions_en_data = parse_vtt(self.captions_en[0])
        if self.captions_ar:
            self.captions_ar_data = parse_vtt(self.captions_ar[0])

    # --------------------------------------------------------
    # Waveform extraction
    # --------------------------------------------------------

    def load_waveform_peaks(self):
        if not self.audio_path:
            return

        self.status_label.setText("Building waveform...")

        # Compact peak extraction using ffmpeg PCM.
        # For very long audio, cap to a manageable number of samples.
        target = 120000
        cmd = [
            FFMPEG_BIN, "-v", "error",
            "-i", str(self.audio_path),
            "-ac", "1",
            "-ar", "8000",
            "-f", "s16le",
            "pipe:1"
        ]
        r = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )

        if r.returncode != 0 or not r.stdout:
            self.waveform.set_samples([])
            self.status_label.setText("Waveform loaded (fallback view)")
            return

        import array
        data = array.array("h")
        data.frombytes(r.stdout)

        n = len(data)
        bucket = max(1, n // target)
        peaks = []

        for i in range(0, n, bucket):
            chunk = data[i:i + bucket]
            if not chunk:
                break
            peak = max(abs(x) for x in chunk) / 32768.0
            peaks.append(min(1.0, peak))

        self.waveform.set_samples(peaks)
        self.status_label.setText("Waveform ready")

    # --------------------------------------------------------
    # Playback
    # --------------------------------------------------------

    def toggle_play(self):
        if not self.audio_path:
            return

        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def stop_audio(self):
        self.stop_timer.stop()
        self.player.stop()

    def seek_audio(self, value):
        self.player.setPosition(value)

    def position_changed(self, position):
        sec = position / 1000.0
        self.position_slider.blockSignals(True)
        self.position_slider.setValue(position)
        self.position_slider.blockSignals(False)

        self.position_label.setText(fmt_time(sec, True))
        self.cursor_time_label.setText(f"Cursor: {fmt_time(sec, True)}")
        self.waveform.cursor = sec
        self.waveform.update()

        # Keep the moving playback pointer visible in the horizontal viewport.
        cursor_x = int(self.waveform.time_to_x(sec))
        viewport = self.wave_scroll.viewport()
        left = self.wave_scroll.horizontalScrollBar().value()
        right = left + viewport.width()

        if cursor_x < left + 60:
            self.wave_scroll.horizontalScrollBar().setValue(max(0, cursor_x - 120))
        elif cursor_x > right - 60:
            self.wave_scroll.horizontalScrollBar().setValue(
                max(0, cursor_x - viewport.width() + 120)
            )


    def duration_changed(self, duration):
        if duration > 0:
            self.duration = duration / 1000.0
            self.waveform.set_duration(self.duration)
            self.position_slider.setRange(0, duration)

    def playback_state_changed(self, state):
        self.play_btn.setText(
            "❚❚ Pause" if state == QMediaPlayer.PlayingState else "▶ Play"
        )
        if state != QMediaPlayer.PlayingState and self.playing_segment_button is not None:
            try:
                self.playing_segment_button.setText("▶ Play")
            except Exception:
                pass
            self.playing_segment = None
            self.playing_segment_button = None

    def play_selection(self):
        if not self.annotation_source:
            return

        start = float(self.waveform.start)
        end = float(self.waveform.end)

        if end <= start + 0.001:
            return

        # Always return to the original full audio.
        # This prevents a previously saved clip from becoming
        # the playback source for the next selection.
        self.player.stop()
        self.player.setSource(
            QUrl.fromLocalFile(str(self.annotation_source))
        )

        # Seek BEFORE play so playback begins exactly at Start.
        self.player.setPosition(int(round(start * 1000)))
        self.change_speed(self.speed_combo.currentText())
        self.player.play()

        self.stop_timer.stop()
        QTimer.singleShot(80, self._confirm_selection_start)
        self.stop_timer.start()

    def _confirm_selection_start(self):
        if not self.annotation_source:
            return
        # Qt may need one event-loop cycle after setSource().
        # Re-apply the exact start position before playback continues.
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            target = int(round(self.waveform.start * 1000))
            current = self.player.position()
            if abs(current - target) > 120:
                self.player.setPosition(target)

    def check_selection_end(self):
        pos = self.player.position() / 1000.0
        if pos >= self.waveform.end - 0.03:
            self.player.pause()
            self.player.setPosition(int(self.waveform.end * 1000))
            self.stop_timer.stop()

    # --------------------------------------------------------
    # Caption synchronization
    # --------------------------------------------------------

    # --------------------------------------------------------
    # Selection
    # --------------------------------------------------------

    def selection_changed(self, start, end):
        duration = max(0.0, end - start)
        self.selection_label.setText(
            f"Selection: {fmt_time(start, True)} → {fmt_time(end, True)} | "
            f"{duration:.3f} sec"
        )

        self.start_input.blockSignals(True)
        self.end_input.blockSignals(True)
        self.start_input.setValue(start)
        self.end_input.setValue(end)
        self.start_input.blockSignals(False)
        self.end_input.blockSignals(False)

    def on_segment_selected(self, seg):
        self.active_segment = seg
        if seg is not None:
            self.speaker_combo.setCurrentText(seg.get("speaker", "Speaker 1"))
            self.speaker_id_input.setText(seg.get("speaker_id", ""))
    
    def cursor_changed(self, sec):
        self.cursor_time_label.setText(f"Cursor: {fmt_time(sec, True)}")

    def manual_selection_changed(self):
        # Fields are intentionally live, but selection is only committed
        # when SET TIMESTAMPS is pressed.
        pass

    def apply_manual_selection(self):
        if self.duration <= 0:
            return

        start = max(0.0, min(self.duration, self.start_input.value()))
        end = max(0.0, min(self.duration, self.end_input.value()))

        if end < start:
            start, end = end, start

        if end <= start:
            QMessageBox.warning(
                self, "Invalid timestamps",
                "End time must be greater than Start time."
            )
            return

        self.waveform.set_selection(start, end)
        self.wave_scroll.ensureVisible(
            int(self.waveform.time_to_x(start)),
            120,
            200,
            120
        )

    def change_speed(self, value):
        try:
            speed = float(value.replace("x", ""))
            self.player.setPlaybackRate(speed)
        except Exception:
            pass

    def clear_selection(self):
        self.waveform.set_selection(0, min(0.5, self.duration))

    def change_zoom(self, delta):
        value = max(1, min(200, self.zoom_slider.value() + delta))
        self.zoom_slider.setValue(value)

    def set_zoom(self, value):
        self.waveform.set_zoom(value)
        self.zoom_label.setText(f"Zoom {value}x")

    # --------------------------------------------------------
    # Segment saving
    # --------------------------------------------------------

    def update_speaker_combo_style(self):
        speaker = self.speaker_combo.currentText()
        color = self.speaker_color(speaker)
        self.speaker_combo.setStyleSheet(
            f"QComboBox {{ background: {color}; color: #000000; "
            f"font-weight: bold; padding: 6px; }}"
        )

    def speaker_color(self, speaker):
        try:
            idx = int(re.search(r"\d+", speaker).group()) - 1
        except Exception:
            idx = 0
        return SPEAKER_COLORS[idx % len(SPEAKER_COLORS)]

    # --------------------------------------------------------
    # Segment / background-noise helpers
    # --------------------------------------------------------

    def background_noise_segments(self):
        return [s for s in self.segments if s.get("type") == "background_noise"]

    def speaker_segments(self):
        return [s for s in self.segments if s.get("type", "speech") != "background_noise"]

    def clean_ranges(self, start, end):
        """Return source ranges inside [start,end] excluding tagged noise."""
        if end <= start:
            return []

        intervals = []
        for n in self.background_noise_segments():
            ns = max(start, float(n["start"]))
            ne = min(end, float(n["end"]))
            if ne > ns:
                intervals.append((ns, ne))

        if not intervals:
            return [(start, end)]

        intervals.sort()
        merged = []
        for a, b in intervals:
            if not merged or a > merged[-1][1] + 0.001:
                merged.append([a, b])
            else:
                merged[-1][1] = max(merged[-1][1], b)

        ranges = []
        cursor = start
        for a, b in merged:
            if a > cursor + 0.001:
                ranges.append((cursor, a))
            cursor = max(cursor, b)
        if cursor < end - 0.001:
            ranges.append((cursor, end))
        return ranges

    def render_clean_segment(self, source, start, end, output):
        """Render a segment while physically removing all tagged noise ranges."""
        ranges = self.clean_ranges(start, end)
        if not ranges:
            return False, "The entire selected range is tagged as background noise."

        if len(ranges) == 1 and abs(ranges[0][0] - start) < 0.0005 and abs(ranges[0][1] - end) < 0.0005:
            cmd = [
                FFMPEG_BIN, "-y", "-v", "error",
                "-ss", f"{start:.3f}", "-to", f"{end:.3f}",
                "-i", str(source), "-vn", "-ac", "1", "-ar", "48000",
                "-b:a", "128k", str(output)
            ]
        else:
            filters = []
            labels = []
            for i, (a, b) in enumerate(ranges):
                filters.append(
                    f"[0:a]atrim=start={a:.3f}:end={b:.3f},asetpts=PTS-STARTPTS[a{i}]"
                )
                labels.append(f"[a{i}]")
            filters.append("".join(labels) + f"concat=n={len(ranges)}:v=0:a=1[out]")
            cmd = [
                FFMPEG_BIN, "-y", "-v", "error", "-i", str(source),
                "-filter_complex", ";".join(filters), "-map", "[out]",
                "-vn", "-ac", "1", "-ar", "48000", "-b:a", "128k",
                str(output)
            ]

        r = run_cmd(cmd)
        return r.returncode == 0, r.stdout[-3000:]

    def tag_background_noise(self):
        if not self.audio_path:
            QMessageBox.warning(self, "No audio", "Load audio first.")
            return
        start = float(self.waveform.start)
        end = float(self.waveform.end)
        if end <= start + 0.001:
            QMessageBox.warning(self, "Invalid selection", "Select the background-noise portion first.")
            return

        # Do not create duplicate/overlapping noise tags unnecessarily.
        for n in self.background_noise_segments():
            if abs(n["start"] - start) < 0.001 and abs(n["end"] - end) < 0.001:
                return

        number = 1
        while any(s.get("type") == "background_noise" and s.get("segment_number") == number for s in self.segments):
            number += 1

        noise = {
            "type": "background_noise",
            "segment_number": number,
            "speaker": "BACKGROUND NOISE",
            "speaker_id": "",
            "start": start,
            "end": end,
            "duration": end - start,
            "file": "",
            "notes": "Excluded from output audio",
            "color": "#ff453a"
        }
        self.segments.append(noise)
        self.active_segment = noise
        self.waveform.set_segments(self.segments)

        # Immediately rebuild existing speaker clips affected by this noise tag.
        self.rebuild_all_speech_segments()
        self.refresh_table()
        self.refresh_project_tree()
        self.status_label.setText(
            f"Background noise tagged: {start:.3f}–{end:.3f} sec — excluded from speaker output"
        )

    def rebuild_all_speech_segments(self):
        if not self.annotation_source:
            return
        removed = []
        for seg in list(self.speaker_segments()):
            # Only rebuild if at least one noise interval intersects the source range.
            ranges = self.clean_ranges(seg["start"], seg["end"])
            original_duration = seg["end"] - seg["start"]
            clean_duration = sum(b - a for a, b in ranges)
            if clean_duration <= 0.001:
                Path(seg["file"]).unlink(missing_ok=True)
                removed.append(seg)
                continue
            if abs(clean_duration - original_duration) < 0.0005 and Path(seg["file"]).exists():
                continue
            self._render_existing_segment(seg)
        for seg in removed:
            if seg in self.segments:
                self.segments.remove(seg)
        self.waveform.set_segments(self.segments)

    def _render_existing_segment(self, seg):
        output = Path(seg["file"])
        tmp = output.with_name(output.stem + "_tmp.mp3")
        ok, detail = self.render_clean_segment(self.annotation_source, seg["start"], seg["end"], tmp)
        if not ok:
            tmp.unlink(missing_ok=True)
            return False
        try:
            tmp.replace(output)
            ranges = self.clean_ranges(seg["start"], seg["end"])
            seg["duration"] = sum(b - a for a, b in ranges)
            return True
        except Exception:
            tmp.unlink(missing_ok=True)
            return False

    def delete_active_segment(self):
        if self.active_segment is None:
            QMessageBox.information(self, "No segment selected", "Click a colored segment or select a row first.")
            return
        self.delete_segment(None, self.active_segment)

    def save_segment(self):
        if not self.audio_path:
            QMessageBox.warning(self, "No audio", "Load audio first.")
            return

        start = self.waveform.start
        end = self.waveform.end

        if end <= start + 0.001:
            QMessageBox.warning(self, "Invalid selection", "Drag over a speech segment first.")
            return

        speaker = self.speaker_combo.currentText()
        speaker_id = self.speaker_id_input.text().strip() or speaker.replace(" ", "_")
        file_id = self.file_id_input.text().strip() or self.audio_path.stem

        folder = PROJECT_DIR / file_id / speaker_id
        folder.mkdir(parents=True, exist_ok=True)

        existing_numbers = [
            int(s.get("segment_number")) for s in self.speaker_segments()
            if s.get("speaker_id") == speaker_id and str(s.get("segment_number", "")).isdigit()
        ]
        number = 1
        while number in existing_numbers or (folder / f"{file_id}_{speaker_id}_{number:04d}.mp3").exists():
            number += 1

        output = folder / f"{file_id}_{speaker_id}_{number:04d}.mp3"

        notes = ""

        ok, detail = self.render_clean_segment(self.audio_path, start, end, output)
        if not ok:
            output.unlink(missing_ok=True)
            QMessageBox.warning(self, "No clean audio", detail)
            return

        clean_duration = sum(b - a for a, b in self.clean_ranges(start, end))
        seg = {
            "type": "speech",
            "speaker": speaker,
            "speaker_id": speaker_id,
            "segment_number": number,
            "start": start,
            "end": end,
            "duration": clean_duration,
            "source_duration": end - start,
            "file": str(output),
            "notes": notes,
            "color": self.speaker_color(speaker)
        }

        self.segments.append(seg)
        self.active_segment = seg
        self.waveform.set_segments(self.segments)
        self.add_table_row(seg)
        self.refresh_project_tree()

        self.status_label.setText(
            f"Saved {output.name} — {end - start:.3f} sec"
        )

        # Immediately preview exactly what was saved.
        # It starts at the selected Start, never at 00:00.
        self.play_selection()

    def update_selected_segment(self):
        if not self.segments:
            QMessageBox.information(self, "No segments", "There are no saved segments.")
            return

        target = self.active_segment
        if target is None or target.get("type") == "background_noise":
            for seg in self.speaker_segments():
                if (abs(seg["start"] - self.waveform.start) < 0.01 and
                    abs(seg["end"] - self.waveform.end) < 0.01):
                    target = seg
                    break

        if target is None or target.get("type") == "background_noise":
            QMessageBox.information(
                self, "No segment selected",
                "Click an existing colored segment first, then move/resize it."
            )
            return

        start = self.waveform.start
        end = self.waveform.end

        if end <= start:
            return

        output = Path(target["file"])
        tmp = output.with_name(output.stem + "_tmp.mp3")

        # Temporarily use the new boundaries when rendering against noise tags.
        old_start, old_end = target["start"], target["end"]
        target["start"], target["end"] = start, end
        ok, detail = self.render_clean_segment(self.annotation_source, start, end, tmp)
        if not ok:
            target["start"], target["end"] = old_start, old_end
            tmp.unlink(missing_ok=True)
            QMessageBox.warning(self, "No clean audio", detail)
            return

        try:
            tmp.replace(output)
        except Exception:
            target["start"], target["end"] = old_start, old_end
            QMessageBox.critical(self, "Update Error", "Could not replace the saved segment.")
            return

        target["duration"] = sum(b - a for a, b in self.clean_ranges(start, end))
        target["source_duration"] = end - start
        target["notes"] = ""

        self.waveform.set_segments(self.segments)
        self.refresh_table()
        self.refresh_project_tree()

        self.status_label.setText(
            f"Updated {output.name} — {end - start:.3f} sec"
        )

        self.play_selection()

    def refresh_table(self):
        self.table.setRowCount(0)
        for seg in self.segments:
            self.add_table_row(seg)

    def add_table_row(self, seg):
        row = self.table.rowCount()
        self.table.insertRow(row)
        is_noise = seg.get("type") == "background_noise"
        seg_no = seg.get("segment_number", "")
        values = [
            "BACKGROUND NOISE" if is_noise else "SPEECH",
            str(seg_no),
            seg.get("speaker", ""),
            seg.get("speaker_id", ""),
            fmt_time(seg["start"], True),
            fmt_time(seg["end"], True),
            f'{seg.get("duration", seg["end"] - seg["start"]):.3f}',
            "", "", "",
            Path(seg["file"]).name if seg.get("file") else "(excluded from output)"
        ]
        for col, value in enumerate(values):
            item = QTableWidgetItem(str(value))
            if is_noise:
                item.setBackground(QColor("#ff453a"))
                item.setForeground(QColor("#ffffff"))
            self.table.setItem(row, col, item)

        play = QPushButton("▶ Play")
        play.setMinimumWidth(78)
        play.clicked.connect(lambda _, s=seg, b=play: self.toggle_segment_play(s, b))
        self.table.setCellWidget(row, 7, play)

        edit = QPushButton("Edit")
        edit.clicked.connect(lambda _, s=seg: self.edit_segment(s))
        self.table.setCellWidget(row, 8, edit)

        delete = QPushButton("Delete")
        delete.clicked.connect(lambda _, s=seg: self.delete_segment(None, s))
        self.table.setCellWidget(row, 9, delete)

    def toggle_segment_play(self, seg, button):
        path = seg.get("file")
        if seg.get("type") == "background_noise":
            if self.playing_segment is seg and self.player.playbackState() == QMediaPlayer.PlayingState:
                self.player.pause()
                button.setText("▶ Play")
                self.playing_segment = None
                self.playing_segment_button = None
                return
            self._stop_segment_button()
            self.waveform.set_selection(seg["start"], seg["end"])
            self.play_selection_for_range(seg["start"], seg["end"])
            button.setText("❚❚ Pause")
            self.playing_segment = seg
            self.playing_segment_button = button
            return
        if not path or not Path(path).exists():
            QMessageBox.warning(self, "Missing file", str(path or ""))
            return
        if self.playing_segment is seg and self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
            button.setText("▶ Play")
            self.playing_segment = None
            self.playing_segment_button = None
            return
        self._stop_segment_button()
        self.stop_timer.stop()
        self.player.stop()
        self.player.setSource(QUrl.fromLocalFile(str(path)))
        self.player.setPosition(0)
        self.change_speed(self.speed_combo.currentText())
        self.player.play()
        button.setText("❚❚ Pause")
        self.playing_segment = seg
        self.playing_segment_button = button

    def _stop_segment_button(self):
        if self.playing_segment_button is not None:
            try:
                self.playing_segment_button.setText("▶ Play")
            except Exception:
                pass
        self.playing_segment = None
        self.playing_segment_button = None

    def play_segment_source(self, seg):
        if seg.get("type") == "background_noise":
            self.play_selection_for_range(seg["start"], seg["end"])
        else:
            self.play_saved_segment(seg)

    def play_selection_for_range(self, start, end):
        if not self.annotation_source or end <= start:
            return
        self.waveform.set_selection(start, end)
        self.play_selection()

    def play_saved_segment(self, seg):
        self._stop_segment_button()
        path = seg.get("file")
        if not path or not Path(path).exists():
            QMessageBox.warning(self, "Missing file", str(path or ""))
            return
        self.stop_timer.stop()
        self.player.stop()
        self.player.setSource(QUrl.fromLocalFile(path))
        self.player.setPosition(0)
        self.change_speed(self.speed_combo.currentText())
        self.player.play()

    def edit_segment(self, seg):
        self.active_segment = seg
        self.waveform.set_selection(seg["start"], seg["end"])
        if seg.get("type") != "background_noise":
            self.speaker_combo.setCurrentText(seg.get("speaker", "Speaker 1"))
            self.speaker_id_input.setText(seg.get("speaker_id", ""))
        self.wave_scroll.ensureVisible(
            int(self.waveform.time_to_x(seg["start"])), 120, 250, 120
        )

    def delete_segment(self, row, seg):
        if seg is None:
            return
        label = (f'background noise {seg["start"]:.3f}–{seg["end"]:.3f}'
                 if seg.get("type") == "background_noise"
                 else Path(seg["file"]).name)
        reply = QMessageBox.question(self, "Delete segment", f"Delete {label}?")
        if reply != QMessageBox.Yes:
            return

        was_noise = seg.get("type") == "background_noise"
        if not was_noise and seg.get("file"):
            try:
                Path(seg["file"]).unlink(missing_ok=True)
            except Exception:
                pass

        if seg in self.segments:
            self.segments.remove(seg)
        if self.active_segment is seg:
            self.active_segment = None

        # Removing a noise tag means the affected speaker files need to be rebuilt
        # so that the previously excluded audio is restored.
        if was_noise:
            self.rebuild_all_speech_segments()

        self.waveform.set_segments(self.segments)
        self.refresh_table()
        self.refresh_project_tree()

    def play_saved_segment(self, seg):
        path = seg["file"]
        if not Path(path).exists():
            QMessageBox.warning(self, "Missing file", path)
            return

        self.stop_timer.stop()
        self.player.stop()

        # The saved MP3 itself is exactly the selected segment.
        self.player.setSource(QUrl.fromLocalFile(path))
        self.player.setPosition(0)
        self.player.play()

    def edit_segment(self, seg):
        self.waveform.set_selection(seg["start"], seg["end"])
        self.speaker_combo.setCurrentText(seg["speaker"])
        self.speaker_id_input.setText(seg["speaker_id"])

        self.wave_scroll.ensureVisible(
            int(self.waveform.time_to_x(seg["start"])),
            120,
            250,
            120
        )

    def delete_segment(self, row, seg):
        reply = QMessageBox.question(
            self, "Delete segment",
            f"Delete {Path(seg['file']).name}?"
        )
        if reply != QMessageBox.Yes:
            return

        try:
            Path(seg["file"]).unlink(missing_ok=True)
        except Exception:
            pass

        if seg in self.segments:
            self.segments.remove(seg)

        self.waveform.set_segments(self.segments)
        self.refresh_table()
        self.refresh_project_tree()

    # --------------------------------------------------------
    # Source / project browser
    # --------------------------------------------------------

    def clear_source(self, delete_downloaded_source=True):
        """Completely clear the current source and reset the annotation session.

        If the current source was downloaded by this app, its MP3/media/caption
        files are removed from DOWNLOAD_DIR. Project speaker clips are NEVER removed.
        """
        old_path = Path(self.audio_path) if self.audio_path else None

        self.stop_timer.stop()

        # Capture the downloaded-source files before destroying the media engine.
        files_to_delete = set()
        if delete_downloaded_source and old_path:
            try:
                if old_path.exists() and old_path.parent.resolve() == DOWNLOAD_DIR.resolve():
                    stem = old_path.stem
                    for p in DOWNLOAD_DIR.iterdir():
                        if p.is_file() and (p.stem == stem or p.name.startswith(stem + ".")):
                            files_to_delete.add(p)
            except Exception:
                pass

        # Completely release the old Qt media backend.
        self._reset_media_engine()

        # Delete only app-created source files in Downloads.
        for p in files_to_delete:
            try:
                if p.exists():
                    p.unlink()
            except Exception as exc:
                print(f"Could not delete old source {p}: {exc}")

        self.audio_path = None
        self.annotation_source = None
        self.file_id = ""
        self.duration = 0.0
        self.captions_en = []
        self.captions_ar = []
        self.current_caption = ""
        self.segments = []
        self.active_segment = None

        self.file_id_input.clear()
        self.url_input.clear()
        self.speaker_id_input.clear()
        self.position_slider.setRange(0, 0)
        self.position_slider.setValue(0)
        self.position_label.setText("00:00:00.000")
        self.duration_label.setText("/ 00:00:00")
        self.cursor_time_label.setText("Cursor: 00:00:00.000")
        self.selection_label.setText("Selection: 00:00:00.000 → 00:00:00.000 | 0.000 sec")
        self.waveform.set_duration(0)
        self.waveform.set_samples([])
        self.waveform.set_segments([])
        self.waveform.set_selection(0, 0)
        self.table.setRowCount(0)

        self.refresh_project_tree()
        self.status_label.setText(
            "Completely cleared — previous source released/deleted. Ready for a new URL."
        )

    def delete_current_audio(self):
        """Delete the currently loaded downloaded source, then reset the UI."""
        if not self.audio_path:
            QMessageBox.information(
                self, "No source audio",
                "There is no loaded source audio to delete."
            )
            return

        path = Path(self.audio_path)

        if path.parent.resolve() != DOWNLOAD_DIR.resolve():
            QMessageBox.information(
                self,
                "Local audio",
                "This audio is a local file outside the app Downloads folder. "
                "It will be unloaded, but the original local file will not be deleted."
            )
            self.clear_source(delete_downloaded_source=False)
            return

        reply = QMessageBox.warning(
            self,
            "Delete source audio",
            f"Delete the current downloaded source and its related media/captions?\n\n"
            f"{path.name}\n\n"
            "Saved Speaker ID clips in Projects will NOT be deleted.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return

        self.clear_source(delete_downloaded_source=True)
        self.status_label.setText(
            "Previous source deleted — ready for a completely new URL."
        )

    def refresh_project_tree(self):
        """Show PROJECT_DIR as File ID -> Speaker ID -> clip files."""
        if not hasattr(self, "project_tree"):
            return

        self.project_tree.clear()
        root = QTreeWidgetItem([PROJECT_DIR.name, "PROJECTS", ""])
        root.setData(0, Qt.UserRole, str(PROJECT_DIR))
        self.project_tree.addTopLevelItem(root)

        if not PROJECT_DIR.exists():
            root.setText(1, "EMPTY")
            return

        try:
            file_dirs = sorted(
                [p for p in PROJECT_DIR.iterdir() if p.is_dir()],
                key=lambda p: p.name.lower()
            )
        except Exception:
            file_dirs = []

        for file_dir in file_dirs:
            file_item = QTreeWidgetItem([file_dir.name, "FILE ID", ""])
            file_item.setData(0, Qt.UserRole, str(file_dir))
            root.addChild(file_item)

            speaker_dirs = sorted(
                [p for p in file_dir.iterdir() if p.is_dir()],
                key=lambda p: p.name.lower()
            )

            for speaker_dir in speaker_dirs:
                speaker_item = QTreeWidgetItem([speaker_dir.name, "SPEAKER ID", ""])
                speaker_item.setData(0, Qt.UserRole, str(speaker_dir))
                file_item.addChild(speaker_item)

                files = sorted(
                    [p for p in speaker_dir.iterdir() if p.is_file()],
                    key=lambda p: p.name.lower()
                )
                for clip in files:
                    size_kb = clip.stat().st_size / 1024.0 if clip.exists() else 0
                    clip_item = QTreeWidgetItem([
                        clip.name,
                        clip.suffix.upper().lstrip(".") or "FILE",
                        f"{size_kb:.1f} KB"
                    ])
                    clip_item.setData(0, Qt.UserRole, str(clip))
                    speaker_item.addChild(clip_item)

        root.setExpanded(True)
        self.project_tree.resizeColumnToContents(0)
        self.project_tree.resizeColumnToContents(1)
        self.project_tree.resizeColumnToContents(2)

    def show_all_folders(self):
        self.refresh_project_tree()
        self.project_tree.expandAll()
        self.status_label.setText("Showing all File ID → Speaker ID → audio folders/files.")

    def play_selected_tree_file(self):
        item = self.project_tree.currentItem()
        if not item:
            QMessageBox.information(self, "Select a file", "Select an audio file in the folder tree first.")
            return
        value = item.data(0, Qt.UserRole)
        if not value:
            return
        path = Path(value)
        if not path.is_file() or path.suffix.lower() not in (".mp3", ".m4a", ".wav", ".flac", ".ogg", ".opus"):
            QMessageBox.information(self, "Not an audio file", "Select an audio file, not a folder.")
            return
        self._stop_segment_button()
        self.stop_timer.stop()
        self.player.stop()
        self.player.setSource(QUrl.fromLocalFile(str(path)))
        self.player.setPosition(0)
        self.change_speed(self.speed_combo.currentText())
        self.player.play()
        self.status_label.setText(f"Playing: {path.name}")

    def open_selected_tree_folder(self):
        item = self.project_tree.currentItem()
        if not item:
            return

        path = Path(item.data(0, Qt.UserRole) or PROJECT_DIR)
        if path.is_file():
            path = path.parent
        path.mkdir(parents=True, exist_ok=True)

        if sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        elif sys.platform == "win32":
            os.startfile(str(path))
        else:
            subprocess.Popen(["xdg-open", str(path)])

    def project_tree_double_click(self, item, column):
        path_value = item.data(0, Qt.UserRole)
        if not path_value:
            return

        path = Path(path_value)
        if path.is_file() and path.suffix.lower() in (".mp3", ".m4a", ".wav", ".flac", ".ogg", ".opus"):
            self.load_audio(path)
            self.status_label.setText(f"Loaded from project tree — {path.name}")
        elif path.is_dir():
            self.open_selected_tree_folder()

    # --------------------------------------------------------
    # Export
    # --------------------------------------------------------

    def export_csv(self):
        if not self.segments:
            QMessageBox.information(self, "No segments", "Save at least one segment.")
            return

        file_id = self.file_id_input.text().strip() or "project"
        out = EXPORT_DIR / f"{file_id}_segments.csv"

        with out.open("w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=[
                    "file_id", "type", "segment_number", "speaker", "speaker_id",
                    "start", "end", "source_duration", "duration", "file", "notes"
                ]
            )
            writer.writeheader()

            for s in self.segments:
                writer.writerow({
                    "file_id": file_id,
                    "type": s.get("type", "speech"),
                    "segment_number": s.get("segment_number", ""),
                    "speaker": s.get("speaker", ""),
                    "speaker_id": s.get("speaker_id", ""),
                    "start": f'{s["start"]:.3f}',
                    "end": f'{s["end"]:.3f}',
                    "source_duration": f'{s.get("source_duration", s["end"] - s["start"]):.3f}',
                    "duration": f'{s.get("duration", s["end"] - s["start"]):.3f}',
                    "file": s.get("file", ""),
                    "notes": s.get("notes", "")
                })

        QMessageBox.information(self, "Export complete", str(out))

    def open_project_folder(self):
        file_id = self.file_id_input.text().strip()
        folder = PROJECT_DIR / file_id if file_id else PROJECT_DIR
        folder.mkdir(parents=True, exist_ok=True)

        if sys.platform == "darwin":
            subprocess.Popen(["open", str(folder)])
        elif sys.platform == "win32":
            os.startfile(str(folder))
        else:
            subprocess.Popen(["xdg-open", str(folder)])


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("UAE Speaker Clipper")
    window = AudioAnnotator()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
