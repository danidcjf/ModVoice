# Memory

## Project Overview
Aplicativo desktop em Python (Windows) de voice changer + soundboard para lives de batalha no
TikTok LIVE Studio. Veja @README.md para a visão geral, o roteamento de áudio e os atalhos.

Stack: numpy, scipy, sounddevice (PortAudio), soundfile (libsndfile), customtkinter, pynput.
Não há Node/npm neste projeto.

## Code Style Guidelines
- Use descriptive variable names
- Follow existing patterns in the codebase
- Extract complex conditions into meaningful boolean variables
- Comentários e docstrings em inglês; interface e documentação voltadas ao usuário em português
- Sem emoji como ícone na interface; cores, fontes e espaçamentos vêm de `src/theme.py`

## Architecture Notes

**Motor de áudio (`src/audio_engine.py`)** — três streams decoplados (mic, saída virtual, fones)
ligados por ring buffers. Regras que valem sempre dentro de um callback:
- nada de alocar arrays, `print`, I/O ou adquirir lock: o callback roda na thread de áudio
- buffers de scratch são pré-alocados em `_ensure_block_buffers` (um por thread)
- o ring buffer é SPSC lock-free com contadores monotônicos; o produtor nunca toca no índice do consumidor
- `VectorizedPitchShifter` reaproveita buffers internos: o retorno só vale até a próxima chamada

**Efeitos** — existem exatamente cinco: `normal`, `megaphone`, `walkie_talkie`, `panic` e `custom`
(o slider de Tom). Todo efeito precisa aparecer na UI, e todo efeito da UI precisa existir aqui.

**Caminhos (`src/paths.py`)** — a separação é o que faz o build funcionar:
- `resource_path()` para recursos empacotados e somente-leitura (resolvidos via `sys._MEIPASS` quando congelado)
- `user_data_dir()` para `%APPDATA%\BattleVoiceMod` (config e áudios do usuário)
- Nunca gravar ao lado do executável: em `Program Files` não há permissão

**Cadeia de estúdio (`src/dsp_chain.py`)** — gate, high-pass, EQ e compressor antes do efeito;
limiter depois da mixagem com a soundboard. A soundboard não passa por gate/compressor.

## Common Workflows

```bash
# Rodar do código-fonte
python main.py

# Verificar se o ambiente está completo (também funciona no .exe)
python main.py --selftest

# Compilar o executável (exige o venv de build com Python 3.11)
build.bat

# Regerar só o ícone
python tools/make_icon.py
```

**Build:** PyInstaller `--onedir` a partir de `BattleVoiceMod.spec`, usando o venv `.venv-build`
com Python 3.11 — o interpretador 3.14 do sistema é novo demais para empacotar de forma confiável.
O instalador fica em `installer/BattleVoiceMod.iss` (Inno Setup 6) e roda o setup do VB-CABLE
dentro do mesmo UAC da instalação.

**Roteamento:** o app deriva a saída virtual do que o TikTok LIVE Studio está captando. Se ele
escolher o dispositivo errado, o áudio tratado não chega à live mesmo com tudo funcionando — por
isso existe o aviso de roteamento na barra inferior.
