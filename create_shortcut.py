"""Create a desktop shortcut for Zenith v2."""
import os
import sys

def create_shortcut():
    target = os.path.join(os.path.dirname(os.path.abspath(__file__)), "zenith.bat")
    workdir = os.path.dirname(os.path.abspath(__file__))
    desktop = os.path.join(os.environ["USERPROFILE"], "Desktop", "Zenith v2.lnk")

    try:
        import win32com.client
        shell = win32com.client.Dispatch("WScript.Shell")
        shortcut = shell.CreateShortCut(desktop)
        shortcut.Targetpath = target
        shortcut.WorkingDirectory = workdir
        shortcut.IconLocation = r"C:\Windows\System32\shell32.dll,13"
        shortcut.Description = "Zenith v2 - Local AI Assistant"
        shortcut.save()
        print(f"OK: {desktop}")
        return True
    except ImportError:
        pass

    # Fallback: write a .url file (always works)
    url_path = os.path.join(os.environ["USERPROFILE"], "Desktop", "Zenith v2.url")
    with open(url_path, "w", encoding="utf-8") as f:
        f.write("[InternetShortcut]\n")
        f.write(f'URL=file:///{target.replace(os.sep, "/")}\n')
        f.write("IconIndex=13\n")
        f.write("IconFile=C:\\Windows\\System32\\shell32.dll\n")
    print(f"OK (url fallback): {url_path}")
    return True

if __name__ == "__main__":
    create_shortcut()
