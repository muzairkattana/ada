# --- Core Imports ---
import asyncio
import base64
import io
import os
import sys
import traceback
import json
import websockets
import argparse
import threading
from html import escape
import subprocess
import webbrowser
import math
import random

# --- PySide6 GUI Imports ---
from PySide6.QtWidgets import (QApplication, QMainWindow, QTextEdit, QLabel,
                               QVBoxLayout, QWidget, QLineEdit, QHBoxLayout,
                               QSizePolicy, QPushButton, QStackedLayout)
from PySide6.QtCore import QObject, Signal, Slot, Qt, QTimer
from PySide6.QtGui import (QImage, QPixmap, QFont, QFontDatabase, QTextCursor, 
                           QPainter, QPen, QVector3D, QMatrix4x4, QColor, QBrush,
                           QLinearGradient, QRadialGradient)
from PySide6.QtOpenGLWidgets import QOpenGLWidget


# --- Media and AI Imports ---
import cv2
import pyaudio
import PIL.Image
from google import genai
from dotenv import load_dotenv
from PIL import ImageGrab
import numpy as np


# --- Load Environment Variables ---
load_dotenv()
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not GEMINI_API_KEY:
    sys.exit("Error: GEMINI_API_KEY not found. Please set it in your .env file.")
if not ELEVENLABS_API_KEY:
    sys.exit("Error: ELEVENLABS_API_KEY not found. Please check your .env file.")

# --- Configuration ---
FORMAT = pyaudio.paInt16
CHANNELS = 1
SEND_SAMPLE_RATE = 16000
RECEIVE_SAMPLE_RATE = 24000
CHUNK_SIZE = 1024
MODEL = "gemini-live-2.5-flash-preview"
VOICE_ID = '56bWURjYFHyYyVf490Dp'
DEFAULT_MODE = "none"  # Options: "camera", "screen", "none"
MAX_OUTPUT_TOKENS = 100

# --- Initialize Clients ---
pya = pyaudio.PyAudio()

# ==============================================================================
# AI Animation Widget
# ==============================================================================
class AIAnimationWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.angle_y = 0
        self.angle_x = 0
        self.sphere_points = self.create_sphere_points()
        self.is_speaking = False
        self.pulse_angle = 0

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_animation)
        self.timer.start(30) # Update about 33 times per second

    def start_speaking_animation(self):
        """Activates the speaking animation state."""
        self.is_speaking = True

    def stop_speaking_animation(self):
        """Deactivates the speaking animation state."""
        self.is_speaking = False
        self.pulse_angle = 0 # Reset for a clean start next time
        self.update() # Schedule a final repaint in the non-speaking state

    def create_sphere_points(self, radius=60, num_points_lat=20, num_points_lon=40):
        """Creates a list of QVector3D points on the surface of a sphere."""
        points = []
        for i in range(num_points_lat + 1):
            lat = math.pi * (-0.5 + i / num_points_lat)
            y = radius * math.sin(lat)
            xy_radius = radius * math.cos(lat)

            for j in range(num_points_lon):
                lon = 2 * math.pi * (j / num_points_lon)
                x = xy_radius * math.cos(lon)
                z = xy_radius * math.sin(lon)
                points.append(QVector3D(x, y, z))
        return points

    def update_animation(self):
        self.angle_y += 0.8
        self.angle_x += 0.2
        if self.is_speaking:
            self.pulse_angle += 0.2
            if self.pulse_angle > math.pi * 2:
                self.pulse_angle -= math.pi * 2

        if self.angle_y >= 360: self.angle_y = 0
        if self.angle_x >= 360: self.angle_x = 0
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), Qt.transparent)

        w, h = self.width(), self.height()
        painter.translate(w / 2, h / 2)

        pulse_factor = 1.0
        if self.is_speaking:
            pulse_amplitude = 0.08 # Pulse by 8%
            pulse = (1 + math.sin(self.pulse_angle)) / 2
            pulse_factor = 1.0 + (pulse * pulse_amplitude)

        rotation_y = QMatrix4x4(); rotation_y.rotate(self.angle_y, 0, 1, 0)
        rotation_x = QMatrix4x4(); rotation_x.rotate(self.angle_x, 1, 0, 0)
        rotation = rotation_y * rotation_x

        projected_points = []
        for point in self.sphere_points:
            rotated_point = rotation.map(point)
            
            z_factor = 200 / (200 + rotated_point.z())
            x = (rotated_point.x() * z_factor) * pulse_factor
            y = (rotated_point.y() * z_factor) * pulse_factor
            
            size = (rotated_point.z() + 60) / 120
            alpha = int(50 + 205 * size)
            point_size = 1 + size * 3
            projected_points.append((x, y, point_size, alpha))

        projected_points.sort(key=lambda p: p[2])
        
        for x, y, point_size, alpha in projected_points:
            color = QColor(170, 255, 255, alpha) if self.is_speaking else QColor(0, 255, 255, alpha)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(color))
            painter.drawEllipse(int(x), int(y), int(point_size), int(point_size))


