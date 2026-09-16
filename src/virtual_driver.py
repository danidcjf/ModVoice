"""
Virtual Audio Device Manager for Battle VoiceMod.
Detects, installs, and customizes virtual audio devices (VB-Cable / Battle VoiceMod Virtual Output)
to route processed audio into TikTok LIVE Studio with 1-click installation.
"""

import os
import sys
import time
import shutil
import ctypes
import zipfile
import subprocess
import urllib.request
from typing import Tuple, Dict, Optional
import sounddevice as sd

from . import paths


OFFICIAL_DRIVER_URL = "https://download.vb-audio.com/Download_CABLE/VBCABLE_Driver_Pack43.zip"


def get_drivers_base_dir() -> str:
    """Absolute path to the bundled VB-CABLE driver package.

    Resolved through paths.resource_path so it also works from a frozen build,
    where the package lives inside the PyInstaller bundle.
    """
    return paths.resource_path("drivers", "vbcable")


def get_installer_executable() -> Optional[str]:
    """Returns path to 64-bit or 32-bit installer executable if present."""
    driver_dir = get_drivers_base_dir()
    is_64bit = sys.maxsize > 2**32
    exe_name = "VBCABLE_Setup_x64.exe" if is_64bit else "VBCABLE_Setup.exe"
    exe_path = os.path.join(driver_dir, exe_name)
    if os.path.exists(exe_path):
        return exe_path
    return None


def is_virtual_driver_installed() -> bool:
    """
    Checks if a virtual audio cable or custom branded device is available in the audio subsystem.
    """
    try:
        devices = sd.query_devices()
        keywords = ["cable", "battle voice", "virtual audio", "voicemeeter", "wave link", "vbcable"]
        for d in devices:
            name_l = d['name'].lower()
            if any(k in name_l for k in keywords):
                if d['max_output_channels'] > 0:
                    return True
    except Exception:
        pass
    return False


def get_virtual_driver_info() -> Dict:
    """
    Returns detailed diagnostics on the virtual driver installation status.
    """
    installed = is_virtual_driver_installed()
    installer_path = get_installer_executable()
    has_installer = installer_path is not None and os.path.exists(installer_path)

    active_device_name = None
    is_branded = False

    try:
        devices = sd.query_devices()
        for d in devices:
            name = d['name']
            name_l = name.lower()
            if "battle voice" in name_l and d['max_output_channels'] > 0:
                active_device_name = name
                is_branded = True
                break
            elif "cable in" in name_l and d['max_output_channels'] > 0:
                # Matches both "CABLE Input" and the "CABLE In 16ch" endpoint, which
                # is the same cable and is sometimes the only one Windows exposes.
                active_device_name = name
                break
    except Exception:
        pass

    return {
        "installed": installed,
        "active_device_name": active_device_name,
        "is_branded": is_branded,
        "has_installer_bundled": has_installer,
        "installer_path": installer_path
    }


def download_driver_if_needed(target_dir: Optional[str] = None) -> Tuple[bool, str]:
    """
    Downloads and extracts the official driver package if not already present.
    """
    if target_dir is None:
        target_dir = get_drivers_base_dir()

    exe_path = get_installer_executable()
    if exe_path and os.path.exists(exe_path):
        return True, exe_path

    os.makedirs(target_dir, exist_ok=True)
    zip_dest = os.path.join(target_dir, "driver_pack.zip")

    try:
        req = urllib.request.Request(
            OFFICIAL_DRIVER_URL,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        )
        with urllib.request.urlopen(req, timeout=15) as resp, open(zip_dest, "wb") as f:
            f.write(resp.read())

        with zipfile.ZipFile(zip_dest, "r") as z:
            z.extractall(target_dir)

        if os.path.exists(zip_dest):
            os.remove(zip_dest)

        exe = get_installer_executable()
        if exe and os.path.exists(exe):
            return True, exe
        return False, "Arquivo executável do instalador não encontrado após descompactação."
    except Exception as e:
        return False, f"Falha ao baixar pacote do driver: {e}"


