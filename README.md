# Battle VoiceMod & Soundboard (TikTok LIVE Studio Edition)

[![Build & Release Installer](https://github.com/danidcjf/ModVoice/actions/workflows/deploy.yml/badge.svg)](https://github.com/danidcjf/ModVoice/actions/workflows/deploy.yml)
[![Python 3.11](https://img.shields.io/badge/python-3.11-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![GitHub Releases](https://img.shields.io/github/v/release/danidcjf/ModVoice?color=cyan&label=Instalador%20.exe)](https://github.com/danidcjf/ModVoice/releases)

Aplicativo de áudio de baixa latência com DSP vetorizado em tempo real, desenvolvido especificamente para **Lives de Batalha (PK Matches / Co-Host)** no **TikTok LIVE Studio**.

Conta com cadeia de voz de estúdio (Noise Gate, EQ, Compressor e Limiter), efeitos clássicos de batalha (Megafone, Walkie-Talkie com Roger Beep dinâmico, Voz de Pânico, Pitch Shift) e Soundboard de alta performance com atalhos globais.

---

## 🚀 Como Baixar e Usar

### Opção 1 — Baixar o Instalador Oficial (Recomendado)
Não precisa ter Python ou qualquer ferramenta de compilação instalada:
1. Acesse a aba **[Releases](https://github.com/danidcjf/ModVoice/releases)** do repositório.
2. Baixe o instalador mais recente: `BattleVoiceMod-Setup-X.X.X.exe`.
3. Execute o instalador. Ele já instala o aplicativo no sistema, cria os atalhos e configura o cabo de áudio virtual (**VB-CABLE**) sob a mesma elevação de administrador.

### Opção 2 — Rodar a partir do Código-Fonte
Para desenvolvedores:

```bash
# Clone o repositório
git clone https://github.com/danidcjf/ModVoice.git
cd ModVoice

# Crie e ative um ambiente virtual com Python 3.11
py -3.11 -m venv .venv
.venv\Scripts\activate

# Instale as dependências
pip install -r requirements.txt

# Inicie o aplicativo
python main.py
```

Para validar a integridade de todas as bibliotecas nativas de áudio e drivers:
```bash
python main.py --selftest
```

---

## 🛠️ Build Local & Deploy Automatizado

### Build Local com `build.bat`
Para compilar localmente na sua máquina:
1. Requisitos: Python 3.11 e [Inno Setup 6](https://jrsoftware.org/isdl.php) (opcional, para gerar o `Setup.exe`).
2. Execute:
```cmd
build.bat
```
O script gerará o executável em `dist\BattleVoiceMod\` e o instalador em `dist\installer\BattleVoiceMod-Setup-1.0.0.exe`.

### Deploy Contínuo no GitHub Actions
O repositório possui uma pipeline automatizada em [`.github/workflows/deploy.yml`](.github/workflows/deploy.yml) que roda em um runner Windows oficial:
- **A cada Push ou Pull Request** nas branches `main` e `master`: compila o app, executa o auto-teste (`--selftest`), compila o instalador via Inno Setup e disponibiliza o instalador `.exe` como artefato para download na aba **Actions**.
- **A cada Tag de Versão (`v*`)**: compila o instalador com a versão correspondente e publica automaticamente uma nova **GitHub Release** com o `.exe` pronto para os usuários finais.

#### Para lançar uma nova versão:
```bash
git tag v1.0.0
git push origin v1.0.0
```

---

## 📂 Onde ficam seus dados

Configurações e os áudios que você adiciona ficam salvos em **`%APPDATA%\BattleVoiceMod\`**, isolados dos arquivos do programa:

```
%APPDATA%\BattleVoiceMod\
├── app_config.json          (dispositivos, volumes, cadeia de estúdio)
├── soundboard_config.json   (quais sons estão ativos)
└── sounds\                  (seus áudios personalizados)
```

Isso garante que ao reinstalar ou atualizar o app por meio do instalador, suas preferências e sons permaneçam intactos.

---

## 🎙️ Roteamento de Áudio com Cabo Virtual

Para que sua voz tratada/modificada e os efeitos da soundboard cheguem aos espectadores sem eco e sem atraso, é necessário um **Cabo Virtual de Áudio** ([VB-CABLE](https://vb-audio.com/Cable/), já incluído no instalador):

```
[Microfone Físico] ─────────► [Battle VoiceMod (DSP & Soundboard)]
                                    │                     │
                                    ▼                     ▼
                     [Cabo Virtual (CABLE Input)]   [Seus Fones de Ouvido]
                                    │                (monitor em tempo real)
                                    ▼
                      [TikTok LIVE Studio]
                    (Microfone = CABLE Output)
```

1. Se já instalou via instalador `.exe`, o VB-CABLE já estará instalado.
2. Abra o **Battle VoiceMod** e selecione na barra inferior:
   - **Microfone**: seu microfone físico.
   - **Saída para o TikTok**: o cabo virtual (`CABLE Input`).
   - **Seus fones**: seu headset/fone para monitoramento da própria voz e dos sons.
3. No **TikTok LIVE Studio**, em **Configurações de Áudio**, defina o **Microfone Principal** como `CABLE Output`.

---

## 🎛️ Voz de Estúdio (Tratamento Broadcast)

Toda voz passa por uma cadeia de condicionamento em tempo real **antes** do efeito especial, e por um limiter transparente na saída:

```
[Mic] → Noise Gate → High-Pass 80Hz → EQ → Compressor → [Efeito] → + Soundboard → Limiter → [TikTok]
```

| Estágio | Finalidade |
|---|---|
| **Noise Gate** | Elimina ruídos de fundo (teclado mecânico, ventilador, ar-condicionado) antes dos efeitos |
| **High-Pass 80 Hz** | Remove ruídos graves, impactos na mesa e estouros de ar ("plosivas") |
| **EQ** | Atenua ressonâncias indesejadas (~300 Hz) e adiciona presença e inteligibilidade (~3 kHz) |
| **Compressor** | Equilibra sussurros e gritos de batalha no mesmo nível (padrão: -20 dB, 3:1) |
| **Limiter** | Garante que a soma da voz e dos efeitos sonoros nunca cause distorção (clipping) |

---

## 🎭 Efeitos de Batalha (PK)

Processamento digital de sinais vetorizado (NumPy/SciPy), garantindo latência ultra-baixa:

- **Normal**: voz limpa, transparente e tratada pela cadeia de estúdio.
- **Megafone**: reprodução acústica de corneta de estádio (passa-alta em 500 Hz, pico de ressonância em 2 kHz e saturação harmônica).
- **Walkie-Talkie**: filtro passa-faixa (400 Hz – 2.6 kHz), ruído dinâmico de radiofrequência e **Roger Beep + Squelch Tail** ao cessar a fala.
- **Pânico**: modulação de tom vibrato (±1.15 semitons a 6.2 Hz) com tremolo de amplitude.
- **Tom (Pitch)**: ajuste contínuo de tom de -12 a +12 semitons.

> **Dica:** Dê um clique duplo em qualquer controle deslizante para restaurar o valor de fábrica.

---

## 🔊 Soundboard

- **Thread-Safe**: mixagem contínua sem travamentos ou interferência na thread principal de áudio.
- **Formatos Compatíveis**: MP3, WAV, OGG e FLAC.
- **Atalhos Rápidos**: botão integrado para baixar memes instantâneos via MyInstants.
- **Áudios de Exemplo**: botão para gerar instantaneamente 6 sons clássicos (Airhorn, Vitória, Buzzer, Moeda, Risada, Impacto).

---

## ⌨️ Atalhos Globais

Os atalhos operam em segundo plano, mesmo enquanto o TikTok LIVE Studio ou um jogo estiver focado:

- `F1` : Voz Normal
- `F2` : Megafone
- `F3` : Walkie-Talkie
- `F4` : Voz de Pânico
- `F7` : Interromper todos os áudios da Soundboard imediatamente

---

## 📄 Licença

Distribuído sob a licença MIT. Consulte [`LICENSE`](LICENSE) para mais detalhes.
