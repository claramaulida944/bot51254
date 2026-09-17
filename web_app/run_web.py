"""
Launcher Script untuk Web Automation Bot Toodat / Quarterfull
Jalankan perintah: py web_app/run_web.py
"""

import os
import sys
import threading
import time
import webbrowser
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
if hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Setup paths
APP_DIR = Path(__file__).parent.resolve()
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

try:
    import uvicorn
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "uvicorn", "fastapi", "rich"])
    import uvicorn
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table

console = Console()

def open_browser(url: str):
    time.sleep(1.2)
    try:
        webbrowser.open(url)
    except Exception:
        pass

def main():
    port = 8000
    host = "127.0.0.1"
    url = f"http://{host}:{port}"

    table = Table(box=None, show_header=False, padding=(0, 2))
    table.add_column("Key", style="bold cyan")
    table.add_column("Val", style="bold white")

    table.add_row("[+] URL Aplikasi Web:", f"[bold green]{url}[/]")
    table.add_row("[+] Master PIN Owner:", "[bold yellow]100401naraA![/] (Bisa diubah di server.py)")
    table.add_row("[+] WhatsApp Owner:", "[bold green]https://wa.me/6287734343023[/]")
    table.add_row("[+] Skema Tarif Saldo:", "1 Akun Valid = Rp500 | 10 Guest = Rp500")
    table.add_row("[+] Folder Web App:", str(APP_DIR))

    console.print()
    console.print(
        Panel(
            table,
            title="[bold green]RINARADEV AUTOMATION CLOUD SERVER[/]",
            subtitle="[dim]Tekan Ctrl+C di terminal untuk mematikan server[/]",
            border_style="bright_blue",
            padding=(1, 2),
        )
    )
    console.print("[dim]Membuka browser otomatis...[/]\n")

    threading.Thread(target=open_browser, args=(url,), daemon=True).start()

    # Jalankan server Uvicorn
    uvicorn.run(
        "server:app",
        host=host,
        port=port,
        app_dir=str(APP_DIR),
        reload=True,
        log_level="info",
    )

if __name__ == "__main__":
    main()
