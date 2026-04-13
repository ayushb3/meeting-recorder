# Meeting Recorder — Setup Guide

## 1. Install BlackHole 2ch

Download from: https://existential.audio/blackhole/
Run the installer. No reboot needed.

## 2. Configure Multi-Output Device

1. Open **Audio MIDI Setup** (Applications > Utilities)
2. Click **+** at bottom left > **Create Multi-Output Device**
3. Check both **BlackHole 2ch** and your speakers/headphones
4. Right-click the new device > **Use This Device for Sound Output**

Now Teams audio goes to your speakers AND gets captured by BlackHole.

## 3. Install whisper.cpp

```bash
git clone https://github.com/ggerganov/whisper.cpp
cd whisper.cpp
make
# Download a model:
bash ./models/download-ggml-model.sh large-v3
```

Note the path to the `whisper-cli` binary and update `config.toml`:
```toml
[whisper]
binary = "/path/to/whisper.cpp/whisper-cli"
model = "large-v3"
```

## 4. Install and start Ollama

Download from: https://ollama.com
```bash
ollama serve          # start the server
ollama pull llama3.2  # or whichever model you configured
```

## 5. Install Python dependencies

```bash
pip install -r requirements.txt
```

## 6. Configure the app

Edit `config.toml` to set your output directory and devices.

## 7. Run

```bash
python app.py
```