# ==============================================================================
# MINI-GAMES FOR VIDEO SECTIONS
# ==============================================================================
class NeonGridGame(QWidget):
    """Simple interactive neon grid game for the screen-share area."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows = 6
        self.cols = 10
        self.grid = [[False for _ in range(self.cols)] for _ in range(self.rows)]
        self.hover_row = -1
        self.hover_col = -1
        self.setMouseTracking(True)
        self.setMinimumHeight(140)

    def mouseMoveEvent(self, event):
        if self.width() <= 0 or self.height() <= 0:
            return
        cell_w = self.width() / self.cols
        cell_h = self.height() / self.rows
        c = int(event.position().x() // cell_w)
        r = int(event.position().y() // cell_h)
        if 0 <= r < self.rows and 0 <= c < self.cols:
            if r != self.hover_row or c != self.hover_col:
                self.hover_row, self.hover_col = r, c
                self.update()
        else:
            if self.hover_row != -1 or self.hover_col != -1:
                self.hover_row, self.hover_col = -1, -1
                self.update()

    def leaveEvent(self, event):
        self.hover_row, self.hover_col = -1, -1
        self.update()

    def mousePressEvent(self, event):
        if self.width() <= 0 or self.height() <= 0:
            return
        cell_w = self.width() / self.cols
        cell_h = self.height() / self.rows
        c = int(event.position().x() // cell_w)
        r = int(event.position().y() // cell_h)
        if 0 <= r < self.rows and 0 <= c < self.cols:
            self.grid[r][c] = not self.grid[r][c]
            self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        # Subtle vertical gradient background
        gradient_bg = QLinearGradient(0, 0, 0, self.height())
        gradient_bg.setColorAt(0.0, QColor(5, 8, 24))
        gradient_bg.setColorAt(1.0, QColor(10, 20, 45))
        painter.fillRect(self.rect(), QBrush(gradient_bg))

        if self.width() <= 0 or self.height() <= 0:
            return

        cell_w = self.width() / self.cols
        cell_h = self.height() / self.rows

        # Draw grid
        pen_grid = QPen(QColor(0, 161, 193, 80))
        pen_grid.setWidth(1)
        painter.setPen(pen_grid)
        for r in range(self.rows + 1):
            y = int(r * cell_h)
            painter.drawLine(0, y, self.width(), y)
        for c in range(self.cols + 1):
            x = int(c * cell_w)
            painter.drawLine(x, 0, x, self.height())

        # Draw active cells and hover highlight
        for r in range(self.rows):
            for c in range(self.cols):
                rect_x = int(c * cell_w) + 2
                rect_y = int(r * cell_h) + 2
                rect_w = int(cell_w) - 4
                rect_h = int(cell_h) - 4

                is_active = self.grid[r][c]
                is_hover = (r == self.hover_row and c == self.hover_col)

                if is_active:
                    painter.fillRect(rect_x, rect_y, rect_w, rect_h, QColor(0, 255, 255, 200))
                elif is_hover:
                    painter.fillRect(rect_x, rect_y, rect_w, rect_h, QColor(0, 255, 180, 90))

        # Draw a small status text at the bottom-left
        active_count = sum(1 for row in self.grid for v in row if v)
        painter.setPen(QColor(160, 190, 255))
        painter.setFont(QFont("Segoe UI", 9))
        painter.drawText(8, self.height() - 10, f"CELLS LIT: {active_count}")


class NeonBounceGame(QWidget):
    """Minimal neon bouncing-ball game for the camera area."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.ball_pos = QVector3D(0, 0, 0)  # reuse QVector3D for convenience
        self.ball_vel = QVector3D(3, 4, 0)
        self.ball_radius = 14
        self.trail = []
        self.initialized = False

        self.setMinimumHeight(140)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(16)

    def mousePressEvent(self, event):
        # Nudge the ball in a random direction on click
        angle = random.uniform(0, 2 * math.pi)
        speed = random.uniform(3, 6)
        self.ball_vel = QVector3D(math.cos(angle) * speed, math.sin(angle) * speed, 0)

    def _tick(self):
        if self.width() <= 0 or self.height() <= 0:
            return

        # Lazy-init ball in the center once we know the size
        if not self.initialized:
            cx = self.width() / 2
            cy = self.height() / 2
            self.ball_pos.setX(cx)
            self.ball_pos.setY(cy)
            angle = random.uniform(0, 2 * math.pi)
            speed = random.uniform(3, 6)
            self.ball_vel = QVector3D(math.cos(angle) * speed, math.sin(angle) * speed, 0)
            self.initialized = True

        x = self.ball_pos.x() + self.ball_vel.x()
        y = self.ball_pos.y() + self.ball_vel.y()

        # Bounce against edges
        if x - self.ball_radius < 0 or x + self.ball_radius > self.width():
            self.ball_vel.setX(-self.ball_vel.x())
        if y - self.ball_radius < 0 or y + self.ball_radius > self.height():
            self.ball_vel.setY(-self.ball_vel.y())

        x = max(self.ball_radius, min(self.width() - self.ball_radius, x))
        y = max(self.ball_radius, min(self.height() - self.ball_radius, y))

        self.ball_pos.setX(x)
        self.ball_pos.setY(y)

        # Maintain a short trail
        self.trail.append((x, y))
        if len(self.trail) > 25:
            self.trail.pop(0)

        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        # Subtle radial gradient background
        center = self.rect().center()
        gradient_bg = QRadialGradient(center, max(self.width(), self.height()))
        gradient_bg.setColorAt(0.0, QColor(3, 10, 30))
        gradient_bg.setColorAt(1.0, QColor(5, 8, 24))
        painter.fillRect(self.rect(), QBrush(gradient_bg))

        # Draw trail
        for i, (tx, ty) in enumerate(self.trail):
            alpha = int(40 + (i / max(1, len(self.trail))) * 160)
            radius = int(self.ball_radius * (0.5 + 0.5 * (i / max(1, len(self.trail)))))
            painter.setBrush(QBrush(QColor(0, 255, 180, alpha)))
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(int(tx - radius), int(ty - radius), radius * 2, radius * 2)

        # Draw main ball
        painter.setBrush(QBrush(QColor(0, 255, 255)))
        painter.setPen(QPen(QColor(0, 255, 255)))
        painter.drawEllipse(int(self.ball_pos.x() - self.ball_radius),
                            int(self.ball_pos.y() - self.ball_radius),
                            self.ball_radius * 2,
                            self.ball_radius * 2)

        # Draw subtle border
        painter.setPen(QPen(QColor(0, 161, 193, 150), 1))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(self.rect().adjusted(1, 1, -1, -1))


