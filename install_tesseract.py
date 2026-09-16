import urllib.request
import subprocess
import os

url = "https://github.com/UB-Mannheim/tesseract/releases/download/v5.4.0.20240606/tesseract-ocr-w64-setup-5.4.0.20240606.exe"
installer = "tesseract_installer.exe"
print("Downloading Tesseract...")
urllib.request.urlretrieve(url, installer)
print("Running installer silently...")
# Install to AppData to avoid needing Admin rights
install_dir = os.path.join(os.environ.get('LOCALAPPDATA', 'C:\\Users\\jishn\\AppData\\Local'), 'Programs', 'Tesseract-OCR')
subprocess.run([installer, "/SILENT", f"/DIR={install_dir}"], check=True)
print(f"Done installing to {install_dir}")
