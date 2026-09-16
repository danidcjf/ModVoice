"""
Interface for TikTok LIVE Battle VoiceMod & Soundboard.

Every colour, radius, spacing step and font comes from `theme.py`, so no screen
hardcodes a value. Icons are deliberately text-only: an inconsistent icon set or
emoji used as iconography is the fastest way to make a desktop app read as
machine-generated rather than designed.
"""

import os
import json
import shutil
import webbrowser
from tkinter import filedialog
import customtkinter as ctk
import threading
from typing import Dict, Optional

from . import paths
from . import theme as t
from .audio_engine import BattleAudioEngine
from .soundboard import SoundboardManager
from .tiktok_bridge import TikTokStudioBridge
from .virtual_driver import (
    is_virtual_driver_installed,
    get_virtual_driver_info,
    install_virtual_driver_silent,
)


ctk.set_appearance_mode("Dark")


class BattleVoiceModApp(ctk.CTk):
    # Single source for factory defaults: used both when loading app_config.json
    # and as the value a double-click on a slider knob restores.
    DEFAULT_CONFIG = {
        "input_device": "",
        "virtual_output": "",
        "monitor_output": "",
        "mic_volume": 1.0,
        "effect_volume": 1.0,
        "soundboard_volume": 1.0,
        "monitor_volume": 0.8,
        "hear_myself": True,
        "gate_enabled": True,
        "gate_threshold_db": -40.0,
        "eq_enabled": True,
        "comp_enabled": True,
        "comp_amount": 0.4,
        "limiter_enabled": True,
        "limiter_ceiling_db": -1.0,
    }
    PITCH_DEFAULT = 0.0

    def __init__(self, engine: BattleAudioEngine, soundboard: SoundboardManager):
        super().__init__()

        self.engine = engine
        self.soundboard = soundboard
        self.bridge = TikTokStudioBridge()
        self.engine.set_soundboard_provider(self.soundboard)

        # Persistent settings live in %APPDATA%, never next to the executable
        self.config_path = paths.user_config_path("app_config.json")
        self.app_config = self._load_app_config()

        # Window Settings
        self.title("Battle VoiceMod — TikTok LIVE Studio")
        self.geometry("1020x860")
        self.minsize(900, 700)
        self.configure(fg_color=t.BG_CANVAS)

        # UI State
        self.active_voice_btn: Optional[ctk.CTkButton] = None
        self.voice_buttons: Dict[str, ctk.CTkButton] = {}
        self._last_error_count = 0
        self._error_bar_visible = False
        self._routing_bar_visible = False

        # Build Interface
        self._build_header()
        self._build_quick_controls()
        self._build_main_content()
        self._build_device_and_volumes_panel()

        # Restore saved volume and switch settings
        self._apply_saved_settings()

        # Start background update loop (VU Meter & Status)
        self.after(50, self._update_loop)

        # Auto-detect or restore devices
        self._auto_select_devices()

    def _load_app_config(self) -> dict:
        """Loads persistent user configuration from app_config.json."""
        config = dict(self.DEFAULT_CONFIG)
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    stored = json.load(f)
                # Ignore keys this version no longer knows about, so settings from
                # removed features do not linger in the file forever.
                config.update({k: v for k, v in stored.items() if k in config})
            except Exception as e:
                print(f"Erro ao carregar app_config.json: {e}")
        return config

    def _save_app_config(self):
        """Saves current devices and volume settings to disk."""
        if getattr(self, '_suspend_config_save', False):
            return
        try:
            self.app_config.update({
                "input_device": self.mic_combo.get() if hasattr(self, 'mic_combo') else "",
                "virtual_output": self.virt_combo.get() if hasattr(self, 'virt_combo') else "",
                "monitor_output": self.mon_combo.get() if hasattr(self, 'mon_combo') else "",
                "mic_volume": float(self.mic_vol_slider.get()) if hasattr(self, 'mic_vol_slider') else 1.0,
                "effect_volume": float(self.fx_vol_slider.get()) if hasattr(self, 'fx_vol_slider') else 1.0,
                "soundboard_volume": float(self.sb_vol_slider.get()) if hasattr(self, 'sb_vol_slider') else 1.0,
                "monitor_volume": float(self.mon_vol_slider.get()) if hasattr(self, 'mon_vol_slider') else 0.8,
                "hear_myself": bool(self.hear_switch.get()) if hasattr(self, 'hear_switch') else True,
                "gate_enabled": bool(self.gate_switch.get()) if hasattr(self, 'gate_switch') else True,
                "gate_threshold_db": float(self.gate_slider.get()) if hasattr(self, 'gate_slider') else -40.0,
                "eq_enabled": bool(self.eq_switch.get()) if hasattr(self, 'eq_switch') else True,
                "comp_enabled": bool(self.comp_switch.get()) if hasattr(self, 'comp_switch') else True,
                "comp_amount": float(self.comp_slider.get()) if hasattr(self, 'comp_slider') else 0.4,
                "limiter_enabled": bool(self.limit_switch.get()) if hasattr(self, 'limit_switch') else True,
                "limiter_ceiling_db": float(self.limit_slider.get()) if hasattr(self, 'limit_slider') else -1.0,
            })
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(self.app_config, f, indent=2)
        except Exception as e:
            print(f"Erro ao salvar app_config.json: {e}")

    def _apply_saved_settings(self):
        """Applies loaded volume levels and toggles to sliders and engine."""
        self._suspend_config_save = True
        mic_v = self.app_config.get("mic_volume", 1.0)
        fx_v = self.app_config.get("effect_volume", 1.0)
        sb_v = self.app_config.get("soundboard_volume", 1.0)
        mon_v = self.app_config.get("monitor_volume", 0.8)
        hear = self.app_config.get("hear_myself", True)

        self.mic_vol_slider.set(mic_v)
        self.engine.mic_volume = mic_v

        self.fx_vol_slider.set(fx_v)
        self.engine.effect_volume = fx_v

        self.sb_vol_slider.set(sb_v)
        self.engine.soundboard_volume = sb_v

        self.mon_vol_slider.set(mon_v)
        self.engine.monitor_volume = mon_v

        if hear:
            self.hear_switch.select()
            self.engine.set_hear_myself(True)
        else:
            self.hear_switch.deselect()
            self.engine.set_hear_myself(False)

        chain = self.engine.studio_chain

        gate_on = self.app_config.get("gate_enabled", True)
        self._set_switch(self.gate_switch, gate_on)
        chain.gate.enabled = gate_on

        eq_on = self.app_config.get("eq_enabled", True)
        self._set_switch(self.eq_switch, eq_on)
        chain.eq.enabled = eq_on

        comp_on = self.app_config.get("comp_enabled", True)
        self._set_switch(self.comp_switch, comp_on)
        chain.compressor.enabled = comp_on

        limit_on = self.app_config.get("limiter_enabled", True)
        self._set_switch(self.limit_switch, limit_on)
        chain.limiter.enabled = limit_on

        # Slider.set() does not trigger the widget command, so the handlers are
        # invoked explicitly to sync the engine and the value labels on startup.
        self.gate_slider.set(self.app_config.get("gate_threshold_db", -40.0))
        self._on_gate_threshold()

        self.comp_slider.set(self.app_config.get("comp_amount", 0.4))
        self._on_comp_amount()

        self.limit_slider.set(self.app_config.get("limiter_ceiling_db", -1.0))
        self._on_limiter_ceiling()

        self._suspend_config_save = False

    @staticmethod
    def _set_switch(switch: ctk.CTkSwitch, value: bool):
        if value:
            switch.select()
        else:
            switch.deselect()

    def _dialog(self, title: str, message: str, confirm: bool = False,
                danger: bool = False, confirm_label: str = "Confirmar") -> bool:
        """Themed replacement for tkinter's messagebox, which renders a light
        system dialog on top of our dark interface."""
        win = ctk.CTkToplevel(self)
        win.title(title)
        win.configure(fg_color=t.BG_CANVAS)
        win.resizable(False, False)
        win.transient(self)

        result = {"value": False}
        body = ctk.CTkFrame(win, fg_color=t.BG_SURFACE, corner_radius=t.RADIUS_LG)
        body.pack(fill="both", expand=True, padx=t.SPACE_4, pady=t.SPACE_4)

        ctk.CTkLabel(
            body, text=title, font=t.title(), text_color=t.TEXT_PRIMARY,
            justify="left", wraplength=400, anchor="w"
        ).pack(fill="x", padx=t.SPACE_4, pady=(t.SPACE_4, t.SPACE_2))

        ctk.CTkLabel(
            body, text=message, font=t.body(), text_color=t.TEXT_SECONDARY,
            justify="left", wraplength=400, anchor="w"
        ).pack(fill="x", padx=t.SPACE_4, pady=(0, t.SPACE_4))

        actions = ctk.CTkFrame(body, fg_color="transparent")
        actions.pack(fill="x", padx=t.SPACE_4, pady=(0, t.SPACE_4))

        def close(value: bool):
            result["value"] = value
            win.destroy()

        primary_fg = t.STATE_ERROR if danger else t.BRAND_PRIMARY
        primary_hover = t.STATE_ERROR_HOVER if danger else t.BRAND_PRIMARY_HOVER

        if confirm:
            ctk.CTkButton(
                actions, text="Cancelar", font=t.body(),
                fg_color=t.BG_SURFACE_RAISED, hover_color=t.BG_SURFACE_HOVER,
                text_color=t.TEXT_SECONDARY, height=t.HEIGHT_CONTROL,
                corner_radius=t.RADIUS_MD, command=lambda: close(False)
            ).pack(side="right", padx=(t.SPACE_2, 0))

        ctk.CTkButton(
            actions, text=confirm_label if confirm else "OK", font=t.body(True),
            fg_color=primary_fg, hover_color=primary_hover,
            text_color=t.TEXT_ON_BRAND, height=t.HEIGHT_CONTROL,
            corner_radius=t.RADIUS_MD, command=lambda: close(True)
        ).pack(side="right")

        win.update_idletasks()
        x = self.winfo_rootx() + (self.winfo_width() - win.winfo_width()) // 2
        y = self.winfo_rooty() + (self.winfo_height() - win.winfo_height()) // 3
        win.geometry(f"+{max(x, 0)}+{max(y, 0)}")

        win.grab_set()
        win.wait_window()
        return result["value"]

    def _slider_knob_hit(self, slider: ctk.CTkSlider, x: int, y: int) -> bool:
        """True when a canvas coordinate falls on the slider's knob."""
        for item in slider._canvas.find_withtag("slider_parts"):
            box = slider._canvas.bbox(item)
            if box and box[0] - 4 <= x <= box[2] + 4 and box[1] - 4 <= y <= box[3] + 4:
                return True
        return False

    def _enable_double_click_reset(self, slider: ctk.CTkSlider, default: float, apply):
        """Double-clicking a slider's knob snaps it back to the factory default.

        CTkSlider.set() deliberately does not fire the widget's command, so the
        handler is invoked explicitly to keep the engine and the saved config in
        sync — the same reason _apply_saved_settings calls its handlers by hand.
        """
        def restore():
            if slider.winfo_exists():
                slider.set(default)
                apply(default)

        def on_double_click(event):
            # Only when the click lands on the knob itself, so a stray double
            # click on the track does not silently change the value.
            if self._slider_knob_hit(slider, event.x, event.y):
                # Deferred by one idle cycle: CTkSlider's own <Button-1> handler
                # snaps the knob to the click position, and Tk may run it after
                # this binding. Restoring last is what makes the reset stick.
                slider.after_idle(restore)

        slider._on_double_click_reset = on_double_click
        slider.bind("<Double-Button-1>", on_double_click)
        return slider

    def _build_header(self):
        """App title, product badge, and TikTok LIVE Studio pairing status."""
        header = ctk.CTkFrame(self, fg_color=t.BG_SURFACE, corner_radius=t.RADIUS_LG)
        header.pack(fill="x", padx=t.SPACE_5, pady=(t.SPACE_4, t.SPACE_3))

        title_box = ctk.CTkFrame(header, fg_color="transparent")
        title_box.pack(side="left", padx=t.SPACE_4, pady=t.SPACE_3)

        ctk.CTkLabel(
            title_box, text="Battle VoiceMod",
            font=t.display(), text_color=t.TEXT_PRIMARY
        ).pack(side="left")

        ctk.CTkLabel(
            title_box, text="TikTok LIVE", font=t.label(),
            text_color=t.TEXT_ON_BRAND, fg_color=t.BRAND_ACCENT,
            corner_radius=t.RADIUS_SM, padx=t.SPACE_2, pady=2
        ).pack(side="left", padx=(t.SPACE_2, 0))

        # Pairing status with TikTok LIVE Studio
        self.status_label = ctk.CTkLabel(
            header,
            text="Verificando TikTok LIVE Studio...",
            font=t.body(),
            text_color=t.TEXT_TERTIARY
        )
        self.status_label.pack(side="right", padx=t.SPACE_4)

    def _build_quick_controls(self):
        """Normal voice reset, sidetone toggle, mute, and the input level meter."""
        bar = ctk.CTkFrame(self, fg_color=t.BG_SURFACE, corner_radius=t.RADIUS_LG)
        bar.pack(fill="x", padx=t.SPACE_5, pady=(0, t.SPACE_3))

        # "Selected" is always the same treatment across the app: a filled brand
        # button. That keeps the palette to one accent instead of a colour per state.
        self.normal_btn = ctk.CTkButton(
            bar, text="Normal", font=t.body(True),
            fg_color=t.BRAND_PRIMARY, hover_color=t.BRAND_PRIMARY_HOVER,
            text_color=t.TEXT_ON_BRAND, width=150, height=t.HEIGHT_CONTROL + 8,
            corner_radius=t.RADIUS_MD, border_width=0,
            command=lambda: self.select_voice_effect("normal")
        )
        self.normal_btn.pack(side="left", padx=t.SPACE_4, pady=t.SPACE_3)
        self.active_voice_btn = self.normal_btn

        self.hear_switch = ctk.CTkSwitch(
            bar, text="Ouvir minha voz", font=t.body(),
            text_color=t.TEXT_SECONDARY, progress_color=t.BRAND_PRIMARY,
            button_color=t.TEXT_PRIMARY, button_hover_color=t.TEXT_PRIMARY,
            fg_color=t.BORDER_STRONG,
            command=self._on_toggle_hear_myself
        )
        self.hear_switch.select()
        self.hear_switch.pack(side="left", padx=t.SPACE_3)

        self.mute_btn = ctk.CTkButton(
            bar, text="Microfone ativo", font=t.body(True),
            fg_color=t.BG_SURFACE_RAISED, hover_color=t.BG_SURFACE_HOVER,
            text_color=t.TEXT_PRIMARY, width=150, height=t.HEIGHT_CONTROL,
            corner_radius=t.RADIUS_MD,
            command=self._on_toggle_mute
        )
        self.mute_btn.pack(side="left", padx=t.SPACE_3)

        meter = ctk.CTkFrame(bar, fg_color="transparent")
        meter.pack(side="right", padx=t.SPACE_4, pady=t.SPACE_3, fill="x", expand=True)

        ctk.CTkLabel(
            meter, text="SINAL DO MICROFONE", font=t.label(),
            text_color=t.TEXT_TERTIARY
        ).pack(anchor="w")

        self.vu_bar = ctk.CTkProgressBar(
            meter, progress_color=t.BRAND_PRIMARY, fg_color=t.BORDER_SUBTLE,
            height=t.HEIGHT_PROGRESS, corner_radius=t.RADIUS_SM
        )
        self.vu_bar.set(0.0)
        self.vu_bar.pack(fill="x", pady=(t.SPACE_1, 0))

    def _divider(self, parent, pady: int = 0):
        """One-pixel separator. A line is only used to divide groups, never as decoration."""
        line = ctk.CTkFrame(parent, height=1, fg_color=t.BORDER_SUBTLE)
        line.pack(fill="x", padx=t.SPACE_4, pady=pady)
        return line

    def _build_main_content(self):
        """Two panels: voice effects on the left, battle soundboard on the right."""
        content = ctk.CTkFrame(self, fg_color="transparent")
        content.pack(fill="both", expand=True, padx=t.SPACE_5, pady=(0, t.SPACE_3))

        # ==================== LEFT: VOICE EFFECTS ====================
        voice_frame = ctk.CTkFrame(content, fg_color=t.BG_SURFACE, corner_radius=t.RADIUS_LG)
        voice_frame.pack(side="left", fill="both", expand=True, padx=(0, t.SPACE_2))

        headings = ctk.CTkFrame(voice_frame, fg_color="transparent")
        headings.pack(fill="x", padx=t.SPACE_4, pady=(t.SPACE_4, t.SPACE_2))
        ctk.CTkLabel(headings, text="Vozes", font=t.title(), text_color=t.TEXT_PRIMARY).pack(side="left")

        v_scroll = ctk.CTkScrollableFrame(voice_frame, fg_color="transparent")
        v_scroll.pack(fill="both", expand=True, padx=t.SPACE_2, pady=0)
        v_scroll.grid_columnconfigure((0, 1), weight=1)

        effects = [
            ("Megafone", "megaphone"),
            ("Walkie-Talkie", "walkie_talkie"),
            ("Pânico", "panic"),
        ]

        for index, (display_name, eff_id) in enumerate(effects):
            card = ctk.CTkButton(
                v_scroll,
                text=display_name,
                font=t.body(True),
                fg_color=t.BG_SURFACE_RAISED,
                hover_color=t.BG_SURFACE_HOVER,
                text_color=t.TEXT_SECONDARY,
                border_width=0,
                corner_radius=t.RADIUS_MD,
                height=52,
                command=lambda e=eff_id: self.select_voice_effect(e)
            )
            card.grid(row=index // 2, column=index % 2, sticky="ew",
                      padx=t.SPACE_1, pady=t.SPACE_1)
            self.voice_buttons[eff_id] = card

        self._divider(voice_frame, pady=(t.SPACE_3, 0))

        # Pitch group (flat: separated by a divider, not by another card)
        pitch_box = ctk.CTkFrame(voice_frame, fg_color="transparent")
        pitch_box.pack(fill="x", padx=t.SPACE_4, pady=t.SPACE_3)

        p_header = ctk.CTkFrame(pitch_box, fg_color="transparent")
        p_header.pack(fill="x")
        ctk.CTkLabel(p_header, text="TOM", font=t.label(), text_color=t.TEXT_TERTIARY).pack(side="left")
        self.pitch_val_label = ctk.CTkLabel(
            p_header, text="0 semitons (original)",
            font=t.body(), text_color=t.BRAND_PRIMARY
        )
        self.pitch_val_label.pack(side="right")

        self.pitch_slider = ctk.CTkSlider(
            pitch_box, from_=-12.0, to=12.0, number_of_steps=48,
            progress_color=t.BRAND_PRIMARY, button_color=t.TEXT_PRIMARY,
            button_hover_color=t.TEXT_PRIMARY, fg_color=t.BORDER_SUBTLE,
            height=t.HEIGHT_SLIDER,
            command=self._on_pitch_slider
        )
        self.pitch_slider.set(self.PITCH_DEFAULT)
        self.pitch_slider.pack(fill="x", pady=(t.SPACE_2, 0))
        self._enable_double_click_reset(self.pitch_slider, self.PITCH_DEFAULT, self._on_pitch_slider)

        # ==================== RIGHT: BATTLE SOUNDBOARD ====================
        sb_frame = ctk.CTkFrame(content, fg_color=t.BG_SURFACE, corner_radius=t.RADIUS_LG)
        sb_frame.pack(side="right", fill="both", expand=True, padx=(t.SPACE_2, 0))

        sb_header = ctk.CTkFrame(sb_frame, fg_color="transparent")
        sb_header.pack(fill="x", padx=t.SPACE_4, pady=(t.SPACE_4, t.SPACE_2))

        ctk.CTkLabel(sb_header, text="Soundboard", font=t.title(), text_color=t.TEXT_PRIMARY).pack(side="left")

        ctk.CTkButton(
            sb_header, text="Parar sons", font=t.body(),
            fg_color=t.BG_SURFACE_RAISED, hover_color=t.STATE_ERROR_DIM,
            text_color=t.STATE_ERROR, width=110, height=t.HEIGHT_CONTROL_SM,
            corner_radius=t.RADIUS_MD,
            command=lambda: self.soundboard.stop()
        ).pack(side="right")

        # Hint row sits directly on the panel (no nested card)
        hint = ctk.CTkFrame(sb_frame, fg_color="transparent")
        hint.pack(fill="x", padx=t.SPACE_4, pady=(0, t.SPACE_2))

        ctk.CTkLabel(
            hint, text="Baixe memes e efeitos em .mp3 no MyInstants",
            font=t.body(), text_color=t.TEXT_TERTIARY, anchor="w"
        ).pack(side="left")

        ctk.CTkButton(
            hint, text="Abrir MyInstants", font=t.body(),
            fg_color=t.BG_SURFACE_RAISED, hover_color=t.BG_SURFACE_HOVER,
            text_color=t.BRAND_PRIMARY, height=t.HEIGHT_CONTROL_SM, width=140,
            corner_radius=t.RADIUS_MD,
            command=lambda: webbrowser.open("https://www.myinstants.com/pt/index/br/")
        ).pack(side="right")

        self._divider(sb_frame, pady=(t.SPACE_1, t.SPACE_2))

        self.sb_scroll = ctk.CTkScrollableFrame(sb_frame, fg_color="transparent")
        self.sb_scroll.pack(fill="both", expand=True, padx=t.SPACE_2, pady=0)

        self._populate_soundboard_cards()

        sb_bottom = ctk.CTkFrame(sb_frame, fg_color="transparent")
        sb_bottom.pack(fill="x", padx=t.SPACE_4, pady=(t.SPACE_3, t.SPACE_4))

        ctk.CTkButton(
            sb_bottom, text="Adicionar áudio", font=t.body(True),
            fg_color=t.BRAND_PRIMARY, hover_color=t.BRAND_PRIMARY_HOVER,
            text_color=t.TEXT_ON_BRAND, height=t.HEIGHT_CONTROL,
            corner_radius=t.RADIUS_MD,
            command=self._add_custom_sound
        ).pack(side="left", fill="x", expand=True, padx=(0, t.SPACE_2))

        ctk.CTkButton(
            sb_bottom, text="Carregar exemplos", font=t.body(),
            fg_color=t.BG_SURFACE_RAISED, hover_color=t.BG_SURFACE_HOVER,
            text_color=t.TEXT_SECONDARY, height=t.HEIGHT_CONTROL, width=160,
            corner_radius=t.RADIUS_MD,
            command=self._load_sample_sounds
        ).pack(side="right")

    def _populate_soundboard_cards(self):
        """Rebuilds the soundboard list. Each row plays, enables/disables, or removes."""
        for w in self.sb_scroll.winfo_children():
            w.destroy()

        sounds = list(self.soundboard.sounds.keys())
        if not sounds:
            ctk.CTkLabel(
                self.sb_scroll,
                text="Soundboard vazio\n\nAdicione arquivos .mp3, .wav, .ogg ou .flac,\nou carregue os exemplos de batalha.",
                font=t.body(),
                text_color=t.TEXT_TERTIARY,
                justify="center"
            ).pack(fill="both", expand=True, pady=t.SPACE_5)
            return

        for s_name in sounds:
            is_enabled = self.soundboard.sound_enabled.get(s_name, True)
            display_name = s_name.replace("_", " ")

            row = ctk.CTkFrame(self.sb_scroll, fg_color="transparent")
            row.pack(fill="x", padx=t.SPACE_1, pady=t.SPACE_1)

            ctk.CTkButton(
                row,
                text=display_name,
                font=t.body(True),
                fg_color=t.BG_SURFACE_RAISED if is_enabled else t.BG_CANVAS,
                hover_color=t.BRAND_PRIMARY_DIM if is_enabled else t.BG_CANVAS,
                text_color=t.TEXT_PRIMARY if is_enabled else t.TEXT_TERTIARY,
                border_width=0,
                corner_radius=t.RADIUS_MD,
                height=40,
                anchor="w",
                state="normal" if is_enabled else "disabled",
                command=lambda name=s_name: self.soundboard.play(name)
            ).pack(side="left", fill="x", expand=True)

            switch = ctk.CTkSwitch(
                row, text="", width=40,
                progress_color=t.BRAND_PRIMARY,
                button_color=t.TEXT_PRIMARY, button_hover_color=t.TEXT_PRIMARY,
                fg_color=t.BORDER_STRONG,
                command=lambda name=s_name: self._toggle_sound_active(name)
            )
            if is_enabled:
                switch.select()
            switch.pack(side="left", padx=t.SPACE_3)

            ctk.CTkButton(
                row,
                text="Remover",
                font=t.body(),
                fg_color="transparent",
                hover_color=t.STATE_ERROR_DIM,
                text_color=t.TEXT_TERTIARY,
                width=90,
                height=t.HEIGHT_CONTROL,
                corner_radius=t.RADIUS_MD,
                command=lambda name=s_name: self._confirm_delete_sound(name)
            ).pack(side="right")

    def _toggle_sound_active(self, sound_name: str):
        """Toggles a sound between active and disabled."""
        current = self.soundboard.sound_enabled.get(sound_name, True)
        self.soundboard.set_sound_active(sound_name, not current)
        self._populate_soundboard_cards()

    def _confirm_delete_sound(self, sound_name: str):
        """Prompts before deleting any sound."""
        confirmed = self._dialog(
            "Remover som",
            f"'{sound_name}' será removido do soundboard e apagado do disco.",
            confirm=True, danger=True, confirm_label="Remover"
        )
        if confirmed:
            self.soundboard.delete_sound(sound_name)
            self._populate_soundboard_cards()

    def _load_sample_sounds(self):
        """Generates sample battle sounds without overwriting existing files."""
        confirmed = self._dialog(
            "Carregar exemplos",
            "Serão gerados os sons clássicos de batalha (Airhorn, Vitória, Buzzer, Moeda, "
            "Risada e Impacto). Arquivos já existentes não são sobrescritos.",
            confirm=True, confirm_label="Carregar"
        )
        if confirmed:
            self.soundboard.load_sample_sounds(overwrite=False)
            self._populate_soundboard_cards()

    def _add_custom_sound(self):
        """Opens the system file dialog to add an audio file to the soundboard."""
        file_path = filedialog.askopenfilename(
            title="Escolha um áudio para a soundboard",
            filetypes=[
                ("Arquivos de áudio", "*.mp3 *.wav *.ogg *.flac"),
                ("MP3", "*.mp3"),
                ("WAV", "*.wav"),
                ("Todos os arquivos", "*.*")
            ]
        )
        if not file_path:
            return
        dest = os.path.join(self.soundboard.sounds_dir, os.path.basename(file_path))
        try:
            shutil.copy2(file_path, dest)
            self.soundboard.reload_sounds()
            self._populate_soundboard_cards()
        except Exception as e:
            self._dialog("Não foi possível adicionar", str(e))

    def _combo(self, parent, values, command, width: Optional[int] = None) -> ctk.CTkComboBox:
        kwargs = {}
        if width:
            kwargs["width"] = width
        return ctk.CTkComboBox(
            parent, values=values, command=command,
            height=t.HEIGHT_CONTROL, font=t.body(), dropdown_font=t.body(),
            fg_color=t.BG_SURFACE_RAISED, button_color=t.BG_SURFACE_RAISED,
            button_hover_color=t.BG_SURFACE_HOVER, border_color=t.BORDER_SUBTLE,
            dropdown_fg_color=t.BG_SURFACE_RAISED, dropdown_hover_color=t.BG_SURFACE_HOVER,
            text_color=t.TEXT_PRIMARY, **kwargs
        )

    def _build_device_and_volumes_panel(self):
        """Bottom panel: audio routing, volume levels, and the studio voice chain."""
        bottom = ctk.CTkFrame(self, fg_color=t.BG_SURFACE, corner_radius=t.RADIUS_LG)
        bottom.pack(fill="x", padx=t.SPACE_5, pady=(0, t.SPACE_4))

        header = ctk.CTkFrame(bottom, fg_color="transparent")
        header.pack(fill="x", padx=t.SPACE_4, pady=(t.SPACE_4, t.SPACE_2))

        ctk.CTkLabel(header, text="Roteamento de áudio", font=t.title(), text_color=t.TEXT_PRIMARY).pack(side="left")

        ctk.CTkButton(
            header, text="Atualizar dispositivos", font=t.body(),
            fg_color=t.BG_SURFACE_RAISED, hover_color=t.BG_SURFACE_HOVER,
            text_color=t.TEXT_SECONDARY, height=t.HEIGHT_CONTROL_SM, width=170,
            corner_radius=t.RADIUS_MD, command=self.refresh_devices
        ).pack(side="right")

        ctk.CTkButton(
            header, text="Saída virtual", font=t.body(),
            fg_color=t.BG_SURFACE_RAISED, hover_color=t.BG_SURFACE_HOVER,
            text_color=t.BRAND_PRIMARY, height=t.HEIGHT_CONTROL_SM, width=130,
            corner_radius=t.RADIUS_MD, command=self._open_virtual_driver_modal
        ).pack(side="right", padx=(0, t.SPACE_2))

        # Alert shown when no virtual output exists (packed/unpacked dynamically)
        self.virtual_alert_frame = ctk.CTkFrame(
            bottom, fg_color=t.STATE_ERROR_DIM, corner_radius=t.RADIUS_MD
        )
        alert_text = ctk.CTkFrame(self.virtual_alert_frame, fg_color="transparent")
        alert_text.pack(side="left", fill="x", expand=True, padx=t.SPACE_3, pady=t.SPACE_2)
        ctk.CTkLabel(
            alert_text,
            text="Saída virtual não detectada — ela é necessária para enviar o som tratado ao TikTok LIVE Studio.",
            font=t.body(), text_color=t.STATE_ERROR, anchor="w", justify="left"
        ).pack(anchor="w", fill="x")

        self.install_cable_btn = ctk.CTkButton(
            self.virtual_alert_frame, text="Instalar saída virtual", font=t.body(True),
            fg_color=t.STATE_ERROR, hover_color=t.STATE_ERROR_HOVER,
            text_color=t.TEXT_ON_BRAND, height=t.HEIGHT_CONTROL, width=170,
            corner_radius=t.RADIUS_MD, command=self._install_virtual_driver_flow
        )
        self.install_cable_btn.pack(side="right", padx=t.SPACE_3, pady=t.SPACE_2)

        # Dropout warning, shown only while the stream error counters are non-zero
        self.audio_error_frame = ctk.CTkFrame(
            bottom, fg_color=t.BG_SURFACE_RAISED, corner_radius=t.RADIUS_MD
        )
        self.audio_error_label = ctk.CTkLabel(
            self.audio_error_frame, text="", font=t.body(),
            text_color=t.STATE_WARNING, anchor="w", justify="left"
        )
        self.audio_error_label.pack(anchor="w", fill="x", padx=t.SPACE_3, pady=t.SPACE_2)

        # Routing mismatch: the processed voice is going somewhere TikTok is not reading
        self.routing_alert_frame = ctk.CTkFrame(
            bottom, fg_color=t.BG_SURFACE_RAISED, corner_radius=t.RADIUS_MD
        )
        routing_text = ctk.CTkFrame(self.routing_alert_frame, fg_color="transparent")
        routing_text.pack(side="left", fill="x", expand=True, padx=t.SPACE_3, pady=t.SPACE_2)
        self.routing_alert_label = ctk.CTkLabel(
            routing_text, text="", font=t.body(), text_color=t.STATE_WARNING,
            anchor="w", justify="left", wraplength=620
        )
        self.routing_alert_label.pack(anchor="w", fill="x")
        ctk.CTkButton(
            self.routing_alert_frame, text="Usar o mesmo que o TikTok", font=t.body(True),
            fg_color=t.BRAND_PRIMARY, hover_color=t.BRAND_PRIMARY_HOVER,
            text_color=t.TEXT_ON_BRAND, height=t.HEIGHT_CONTROL, width=210,
            corner_radius=t.RADIUS_MD, command=self._fix_routing
        ).pack(side="right", padx=t.SPACE_3, pady=t.SPACE_2)

        # Device selectors
        self.dev_frame = ctk.CTkFrame(bottom, fg_color="transparent")
        self.dev_frame.pack(fill="x", padx=t.SPACE_4, pady=(t.SPACE_2, t.SPACE_2))

        input_devs, output_devs = self.engine.get_devices()
        self.input_dev_map = {d['name']: d['id'] for d in input_devs}
        self.output_dev_map = {d['name']: d['id'] for d in output_devs}

        col1 = ctk.CTkFrame(self.dev_frame, fg_color="transparent")
        col1.pack(side="left", fill="x", expand=True, padx=(0, t.SPACE_2))
        ctk.CTkLabel(col1, text="MICROFONE", font=t.label(), text_color=t.TEXT_TERTIARY).pack(anchor="w")
        self.mic_combo = self._combo(
            col1, list(self.input_dev_map.keys()) or ["Nenhum microfone encontrado"],
            self._on_device_changed
        )
        self.mic_combo.pack(fill="x", pady=(t.SPACE_1, 0))

        col2 = ctk.CTkFrame(self.dev_frame, fg_color="transparent")
        col2.pack(side="left", fill="x", expand=True, padx=t.SPACE_2)
        ctk.CTkLabel(col2, text="SAÍDA PARA O TIKTOK", font=t.label(), text_color=t.BRAND_PRIMARY).pack(anchor="w")
        self.virt_combo = self._combo(
            col2, list(self.output_dev_map.keys()) or ["Nenhuma saída encontrada"],
            self._on_device_changed
        )
        self.virt_combo.pack(fill="x", pady=(t.SPACE_1, 0))

        col3 = ctk.CTkFrame(self.dev_frame, fg_color="transparent")
        col3.pack(side="left", fill="x", expand=True, padx=(t.SPACE_2, 0))
        ctk.CTkLabel(col3, text="SEUS FONES", font=t.label(), text_color=t.TEXT_TERTIARY).pack(anchor="w")
        self.mon_combo = self._combo(
            col3, ["Nenhum (Desativado)"] + list(self.output_dev_map.keys()),
            self._on_device_changed
        )
        self.mon_combo.pack(fill="x", pady=(t.SPACE_1, 0))

        self._divider(bottom, pady=(t.SPACE_2, t.SPACE_3))

        # Volume levels
        volumes = ctk.CTkFrame(bottom, fg_color="transparent")
        volumes.pack(fill="x", padx=t.SPACE_4, pady=(0, t.SPACE_3))

        def volume_slider(parent, label_text, key, from_, to, command):
            box = ctk.CTkFrame(parent, fg_color="transparent")
            box.pack(side="left", fill="x", expand=True, padx=t.SPACE_1)
            ctk.CTkLabel(box, text=label_text.upper(), font=t.label(), text_color=t.TEXT_TERTIARY).pack(anchor="w")
            slider = ctk.CTkSlider(
                box, from_=from_, to=to, command=command,
                progress_color=t.BRAND_PRIMARY, button_color=t.TEXT_PRIMARY,
                button_hover_color=t.TEXT_PRIMARY, fg_color=t.BORDER_SUBTLE,
                height=t.HEIGHT_SLIDER
            )
            slider.pack(fill="x", pady=(t.SPACE_1, 0))
            self._enable_double_click_reset(slider, self.DEFAULT_CONFIG[key], command)
            return slider

        self.mic_vol_slider = volume_slider(volumes, "Mic", "mic_volume", 0.0, 2.0, self._on_mic_vol_changed)
        self.fx_vol_slider = volume_slider(volumes, "Efeito", "effect_volume", 0.0, 2.0, self._on_fx_vol_changed)
        self.sb_vol_slider = volume_slider(volumes, "Sons", "soundboard_volume", 0.0, 2.0, self._on_sb_vol_changed)
        self.mon_vol_slider = volume_slider(volumes, "Fone", "monitor_volume", 0.0, 1.5, self._on_mon_vol_changed)

        self._divider(bottom, pady=(0, t.SPACE_3))

        # Studio voice chain
        studio_header = ctk.CTkFrame(bottom, fg_color="transparent")
        studio_header.pack(fill="x", padx=t.SPACE_4, pady=(0, t.SPACE_2))

        ctk.CTkLabel(studio_header, text="VOZ DE ESTÚDIO", font=t.label(), text_color=t.TEXT_TERTIARY).pack(side="left", padx=(0, t.SPACE_4))

        def studio_switch(parent, text):
            return ctk.CTkSwitch(
                parent, text=text, font=t.body(), text_color=t.TEXT_SECONDARY,
                progress_color=t.BRAND_PRIMARY, button_color=t.TEXT_PRIMARY,
                button_hover_color=t.TEXT_PRIMARY, fg_color=t.BORDER_STRONG,
                command=self._on_studio_toggled
            )

        self.gate_switch = studio_switch(studio_header, "Noise Gate")
        self.gate_switch.pack(side="left", padx=(0, t.SPACE_4))
        self.eq_switch = studio_switch(studio_header, "EQ")
        self.eq_switch.pack(side="left", padx=(0, t.SPACE_4))
        self.comp_switch = studio_switch(studio_header, "Compressor")
        self.comp_switch.pack(side="left", padx=(0, t.SPACE_4))
        self.limit_switch = studio_switch(studio_header, "Limiter")
        self.limit_switch.pack(side="left")

        studio_sliders = ctk.CTkFrame(bottom, fg_color="transparent")
        studio_sliders.pack(fill="x", padx=t.SPACE_4, pady=(0, t.SPACE_4))

        def studio_slider(parent, label_text, key, from_, to, command, initial_text):
            ctk.CTkLabel(parent, text=label_text.upper(), font=t.label(), text_color=t.TEXT_TERTIARY).pack(side="left", padx=(0, t.SPACE_2))
            slider = ctk.CTkSlider(
                parent, from_=from_, to=to, command=command, width=110,
                progress_color=t.BRAND_PRIMARY, button_color=t.TEXT_PRIMARY,
                button_hover_color=t.TEXT_PRIMARY, fg_color=t.BORDER_SUBTLE,
                height=t.HEIGHT_SLIDER
            )
            slider.pack(side="left")
            value = ctk.CTkLabel(parent, text=initial_text, font=t.body(), text_color=t.BRAND_PRIMARY, width=52)
            value.pack(side="left", padx=(t.SPACE_2, t.SPACE_4))
            self._enable_double_click_reset(slider, self.DEFAULT_CONFIG[key], command)
            return slider, value

        self.gate_slider, self.gate_value_label = studio_slider(
            studio_sliders, "Gate", "gate_threshold_db", -60.0, -20.0, self._on_gate_threshold, "-40 dB")
        self.comp_slider, self.comp_value_label = studio_slider(
            studio_sliders, "Compressor", "comp_amount", 0.0, 1.0, self._on_comp_amount, "3.0:1")
        self.limit_slider, self.limit_value_label = studio_slider(
            studio_sliders, "Limiter", "limiter_ceiling_db", -6.0, 0.0, self._on_limiter_ceiling, "-1 dB")

    def _on_mic_vol_changed(self, val: float):
        self.engine.mic_volume = val
        self._save_app_config()

    def _on_fx_vol_changed(self, val: float):
        self.engine.effect_volume = val
        self._save_app_config()

    def _on_sb_vol_changed(self, val: float):
        self.engine.soundboard_volume = val
        self._save_app_config()

    def _on_mon_vol_changed(self, val: float):
        self.engine.monitor_volume = val
        self._save_app_config()

    def _on_studio_toggled(self):
        chain = self.engine.studio_chain
        chain.gate.enabled = bool(self.gate_switch.get())
        chain.eq.enabled = bool(self.eq_switch.get())
        chain.compressor.enabled = bool(self.comp_switch.get())
        chain.limiter.enabled = bool(self.limit_switch.get())
        self._save_app_config()

    def _on_gate_threshold(self, _=None):
        value = float(self.gate_slider.get())
        self.engine.studio_chain.gate.set_threshold_db(value)
        self.gate_value_label.configure(text=f"{value:.0f} dB")
        self._save_app_config()

    def _on_comp_amount(self, _=None):
        value = float(self.comp_slider.get())
        self.engine.studio_chain.compressor.set_amount(value)
        self.comp_value_label.configure(text=f"{1.0 + 5.0 * value:.1f}:1")
        self._save_app_config()

    def _on_limiter_ceiling(self, _=None):
        value = float(self.limit_slider.get())
        self.engine.studio_chain.limiter.set_ceiling_db(value)
        self.limit_value_label.configure(text=f"{value:.0f} dB")
        self._save_app_config()

    def refresh_devices(self):
        """Refreshes available audio devices and updates dropdowns."""
        input_devs, output_devs = self.engine.get_devices(include_all_apis=False)
        self.input_dev_map = {d['name']: d['id'] for d in input_devs}
        self.output_dev_map = {d['name']: d['id'] for d in output_devs}

        in_values = list(self.input_dev_map.keys()) or ["Nenhum microfone encontrado"]
        out_values = list(self.output_dev_map.keys()) or ["Nenhuma saída encontrada"]
        mon_values = ["Nenhum (Desativado)"] + list(self.output_dev_map.keys())

        self.mic_combo.configure(values=in_values)
        self.virt_combo.configure(values=out_values)
        self.mon_combo.configure(values=mon_values)

        cur_mic = self.mic_combo.get()
        cur_virt = self.virt_combo.get()
        cur_mon = self.mon_combo.get()

        if cur_mic not in self.input_dev_map and in_values:
            self.mic_combo.set(in_values[0])
        if cur_virt not in self.output_dev_map and out_values:
            self.virt_combo.set(out_values[0])
        if cur_mon != "Nenhum (Desativado)" and cur_mon not in self.output_dev_map:
            self.mon_combo.set(mon_values[0])

        self._check_virtual_driver_ui()
        self._start_engine()

    def _check_virtual_driver_ui(self):
        """Checks if a virtual output is present and toggles the alert banner."""
        if not hasattr(self, "virtual_alert_frame"):
            return
        is_installed = is_virtual_driver_installed()
        if not is_installed:
            if not self.virtual_alert_frame.winfo_ismapped():
                self.virtual_alert_frame.pack(fill="x", padx=15, pady=(2, 6), before=self.dev_frame)
        else:
            if self.virtual_alert_frame.winfo_ismapped():
                self.virtual_alert_frame.pack_forget()

    def _install_virtual_driver_flow(self, on_done_cb=None):
        """Dispatches silent virtual driver installation with elevated UAC."""
        confirmed = self._dialog(
            "Instalar saída virtual",
            "O app vai instalar a saída de áudio virtual em segundo plano.\n\n"
            "O Windows vai pedir autorização (UAC). Deseja continuar?",
            confirm=True, confirm_label="Instalar"
        )
        if not confirmed:
            return

        if hasattr(self, "install_cable_btn"):
            self.install_cable_btn.configure(text="Instalando...", state="disabled")

        def worker():
            success, msg = install_virtual_driver_silent()

            def on_finish():
                if hasattr(self, "install_cable_btn"):
                    self.install_cable_btn.configure(text="Instalar saída virtual", state="normal")
                self.refresh_devices()
                self._auto_select_devices()
                if success:
                    self._dialog("Saída virtual", msg)
                else:
                    self._dialog("Falha na instalação", msg)
                if on_done_cb:
                    on_done_cb()

            self.after(0, on_finish)

        threading.Thread(target=worker, daemon=True).start()

    def _open_virtual_driver_modal(self):
        """Opens the virtual audio driver manager."""
        info = get_virtual_driver_info()
        modal = ctk.CTkToplevel(self)
        modal.title("Saída de áudio virtual")
        modal.geometry("540x400")
        modal.resizable(False, False)
        modal.transient(self)
        modal.configure(fg_color=t.BG_CANVAS)

        content = ctk.CTkFrame(modal, fg_color=t.BG_SURFACE, corner_radius=t.RADIUS_LG)
        content.pack(fill="both", expand=True, padx=t.SPACE_4, pady=t.SPACE_4)

        ctk.CTkLabel(
            content, text="Saída de áudio virtual",
            font=t.title(), text_color=t.TEXT_PRIMARY
        ).pack(anchor="w", padx=t.SPACE_4, pady=(t.SPACE_4, t.SPACE_1))

        ctk.CTkLabel(
            content,
            text="Roteia a voz com efeitos e a soundboard para o TikTok LIVE Studio.",
            font=t.body(), text_color=t.TEXT_SECONDARY, justify="left", wraplength=460
        ).pack(anchor="w", padx=t.SPACE_4, pady=(0, t.SPACE_3))

        status_row = ctk.CTkFrame(content, fg_color="transparent")
        status_row.pack(fill="x", padx=t.SPACE_4)
        ctk.CTkLabel(status_row, text="Status", font=t.body(), text_color=t.TEXT_SECONDARY).pack(side="left")
        ctk.CTkLabel(
            status_row,
            text="Instalado e ativo" if info["installed"] else "Não detectado",
            font=t.body(True),
            text_color=t.BRAND_PRIMARY if info["installed"] else t.STATE_ERROR
        ).pack(side="right")

        device_row = ctk.CTkFrame(content, fg_color="transparent")
        device_row.pack(fill="x", padx=t.SPACE_4, pady=(t.SPACE_1, t.SPACE_3))
        ctk.CTkLabel(device_row, text="Dispositivo", font=t.body(), text_color=t.TEXT_SECONDARY).pack(side="left")
        ctk.CTkLabel(
            device_row,
            text=(info.get("active_device_name") or "Nenhum")[:38],
            font=t.body(), text_color=t.TEXT_PRIMARY
        ).pack(side="right")

        self._divider(content, pady=(0, t.SPACE_3))

        ctk.CTkLabel(
            content,
            text="Como configurar no TikTok LIVE Studio\n"
                 "1. Aqui: saída para o TikTok = CABLE Input\n"
                 "2. No TikTok LIVE Studio: microfone principal = CABLE Output",
            font=t.body(), text_color=t.TEXT_TERTIARY, justify="left"
        ).pack(anchor="w", padx=t.SPACE_4, pady=(0, t.SPACE_4))

        install_label = "Reinstalar driver" if info["installed"] else "Instalar driver"
        ctk.CTkButton(
            content, text=install_label, font=t.body(True),
            fg_color=t.BRAND_PRIMARY, hover_color=t.BRAND_PRIMARY_HOVER,
            text_color=t.TEXT_ON_BRAND, height=t.HEIGHT_CONTROL,
            corner_radius=t.RADIUS_MD,
            command=lambda: self._install_virtual_driver_flow(on_done_cb=modal.destroy)
        ).pack(fill="x", padx=t.SPACE_4)

        ctk.CTkButton(
            content, text="Fechar", font=t.body(),
            fg_color=t.BG_SURFACE_RAISED, hover_color=t.BG_SURFACE_HOVER,
            text_color=t.TEXT_SECONDARY, height=t.HEIGHT_CONTROL,
            corner_radius=t.RADIUS_MD, command=modal.destroy
        ).pack(fill="x", padx=t.SPACE_4, pady=(t.SPACE_2, t.SPACE_4))

    def _auto_select_devices(self):
        """Selects saved devices or automatically chooses best matches."""
        studio_info = self.bridge.get_studio_audio_info()
        studio_mic = studio_info.get("mic_name", "")

        # 1. Microphone Selection (Saved -> Physical Mic -> Fallback)
        saved_mic = self.app_config.get("input_device")
        is_saved_valid_mic = (
            saved_mic 
            and saved_mic in self.input_dev_map 
            and not any(bad in saved_mic.lower() for bad in ["saida", "mix", "loopback"])
        )
        if is_saved_valid_mic:
            self.mic_combo.set(saved_mic)
        else:
            found = False
            for name in self.input_dev_map:
                name_l = name.lower()
                # Skip loopback inputs like "Saida de Audio", "Record Mix", "Stream Mix"
                if any(bad in name_l for bad in ["saida", "mix", "loopback"]):
                    continue
                if any(t in name_l for t in ["mic in", "wave:1", "wave", "microfone", "mic"]):
                    self.mic_combo.set(name)
                    found = True
                    break
            if not found and self.input_dev_map:
                for name in self.input_dev_map:
                    if not any(bad in name.lower() for bad in ["saida", "mix", "loopback"]):
                        self.mic_combo.set(name)
                        found = True
                        break
                if not found:
                    self.mic_combo.set(list(self.input_dev_map.keys())[0])

        # 2. Virtual output: it must be the counterpart of whatever TikTok captures,
        #    otherwise the processed voice never reaches the live at all.
        saved_virt = self.app_config.get("virtual_output")
        saved_pairs = False
        if saved_virt and saved_virt in self.output_dev_map:
            saved_pairs, _ = self.bridge.check_pairing(saved_virt)

        if saved_pairs:
            self.virt_combo.set(saved_virt)
        else:
            derived = self._pick_virtual_output(studio_mic)
            if derived:
                self.virt_combo.set(derived)
            elif saved_virt and saved_virt in self.output_dev_map:
                self.virt_combo.set(saved_virt)
            elif self.output_dev_map:
                self.virt_combo.set(list(self.output_dev_map.keys())[0])

        self._check_virtual_driver_ui()

        # 3. Headphones Selection (Saved -> Headphones -> Speakers -> Nenhum)
        saved_mon = self.app_config.get("monitor_output")
        if saved_mon == "Nenhum (Desativado)":
            self.mon_combo.set(saved_mon)
        elif saved_mon and saved_mon in self.output_dev_map and not any(bad in saved_mon.lower() for bad in ["game", "cable", "system"]):
            self.mon_combo.set(saved_mon)
        else:
            found = False
            for name in self.output_dev_map:
                name_l = name.lower()
                if any(bad in name_l for bad in ["cable", "game", "virtual audio", "voice chat"]):
                    continue
                if any(term in name_l for term in ["headphones", "headphone", "fone", "alto-falante", "speakers"]):
                    self.mon_combo.set(name)
                    found = True
                    break
            if not found:
                self.mon_combo.set("Nenhum (Desativado)")

        self._start_engine()

    def _pick_virtual_output(self, studio_mic: str) -> Optional[str]:
        """Chooses the output that feeds the input TikTok LIVE Studio is capturing.

        VB-CABLE and Elgato Wave Link each expose a pair of endpoints: the app
        writes to one and the other application reads the matching one. Selecting
        anything else sends the processed voice somewhere nobody is listening,
        which looks exactly like "the effects do nothing".
        """
        names = list(self.output_dev_map.keys())
        if not names:
            return None

        # Normal playback endpoints that will never feed a virtual microphone
        generic = ("system", "game", "music", "browser", "headphone",
                   "alto-falante", "speakers", "steam streaming")

        def first_match(substrings, exclude=()):
            for sub in substrings:
                for name in names:
                    low = name.lower()
                    if sub in low and not any(bad in low for bad in exclude):
                        return name
            return None

        mic = (studio_mic or "").lower()

        # VB-CABLE: TikTok captures 'CABLE Output', so we must write to 'CABLE Input'.
        # The 16-channel endpoint is the same cable — use it when it is the only one.
        if "cable" in mic:
            return (first_match(("cable in",), exclude=generic + ("16ch",))
                    or first_match(("cable in",)))

        # Elgato Wave Link: TikTok captures a mix, applications feed a channel.
        if "elgato" in mic or "wave" in mic:
            return (first_match(("voice chat",))
                    or first_match(("wave",), exclude=("headphone",)))

        # Destination unknown: prefer a real virtual cable, then an Elgato channel.
        # ("virtual audio" is deliberately not a keyword here — Elgato names its
        # endpoints "Elgato Virtual Audio", which are not a cable.)
        cable = (first_match(("battle voice", "cable in", "voicemeeter"),
                            exclude=generic + ("16ch",))
                 or first_match(("battle voice", "cable in", "voicemeeter"),
                                exclude=generic))
        return cable or first_match(("voice chat", "wave link"))

    def _fix_routing(self):
        """Points the virtual output at whatever TikTok LIVE Studio is listening to."""
        derived = self._pick_virtual_output(self.bridge.get_studio_audio_info().get("mic_name", ""))
        if derived and derived in self.output_dev_map:
            self.virt_combo.set(derived)
            self._start_engine()

    def _update_routing_alert(self, paired: bool, message: str):
        """Shows a bar whenever the processed voice is not reaching TikTok's device."""
        if paired:
            if self._routing_bar_visible:
                self.routing_alert_frame.pack_forget()
                self._routing_bar_visible = False
            return

        self.routing_alert_label.configure(text=message)
        if not self._routing_bar_visible:
            self.routing_alert_frame.pack(
                fill="x", padx=t.SPACE_4, pady=(0, t.SPACE_2), before=self.dev_frame)
            self._routing_bar_visible = True

    def _start_engine(self):
        mic_name = self.mic_combo.get()
        virt_name = self.virt_combo.get()
        mon_name = self.mon_combo.get()

        mic_id = self.input_dev_map.get(mic_name)
        virt_id = self.output_dev_map.get(virt_name)
        mon_id = self.output_dev_map.get(mon_name) if mon_name != "Nenhum (Desativado)" else None

        if mic_id is None or virt_id is None:
            self.status_label.configure(
                text="Selecione um microfone e uma saída virtual.", text_color=t.STATE_WARNING)
            self._update_routing_alert(
                False, "Nenhuma saída virtual selecionada: o áudio tratado não está indo para o TikTok.")
            return

        try:
            self.engine.start(mic_id, virt_id, mon_id)
            self._save_app_config()
            paired, msg = self.bridge.check_pairing(virt_name)
            self.status_label.configure(
                text=msg, text_color=t.BRAND_PRIMARY if paired else t.STATE_WARNING)
            self._update_routing_alert(paired, msg)
        except Exception as e:
            self.status_label.configure(
                text=f"Erro ao iniciar áudio: {e}", text_color=t.STATE_ERROR)

    def _on_device_changed(self, _=None):
        self._start_engine()

    def select_voice_effect(self, effect_name: str):
        self.engine.set_effect(effect_name)
        self._last_highlighted_effect = effect_name
        self._update_voice_button_styles(effect_name)

        if effect_name == "normal":
            self.pitch_slider.set(0.0)
            self.pitch_val_label.configure(text="0 semitons (original)")

    def _on_pitch_slider(self, val: float):
        semitones = round(val, 1)
        self.pitch_val_label.configure(text=f"{semitones:+.1f} semitons")
        self.engine.set_effect("custom", semitones)
        self._last_highlighted_effect = "custom"
        self._update_voice_button_styles("custom")

    def _on_toggle_hear_myself(self):
        enabled = bool(self.hear_switch.get())
        self.engine.set_hear_myself(enabled)
        self._save_app_config()

    def _on_toggle_mute(self):
        self.engine.is_muted = not self.engine.is_muted
        if self.engine.is_muted:
            self.mute_btn.configure(
                text="Microfone mudo",
                fg_color=t.STATE_ERROR, hover_color=t.STATE_ERROR_HOVER,
                text_color=t.TEXT_ON_BRAND
            )
        else:
            self.mute_btn.configure(
                text="Microfone ativo",
                fg_color=t.BG_SURFACE_RAISED, hover_color=t.BG_SURFACE_HOVER,
                text_color=t.TEXT_PRIMARY
            )

    def _update_loop(self):
        # 1. Sync voice effect button highlights if changed via hotkeys
        current_eff = self.engine.active_effect
        if getattr(self, '_last_highlighted_effect', None) != current_eff:
            self._last_highlighted_effect = current_eff
            self._update_voice_button_styles(current_eff)

        # 2. Update the input level meter
        level = self.engine.current_input_level
        smoothed = self.vu_bar.get() * 0.4 + level * 0.6
        self.vu_bar.set(min(smoothed * 1.5, 1.0))
        self.vu_bar.configure(progress_color=t.STATE_ERROR if level > 0.9 else t.BRAND_PRIMARY)

        # 3. Surface dropouts reported by the audio callbacks
        self._update_audio_errors()

        self.after(50, self._update_loop)

    def _update_audio_errors(self):
        """Shows a warning bar while the stream callbacks report over/underflows."""
        errors = self.engine.get_stream_errors()
        total = sum(errors.values())

        if total == 0:
            if self._error_bar_visible:
                self.audio_error_frame.pack_forget()
                self._error_bar_visible = False
            self._last_error_count = 0
            return

        if total != self._last_error_count:
            parts = [name.replace('_', ' ') + f": {count}"
                     for name, count in errors.items() if count]
            self.audio_error_label.configure(
                text="Falhas de áudio detectadas (" + ", ".join(parts) + "). "
                     "Feche apps que consomem CPU ou aumente o bloco de áudio."
            )
            self._last_error_count = total

        if not self._error_bar_visible:
            self.audio_error_frame.pack(
                fill="x", padx=t.SPACE_4, pady=(0, t.SPACE_2), before=self.dev_frame)
            self._error_bar_visible = True

    def _update_voice_button_styles(self, effect_name: str):
        """Applies the shared 'selected' treatment (filled brand) to the active voice."""
        is_normal = (effect_name == "normal")
        self.normal_btn.configure(
            fg_color=t.BRAND_PRIMARY if is_normal else t.BG_SURFACE_RAISED,
            hover_color=t.BRAND_PRIMARY_HOVER if is_normal else t.BG_SURFACE_HOVER,
            text_color=t.TEXT_ON_BRAND if is_normal else t.TEXT_SECONDARY,
        )
        for key, button in self.voice_buttons.items():
            selected = (key == effect_name)
            button.configure(
                fg_color=t.BRAND_PRIMARY if selected else t.BG_SURFACE_RAISED,
                hover_color=t.BRAND_PRIMARY_HOVER if selected else t.BG_SURFACE_HOVER,
                text_color=t.TEXT_ON_BRAND if selected else t.TEXT_SECONDARY,
            )

    def destroy(self):
        self._save_app_config()
        self.engine.stop()
        super().destroy()
