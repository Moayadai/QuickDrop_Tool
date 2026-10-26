# QuickDrop Tool 🚀

A modern, high-performance desktop productivity and media utility built with **PyQt6** and **QtWebEngine**. QuickDrop features an interactive radial menu with drag-and-drop capabilities, persistent system tray integration, and deep **AI Image Detection** powered by Hugging Face neural models.

---

## ✨ Features

- **🧠 AI Image Detector**: Deep neural classification powered by Hugging Face (`umm-maybe/AI-image-detector`) to detect synthetic AI-generated images vs. authentic photography with real-time confidence scoring.
- **🎯 Dynamic Radial Menu**: 10-petal interactive SVG radial interface supporting multi-state modifier layers (Standard, `Ctrl`/`Cmd`, and `Shift`).
- **📱 Local AirDrop / Share**: Spin up an instant, zero-configuration local HTTP server with QR code generation for fast file transfers across devices on your local network.
- **🎨 Modern Annotation Studio**: Rich screenshot annotation canvas supporting freehand pen, geometric shapes (rectangles, ellipses), directional arrows, custom text placement, thickness control, and full color picking.
- **☕ Keep Awake Utility**: OS-level sleep prevention for Windows, macOS, and Linux, toggleable straight from the radial petal.
- **📌 Always-on-Top Floating Pin**: Pin QuickDrop or dropped assets to float above all active desktop windows.
- **⌨️ Global Summon Shortcut**: Summon or dismiss QuickDrop from anywhere on your system using `<Ctrl>+<Shift>+<Space>` (or customize via `pynput`).
- **🛡️ Persistent System Tray**: Runs silently in the background with quick toggle and exit actions.

---

## 📋 Prerequisites

- **Python**: Version `3.10` or higher
- **OS**: Linux (X11 / Wayland), macOS, or Windows
- **Hugging Face Account**: A free [Hugging Face User Access Token](https://huggingface.co/settings/tokens) for AI Image Detection.

---

## 🛠️ Installation & Setup

### 1. Clone the Repository
```bash
git clone https://github.com/your-username/QuickDrop-Tool.git
cd QuickDrop-Tool
```

### 2. Set Up a Virtual Environment
```bash
python3 -m venv venv
source venv/bin/activate      # On Linux/macOS
# or
venv\Scripts\activate         # On Windows
```

### 3. Install Dependencies
```bash
pip install --upgrade pip
pip install -r requirements.txt
```

---

## 🔑 Environment Configuration

QuickDrop requires a Hugging Face API key for the AI Image Detection feature.

1. Copy the example environment file:
   ```bash
   cp .env.example .env
   ```
2. Open `.env` and add your Hugging Face API token:
   ```env
   HF_API_KEY="your_huggingface_token_here"
   ```

> **Note:** `.env` is strictly excluded by `.gitignore` to protect your API credentials.

---

## 🚀 Running QuickDrop

Activate your virtual environment and launch the application:

```bash
python main.py
```
*(or `python QuickDrop_Tool/main.py` if running from the root repository)*

### Basic Usage & Shortcuts
- **Summon / Dismiss Window**: `<Ctrl>+<Shift>+<Space>`
- **Standard State**: Drag and drop any image or document onto the outer petals to convert or process.
- **Ctrl / Cmd State**: Hold `Ctrl` (or `Cmd` on macOS) to access the creative and AI suite:
  - Drop or click **AI DETECT** to inspect image authenticity.
  - Drop onto **ANNOTATE** to open the rich markup studio.
  - Drop onto **REDACT**, **WATERMARK**, **COMPRESS**, or **CROP**.
- **Shift State**: Hold `Shift` to reveal system tools:
  - **AIRDROP**: Share files locally via QR code.
  - **KEEP AWAKE**: Prevent OS sleep/idle display timeout.
  - **FLOAT**: Pin QuickDrop on top of other windows.

---

## 📂 Project Structure

```
QuickDrop-Tool/
├── .env.example         # Example environment configuration
├── .gitignore            # Git exclusion rules for secrets, caches, and venv
├── index.html            # Radial SVG menu & UI front-end
├── main.py               # PyQt6 core application, workers, and QtWebEngine bridge
├── qwebchannel.js        # QWebChannel bridge for JS-to-Python communication
├── requirements.txt      # Production dependencies
└── README.md             # Project documentation
```

---

## 🛡️ Security & Privacy

- All local file processing (annotations, format conversions, keep-awake commands) runs locally on your machine.
- For AI Image Detection, only the selected image is transmitted securely via HTTPS directly to Hugging Face's official inference endpoints using your personal token.
- No telemetry, analytics, or user tracking data is collected.

---

## 📄 License

This project is licensed under the MIT License - see the LICENSE file for details.
