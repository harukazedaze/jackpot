# yuji_funk_gui.py
# Rewritten: only accepts WAV and OGG files; consolidated & bug-fixed
import os
import sys
import glob
import random
import time
import json
import threading
import traceback

from PyQt5 import QtCore, QtGui, QtWidgets
import pygame
import keyboard

# -----------------------------
# Core: Yuji Funk Sound / Logic
# -----------------------------
class YujiFunkCore(QtCore.QObject):
    token_count_changed = QtCore.pyqtSignal(int)
    token_active_changed = QtCore.pyqtSignal(bool)
    hyper_active_changed = QtCore.pyqtSignal(bool)
    last_sound_changed = QtCore.pyqtSignal(str)
    status_message = QtCore.pyqtSignal(str)
    score_changed = QtCore.pyqtSignal(int)
    high_score_changed = QtCore.pyqtSignal(int)
    multiplier_changed = QtCore.pyqtSignal(float)

    def __init__(self, parent=None):
        super().__init__(parent)

        # -------- audio init --------
        try:
            pygame.mixer.init()
            # ensure enough channels
            try:
                pygame.mixer.set_num_channels(16)
            except Exception as e:
                print("Warning: set_num_channels failed:", e)
            print("Pygame mixer initialized.")
        except Exception as e:
            print("Pygame mixer failed to init:", e)
            raise

        # Input delay & hyper grace
        self.input_delay_start = 0
        self.input_delay_duration = 0.2
        self.hyper_end_time = 0
        self.hyper_grace_active = False
        self.hyper_grace_period = 10.0

        # ---------- default paths (editable in Settings) ----------
        self.shared_folder = r"C:\Users\fresh\Desktop\Yuji funk"
        self.funk_folder = r"C:\Users\fresh\Desktop\Yuji Funk Ultimate"
        self.special_folder = r"D:\jackpot\JackpotAwakening"
        self.hyper_funk_folder = r"C:\Users\fresh\Desktop\Yuji Hyper Funk"
        self.hyperborb_folder = r"C:\Users\fresh\Desktop\Yuji Hyper Funk\HyperBorps"
        self.token_folder = r"D:\jackpot\JackpotTokens"
        self.stage_sounds_folder = r"D:\jackpot\JackpotTransition"  # achievement sounds folder (configurable)

        # -------- helper loader only for WAV/OGG ----------
        def load_wav_ogg(folder):
            if not folder or not os.path.exists(folder):
                return []
            wavs = glob.glob(os.path.join(folder, "*.wav"))
            oggs = glob.glob(os.path.join(folder, "*.ogg"))
            files = wavs + oggs
            return files

        # load initial lists
        self.shared_files = load_wav_ogg(self.shared_folder)
        self.funk_files = load_wav_ogg(self.funk_folder)
        self.special_files = load_wav_ogg(self.special_folder)
        self.hyper_funk_files = load_wav_ogg(self.hyper_funk_folder)

        # ensure hyperborb folder exists
        try:
            os.makedirs(self.hyperborb_folder, exist_ok=True)
        except Exception:
            pass
        self.hyperborb_files = []
        self.reload_hyperborb_files()

        # token system sound files (explicit)
        self.token_appeared_sound = os.path.join(self.token_folder, "TokenAppeared.wav")
        self.collected_one_sound = os.path.join(self.token_folder, "CollectedOneToken.wav")
        self.collected_two_sound = os.path.join(self.token_folder, "CollectedTwoTokens.wav")
        self.collected_three_sound = os.path.join(self.token_folder, "CollectedThreeTokens.wav")
        self.winner_sound = os.path.join(self.token_folder, "Winner.wav")
        self.loser_sound = os.path.join(self.token_folder, "Loser.wav")

        # ---------- borp stages ----------
        self.borp_stages = {
            'normal': {'folder': 'Normal', 'points': 1, 'threshold': 0, 'files': [], 'current': 0, 'quota': 0, 'active': True},
            'super': {'folder': 'Super', 'points': 10, 'threshold': 100, 'files': [], 'current': 0, 'quota': 0, 'active': False},
            'miracle': {'folder': 'Miracle', 'points': 100, 'threshold': 10000, 'files': [], 'current': 0, 'quota': 0, 'active': False}
        }
        # initialize current stage to avoid AttributeError in borp flow
        self.current_stage = 'normal'

        # scoring
        self.score = 0
        self.high_score = 0
        self.total_score = 0
        self.games_played = 0
        self.last_press_time = 0.0
        self.current_multiplier = 1.0
        self.combo_window = 3.0

        # persistence
        self.settings_file = os.path.join(os.path.dirname(os.path.realpath(__file__)), "yuji_funk_settings.json")
        self.sound_settings = {}
        self.load_settings()

        # after load, reload dynamic folders
        self.reload_borp_stage_files()
        self.reload_hyper_funk_files()
        self.reload_hyperborb_files()
        self.reload_token_sounds()

        # cooldowns
        self.cooldown = getattr(self, 'cooldown', 0)
        self.last_play_time = 0.0

        # token & flow flags
        self.token_count = 0
        self.token_chance = getattr(self, 'token_chance', 0.30)
        self.token_active = False
        self.priority_active = False
        self.hyper_active = False
        self.token_start_time = 0.0
        self.key_input_allowed = True
        self.delayed_input = False

        # hyper sequence control
        self.hyperborb_index = 0
        self.await_hyperfunk = False
        self.pending_hyperfunk_file = None
        self.hyper_state = 'idle'
        self.hyperfunk_start_time = 0.0

        # hyper input cooldown (to prevent accidental double-advances)
        self.hyper_input_cooldown = 0.1
        self.last_hyper_input_time = 0.0

        # mixer channels
        # ensure channels exist (we set number earlier)
        try:
            self.borp_channel = pygame.mixer.Channel(0)
            self.sound_channel = pygame.mixer.Channel(1)       # normal funk & misc
            self.special_channel = pygame.mixer.Channel(2)     # achievements / special
            self.winner_channel = pygame.mixer.Channel(3)      # winner voice
            self.hyper_funk_channel = pygame.mixer.Channel(4)  # hyper funk music
        except Exception as e:
            print("Channel creation warning:", e)
            # fallback: access channels lazily later

        # volumes: clamp between 0.0 and 1.0
        def clamp(v):
            try:
                return max(0.0, min(1.0, float(v)))
            except Exception:
                return 1.0
        self.borp_volume = clamp(1.0)
        self.funk_volume = clamp(1.0)
        self.special_volume = clamp(1.0)
        self.token_volume = clamp(1.0)
        self.hyper_volume = clamp(1.0)
        self.winner_volume = clamp(1.0)

        # funk scheduling
        self.borp_play_count = 0
        self.funk_every_n_borps = getattr(self, 'funk_every_n_borps', 2)

        # dedicated normal-funk channel to avoid collisions with misc sounds
        try:
            self.normal_funk_channel = pygame.mixer.Channel(5)
        except Exception:
            self.normal_funk_channel = getattr(self, 'sound_channel', None)

        # track stage achievement sound playback per run
        self.stage_sound_played = {'super': False, 'miracle': False}

        # runtime control
        self.running = False
        self.loop_thread = None

    # -------------------------
    # File loaders (wav/ogg only)
    # -------------------------
    def _filter_loadable(self, files):
        """Return only files pygame can load (wav or ogg)."""
        loadable = []
        for f in files:
            try:
                if not os.path.exists(f):
                    continue
                # only allow .wav or .ogg
                lower = f.lower()
                if not (lower.endswith('.wav') or lower.endswith('.ogg')):
                    continue
                # attempt to create a Sound object to verify loadable
                try:
                    _ = pygame.mixer.Sound(f)
                    loadable.append(f)
                except Exception as e:
                    # skip but log
                    self.status_message.emit(f"Unplayable (skipped): {os.path.basename(f)} -> {e}")
            except Exception:
                continue
        return loadable

    def reload_hyperborb_files(self):
        """Populate hyperborb_files from hyperborb_folder, numeric sort when possible."""
        folder = self.hyperborb_folder
        self.hyperborb_files = []
        if not folder or not os.path.exists(folder):
            self.status_message.emit(f"Hyperborb folder missing: {folder}")
            return
        try:
            wavs = glob.glob(os.path.join(folder, "**", "*.wav"), recursive=True)
            oggs = glob.glob(os.path.join(folder, "**", "*.ogg"), recursive=True)
            files = self._filter_loadable(wavs + oggs)

            # Exclude any stage achievement sounds from hyperborbs
            # 1) Anything present in stage_sounds_folder by basename
            # 2) Any filename that includes 'unlock'/'unlocked' to avoid stage unlock VO/SFX
            stage_basenames = set()
            try:
                if self.stage_sounds_folder and os.path.isdir(self.stage_sounds_folder):
                    st_wavs = glob.glob(os.path.join(self.stage_sounds_folder, "*.wav"))
                    st_oggs = glob.glob(os.path.join(self.stage_sounds_folder, "*.ogg"))
                    stage_basenames = {os.path.basename(p).lower() for p in (st_wavs + st_oggs)}
            except Exception:
                pass
            filtered = []
            excluded = 0
            for p in files:
                base = os.path.basename(p).lower()
                if base in stage_basenames or ('unlock' in base or 'unlocked' in base):
                    excluded += 1
                    continue
                filtered.append(p)
            files = filtered

            # numeric-ish sort where possible
            def _extract_num(path):
                name = os.path.basename(path)
                digits = ''.join(ch for ch in name if ch.isdigit())
                try:
                    return int(digits) if digits else float('inf')
                except Exception:
                    return float('inf')
            files.sort(key=lambda p: (_extract_num(p), os.path.basename(p).lower()))
            self.hyperborb_files = files
            extra = f" (excluded {excluded} stage-related)" if excluded else ""
            self.status_message.emit(f"Hyperborbs loaded: {len(self.hyperborb_files)} from {folder}{extra}")
        except Exception as e:
            self.hyperborb_files = []
            self.status_message.emit(f"Error loading Hyperborbs: {e}")

    def reload_hyper_funk_files(self):
        folder = self.hyper_funk_folder
        self.hyper_funk_files = []
        if not folder or not os.path.exists(folder):
            self.status_message.emit(f"Hyper Funk folder missing: {folder}")
            return
        try:
            wavs = glob.glob(os.path.join(folder, "**", "*.wav"), recursive=True)
            oggs = glob.glob(os.path.join(folder, "**", "*.ogg"), recursive=True)
            files = self._filter_loadable(wavs + oggs)
            self.hyper_funk_files = files
            self.status_message.emit(f"Hyper Funk loaded: {len(self.hyper_funk_files)} from {folder}")
        except Exception as e:
            self.hyper_funk_files = []
            self.status_message.emit(f"Error loading Hyper Funk: {e}")

    def reload_borp_stage_files(self):
        """Load borp stage files for normal/super/miracle. Accept absolute path or join with shared_folder."""
        for key, stage in self.borp_stages.items():
            folder = stage['folder'] if os.path.isabs(stage['folder']) else os.path.join(self.shared_folder, stage['folder'])
            files = []
            if os.path.exists(folder):
                wavs = glob.glob(os.path.join(folder, "*.wav"))
                oggs = glob.glob(os.path.join(folder, "*.ogg"))
                files = self._filter_loadable(wavs + oggs)
                files.sort(key=lambda p: os.path.basename(p).lower())
            stage['files'] = files
            stage['current'] = 0
            stage['quota'] = len(files)
            self.status_message.emit(f"Loaded {len(files)} files for {key} stage from {folder}")

    def reload_token_sounds(self):
        self.token_appeared_sound = os.path.join(self.token_folder, "TokenAppeared.wav")
        self.collected_one_sound = os.path.join(self.token_folder, "CollectedOneToken.wav")
        self.collected_two_sound = os.path.join(self.token_folder, "CollectedTwoTokens.wav")
        self.collected_three_sound = os.path.join(self.token_folder, "CollectedThreeTokens.wav")
        self.winner_sound = os.path.join(self.token_folder, "Winner.wav")
        self.loser_sound = os.path.join(self.token_folder, "Loser.wav")
        self.status_message.emit(f"Token sounds reloaded from {self.token_folder}")

    # -------------------------
    # Sound playback (safe)
    # -------------------------
    def _play_file(self, file_path, channel, priority=False):
        """Play a WAV/OGG file on a given channel. Priority stops currently-playing sound on that channel."""
        if not file_path:
            self.status_message.emit("Play called with None path")
            self.last_sound_changed.emit("MISSING")
            return
        if not os.path.exists(file_path):
            self.status_message.emit(f"Sound not found: {file_path}")
            self.last_sound_changed.emit("MISSING")
            return
        # ensure allowed extensions
        lower = file_path.lower()
        if not (lower.endswith('.wav') or lower.endswith('.ogg')):
            self.status_message.emit(f"Unsupported format (only WAV/OGG): {file_path}")
            self.last_sound_changed.emit("MISSING")
            return

        # ensure channel exists
        try:
            if priority and channel.get_busy():
                channel.stop()
            try:
                sound = pygame.mixer.Sound(file_path)
                # per-sound volume
                vol = 1.0
                try:
                    vol = float(self.sound_settings.get(file_path, {}).get('volume', 1.0))
                except Exception:
                    vol = 1.0
                vol = max(0.0, min(1.0, vol))
                sound.set_volume(vol)
                channel.play(sound)
                self.last_sound_changed.emit(os.path.basename(file_path))
                self.status_message.emit(f"Played: {os.path.basename(file_path)}")
            except Exception as e:
                # loading as Sound failed - log traceback
                tb = traceback.format_exc()
                self.status_message.emit(f"Error loading sound {file_path}: {e}")
                print("Traceback while loading sound:", tb)
                self.last_sound_changed.emit("MISSING")
        except Exception as e:
            self.status_message.emit(f"Channel play error: {e}")
            print("Channel play exception:", traceback.format_exc())
            self.last_sound_changed.emit("MISSING")

    def play_sound(self, file_path, channel, priority=False):
        # wrapper (kept for potential thread-safety later)
        self._play_file(file_path, channel, priority=priority)

    # -------------------------
    # Stage achievement lookup & progression
    # -------------------------
    def _find_stage_sound(self, stage_name):
        """Search stage_sounds_folder for a file that likely matches stage_name (case-insensitive)."""
        folder = self.stage_sounds_folder
        if not folder or not os.path.isdir(folder):
            self.status_message.emit(f"Stage sounds folder invalid: {folder}")
            return None
        candidates = []
        for ext in ('.wav', '.ogg'):
            candidates += glob.glob(os.path.join(folder, f"*{ext}"))
        if not candidates:
            return None
        key = stage_name.lower()
        fav = []
        for p in candidates:
            name = os.path.basename(p).lower()
            score = 10
            if key in name:
                score = 1
                if 'unlock' in name or 'unlocked' in name:
                    score = 0
            fav.append((score, p))
        fav.sort(key=lambda x: (x[0], x[1]))
        return fav[0][1] if fav else None

    # ---- NEW ----
    # Consolidated and corrected stage advancement logic.
    def check_and_advance_stage(self):
        """Checks for stage advancement by both quota and score threshold and handles the transition."""
        if self.hyper_active:
            return

        # Defensive check
        if not hasattr(self, 'current_stage'):
            self.current_stage = 'normal'

        current_stage_key = self.current_stage
        current_stage_data = self.borp_stages[current_stage_key]
        next_stage_key = None

        # Determine potential next stage based on current stage
        if current_stage_key == 'normal':
            super_stage_data = self.borp_stages['super']
            quota_met = (current_stage_data.get('quota', 0) > 0 and
                         current_stage_data.get('current', 0) >= current_stage_data.get('quota', 0))
            threshold_met = self.score >= super_stage_data['threshold']
            if quota_met or threshold_met:
                next_stage_key = 'super'
        elif current_stage_key == 'super':
            miracle_stage_data = self.borp_stages['miracle']
            quota_met = (current_stage_data.get('quota', 0) > 0 and
                         current_stage_data.get('current', 0) >= current_stage_data.get('quota', 0))
            threshold_met = self.score >= miracle_stage_data['threshold']
            if quota_met or threshold_met:
                next_stage_key = 'miracle'

        # If a stage advancement is determined, execute it
        if next_stage_key and self.current_stage != next_stage_key:
            self.borp_stages[current_stage_key]['active'] = False
            self.borp_stages[next_stage_key]['active'] = True
            self.current_stage = next_stage_key
            self.borp_stages[next_stage_key]['current'] = 0  # Reset counter for the new stage

            self.status_message.emit(f"Advanced to {next_stage_key.upper()} stage!")

            # Play achievement sound only once per run
            if not self.stage_sound_played.get(next_stage_key, False):
                sound_file = self._find_stage_sound(next_stage_key)
                if sound_file:
                    self.play_sound(sound_file, self.special_channel, priority=True)
                    self.status_message.emit(f"Played stage sound: {os.path.basename(sound_file)}")
                else:
                    self.status_message.emit(f"No stage sound found for {next_stage_key} in {self.stage_sounds_folder}")
                self.stage_sound_played[next_stage_key] = True  # Mark as played

    def reset_to_stage_one(self):
        """Reset progression to normal stage."""
        self.current_stage = 'normal'
        for stage in self.borp_stages.values():
            stage['current'] = 0
            stage['active'] = False
        self.borp_stages['normal']['active'] = True
        # allow stage achievement sounds to play again on next progression
        self.stage_sound_played = {'super': False, 'miracle': False}
        # optionally play a 'normal' stage sound when restarting
        try:
            sf = self._find_stage_sound('normal')
            if sf:
                self.play_sound(sf, self.special_channel, priority=True)
        except Exception:
            pass
        self.status_message.emit("Stage reset to NORMAL")

    # -------------------------
    # Scoring & borp selection
    # -------------------------
    def get_next_borp_sound(self):
        """Return path to next borp sound for current stage and update scoring."""
        if self.hyper_active:
            return None
        # defensive: ensure current_stage exists
        if not hasattr(self, 'current_stage'):
            self.current_stage = 'normal'

        # --- REVISED LOGIC ---
        # 1. Update score and time based on the press
        current_time = time.time()
        stage_for_points = self.borp_stages[self.current_stage]
        points = stage_for_points['points'] * self.current_multiplier
        self.score += points
        if self.score > self.high_score:
            self.high_score = self.score
            self.high_score_changed.emit(self.high_score)
        self.score_changed.emit(self.score)
        self.last_press_time = current_time
        self.status_message.emit(f"Score: {self.score} (x{self.current_multiplier})")
        
        # 2. Increment counter for the current stage *before* checking for advancement.
        self.borp_stages[self.current_stage]['current'] += 1

        # 3. Check for stage advancement. This might change self.current_stage.
        try:
            self.check_and_advance_stage()
        except Exception as e:
            self.status_message.emit(f"Error checking stage advancement: {e}")
            print(traceback.format_exc())

        # 4. Get the sound from the current stage (which may have just changed).
        stage = self.borp_stages[self.current_stage]
        if not stage['files']:
            self.status_message.emit(f"No files in current stage '{self.current_stage}'")
            return None

        # Use the counter for the current stage to select the file.
        current_index = (stage['current'] -1) % len(stage['files']) # -1 because we already incremented
        return stage['files'][current_index]

    # -------------------------
    # Hyper Funk helpers
    # -------------------------
    def get_next_hyper_funk_sound(self):
        """Pick next hyper funk using per-file chance weights from sound_settings."""
        files = getattr(self, 'hyper_funk_files', [])
        if not files:
            return None
        return self._select_weighted_random(files)

    def play_hyper_funk_sound(self):
        """Play next hyper funk track on hyper_funk_channel (non-blocking)."""
        try:
            if self.hyper_funk_channel.get_busy():
                self.status_message.emit("Hyper Funk channel busy; skipping hyper funk")
                return
        except Exception:
            pass
        file = self.get_next_hyper_funk_sound()
        if file:
            self.status_message.emit(f"Playing Hyper Funk: {os.path.basename(file)}")
            self.hyper_state = 'funk'
            self.play_sound(file, self.hyper_funk_channel, priority=True)
            self.hyperfunk_start_time = time.time()
        else:
            self.status_message.emit("No Hyper Funk files found")

    # -------------------------
    # Borp / Funk sequence
    # -------------------------
    def play_random_funk_sound(self):
        files = self.funk_files
        if not files:
            self.status_message.emit("No funk files available.")
            return
        f = self._select_weighted_random(files)
        try:
            # prefer a free channel; else fall back to dedicated normal_funk_channel
            ch = None
            try:
                ch = pygame.mixer.find_channel()
            except Exception:
                ch = None
            if ch is None:
                ch = getattr(self, 'normal_funk_channel', None)
            if ch is None:
                ch = getattr(self, 'sound_channel', None)
            if ch is not None:
                self.play_sound(f, ch, priority=True)
                # per-user spec: each normal funk increases multiplier by 0.2
                try:
                    self.current_multiplier += 0.2
                    self.multiplier_changed.emit(self.current_multiplier)
                except Exception:
                    pass
            else:
                self.status_message.emit("No available channel for funk")
        except Exception as e:
            self.status_message.emit(f"Error playing funk: {e}")
            print(traceback.format_exc())

    def handle_borp_sequence(self):
        """Main borp sequence (non-hyper)."""
        if self.hyper_active:
            return
        if self.priority_active or self.delayed_input:
            return
        borp_file = self.get_next_borp_sound()
        if borp_file:
            self.borp_play_count += 1
            # play borp
            try:
                self.play_sound(borp_file, self.borp_channel)
            except Exception as e:
                self.status_message.emit(f"Error playing borp: {e}")
                print(traceback.format_exc())
            self.score_changed.emit(self.score)
            # normal funk scheduling
            try:
                if (self.borp_play_count % self.funk_every_n_borps) == 0:
                    self.play_random_funk_sound()
            except Exception as e:
                self.status_message.emit(f"Funk scheduling error: {e}")
            # token handling guard (outside hyper)
            if not self.hyper_active:
                self.handle_token()

    # -------------------------
    # Token handling (disabled during hyper)
    # -------------------------
    def reset_token_system(self):
        self.token_active = False
        self.token_start_time = 0.0
        self.priority_active = False
        self.key_input_allowed = True
        self.token_active_changed.emit(self.token_active)
        self.status_message.emit("Token system reset (ready).")
        self.delayed_input = False

    def handle_token(self):
        if self.hyper_active:
            return
        if not self.token_active and not self.priority_active and random.random() < self.token_chance:
            self.status_message.emit("Token appeared!")
            if self.token_appeared_sound and os.path.exists(self.token_appeared_sound):
                self.play_sound(self.token_appeared_sound, self.sound_channel, priority=True)
            self.token_active = True
            self.token_start_time = time.time()
            self.token_active_changed.emit(True)
            self.key_input_allowed = True

    def handle_token_timeout(self):
        if self.hyper_active:
            return
        current_time = time.time()
        if self.token_active and (current_time - self.token_start_time > 2.0):
            self.status_message.emit("Token timed out. Resetting...")
            if self.loser_sound and os.path.exists(self.loser_sound):
                self.play_sound(self.loser_sound, self.sound_channel, priority=True)
            self.token_active = False
            self.token_start_time = 0.0
            self.priority_active = False
            self.key_input_allowed = True
            self.token_active_changed.emit(False)
            self.score = 0
            self.current_multiplier = 1.0
            self.borp_play_count = 0  # <-- FIX: Reset funk counter
            self.score_changed.emit(self.score)
            self.multiplier_changed.emit(self.current_multiplier)
            self.reset_to_stage_one()
            self.delayed_input = True
            self.input_delay_start = current_time

    def collect_token(self):
        if not self.token_active or self.hyper_active:
            self.status_message.emit("Cannot collect token now.")
            return
        self.token_active = False
        self.token_active_changed.emit(False)
        self.priority_active = True
        self.key_input_allowed = False
        self.token_count += 1
        self.token_count_changed.emit(self.token_count)
        self.status_message.emit(f"Token collected: {self.token_count}")
        if self.token_count == 1:
            sound_file = self.collected_one_sound
        elif self.token_count == 2:
            sound_file = self.collected_two_sound
        else:
            sound_file = self.collected_three_sound
        if sound_file and os.path.exists(sound_file):
            self.play_sound(sound_file, self.special_channel, priority=True)
        else:
            self.status_message.emit(f"Token sound missing: {sound_file}")
        if self.token_count >= 3:
            self.enter_hyper_mode()
        else:
            # reset flags but keep token_count
            self.priority_active = False
            self.key_input_allowed = True
            self.delayed_input = False
            self.token_active = False
            self.token_active_changed.emit(False)

    # -------------------------
    # Hyper flow (isolated)
    # -------------------------
    def enter_hyper_mode(self):
        self.status_message.emit("ENTERING HYPER MODE")
        self.hyper_active = True
        self.hyper_active_changed.emit(True)
        self.priority_active = True
        self.token_active = False
        self.token_active_changed.emit(False)
        self.delayed_input = False
        self.key_input_allowed = True
        self.hyperborb_index = 0
        self.await_hyperfunk = False
        self.pending_hyperfunk_file = None
        # play a special then winner then start hyper
        try:
            if self.special_files:
                special_file = random.choice(self.special_files)
                self.play_sound(special_file, self.special_channel, priority=True)
        except Exception:
            pass

        def _play_winner_and_start():
            if self.winner_sound and os.path.exists(self.winner_sound):
                self.play_sound(self.winner_sound, self.winner_channel, priority=True)
            threading.Timer(0.3, self.start_hyper_mode).start()

        threading.Timer(0.05, _play_winner_and_start).start()

    def start_hyper_mode(self):
        self.status_message.emit("HYPER MODE ACTIVATED")
        self.priority_active = False
        self.key_input_allowed = True
        self.delayed_input = False
        self.token_count = 0
        self.token_count_changed.emit(0)
        # ensure latest hyperborb list (filtered to exclude stage-related sounds)
        try:
            self.reload_hyperborb_files()
        except Exception:
            pass
        self.hyperborb_index = 0
        if self.hyperborb_files:
            self.hyper_state = 'borps'
            self.status_message.emit(f"Starting hyperborb sequence ({len(self.hyperborb_files)} files)")
            # play first hyperborb immediately
            self.handle_hyperborb_sequence()
        else:
            self.status_message.emit("No hyperborbs found. Playing Hyper Funk...")
            self.hyper_state = 'funk'
            self.play_hyper_funk_sound()

    def handle_hyperborb_sequence(self):
        """Play next hyperborb. After last, play hyper funk."""
        if not self.hyper_active:
            self.status_message.emit("Hyperborb called while not hyper.")
            return
        if not self.hyperborb_files:
            self.status_message.emit("No hyperborbs, going to hyper funk.")
            self.play_hyper_funk_sound()
            return
        if self.hyperborb_index < len(self.hyperborb_files):
            f = self.hyperborb_files[self.hyperborb_index]
            self.status_message.emit(f"Hyperborb {self.hyperborb_index + 1}/{len(self.hyperborb_files)}: {os.path.basename(f)}")
            # Play on a free channel to allow overlap (no priority to avoid cutting existing audio)
            try:
                ch = pygame.mixer.find_channel()
            except Exception:
                ch = None
            if ch is None:
                # fallback to a stable channel if needed
                ch = getattr(self, 'sound_channel', None)
            if ch is None:
                self.status_message.emit("No free channel for hyperborb")
                return
            try:
                self.play_sound(f, ch, priority=False)
            except Exception:
                pass
            self.hyperborb_index += 1
        else:
            # Start Hyper Funk via the dedicated method so state/channel are correct for end detection
            self.status_message.emit("Hyperborbs finished -> Hyper Funk (as next borb)")
            self.play_hyper_funk_sound()
            self.hyperborb_index = 0

    def on_key_event_name(self, key_name):
        key = key_name.lower() if isinstance(key_name, str) else str(key_name)
        current_time = time.time()
        self.last_press_time = current_time

        # exit key passthrough
        if key in ['numpad 9', 'num 9', 'numpad9', '9']:
            self.status_message.emit("Exit key pressed.")
            return

        # end hyper grace early on key
        if self.hyper_grace_active:
            self.hyper_grace_active = False
            self.status_message.emit("Grace period ended early due to input.")

        # Hyper mode: only borp keys advance hyper sequence (no cooldown, allow overlap)
        if self.hyper_active:
            if key not in ['r', '1', '2', '3', '4']:
                # ignore other keys in hyper
                return
            self.status_message.emit("Hyper active: advancing hyperborb now")
            self.handle_hyperborb_sequence()
            return

        # normal keys: R 1 2 3 4 map to borp sequences
        if key in ['r', '1', '2', '3', '4']:
            if self.token_active:
                self.status_message.emit("Collecting token...")
                self.collect_token()
            elif self.key_input_allowed:
                self.status_message.emit("Playing borp...")
                self.handle_borp_sequence()
            else:
                self.status_message.emit("Input not allowed right now.")
        # refresh input allowed state
        self.key_input_allowed = not (self.token_active or self.priority_active or self.delayed_input)

    # -------------------------
    # Weight selection
    # -------------------------
    def _select_weighted_random(self, files_list):
        weights = []
        all_zero = True
        for f in files_list:
            try:
                w = float(self.sound_settings.get(f, {}).get('chance', 1.0))
            except Exception:
                w = 1.0
            if w > 0:
                all_zero = False
            weights.append(max(0.0, w))
        try:
            if not all_zero and any(w > 0 for w in weights):
                return random.choices(files_list, weights=weights, k=1)[0]
        except Exception:
            pass
        return random.choice(files_list)

    # -------------------------
    # Settings persistence
    # -------------------------
    def save_settings(self):
        settings = {
            'high_score': self.high_score,
            'total_score': self.total_score,
            'games_played': self.games_played,
            'token_chance': self.token_chance,
            'cooldown': self.cooldown,
            'sound_settings': self.sound_settings,
            'paths': {
                'shared': self.shared_folder,
                'funk': self.funk_folder,
                'special': self.special_folder,
                'hyper': self.hyper_funk_folder,
                'hyperborb': self.hyperborb_folder,
                'token': self.token_folder,
                'borp_stages': {
                    'normal': self.borp_stages['normal']['folder'],
                    'super': self.borp_stages['super']['folder'],
                    'miracle': self.borp_stages['miracle']['folder']
                },
                'stage_sounds': self.stage_sounds_folder
            },
            'funk_every_n_borps': self.funk_every_n_borps
        }
        try:
            with open(self.settings_file, 'w') as f:
                json.dump(settings, f, indent=2)
            self.status_message.emit("Settings saved.")
        except Exception as e:
            self.status_message.emit(f"Error saving settings: {e}")
            print(traceback.format_exc())

    def load_settings(self):
        try:
            if os.path.exists(self.settings_file):
                with open(self.settings_file, 'r') as f:
                    settings = json.load(f)
                self.high_score = settings.get('high_score', 0)
                self.total_score = settings.get('total_score', 0)
                self.games_played = settings.get('games_played', 0)
                self.token_chance = settings.get('token_chance', 0.30)
                self.cooldown = settings.get('cooldown', 0)
                self.sound_settings = settings.get('sound_settings', {})
                if 'paths' in settings:
                    paths = settings['paths']
                    self.shared_folder = paths.get('shared', self.shared_folder)
                    self.funk_folder = paths.get('funk', self.funk_folder)
                    self.special_folder = paths.get('special', self.special_folder)
                    self.hyper_funk_folder = paths.get('hyper', self.hyper_funk_folder)
                    self.hyperborb_folder = paths.get('hyperborb', self.hyperborb_folder)
                    self.token_folder = paths.get('token', self.token_folder)
                    borp_paths = paths.get('borp_stages', {})
                    if borp_paths:
                        self.borp_stages['normal']['folder'] = borp_paths.get('normal', self.borp_stages['normal']['folder'])
                        self.borp_stages['super']['folder'] = borp_paths.get('super', self.borp_stages['super']['folder'])
                        self.borp_stages['miracle']['folder'] = borp_paths.get('miracle', self.borp_stages['miracle']['folder'])
                    self.stage_sounds_folder = paths.get('stage_sounds', self.stage_sounds_folder)
                self.funk_every_n_borps = settings.get('funk_every_n_borps', getattr(self, 'funk_every_n_borps', 2))
        except Exception as e:
            self.status_message.emit(f"Error loading settings: {e}")
            print(traceback.format_exc())

    # -------------------------
    # Debug dumping
    # -------------------------
    def dump_debug_info(self):
        try:
            self.status_message.emit(f"DEBUG: shared_folder={self.shared_folder}")
            self.status_message.emit(f"DEBUG: stage_sounds_folder={self.stage_sounds_folder}")
            self.status_message.emit(f"DEBUG: normal_quota={self.borp_stages['normal'].get('quota')} files={len(self.borp_stages['normal'].get('files',[]))}")
            self.status_message.emit(f"DEBUG: hyperborb_count={len(getattr(self,'hyperborb_files',[]))} hyper_funk_count={len(getattr(self,'hyper_funk_files',[]))}")
            self.status_message.emit(f"DEBUG: funk_every_n_borps={self.funk_every_n_borps} borp_play_count={self.borp_play_count}")
        except Exception as e:
            self.status_message.emit(f"Debug dump failed: {e}")
            print(traceback.format_exc())

    # -------------------------
    # Loop / lifecycle
    # -------------------------
    def start(self):
        if not self.running:
            self.running = True
            self.loop_thread = threading.Thread(target=self._loop, daemon=True)
            self.loop_thread.start()
            self.status_message.emit("Core loop started.")

    def stop(self):
        self.running = False
        self.total_score += self.score
        try:
            self.save_settings()
        except Exception:
            pass
        try:
            keyboard.unhook_all()
        except Exception:
            pass
        try:
            pygame.mixer.stop()
            pygame.quit()
        except Exception:
            pass
        self.status_message.emit("Core stopped.")

    def _loop(self):
        while self.running:
            try:
                # delayed input reset
                if self.delayed_input:
                    current_time = time.time()
                    if current_time - self.input_delay_start >= self.input_delay_duration:
                        self.delayed_input = False
                        self.key_input_allowed = True

                # --- FIX: Safer channel check ---
                hyper_channel = getattr(self, 'hyper_funk_channel', None)
                hyper_channel_busy = hyper_channel.get_busy() if hyper_channel else False

                # hyper funk finished -> end hyper mode cleanly
                if (
                    self.hyper_active
                    and self.hyper_state == 'funk'
                    and not hyper_channel_busy
                    and (getattr(self, 'hyperfunk_start_time', 0.0) > 0)
                    and (time.time() - self.hyperfunk_start_time > 0.05)
                ):
                    self.status_message.emit("Hyper Funk finished -> ending Hyper Mode")
                    try:
                        self.end_hyper_mode()
                    except Exception as e:
                        self.status_message.emit(f"End hyper error: {e}")

                # handle token timeout (guarded inside)
                self.handle_token_timeout()

                # inactivity reset (not during hyper or token)
                current_time = time.time()
                if not self.hyper_active and not self.token_active:
                    time_since_last = current_time - self.last_press_time
                    inactivity_threshold = 3.0
                    if self.hyper_grace_active:
                        if current_time - self.hyper_end_time > self.hyper_grace_period:
                            self.hyper_grace_active = False
                            self.status_message.emit("Grace period ended.")
                        else:
                            inactivity_threshold = self.hyper_grace_period
                    if time_since_last > inactivity_threshold and (self.score > 0 or self.current_multiplier > 1):
                        self.status_message.emit(f"Inactivity reset from score {self.score}")
                        if self.loser_sound and os.path.exists(self.loser_sound):
                            self.play_sound(self.loser_sound, self.sound_channel, priority=True)
                        self.score = 0
                        self.current_multiplier = 1.0
                        self.borp_play_count = 0  # <-- FIX: Reset funk counter
                        self.score_changed.emit(self.score)
                        self.multiplier_changed.emit(self.current_multiplier)
                        self.reset_to_stage_one()
                        self.last_press_time = current_time
            except Exception as e:
                self.status_message.emit(f"Error in main loop: {e}")
                print(traceback.format_exc())
            time.sleep(0.01)

    def end_hyper_mode(self):
        """Cleanly exit hyper mode after the special/hyper funk finishes."""
        if not self.hyper_active:
            return
        # stop hyper-specific channels if still running (best-effort)
        try:
            if getattr(self, 'hyper_funk_channel', None) is not None:
                self.hyper_funk_channel.stop()
        except Exception:
            pass
        try:
            if getattr(self, 'winner_channel', None) is not None:
                self.winner_channel.stop()
        except Exception:
            pass
        # reset state
        self.hyper_active = False
        self.hyper_active_changed.emit(False)
        self.hyper_state = 'idle'
        self.hyperborb_index = 0
        self.await_hyperfunk = False
        self.pending_hyperfunk_file = None
        # enable grace period to avoid immediate reset
        self.hyper_end_time = time.time()
        self.hyper_grace_active = True
        # allow inputs again
        self.priority_active = False
        self.key_input_allowed = True
        self.delayed_input = False
        self.status_message.emit("Exited HYPER MODE")