# ==============================================================================
# AI BACKEND LOGIC
# ==============================================================================
class AI_Core(QObject):
    """
    Handles all backend operations. Inherits from QObject to emit signals
    for thread-safe communication with the GUI.
    """
    text_received = Signal(str)
    end_of_turn = Signal()
    camera_frame_received = Signal(QImage)
    screen_frame_received = Signal(QImage)
    search_results_received = Signal(list)
    code_being_executed = Signal(str, str)
    file_list_received = Signal(str, list)
    video_mode_changed = Signal(str)
    speaking_started = Signal()
    speaking_stopped = Signal()
    microphone_started = Signal()
    microphone_stopped = Signal()

    def __init__(self, video_mode=DEFAULT_MODE):
        super().__init__()
        self.video_mode = video_mode
        self.is_running = True
        self.client = genai.Client(api_key=GEMINI_API_KEY)
        self.mic_enabled = True


        create_folder = {
            "name": "create_folder",
            "description": "Creates a new folder at the specified path relative to the script's root directory.",
            "parameters": {
                "type": "OBJECT",
                "properties": { "folder_path": { "type": "STRING", "description": "The path for the new folder (e.g., 'new_project/assets')."}},
                "required": ["folder_path"]
            }
        }

        create_file = {
            "name": "create_file",
            "description": "Creates a new file with specified content at a given path.",
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "file_path": { "type": "STRING", "description": "The path for the new file (e.g., 'new_project/notes.txt')."},
                    "content": { "type": "STRING", "description": "The content to write into the new file."}
                },
                "required": ["file_path", "content"]
            }
        }

        edit_file = {
            "name": "edit_file",
            "description": "Appends content to an existing file at a specified path.",
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "file_path": { "type": "STRING", "description": "The path of the file to edit (e.g., 'project/notes.txt')."},
                    "content": { "type": "STRING", "description": "The content to append to the file."}
                },
                "required": ["file_path", "content"]
            }
        }

        list_files = {
            "name": "list_files",
            "description": "Lists all files and directories within a specified folder. Defaults to the current directory if no path is provided.",
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "directory_path": { "type": "STRING", "description": "The path of the directory to inspect. Defaults to '.' (current directory) if omitted."}
                }
            }
        }

        read_file = {
            "name": "read_file",
            "description": "Reads the entire content of a specified file.",
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "file_path": { "type": "STRING", "description": "The path of the file to read (e.g., 'project/notes.txt')."}
                },
                "required": ["file_path"]
            }
        }

        open_application = {
            "name": "open_application",
            "description": "Opens or launches a desktop application on the user's computer.",
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "application_name": { "type": "STRING", "description": "The name of the application to open (e.g., 'Notepad', 'Calculator', 'Chrome')."}
                },
                "required": ["application_name"]
            }
        }

        open_website = {
            "name": "open_website",
            "description": "Opens a given URL in the default web browser.",
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "url": { "type": "STRING", "description": "The full URL of the website to open (e.g., 'https://www.google.com')."}
                },
                "required": ["url"]
            }
        }
        
        tools = [{'google_search': {}}, {'code_execution': {}}, {"function_declarations": [create_folder, create_file, edit_file, list_files, read_file, open_application, open_website]}]
        
        self.config = {
            "response_modalities": ["TEXT"],
            "system_instruction": """
            Your name is Ada and you are my AI assistant.

            You are a multimodal assistant: you can understand and generate text, and you receive a live video stream
            that can be switched between the user's webcam and their screen, or turned off.

            IMPORTANT CAPABILITIES
            - When the webcam or screen mode is active, you are able to SEE what the camera/screen shows from the frames
              that are streamed to you. Do not claim that you are "text-only" or "unable to see images".
            - If the user asks things like "do you see me?", "what is on my screen?", or otherwise explicitly requests
              visual analysis, you MUST treat the current video frames as visual input and respond based on what you see.
            - If the video mode is off, you should say that you currently do not see anything because the video feed is disabled.

            PRIVACY & BEHAVIOR
            - By default, do not comment on webcam or screen content unless the user explicitly asks you to analyze,
              describe, or use what you see.
            - Be clear and honest about what you can and cannot observe at the moment (e.g., whether the camera/screen
              feed is enabled).

            TOOL USE GUIDELINES
            You have access to tools for searching, code execution, file management, and system actions.
            Follow these guidelines when choosing tools:
            1. For information or general questions, use `google_search` when web results are helpful.
            2. For math or running Python code, use `code_execution`.
            3. Use file system functions (`create_folder`, `create_file`, `edit_file`, `list_files`, `read_file`) for any
               task that involves creating, editing, reading, or listing files and folders on the user's computer.
            4. If the user asks to open or launch a desktop application, you must use the `open_application` function.
            5. If the user asks to open a website or a URL, you must use the `open_website` function.

            GENERAL BEHAVIOR
            - Whenever possible, take actions on the user's behalf using the available tools instead of only explaining
              what to do.
            - If a task cannot be completed with the available tools, explain the limitation clearly and suggest a
              concrete manual workaround the user can perform.
            Prioritize the most appropriate tool or combination of tools for the user's specific request.""",
                                "tools": tools,
                                "max_output_tokens": MAX_OUTPUT_TOKENS
        }
        self.session = None
        self.audio_stream = None
        self.out_queue_gemini = asyncio.Queue(maxsize=20)
        self.response_queue_tts = asyncio.Queue()
        self.audio_in_queue_player = asyncio.Queue()
        self.text_input_queue = asyncio.Queue()
        self.latest_frame = None
        self.camera_frame = None
        self.screen_frame = None
        # Enable camera/screen based on the initial video_mode
        # If video_mode is set from CLI args, we should enable the corresponding capture
        self.camera_enabled = (video_mode == "camera")
        self.screen_enabled = (video_mode == "screen")
        self.tasks = []
        self.loop = asyncio.new_event_loop()

    def _create_folder(self, folder_path):
        try:
            if not folder_path or not isinstance(folder_path, str): return {"status": "error", "message": "Invalid folder path provided."}
            if os.path.exists(folder_path): return {"status": "skipped", "message": f"The folder '{folder_path}' already exists."}
            os.makedirs(folder_path)
            return {"status": "success", "message": f"Successfully created the folder at '{folder_path}'."}
        except Exception as e: return {"status": "error", "message": f"An error occurred: {str(e)}"}

    def _create_file(self, file_path, content):
        try:
            if not file_path or not isinstance(file_path, str): return {"status": "error", "message": "Invalid file path provided."}
            if os.path.exists(file_path): return {"status": "skipped", "message": f"The file '{file_path}' already exists."}
            with open(file_path, 'w') as f: f.write(content)
            return {"status": "success", "message": f"Successfully created the file at '{file_path}'."}
        except Exception as e: return {"status": "error", "message": f"An error occurred while creating the file: {str(e)}"}

    def _edit_file(self, file_path, content):
        try:
            if not file_path or not isinstance(file_path, str): return {"status": "error", "message": "Invalid file path provided."}
            if not os.path.exists(file_path): return {"status": "error", "message": f"The file '{file_path}' does not exist. Please create it first."}
            with open(file_path, 'a') as f: f.write(f"\n{content}")
            return {"status": "success", "message": f"Successfully appended content to the file at '{file_path}'."}
        except Exception as e: return {"status": "error", "message": f"An error occurred while editing the file: {str(e)}"}

    def _list_files(self, directory_path):
        try:
            path_to_list = directory_path if directory_path else '.'
            if not isinstance(path_to_list, str): return {"status": "error", "message": "Invalid directory path provided."}
            if not os.path.isdir(path_to_list): return {"status": "error", "message": f"The path '{path_to_list}' is not a valid directory."}
            files = os.listdir(path_to_list)
            return {"status": "success", "message": f"Found {len(files)} items in '{path_to_list}'.", "files": files, "directory_path": path_to_list}
        except Exception as e: return {"status": "error", "message": f"An error occurred: {str(e)}"}

    def _read_file(self, file_path):
        try:
            if not file_path or not isinstance(file_path, str): return {"status": "error", "message": "Invalid file path provided."}
            if not os.path.exists(file_path): return {"status": "error", "message": f"The file '{file_path}' does not exist."}
            if not os.path.isfile(file_path): return {"status": "error", "message": f"The path '{file_path}' is not a file."}
            with open(file_path, 'r') as f: content = f.read()
            return {"status": "success", "message": f"Successfully read the file '{file_path}'.", "content": content}
        except Exception as e: return {"status": "error", "message": f"An error occurred while reading the file: {str(e)}"}

    @Slot(bool)
    def set_mic_enabled(self, enabled):
        """Enable or disable sending microphone audio to the assistant."""
        self.mic_enabled = enabled
        if enabled:
            self.microphone_started.emit()
        else:
            self.microphone_stopped.emit()

    @Slot(bool)
    def set_camera_enabled(self, enabled):
        """Enable or disable webcam capture and preview."""
        self.camera_enabled = enabled
        if not enabled:
            self.camera_frame = None
            # Clear preview
            self.camera_frame_received.emit(QImage())
            if self.video_mode == "camera":
                self.set_video_mode("none")

    @Slot(bool)
    def set_screen_enabled(self, enabled):
        """Enable or disable screen capture and preview."""
        self.screen_enabled = enabled
        if not enabled:
            self.screen_frame = None
            # Clear preview
            self.screen_frame_received.emit(QImage())
            if self.video_mode == "screen":
                self.set_video_mode("none")

    def _open_application(self, application_name):
        print(f">>> [DEBUG] Attempting to open application: '{application_name}'")
        try:
            if not application_name or not isinstance(application_name, str):
                return {"status": "error", "message": "Invalid application name provided."}
            command, shell_mode = [], False
            if sys.platform == "win32":
                app_map = {"calculator": "calc:", "notepad": "notepad", "chrome": "chrome", "google chrome": "chrome", "firefox": "firefox", "explorer": "explorer", "file explorer": "explorer"}
                app_command = app_map.get(application_name.lower(), application_name)
                command, shell_mode = f"start {app_command}", True
            elif sys.platform == "darwin":
                app_map = {"calculator": "Calculator", "chrome": "Google Chrome", "firefox": "Firefox", "finder": "Finder", "textedit": "TextEdit"}
                app_name = app_map.get(application_name.lower(), application_name)
                command = ["open", "-a", app_name]
            else:
                command = [application_name.lower()]
            subprocess.Popen(command, shell=shell_mode)
            return {"status": "success", "message": f"Successfully launched '{application_name}'."}
        except FileNotFoundError: return {"status": "error", "message": f"Application '{application_name}' not found."}
        except Exception as e: return {"status": "error", "message": f"An error occurred: {str(e)}"}

    def _open_website(self, url):
        print(f">>> [DEBUG] Attempting to open URL: '{url}'")
        try:
            if not url or not isinstance(url, str): return {"status": "error", "message": "Invalid URL provided."}
            if not url.startswith(('http://', 'https://')): url = 'https://' + url
            webbrowser.open(url)
            return {"status": "success", "message": f"Successfully opened '{url}'."}
        except Exception as e: return {"status": "error", "message": f"An error occurred: {str(e)}"}

    @Slot(str)
    def set_video_mode(self, mode):
        """Sets which feed (camera/screen) is sent to the AI, not what is previewed in the UI."""
        if mode in ["camera", "screen", "none"]:
            self.video_mode = mode
            print(f">>> [INFO] Switched video mode to: {self.video_mode}")
            if mode == "none":
                self.latest_frame = None
            elif mode == "camera" and self.camera_frame is not None:
                self.latest_frame = self.camera_frame
            elif mode == "screen" and self.screen_frame is not None:
                self.latest_frame = self.screen_frame
            self.video_mode_changed.emit(mode)

    async def stream_camera_to_gui(self):
        """Continuously capture from the webcam and preview it, independent of AI video_mode."""
        video_capture = None
        try:
            video_capture = await asyncio.to_thread(cv2.VideoCapture, 0)
            if not video_capture.isOpened():
                print(">>> [ERROR] Could not open webcam (index 0)")
                return
            while self.is_running:
                if not self.camera_enabled:
                    await asyncio.sleep(0.1)
                    continue
                try:
                    ret, frame = await asyncio.to_thread(video_capture.read)
                    if not ret:
                        await asyncio.sleep(0.05)
                        continue
                    self.camera_frame = frame
                    if self.video_mode == "camera":
                        self.latest_frame = frame
                    h, w, ch = frame.shape
                    bytes_per_line = ch * w
                    qt_image = QImage(frame.data, w, h, bytes_per_line, QImage.Format_BGR888)
                    self.camera_frame_received.emit(qt_image.copy())
                    await asyncio.sleep(0.033)
                except Exception as e:
                    print(f">>> [ERROR] Webcam streaming error: {e}")
                    await asyncio.sleep(0.5)
        finally:
            if video_capture is not None:
                await asyncio.to_thread(video_capture.release)

    async def stream_screen_to_gui(self):
        """Continuously capture the screen and preview it, independent of AI video_mode."""
        while self.is_running:
            try:
                if not self.screen_enabled:
                    await asyncio.sleep(0.2)
                    continue
                screenshot = await asyncio.to_thread(ImageGrab.grab)
                frame = cv2.cvtColor(np.array(screenshot), cv2.COLOR_RGB2BGR)
                self.screen_frame = frame
                if self.video_mode == "screen":
                    self.latest_frame = frame
                h, w, ch = frame.shape
                bytes_per_line = ch * w
                qt_image = QImage(frame.data, w, h, bytes_per_line, QImage.Format_BGR888)
                self.screen_frame_received.emit(qt_image.copy())
                await asyncio.sleep(0.2)
            except Exception as e:
                print(f">>> [ERROR] Screen capture error: {e}")
                await asyncio.sleep(1.0)

    async def send_frames_to_gemini(self):
        while self.is_running:
            await asyncio.sleep(1.0)
            if self.video_mode != "none" and self.latest_frame is not None:
                frame_rgb = cv2.cvtColor(self.latest_frame, cv2.COLOR_BGR2RGB)
                pil_img = PIL.Image.fromarray(frame_rgb)
                pil_img.thumbnail([1024, 1024])
                image_io = io.BytesIO()
                pil_img.save(image_io, format="jpeg")
                gemini_data = {"mime_type": "image/jpeg", "data": base64.b64encode(image_io.getvalue()).decode()}
                await self.out_queue_gemini.put(gemini_data)

    async def receive_text(self):
        while self.is_running:
            try:
                turn_urls, turn_code_content, turn_code_result, file_list_data = set(), "", "", None
                turn = self.session.receive()
                async for chunk in turn:
                    if chunk.tool_call and chunk.tool_call.function_calls:
                        function_responses = []
                        for fc in chunk.tool_call.function_calls:
                            args, result = fc.args, {}
                            if fc.name == "create_folder": result = self._create_folder(folder_path=args.get("folder_path"))
                            elif fc.name == "create_file": result = self._create_file(file_path=args.get("file_path"), content=args.get("content"))
                            elif fc.name == "edit_file": result = self._edit_file(file_path=args.get("file_path"), content=args.get("content"))
                            elif fc.name == "list_files":
                                result = self._list_files(directory_path=args.get("directory_path"))
                                if result.get("status") == "success": file_list_data = (result.get("directory_path"), result.get("files"))
                            elif fc.name == "read_file": result = self._read_file(file_path=args.get("file_path"))
                            elif fc.name == "open_application": result = self._open_application(application_name=args.get("application_name"))
                            elif fc.name == "open_website": result = self._open_website(url=args.get("url"))
                            function_responses.append({"id": fc.id, "name": fc.name, "response": result})
                        await self.session.send_tool_response(function_responses=function_responses)
                        continue
                    if chunk.server_content:
                        if hasattr(chunk.server_content, 'grounding_metadata') and chunk.server_content.grounding_metadata:
                            for g_chunk in chunk.server_content.grounding_metadata.grounding_chunks:
                                if g_chunk.web and g_chunk.web.uri: turn_urls.add(g_chunk.web.uri)
                        if chunk.server_content.model_turn:
                            for part in chunk.server_content.model_turn.parts:
                                if part.executable_code: turn_code_content = part.executable_code.code
                                if part.code_execution_result: turn_code_result = part.code_execution_result.output
                    if chunk.text:
                        self.text_received.emit(chunk.text)
                        await self.response_queue_tts.put(chunk.text)
                if file_list_data: self.file_list_received.emit(file_list_data[0], file_list_data[1])
                elif turn_code_content: self.code_being_executed.emit(turn_code_content, turn_code_result)
                elif turn_urls: self.search_results_received.emit(list(turn_urls))
                else:
                    self.code_being_executed.emit("",""); self.search_results_received.emit([]); self.file_list_received.emit("",[])
                self.end_of_turn.emit()
                await self.response_queue_tts.put(None)
            except Exception:
                if not self.is_running: break
                traceback.print_exc()

    async def listen_audio(self):
        mic_info = pya.get_default_input_device_info()
        self.audio_stream = pya.open(format=FORMAT, channels=CHANNELS, rate=SEND_SAMPLE_RATE, input=True, input_device_index=mic_info["index"], frames_per_buffer=CHUNK_SIZE)
        # Initial state reflects current mic_enabled flag
        if self.mic_enabled:
            self.microphone_started.emit()
        else:
            self.microphone_stopped.emit()
        while self.is_running:
            data = await asyncio.to_thread(self.audio_stream.read, CHUNK_SIZE, exception_on_overflow=False)
            if not self.is_running:
                break
            if not self.mic_enabled:
                continue
            await self.out_queue_gemini.put({"data": data, "mime_type": "audio/pcm"})

    async def send_realtime(self):
        while self.is_running:
            msg = await self.out_queue_gemini.get()
            if not self.is_running: break
            await self.session.send(input=msg)
            self.out_queue_gemini.task_done()

    async def process_text_input_queue(self):
        while self.is_running:
            text = await self.text_input_queue.get()
            if text is None:
                self.text_input_queue.task_done(); break
            if self.session:
                for q in [self.response_queue_tts, self.audio_in_queue_player]:
                    while not q.empty(): q.get_nowait()
                await self.session.send_client_content(turns=[{"role": "user", "parts": [{"text": text or "."}]}])
            self.text_input_queue.task_done()

    async def tts(self):
        uri = f"wss://api.elevenlabs.io/v1/text-to-speech/{VOICE_ID}/stream-input?model_id=eleven_turbo_v2_5&output_format=pcm_24000"
        while self.is_running:
            text_chunk = await self.response_queue_tts.get()
            if text_chunk is None or not self.is_running:
                self.response_queue_tts.task_done()
                continue

            self.speaking_started.emit()
            try:
                async with websockets.connect(uri) as websocket:
                    # Configure voice for a clear, stable assistant-style delivery
                    await websocket.send(json.dumps({
                        "text": " ",
                        "voice_settings": {
                            "stability": 0.75,
                            "similarity_boost": 0.9,
                            "style": 0.4,
                            "use_speaker_boost": True
                        },
                        "xi_api_key": ELEVENLABS_API_KEY,
                    }))

                    async def listen():
                        while self.is_running:
                            try:
                                message = await websocket.recv()
                                data = json.loads(message)
                                if data.get("audio"):
                                    await self.audio_in_queue_player.put(base64.b64decode(data["audio"]))
                                elif data.get("isFinal"):
                                    break
                            except websockets.exceptions.ConnectionClosed:
                                break

                    listen_task = asyncio.create_task(listen())
                    await websocket.send(json.dumps({"text": text_chunk + " "}))
                    self.response_queue_tts.task_done()

                    while self.is_running:
                        text_chunk = await self.response_queue_tts.get()
                        if text_chunk is None:
                            await websocket.send(json.dumps({"text": ""}))
                            self.response_queue_tts.task_done()
                            break
                        await websocket.send(json.dumps({"text": text_chunk + " "}))
                        self.response_queue_tts.task_done()

                    await listen_task
            except Exception as e:
                print(f">>> [ERROR] TTS Error: {e}")
            finally:
                self.speaking_stopped.emit()

    async def play_audio(self):
        stream = await asyncio.to_thread(pya.open, format=pyaudio.paInt16, channels=CHANNELS, rate=RECEIVE_SAMPLE_RATE, output=True)
        while self.is_running:
            bytestream = await self.audio_in_queue_player.get()
            if bytestream and self.is_running: await asyncio.to_thread(stream.write, bytestream)
            self.audio_in_queue_player.task_done()

    async def main_task_runner(self, session):
        self.session = session
        self.tasks.extend([
            asyncio.create_task(self.stream_camera_to_gui()),
            asyncio.create_task(self.stream_screen_to_gui()),
            asyncio.create_task(self.send_frames_to_gemini()),
            asyncio.create_task(self.listen_audio()),
            asyncio.create_task(self.send_realtime()),
            asyncio.create_task(self.receive_text()),
            asyncio.create_task(self.tts()),
            asyncio.create_task(self.play_audio()),
            asyncio.create_task(self.process_text_input_queue()),
        ])
        await asyncio.gather(*self.tasks, return_exceptions=True)

    async def run(self):
        try:
            async with self.client.aio.live.connect(model=MODEL, config=self.config) as session:
                await self.main_task_runner(session)
        except asyncio.CancelledError: print(f"\n>>> [INFO] AI Core run loop gracefully cancelled.")
        except Exception as e: print(f"\n>>> [ERROR] AI Core run loop encountered an error: {type(e).__name__}: {e}")
        finally:
            if self.is_running: self.stop()

    def start_event_loop(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self.run())

    @Slot(str)
    def handle_user_text(self, text):
        if self.is_running and self.loop.is_running(): asyncio.run_coroutine_threadsafe(self.text_input_queue.put(text), self.loop)

    async def shutdown_async_tasks(self):
        if self.text_input_queue: await self.text_input_queue.put(None)
        for task in self.tasks: task.cancel()
        await asyncio.sleep(0.1)

    def stop(self):
        if self.is_running and self.loop.is_running():
            self.is_running = False
            future = asyncio.run_coroutine_threadsafe(self.shutdown_async_tasks(), self.loop)
            try: future.result(timeout=5)
            except Exception as e: print(f">>> [ERROR] Timeout or error during async shutdown: {e}")
        if self.audio_stream and self.audio_stream.is_active():
            self.audio_stream.stop_stream(); self.audio_stream.close()
            self.microphone_stopped.emit()

