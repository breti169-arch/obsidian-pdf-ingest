# PDF Ingest Plugin for Obsidian

Ergänzt ein Kontextmenü **"PDF-Ingest"** für PDF-Dateien im Obsidian-Dateibaum, um diese über ein lokales Python-Skript (`pdf2md.py`) in sauberes, strukturiertes Markdown zu konvertieren. 

## Features
- **Kontextmenü:** Rechtsklick auf eine PDF-Datei im Dateibaum ➔ PDF-Ingest.
- **Echtzeit-Fortschritt:** Meldet den Status live in Obsidian (z. B. welche Seite gerade verarbeitet wird).
- **Semantische Textglättung:** Optionale Nutzung eines LLMs (z. B. DeepSeek) für perfektes Postprocessing (Bereinigung von Trennungsfehlern, Tabellenglättung, etc.).
- **Autarker Betrieb:** Das Python-Skript ist im Plugin enthalten und läuft out-of-the-box.

## Voraussetzungen
- Installiertes Python auf dem System.
- Die Python-Bibliotheken `pdfplumber`, `requests`, `dotenv` und `pillow` müssen installiert sein:
  ```bash
  pip install pdfplumber requests python-dotenv pillow
  ```

## Lizenz
MIT