# -----------------------------
# Vignette Overlay (unchanged styling but kept stable)
# -----------------------------
class VignetteOverlay(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground)
        self.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents)
        self.setWindowFlags(QtCore.Qt.FramelessWindowHint | QtCore.Qt.WindowStaysOnTopHint | QtCore.Qt.Tool)

        self.fade_animation = QtCore.QPropertyAnimation(self, b"windowOpacity")
        self.time_offset = time.time()
        self.current_opacity = 0.0

        self.zoom_scale = 1.0
        self.zoom_velocity = 0.0
        self.zoom_target = 1.0
        self.last_update_time = time.time()
        self.spring_constant = 800.0
        self.damping = 20.0

        self.update_timer = QtCore.QTimer(self)
        self.update_timer.timeout.connect(self.update_animation)
        self.update_timer.start(16)

        self.base_colors = [
            QtGui.QColor(0,255,255),
            QtGui.QColor(0,255,0),
            QtGui.QColor(255,0,255),
            QtGui.QColor(0,128,255),
        ]
        self.current_color = self.base_colors[0]

    def showEvent(self, event):
        screen = QtWidgets.QApplication.primaryScreen()
        if screen:
            self.setGeometry(screen.geometry())
        super().showEvent(event)

    def start_effect(self):
        self.fade_animation.setDuration(500)
        self.fade_animation.setStartValue(0.0)
        self.fade_animation.setEndValue(1.0)
        self.fade_animation.start()
        self.time_offset = time.time()
        self.show()

    def stop_effect(self):
        self.fade_animation.setDuration(500)
        self.fade_animation.setStartValue(1.0)
        self.fade_animation.setEndValue(0.0)
        self.fade_animation.finished.connect(self._on_fade_out_finished)
        self.fade_animation.start()

    def _on_fade_out_finished(self):
        self.hide()
        try:
            self.fade_animation.finished.disconnect(self._on_fade_out_finished)
        except TypeError:
            pass # Already disconnected

    def trigger_zoom(self):
        self.zoom_target = 1.15
        self.zoom_velocity += 15.0

    def update_animation(self):
        current_time = time.time()
        t = (current_time - self.time_offset) * 2.0
        idx = int(t) % len(self.base_colors)
        next_idx = (idx + 1) % len(self.base_colors)
        fraction = t - int(t)
        c1 = self.base_colors[idx]
        c2 = self.base_colors[next_idx]
        r = int(c1.red() * (1-fraction) + c2.red() * fraction)
        g = int(c1.green() * (1-fraction) + c2.green() * fraction)
        b = int(c1.blue() * (1-fraction) + c2.blue() * fraction)
        self.current_color = QtGui.QColor(r,g,b)

        # zoom physics
        dt = min(current_time - self.last_update_time, 0.016)
        self.last_update_time = current_time
        displacement = self.zoom_scale - self.zoom_target
        spring_force = -self.spring_constant * displacement
        damping_force = -self.damping * self.zoom_velocity
        self.zoom_velocity += (spring_force + damping_force) * dt
        self.zoom_scale += self.zoom_velocity * dt
        self.zoom_scale = max(0.9, min(1.3, self.zoom_scale))
        self.update()

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        rect = self.rect()
        w = rect.width()
        h = rect.height()
        color_with_alpha = QtGui.QColor(self.current_color)
        scale_factor = (self.zoom_scale - 0.9)/0.4
        base_alpha = 40 + int(scale_factor * 80)
        color_with_alpha.setAlpha(min(120, base_alpha))
        edge_size = int(min(w,h) * 0.3 * self.zoom_scale)

        top_rect = QtCore.QRect(0,0,w,edge_size)
        gradient = QtGui.QLinearGradient(0,0,0,edge_size)
        gradient.setColorAt(0.0, color_with_alpha)
        gradient.setColorAt(1.0, QtGui.QColor(0,0,0,0))
        painter.fillRect(top_rect, gradient)

        bottom_rect = QtCore.QRect(0,h-edge_size,w,edge_size)
        gradient = QtGui.QLinearGradient(0,h,0,h-edge_size)
        gradient.setColorAt(0.0, color_with_alpha)
        gradient.setColorAt(1.0, QtGui.QColor(0,0,0,0))
        painter.fillRect(bottom_rect, gradient)

        left_rect = QtCore.QRect(0,0,edge_size,h)
        gradient = QtGui.QLinearGradient(0,0,edge_size,0)
        gradient.setColorAt(0.0, color_with_alpha)
        gradient.setColorAt(1.0, QtGui.QColor(0,0,0,0))
        painter.fillRect(left_rect, gradient)

        right_rect = QtCore.QRect(w-edge_size,0,edge_size,h)
        gradient = QtGui.QLinearGradient(w,0,w-edge_size,0)
        gradient.setColorAt(0.0, color_with_alpha)
        gradient.setColorAt(1.0, QtGui.QColor(0,0,0,0))
        painter.fillRect(right_rect, gradient)

        corner_radius = int(min(w,h) * 0.5 * self.zoom_scale)
        color_with_alpha.setAlpha(50)
        corner_rect = QtCore.QRect(0,0,w,h)
        corners = [(0,0),(w,0),(0,h),(w,h)]
        for cx,cy in corners:
            gradient = QtGui.QRadialGradient(cx,cy,corner_radius)
            gradient.setColorAt(0.0, color_with_alpha)
            gradient.setColorAt(0.5, QtGui.QColor(self.current_color.red(), self.current_color.green(), self.current_color.blue(), 20))
            gradient.setColorAt(1.0, QtGui.QColor(0,0,0,0))
            painter.fillRect(corner_rect, gradient)