def install_virtual_driver_silent() -> Tuple[bool, str]:
    """
    Executes the virtual driver installation silently with Administrator privileges (UAC prompt).
    """
    exe_path = get_installer_executable()
    if not exe_path or not os.path.exists(exe_path):
        success, msg = download_driver_if_needed()
        if not success:
            return False, f"Não foi possível obter o instalador: {msg}"
        exe_path = get_installer_executable()

    if not exe_path or not os.path.exists(exe_path):
        return False, "Instalador do driver não encontrado."

    working_dir = os.path.dirname(exe_path)

    # Execute via PowerShell Start-Process with -Verb RunAs for elevated UAC prompt
    # -i: install, -h: hide window (silent)
    ps_cmd = f"Start-Process -FilePath '{exe_path}' -ArgumentList '-i -h' -WorkingDirectory '{working_dir}' -Verb RunAs -Wait"

    try:
        res = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_cmd],
            capture_output=True,
            text=True,
            timeout=45
        )
        if res.returncode != 0:
            return False, f"Processo cancelado ou erro na instalação (código {res.returncode})."
    except subprocess.TimeoutExpired:
        return False, "Tempo limite excedido durante a instalação do driver."
    except Exception as e:
        return False, f"Erro ao disparar instalador: {e}"

    # Wait up to 8 seconds for Windows audio subsystem to register the new endpoint
    for _ in range(8):
        time.sleep(1)
        if is_virtual_driver_installed():
            return True, "Saída Virtual instalada e detectada com sucesso!"

    return True, "Instalação concluída! Se o dispositivo não aparecer imediatamente, reinicie o aplicativo."


def apply_device_branding(
    mic_friendly_name: str = "Battle VoiceMod - TikTok Mic",
    cable_in_friendly_name: str = "Battle VoiceMod - App Output"
) -> Tuple[bool, str]:
    """
    Customizes the display name (FriendlyName) of the virtual cable endpoints in the Windows Registry
    so TikTok LIVE Studio and Windows Settings show the application's branding.
    Requires elevated privileges (UAC).
    """
    ps_script = f"""
    $captureKeys = Get-ItemProperty 'HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\MMDevices\\Audio\\Capture\\*\\Properties' -ErrorAction SilentlyContinue | Where-Object {{ $_.'{{a45c254e-df1c-4efd-8020-67d146a850e0}},2' -like '*CABLE Output*' -or $_.'{{a45c254e-df1c-4efd-8020-67d146a850e0}},2' -like '*Battle Voice*' }}
    foreach ($k in $captureKeys) {{
        Set-ItemProperty -Path $k.PSPath -Name '{{a45c254e-df1c-4efd-8020-67d146a850e0}},2' -Value '{mic_friendly_name}'
    }}

    $renderKeys = Get-ItemProperty 'HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\MMDevices\\Audio\\Render\\*\\Properties' -ErrorAction SilentlyContinue | Where-Object {{ ($_.'{{a45c254e-df1c-4efd-8020-67d146a850e0}},2' -like '*CABLE Input*' -and $_.'{{a45c254e-df1c-4efd-8020-67d146a850e0}},2' -notlike '*16ch*') -or $_.'{{a45c254e-df1c-4efd-8020-67d146a850e0}},2' -like '*Battle Voice*' }}
    foreach ($k in $renderKeys) {{
        Set-ItemProperty -Path $k.PSPath -Name '{{a45c254e-df1c-4efd-8020-67d146a850e0}},2' -Value '{cable_in_friendly_name}'
    }}
    """

    try:
        ps_cmd = f"Start-Process -FilePath 'powershell.exe' -ArgumentList '-NoProfile -ExecutionPolicy Bypass -Command \"{ps_script.replace(chr(10), ' ')}\"' -Verb RunAs -Wait"
        res = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_cmd],
            capture_output=True,
            text=True,
            timeout=30
        )
        if res.returncode == 0:
            return True, f"Dispositivos personalizados com sucesso para '{mic_friendly_name}'!"
        return False, f"Falha ao personalizar nomes (código {res.returncode})."
    except Exception as e:
        return False, f"Erro ao aplicar marca no dispositivo: {e}"