# ==============================================================================
# STYLED GUI APPLICATION
# ==============================================================================
class MainWindow(QMainWindow):
    user_text_submitted = Signal(str)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("A.D.A. - Advanced Digital Assistant")
        self.setGeometry(100, 100, 1600, 900)
        self.setMinimumSize(1280, 720)
        
        self.setStyleSheet("""
            QMainWindow { 
                background-color: #0a0a1a; 
                font-family: 'Segoe UI', 'Helvetica Neue', sans-serif;
            }
            QWidget#left_panel, QWidget#middle_panel, QWidget#right_panel { 
                background-color: #10182a; 
                border: 1px solid #00a1c1;
                border-radius: 0;
            }
            QLabel#tool_activity_title { 
                color: #00d1ff; 
                font-weight: bold; 
                font-size: 11pt; 
                padding: 5px;
                background-color: #1a2035;
                text-transform: uppercase;
                letter-spacing: 1px;
            }
            QTextEdit#text_display { 
                background-color: transparent; 
                color: #e0e0ff; 
                font-size: 12pt; 
                border: none; 
                padding: 10px; 
            }
            QLineEdit#input_box { 
                background-color: #0a0a1a; 
                color: #e0e0ff; 
                font-size: 11pt; 
                border: 1px solid #00a1c1; 
                border-radius: 0px; 
                padding: 10px; 
            }
            QLineEdit#input_box:focus { border: 1px solid #00ffff; }
            QLabel#video_label { 
                background-color: #000000; 
                border: 1px solid #00a1c1;
                border-radius: 0px; 
            }
            QLabel#mic_status_label {
                color: #8888aa;
                font-size: 9pt;
                padding: 0 6px;
            }
            QLabel#mic_status_label_active {
                color: #00ff99;
                font-size: 9pt;
                padding: 0 6px;
            }
            QLabel#tool_activity_display { 
                background-color: #0a0a1a; 
                color: #a0a0ff; 
                font-family: 'Consolas', 'Monaco', monospace;
                font-size: 10pt; 
                border: none;
                border-top: 1px solid #00a1c1;
                padding: 8px; 
            }
            QLabel#credits_label {
                color: #6f7fb0;
                font-size: 8pt;
                letter-spacing: 0.5px;
                padding: 6px 4px 10px 4px;
                background-color: #0a0a1a;
                border-top: 1px solid #00a1c1;
                text-align: center;
            }
            QScrollBar:vertical { 
                border: none; 
                background: #10182a; 
                width: 10px; margin: 0px; 
            }
            QScrollBar::handle:vertical { 
                background: #00a1c1; 
                min-height: 20px; 
                border-radius: 0px; 
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; }
            QPushButton { 
                background-color: transparent; 
                color: #00d1ff; 
                border: 1px solid #00d1ff; 
                padding: 10px; 
                border-radius: 0px; 
                font-size: 10pt; 
                font-weight: bold;
            }
            QPushButton:hover { background-color: #00d1ff; color: #0a0a1a; }
            QPushButton:pressed { background-color: #00ffff; color: #0a0a1a; border: 1px solid #00ffff;}
            QPushButton#webcam_button:checked,
            QPushButton#screenshare_button:checked {
                background-color: #00ffff; 
                color: #0a0a1a; 
                border: 1px solid #00ffff;
            }
            QPushButton#mic_toggle_button {
                min-width: 80px;
                padding: 6px 10px;
                font-size: 9pt;
            }
            QPushButton#mic_toggle_button:checked {
                background-color: #ff5555;
                border-color: #ff5555;
                color: #0a0a1a;
            }
        """)

        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)
        self.main_layout = QHBoxLayout(self.central_widget)
        self.main_layout.setContentsMargins(15, 15, 15, 15)
        self.main_layout.setSpacing(15)
        self.left_panel = QWidget(); self.left_panel.setObjectName("left_panel")
        self.left_layout = QVBoxLayout(self.left_panel)
        self.left_layout.setContentsMargins(0, 0, 0, 0)
        self.left_layout.setSpacing(0)
        self.tool_activity_title = QLabel("SYSTEM ACTIVITY"); self.tool_activity_title.setObjectName("tool_activity_title")
        self.left_layout.addWidget(self.tool_activity_title)
        self.tool_activity_display = QLabel(); self.tool_activity_display.setObjectName("tool_activity_display")
        self.tool_activity_display.setWordWrap(True); self.tool_activity_display.setAlignment(Qt.AlignTop)
        self.tool_activity_display.setOpenExternalLinks(True); self.tool_activity_display.setTextInteractionFlags(Qt.TextBrowserInteraction)
        self.left_layout.addWidget(self.tool_activity_display, 1)

        # Developer credits footer
        self.credits_label = QLabel("DEVELOPED & DESIGNED BY MUHAMMAD UZAIR & MURTAZA")
        self.credits_label.setObjectName("credits_label")
        self.credits_label.setAlignment(Qt.AlignCenter)
        self.left_layout.addWidget(self.credits_label)
        self.middle_panel = QWidget(); self.middle_panel.setObjectName("middle_panel")
        self.middle_layout = QVBoxLayout(self.middle_panel)
        self.middle_layout.setContentsMargins(0, 0, 0, 15); self.middle_layout.setSpacing(0)

        # --- ADDED: Animation Widget ---
        self.animation_widget = AIAnimationWidget()
        self.animation_widget.setMinimumHeight(150)
        self.animation_widget.setMaximumHeight(200)
        self.middle_layout.addWidget(self.animation_widget, 2) # Add with a stretch factor

        self.text_display = QTextEdit(); self.text_display.setObjectName("text_display"); self.text_display.setReadOnly(True)
        self.middle_layout.addWidget(self.text_display, 5) # Add with a stretch factor
        
        input_container = QWidget()
        input_layout = QHBoxLayout(input_container)
        input_layout.setContentsMargins(15, 10, 15, 0)
        self.input_box = QLineEdit(); self.input_box.setObjectName("input_box")
        self.input_box.setPlaceholderText("Type here or just start speaking...")
        self.input_box.returnPressed.connect(self.send_user_text)
        input_layout.addWidget(self.input_box, 1)

        # Microphone mute/unmute control
        self.mic_toggle_button = QPushButton("MUTE")
        self.mic_toggle_button.setObjectName("mic_toggle_button")
        self.mic_toggle_button.setCheckable(True)
        self.mic_toggle_button.setToolTip("Toggle microphone on/off for the assistant")
        self.mic_toggle_button.clicked.connect(self.on_mic_toggle_clicked)
        input_layout.addWidget(self.mic_toggle_button)

        # Microphone status indicator
        self.mic_status_label = QLabel("● MIC OFF")
        self.mic_status_label.setObjectName("mic_status_label")
        self.mic_status_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        input_layout.addWidget(self.mic_status_label)

        self.middle_layout.addWidget(input_container)

        self.right_panel = QWidget(); self.right_panel.setObjectName("right_panel")
        self.right_layout = QVBoxLayout(self.right_panel)
        self.right_layout.setContentsMargins(15, 15, 15, 15); self.right_layout.setSpacing(15)
        
        # --- VIDEO AREA: top = screen share, bottom = camera ---
        self.screen_container = QWidget()
        self.screen_container.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.screen_stack = QStackedLayout(self.screen_container)
        self.screen_stack.setContentsMargins(0, 0, 0, 8)

        self.screen_label = QLabel()
        self.screen_label.setObjectName("video_label")
        self.screen_label.setAlignment(Qt.AlignCenter)
        self.screen_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)

        self.screen_game = NeonGridGame()

        self.screen_stack.addWidget(self.screen_label)
        self.screen_stack.addWidget(self.screen_game)
        # Start with game visible when screen sharing is off
        self.screen_stack.setCurrentWidget(self.screen_game)

        self.camera_container = QWidget()
        self.camera_container.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.camera_stack = QStackedLayout(self.camera_container)
        self.camera_stack.setContentsMargins(0, 8, 0, 0)

        self.camera_label = QLabel()
        self.camera_label.setObjectName("video_label")
        self.camera_label.setAlignment(Qt.AlignCenter)
        self.camera_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)

        self.camera_game = NeonBounceGame()

        self.camera_stack.addWidget(self.camera_label)
        self.camera_stack.addWidget(self.camera_game)
        # Start with game visible when webcam is off
        self.camera_stack.setCurrentWidget(self.camera_game)

        # Add both containers with equal stretch so they share the space evenly
        self.right_layout.addWidget(self.screen_container, 1)
        self.right_layout.addWidget(self.camera_container, 1)
        
        self.button_container = QHBoxLayout(); self.button_container.setSpacing(10)
        self.webcam_button = QPushButton("WEBCAM")
        self.webcam_button.setObjectName("webcam_button")
        self.webcam_button.setCheckable(True)
        self.screenshare_button = QPushButton("SCREEN")
        self.screenshare_button.setObjectName("screenshare_button")
        self.screenshare_button.setCheckable(True)
        self.off_button = QPushButton("OFFLINE")
        self.button_container.addWidget(self.webcam_button)
        self.button_container.addWidget(self.screenshare_button)
        self.button_container.addWidget(self.off_button)
        self.right_layout.addLayout(self.button_container)
        
        self.main_layout.addWidget(self.left_panel, 2)
        self.main_layout.addWidget(self.middle_panel, 5)
        self.main_layout.addWidget(self.right_panel, 3)
        self.is_first_ada_chunk = True
        self.current_video_mode = DEFAULT_MODE
        self.setup_backend_thread()

    def setup_backend_thread(self):
        parser = argparse.ArgumentParser()
        parser.add_argument("--mode", type=str, default=DEFAULT_MODE, help="pixels to stream from", choices=["camera", "screen", "none"])
        args, unknown = parser.parse_known_args()
        
        self.ai_core = AI_Core(video_mode=args.mode)
        
        self.user_text_submitted.connect(self.ai_core.handle_user_text)
        self.webcam_button.toggled.connect(self.on_webcam_toggled)
        self.screenshare_button.toggled.connect(self.on_screenshare_toggled)
        self.off_button.clicked.connect(lambda: self.ai_core.set_video_mode("none"))
        
        self.ai_core.text_received.connect(self.update_text)
        self.ai_core.search_results_received.connect(self.update_search_results)
        self.ai_core.code_being_executed.connect(self.display_executed_code)
        self.ai_core.file_list_received.connect(self.update_file_list)
        self.ai_core.end_of_turn.connect(self.add_newline)
        self.ai_core.screen_frame_received.connect(self.update_screen_frame)
        self.ai_core.camera_frame_received.connect(self.update_camera_frame)
        self.ai_core.video_mode_changed.connect(self.update_video_mode_ui)
        self.ai_core.speaking_started.connect(self.animation_widget.start_speaking_animation)
        self.ai_core.speaking_stopped.connect(self.animation_widget.stop_speaking_animation)
        self.ai_core.microphone_started.connect(self.set_mic_active)
        self.ai_core.microphone_stopped.connect(self.set_mic_inactive)

        self.backend_thread = threading.Thread(target=self.ai_core.start_event_loop)
        self.backend_thread.daemon = True
        self.backend_thread.start()
        
        self.update_video_mode_ui(self.ai_core.video_mode)
        
        # Sync GUI buttons with the initial video mode from CLI args
        # Block signals temporarily to avoid triggering toggle handlers during initialization
        self.webcam_button.blockSignals(True)
        self.screenshare_button.blockSignals(True)
        
        if args.mode == "camera":
            self.webcam_button.setChecked(True)
            self.camera_stack.setCurrentWidget(self.camera_label)
        elif args.mode == "screen":
            self.screenshare_button.setChecked(True)
            self.screen_stack.setCurrentWidget(self.screen_label)
        # else args.mode == "none" - buttons remain unchecked, games shown
        
        self.webcam_button.blockSignals(False)
        self.screenshare_button.blockSignals(False)

    def send_user_text(self):
        text = self.input_box.text().strip()
        if text:
            self.text_display.append(f"<p style='color:#00ffff; font-weight:bold;'>&gt; USER:</p><p style='color:#e0e0ff; padding-left: 10px;'>{escape(text)}</p>")
            self.user_text_submitted.emit(text)
            self.input_box.clear()

    @Slot(str)
    def update_video_mode_ui(self, mode):
        """Track which feed the AI is currently using (camera/screen/none)."""
        self.current_video_mode = mode

    @Slot(str)
    def update_text(self, text):
        if self.is_first_ada_chunk:
            self.is_first_ada_chunk = False
            self.text_display.append(f"<p style='color:#00d1ff; font-weight:bold;'>&gt; A.D.A.:</p>")
        cursor = self.text_display.textCursor()
        cursor.movePosition(QTextCursor.End)
        cursor.insertText(text)
        self.text_display.verticalScrollBar().setValue(self.text_display.verticalScrollBar().maximum())

    @Slot(list)
    def update_search_results(self, urls):
        base_title = "SYSTEM ACTIVITY"
        if not urls:
            if "SEARCH" in self.tool_activity_title.text():
                self.tool_activity_display.clear(); self.tool_activity_title.setText(base_title)
            return
        self.tool_activity_display.clear()
        self.tool_activity_title.setText(f"{base_title} // SEARCH")
        html_content = ""
        for i, url in enumerate(urls):
            display_text = url.split('//')[1].split('/')[0] if '//' in url else url
            html_content += f'<p style="margin:0; padding: 4px;">{i+1}: <a href="{url}" style="color: #00ffff; text-decoration: none;">{display_text}</a></p>'
        self.tool_activity_display.setText(html_content)

    @Slot(str, str)
    def display_executed_code(self, code, result):
        base_title = "SYSTEM ACTIVITY"
        if not code:
            if "CODE EXEC" in self.tool_activity_title.text():
                 self.tool_activity_display.clear(); self.tool_activity_title.setText(base_title)
            return
        self.tool_activity_display.clear()
        self.tool_activity_title.setText(f"{base_title} // CODE EXEC")
        html = f'<pre style="white-space: pre-wrap; word-wrap: break-word; color: #e0e0ff; font-size: 9pt; line-height: 1.4;">{escape(code)}</pre>'
        if result:
            html += f'<p style="color:#00d1ff; font-weight:bold; margin-top:10px; margin-bottom: 5px;">&gt; OUTPUT:</p><pre style="white-space: pre-wrap; word-wrap: break-word; color: #90EE90; font-size: 9pt;">{escape(result.strip())}</pre>'
        self.tool_activity_display.setText(html)

    @Slot(str, list)
    def update_file_list(self, directory_path, files):
        base_title = "SYSTEM ACTIVITY"
        if not directory_path:
            if "FILESYS" in self.tool_activity_title.text():
                self.tool_activity_display.clear(); self.tool_activity_title.setText(base_title)
            return
        self.tool_activity_display.clear()
        self.tool_activity_title.setText(f"{base_title} // FILESYS")
        html = f'<p style="color:#00d1ff; margin-bottom: 5px;">DIR &gt; <strong>{escape(directory_path)}</strong></p>'
        if not files:
            html += '<p style="margin-top:5px; color:#a0a0ff;"><em>(Directory is empty)</em></p>'
        else:
            folders = sorted([i for i in files if os.path.isdir(os.path.join(directory_path, i))])
            file_items = sorted([i for i in files if not os.path.isdir(os.path.join(directory_path, i))])
            html += '<ul style="list-style-type:none; padding-left: 5px; margin-top: 5px;">'
            for folder in folders: html += f'<li style="margin: 2px 0; color: #87CEEB;">[+] {escape(folder)}</li>'
            for file_item in file_items: html += f'<li style="margin: 2px 0; color: #e0e0ff;">&#9679; {escape(file_item)}</li>'
            html += '</ul>'
        self.tool_activity_display.setText(html)

    @Slot()
    def add_newline(self):
        if not self.is_first_ada_chunk: self.text_display.append("")
        self.is_first_ada_chunk = True

    def _refresh_mic_label_style(self):
        self.mic_status_label.style().unpolish(self.mic_status_label)
        self.mic_status_label.style().polish(self.mic_status_label)

    @Slot()
    def set_mic_active(self):
        self.mic_status_label.setText("● LISTENING")
        self.mic_status_label.setObjectName("mic_status_label_active")
        self._refresh_mic_label_style()
        if self.mic_toggle_button.isChecked():
            # Ensure toggle visual matches active mic state
            self.mic_toggle_button.blockSignals(True)
            self.mic_toggle_button.setChecked(False)
            self.mic_toggle_button.setText("MUTE")
            self.mic_toggle_button.blockSignals(False)

    @Slot()
    def set_mic_inactive(self):
        self.mic_status_label.setText("● MIC OFF")
        self.mic_status_label.setObjectName("mic_status_label")
        self._refresh_mic_label_style()
        if not self.mic_toggle_button.isChecked():
            # Ensure toggle visual matches inactive mic state
            self.mic_toggle_button.blockSignals(True)
            self.mic_toggle_button.setChecked(True)
            self.mic_toggle_button.setText("UNMUTE")
            self.mic_toggle_button.blockSignals(False)

    @Slot(bool)
    def on_mic_toggle_clicked(self, checked):
        """Handle clicks on the mic toggle button and forward state to the backend."""
        if checked:
            # Button checked means we are muted
            self.mic_toggle_button.setText("UNMUTE")
            self.ai_core.set_mic_enabled(False)
        else:
            self.mic_toggle_button.setText("MUTE")
            self.ai_core.set_mic_enabled(True)

    @Slot(bool)
    def on_webcam_toggled(self, checked):
        """Toggle webcam capture and, if enabled, route it to the AI."""
        self.ai_core.set_camera_enabled(checked)
        if checked:
            self.camera_stack.setCurrentWidget(self.camera_label)
            # Prefer camera as the AI video source when turned on
            self.ai_core.set_video_mode("camera")
        else:
            # Show game when webcam is off
            self.camera_stack.setCurrentWidget(self.camera_game)
            # If camera was the active source, fall back to screen if available, otherwise none
            if self.current_video_mode == "camera":
                if self.screenshare_button.isChecked():
                    self.ai_core.set_video_mode("screen")
                else:
                    self.ai_core.set_video_mode("none")

    @Slot(bool)
    def on_screenshare_toggled(self, checked):
        """Toggle screen capture and, if enabled, route it to the AI."""
        self.ai_core.set_screen_enabled(checked)
        if checked:
            self.screen_stack.setCurrentWidget(self.screen_label)
            # Prefer screen as the AI video source when turned on
            self.ai_core.set_video_mode("screen")
        else:
            # Show game when screen share is off
            self.screen_stack.setCurrentWidget(self.screen_game)
            # If screen was the active source, fall back to camera if available, otherwise none
            if self.current_video_mode == "screen":
                if self.webcam_button.isChecked():
                    self.ai_core.set_video_mode("camera")
                else:
                    self.ai_core.set_video_mode("none")

    @Slot(QImage)
    def update_screen_frame(self, image):
        if not image.isNull():
            pixmap = QPixmap.fromImage(image)
            scaled_pixmap = pixmap.scaled(self.screen_container.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            self.screen_label.setPixmap(scaled_pixmap)
            if self.screenshare_button.isChecked():
                self.screen_stack.setCurrentWidget(self.screen_label)
        else:
            self.screen_label.clear()

    @Slot(QImage)
    def update_camera_frame(self, image):
        if not image.isNull():
            pixmap = QPixmap.fromImage(image)
            scaled_pixmap = pixmap.scaled(self.camera_container.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            self.camera_label.setPixmap(scaled_pixmap)
            if self.webcam_button.isChecked():
                self.camera_stack.setCurrentWidget(self.camera_label)
        else:
            self.camera_label.clear()
            
    def closeEvent(self, event):
        self.ai_core.stop()
        event.accept()

# ==============================================================================
# MAIN EXECUTION
# ==============================================================================
if __name__ == "__main__":
    try:
        app = QApplication(sys.argv)
        window = MainWindow()
        window.show()
        sys.exit(app.exec())
    except KeyboardInterrupt:
        print(">>> [INFO] Application interrupted by user.")
    finally:
        pya.terminate()
        print(">>> [INFO] Application terminated.")