# -----------------------------
# Settings Dialog (exposes stage_sounds path)
# -----------------------------
class SettingsDialog(QtWidgets.QDialog):
    def __init__(self, core: YujiFunkCore, parent=None):
        super().__init__(parent)
        self.core = core
        self.setWindowTitle("Settings")
        self.setModal(True)
        self.resize(520, 640)
        self.setFont(QtGui.QFont("Segoe UI", 11))
        layout = QtWidgets.QVBoxLayout(self)

        tabs = QtWidgets.QTabWidget()
        layout.addWidget(tabs)

        # General
        general_tab = QtWidgets.QWidget()
        gl = QtWidgets.QFormLayout(general_tab)
        self.token_chance = QtWidgets.QDoubleSpinBox()
        self.token_chance.setRange(0.0, 1.0)
        self.token_chance.setSingleStep(0.05)
        self.token_chance.setValue(self.core.token_chance)
        gl.addRow("Token Chance:", self.token_chance)

        self.cooldown = QtWidgets.QDoubleSpinBox()
        self.cooldown.setRange(0.0, 2.0)
        self.cooldown.setSingleStep(0.1)
        self.cooldown.setValue(self.core.cooldown)
        gl.addRow("Input Cooldown:", self.cooldown)

        self.funk_every_spin = QtWidgets.QSpinBox()
        self.funk_every_spin.setRange(1, 10)
        self.funk_every_spin.setValue(self.core.funk_every_n_borps)
        gl.addRow("Normal Funk every N borps:", self.funk_every_spin)

        tabs.addTab(general_tab, "General")

        # Paths
        paths_tab = QtWidgets.QWidget()
        pl = QtWidgets.QFormLayout(paths_tab)
        self.shared_folder = self._make_path_edit(self.core.shared_folder, "Shared Sounds")
        pl.addRow("Shared Folder:", self.shared_folder)

        pl.addRow(QtWidgets.QLabel("\nBorp Stage Folders:"))
        self.normal_folder = self._make_path_edit(os.path.join(self.core.shared_folder, self.core.borp_stages['normal']['folder']), "Normal Stage")
        pl.addRow("Normal Stage:", self.normal_folder)
        self.super_folder = self._make_path_edit(os.path.join(self.core.shared_folder, self.core.borp_stages['super']['folder']), "Super Stage")
        pl.addRow("Super Stage:", self.super_folder)
        self.miracle_folder = self._make_path_edit(os.path.join(self.core.shared_folder, self.core.borp_stages['miracle']['folder']), "Miracle Stage")
        pl.addRow("Miracle Stage:", self.miracle_folder)

        self.stage_sounds = self._make_path_edit(self.core.stage_sounds_folder, "Stage Achievement Sounds")
        pl.addRow("Stage Sounds:", self.stage_sounds)

        self.funk_folder = self._make_path_edit(self.core.funk_folder, "Funk Sounds")
        pl.addRow("Funk Folder:", self.funk_folder)
        self.special_folder = self._make_path_edit(self.core.special_folder, "Special Sounds")
        pl.addRow("Special Folder:", self.special_folder)
        self.hyper_folder = self._make_path_edit(self.core.hyper_funk_folder, "Hyper Funk")
        pl.addRow("Hyper Folder:", self.hyper_folder)
        self.hyperborb_folder = self._make_path_edit(self.core.hyperborb_folder, "HyperBorps Folder")
        pl.addRow("HyperBorps Folder:", self.hyperborb_folder)
        self.token_folder = self._make_path_edit(self.core.token_folder, "Token Sounds")
        pl.addRow("Token Folder:", self.token_folder)

        tabs.addTab(paths_tab, "Paths")

        # Sound Settings tab (per-sound chance/volume)
        sound_tab = QtWidgets.QWidget()
        sl = QtWidgets.QVBoxLayout(sound_tab)
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        container = QtWidgets.QWidget()
        cont_l = QtWidgets.QVBoxLayout(container)
        cont_l.setContentsMargins(0,0,0,0)
        self._sound_rows = {}

        def add_section(title, files):
            group = QtWidgets.QGroupBox(title)
            form = QtWidgets.QFormLayout(group)
            for f in files:
                row = QtWidgets.QWidget()
                hl = QtWidgets.QHBoxLayout(row)
                hl.setContentsMargins(0,0,0,0)
                name = QtWidgets.QLabel(os.path.basename(f))
                name.setToolTip(f)
                name.setMinimumWidth(180)
                hl.addWidget(name, 1)
                chance = QtWidgets.QDoubleSpinBox()
                chance.setRange(0.0,1.0)
                chance.setSingleStep(0.05)
                chance.setDecimals(2)
                chance.setValue(float(self.core.sound_settings.get(f, {}).get('chance', 1.0)))
                hl.addWidget(QtWidgets.QLabel("Chance"))
                hl.addWidget(chance)
                vol = QtWidgets.QDoubleSpinBox()
                vol.setRange(0.0,1.0)
                vol.setSingleStep(0.05)
                vol.setDecimals(2)
                vol.setValue(float(self.core.sound_settings.get(f, {}).get('volume', 1.0)))
                hl.addWidget(QtWidgets.QLabel("Volume"))
                hl.addWidget(vol)
                form.addRow(row)
                self._sound_rows[f] = (chance, vol)
            cont_l.addWidget(group)

        add_section("Shared", getattr(self.core,'shared_files',[]))
        add_section("Funk", getattr(self.core,'funk_files',[]))
        # Include Hyper Funk files so their chance/volume can be tuned and saved
        add_section("Hyper Funk", getattr(self.core,'hyper_funk_files',[]))
        add_section("Special", getattr(self.core,'special_files',[]))
        cont_l.addStretch(1)
        scroll.setWidget(container)
        sl.addWidget(scroll)
        tabs.addTab(sound_tab, "Sound Settings")

        # Visuals tab (simple)
        visuals_tab = QtWidgets.QWidget()
        vl = QtWidgets.QFormLayout(visuals_tab)
        self.color_list = QtWidgets.QListWidget()
        self.color_list.setFixedHeight(120)
        for color in self.parent().vignette.base_colors:
            item = QtWidgets.QListWidgetItem()
            item.setBackground(color)
            self.color_list.addItem(item)
        cbox = QtWidgets.QHBoxLayout()
        cbox.addWidget(self.color_list)
        add_color_btn = QtWidgets.QPushButton("Add")
        add_color_btn.clicked.connect(self._add_color)
        cbox.addWidget(add_color_btn)
        vl.addRow("Effect Colors:", cbox)
        tabs.addTab(visuals_tab, "Visuals")

        # buttons
        btns = QtWidgets.QHBoxLayout()
        save_btn = QtWidgets.QPushButton("Save")
        save_btn.clicked.connect(self.save_settings)
        btns.addWidget(save_btn)
        cancel_btn = QtWidgets.QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btns.addWidget(cancel_btn)
        layout.addLayout(btns)
        self.apply_styles()

    def _make_path_edit(self, initial, title):
        container = QtWidgets.QWidget()
        hl = QtWidgets.QHBoxLayout(container)
        hl.setContentsMargins(0,0,0,0)
        edit = QtWidgets.QLineEdit(initial)
        hl.addWidget(edit)
        browse = QtWidgets.QPushButton("Browse")
        browse.clicked.connect(lambda: self._browse_folder(edit, title))
        hl.addWidget(browse)
        return container

    def _browse_folder(self, edit, title):
        folder = QtWidgets.QFileDialog.getExistingDirectory(self, f"Select {title} Folder", edit.text())
        if folder:
            edit.setText(folder)

    def _add_color(self):
        color = QtWidgets.QColorDialog.getColor()
        if color.isValid():
            item = QtWidgets.QListWidgetItem()
            item.setBackground(color)
            self.color_list.addItem(item)

    def apply_styles(self):
        self.setStyleSheet("""
            QDialog { background-color: #0b0f14; color: #dbefff; }
            QTabWidget::pane { border: 1px solid #0ea5ff; }
            QTabBar::tab { background-color: #071526; color: #dbefff; padding:8px 16px; border:1px solid #0ea5ff; }
            QTabBar::tab:selected { background-color: #0d2b4a; }
            QPushButton { background-color:#071526; color:#dbefff; border:2px solid #0ea5ff; padding:6px 12px; border-radius:4px; }
            QLineEdit, QSpinBox, QDoubleSpinBox { background-color:#0d1824; color:#dbefff; border:1px solid #0ea5ff; padding:3px; }
            QLabel { color:#dbefff; }
        """)

    def save_settings(self):
        # update core fields
        self.core.token_chance = self.token_chance.value()
        self.core.cooldown = self.cooldown.value()
        self.core.funk_every_n_borps = int(self.funk_every_spin.value())

        self.core.shared_folder = self.shared_folder.findChild(QtWidgets.QLineEdit).text()
        self.core.funk_folder = self.funk_folder.findChild(QtWidgets.QLineEdit).text() if hasattr(self,'funk_folder') else self.core.funk_folder
        self.core.special_folder = self.special_folder.findChild(QtWidgets.QLineEdit).text() if hasattr(self,'special_folder') else self.core.special_folder
        self.core.hyper_funk_folder = self.hyper_folder.findChild(QtWidgets.QLineEdit).text() if hasattr(self,'hyper_folder') else self.core.hyper_funk_folder
        self.core.hyperborb_folder = self.hyperborb_folder.findChild(QtWidgets.QLineEdit).text() if hasattr(self,'hyperborb_folder') else self.core.hyperborb_folder
        self.core.token_folder = self.token_folder.findChild(QtWidgets.QLineEdit).text() if hasattr(self,'token_folder') else self.core.token_folder

        # borp stage folder entries: accept absolute path or store basename
        normal_text = self.normal_folder.findChild(QtWidgets.QLineEdit).text()
        super_text = self.super_folder.findChild(QtWidgets.QLineEdit).text()
        miracle_text = self.miracle_folder.findChild(QtWidgets.QLineEdit).text()
        self.core.borp_stages['normal']['folder'] = normal_text if os.path.isabs(normal_text) else os.path.basename(normal_text)
        self.core.borp_stages['super']['folder'] = super_text if os.path.isabs(super_text) else os.path.basename(super_text)
        self.core.borp_stages['miracle']['folder'] = miracle_text if os.path.isabs(miracle_text) else os.path.basename(miracle_text)

        # stage achievement sounds path
        self.core.stage_sounds_folder = self.stage_sounds.findChild(QtWidgets.QLineEdit).text()

        # reload file lists using core methods (no duplicate code)
        self.core.reload_borp_stage_files()
        self.core.reload_hyper_funk_files()
        self.core.reload_hyperborb_files()
        self.core.reload_token_sounds()

        # persist per-sound settings
        updated = {}
        for f, (chance_widget, volume_widget) in self._sound_rows.items():
            updated[f] = {'chance': float(chance_widget.value()), 'volume': float(volume_widget.value())}
        self.core.sound_settings = updated

        try:
            self.core.save_settings()
            self.core.status_message.emit("Settings saved & reloaded.")
            self.core.dump_debug_info()
        except Exception as e:
            self.core.status_message.emit(f"Error saving settings: {e}")
            print(traceback.format_exc())
        self.accept()

