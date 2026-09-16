"""
TikTok LIVE Studio Bridge.
Detects audio configuration directly from TikTok LIVE Studio's AppData,
verifying device pairing and providing battle gift triggers.
"""

import os
import json
from typing import Dict, Optional, Tuple


class TikTokStudioBridge:
    def __init__(self):
        self.appdata_dir = os.path.join(os.environ.get("APPDATA", ""), "TikTok LIVE Studio")
        self.services_file = os.path.join(self.appdata_dir, "TTStore", "services.json")

    def get_studio_audio_info(self) -> Dict:
        """
        Reads TikTok LIVE Studio configuration to detect the configured microphone device.
        """
        info = {
            "installed": os.path.exists(self.appdata_dir),
            "mic_name": None,
            "mic_device_id": None,
            "record_folder": None
        }

        if not os.path.exists(self.services_file):
            return info

        try:
            with open(self.services_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            stream_setting = data.get("StreamSetting", {}).get("state", {})
            audio_inputs = stream_setting.get("audioInputs", [])
            if audio_inputs and len(audio_inputs) > 0:
                primary = audio_inputs[0]
                info["mic_name"] = primary.get("name", "")
                info["mic_device_id"] = primary.get("deviceId", "")

            info["record_folder"] = stream_setting.get("recordFolder", "")
        except Exception as e:
            print(f"Erro ao ler configuracao do TikTok LIVE Studio: {e}")

        return info

    def check_pairing(self, selected_virtual_device_name: str) -> Tuple[bool, str]:
        """
        Checks if the selected virtual output in VoiceMod matches what TikTok LIVE Studio is listening to.
        """
        info = self.get_studio_audio_info()
        if not info["installed"]:
            return False, "TikTok LIVE Studio não encontrado na pasta padrão."

        studio_mic = info.get("mic_name", "")
        if not studio_mic:
            return False, "Nenhum microfone configurado no TikTok LIVE Studio."

        # Match known virtual cable pairs and audio routing
        s_lower = studio_mic.lower()
        v_lower = selected_virtual_device_name.lower()

        # VB-Cable: app sends to 'CABLE Input', TikTok captures from 'CABLE Output'
        if "cable" in s_lower and "cable" in v_lower:
            return True, f"Conectado ao TikTok LIVE Studio ({studio_mic})"

        # Elgato Wave Link / Virtual Audio
        if "elgato" in s_lower and "elgato" in v_lower:
            if ("voice chat" in s_lower and "voice chat" in v_lower) or ("wave" in s_lower and "wave" in v_lower):
                return True, f"Conectado ao TikTok LIVE Studio ({studio_mic})"

        # Voicemeeter
        if "voicemeeter" in s_lower and "voicemeeter" in v_lower:
            return True, f"Conectado ao TikTok LIVE Studio ({studio_mic})"

        # Battle VoiceMod branded device pair
        if "battle voice" in s_lower and "battle voice" in v_lower:
            return True, f"Conectado ao TikTok LIVE Studio ({studio_mic})"

        # Direct match or exact substring match (if not cross-brand)
        if (s_lower in v_lower or v_lower in s_lower) and not ("elgato" in s_lower and "cable" in v_lower) and not ("cable" in s_lower and "elgato" in v_lower):
            return True, f"Conectado ao TikTok LIVE Studio ({studio_mic})"

        return False, f"TikTok LIVE Studio está usando: '{studio_mic}'. Selecione o mesmo dispositivo aqui!"