# -----------------------------
# GUI
# -----------------------------
class YujiFunkGUI(QtWidgets.QMainWindow):
    def __init__(self, core: YujiFunkCore):
        super().__init__()
        self.core = core
        self.setWindowTitle("Yuji Funk")
        self.resize(880, 480)
        self.setWindowFlags(self.windowFlags() | QtCore.Qt.WindowStaysOnTopHint)

        # vignette overlay
        self.vignette = VignetteOverlay()

        # central layout
        w = QtWidgets.QWidget()
        self.setCentralWidget(w)
        layout = QtWidgets.QHBoxLayout(w)

        # left: score column
        score_layout = QtWidgets.QVBoxLayout()
        layout.addLayout(score_layout, 1)
        score_font = QtGui.QFont("Segoe UI", 28)
        score_font.setWeight(QtGui.QFont.DemiBold)
        self.score_label = QtWidgets.QLabel("0")
        self.score_label.setFont(score_font)
        self.score_label.setAlignment(QtCore.Qt.AlignCenter)
        self.score_label.setStyleSheet("QLabel{ color:#00ffff; background: rgba(7,21,38,0.8); border:2px solid #0ea5ff; border-radius:10px; padding:10px; min-width:200px; }")
        score_layout.addWidget(self.score_label)

        mult_font = QtGui.QFont("Segoe UI", 16)
        mult_font.setWeight(QtGui.QFont.DemiBold)
        self.multiplier_label = QtWidgets.QLabel("x1")
        self.multiplier_label.setFont(mult_font)
        self.multiplier_label.setAlignment(QtCore.Qt.AlignCenter)
        self.multiplier_label.setStyleSheet("QLabel{ color:#00ff88; background: rgba(7,21,38,0.7); border:1px solid #0ea5ff; border-radius:8px; padding:6px; }")
        score_layout.addWidget(self.multiplier_label)

        self.high_score_label = QtWidgets.QLabel(f"Best: {self.core.high_score}")
        self.high_score_label.setFont(QtGui.QFont("Segoe UI", 14))
        self.high_score_label.setAlignment(QtCore.Qt.AlignCenter)
        self.high_score_label.setStyleSheet("QLabel{ color:#00ffaa; background: rgba(7,21,38,0.6); border:1px solid #0ea5ff; border-radius:8px; padding:6px; }")
        score_layout.addWidget(self.high_score_label)
        score_layout.addStretch()

        # middle: buttons
        left_buttons = QtWidgets.QVBoxLayout()
        layout.addLayout(left_buttons, 3)
        btn_font = QtGui.QFont("Segoe UI", 20, QtGui.QFont.Bold)
        for t in ["R","1","2","3","4"]:
            btn = QtWidgets.QPushButton(t)
            btn.setFixedHeight(64)
            btn.setFont(btn_font)
            btn.clicked.connect(lambda checked, k=t.lower(): self._button_pressed(k))
            left_buttons.addWidget(btn)
        left_buttons.addStretch()

        # right: status
        right = QtWidgets.QVBoxLayout()
        layout.addLayout(right, 2)
        title = QtWidgets.QLabel("Yuji Funk")
        title.setFont(QtGui.QFont("Segoe UI", 22, QtGui.QFont.Bold))
        title.setAlignment(QtCore.Qt.AlignCenter)
        right.addWidget(title)

        grid = QtWidgets.QGridLayout()
        right.addLayout(grid)
        self.lbl_token_count = self._make_status_label("Tokens: 0")
        self.lbl_token_active = self._make_status_label("Token Active: NO")
        self.lbl_hyper_active = self._make_status_label("Hyper Funk: NO")
        self.lbl_last_sound = self._make_status_label("Last Sound: ---")
        grid.addWidget(self.lbl_token_count, 0, 0)
        grid.addWidget(self.lbl_token_active, 1, 0)
        grid.addWidget(self.lbl_hyper_active, 2, 0)
        grid.addWidget(self.lbl_last_sound, 3, 0)

        self.msg_box = QtWidgets.QLabel("")
        self.msg_box.setWordWrap(True)
        self.msg_box.setFixedHeight(80)
        right.addWidget(self.msg_box)

        right.addStretch()
        btn_row = QtWidgets.QHBoxLayout()
        self.settings_btn = QtWidgets.QPushButton("Settings")
        self.settings_btn.setFixedHeight(48)
        self.settings_btn.clicked.connect(self.show_settings)
        btn_row.addWidget(self.settings_btn)
        self.quit_btn = QtWidgets.QPushButton("QUIT")
        self.quit_btn.setFixedHeight(48)
        self.quit_btn.clicked.connect(self.close_app)
        btn_row.addWidget(self.quit_btn)
        right.addLayout(btn_row)
        self._apply_styles()

        # settings dialog
        self.settings_dialog = SettingsDialog(self.core, self)

        # connect core signals
        self.core.token_count_changed.connect(self.on_token_count_changed)
        self.core.token_active_changed.connect(self.on_token_active_changed)
        self.core.hyper_active_changed.connect(self.on_hyper_active_changed)
        self.core.last_sound_changed.connect(self.on_last_sound_changed)
        self.core.status_message.connect(self.on_status_message)
        self.core.score_changed.connect(self.on_score_changed)
        self.core.high_score_changed.connect(self.on_high_score_changed)
        self.core.multiplier_changed.connect(self.on_multiplier_changed)

        # keyboard hook
        try:
            keyboard.on_press(lambda e: self._keyboard_callback(e))
        except Exception as e:
            self.msg_box.setText(f"Keyboard hook error (try running as admin): {e}")

    def _make_status_label(self, text):
        lbl = QtWidgets.QLabel(text)
        lbl.setFont(QtGui.QFont("Segoe UI", 12))
        lbl.setFixedHeight(34)
        lbl.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        return lbl

    def _apply_styles(self):
        style = """
        QWidget { background-color: #0b0f14; color: #dbefff; }
        QPushButton { background-color: #071526; border:2px solid #0ea5ff; border-radius:8px; padding:8px; }
        QPushButton:hover { border:3px solid #2ebaff; margin:-1px; }
        QLabel { color: #cfeeff; }
        #QUIT { background-color:#2b0207; border:2px solid #ff4d6d; color:#ffdfe3; }
        """
        self.setStyleSheet(style)
        self.quit_btn.setObjectName("QUIT")
        pal = self.palette()
        pal.setColor(QtGui.QPalette.Window, QtGui.QColor("#06080a"))
        self.setPalette(pal)

    def _button_pressed(self, key_name):
        self.core.on_key_event_name(key_name)
        if key_name.lower() in ['r','1','2','3','4']:
            self.vignette.trigger_zoom()

    def _keyboard_callback(self, event):
        try:
            k = event.name
            self.core.on_key_event_name(k)
            if k.lower() in ['r','1','2','3','4']:
                self.vignette.trigger_zoom()
        except Exception:
            pass

    def on_token_count_changed(self, n):
        self.lbl_token_count.setText(f"Tokens: {n}")

    def on_token_active_changed(self, active):
        self.lbl_token_active.setText(f"Token Active: {'YES' if active else 'NO'}")

    def on_hyper_active_changed(self, active):
        self.lbl_hyper_active.setText(f"Hyper Funk: {'YES' if active else 'NO'}")
        if active:
            self.vignette.start_effect()
        else:
            self.vignette.stop_effect()

    def on_last_sound_changed(self, name):
        self.lbl_last_sound.setText(f"Last Sound: {name}")

    def on_status_message(self, msg):
        # show in GUI and print to console for diagnostics
        try:
            self.msg_box.setText(msg)
        except Exception:
            pass
        print("[STATUS]", msg)

    def on_score_changed(self, score):
        self.score_label.setText(str(int(score)))

    def on_high_score_changed(self, hs):
        self.high_score_label.setText(f"Best: {int(hs)}")

    def on_multiplier_changed(self, m):
        self.multiplier_label.setText(f"x{m:.1f}")

    def show_settings(self):
        self.settings_dialog.exec_()

    def close_app(self):
        self.core.stop()
        self.vignette.close()
        self.close()

# -----------------------------
# App entrypoint
# -----------------------------
def main():
    app = QtWidgets.QApplication(sys.argv)
    core = YujiFunkCore()
    # connect console logging for quick debugging
    core.status_message.connect(lambda m: print("[CORE STATUS]", m))
    core.last_sound_changed.connect(lambda s: print("[SOUND]", s))
    gui = YujiFunkGUI(core)
    gui.show()
    core.start()
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()