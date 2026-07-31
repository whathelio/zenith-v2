"""Create a desktop shortcut for Zenith v2.

使用 PowerShell WScript.Shell 生成 .lnk（不再依赖 pywin32）。
快捷方式指向 zenith.bat（统一启动入口）。
"""
import os
import subprocess
import sys


def create_shortcut():
    project_dir = os.path.dirname(os.path.abspath(__file__))
    target = os.path.join(project_dir, "zenith.bat")
    icon = os.path.join(project_dir, "zenith.ico")
    desktop = os.path.join(os.environ["USERPROFILE"], "Desktop", "Zenith v2.lnk")

    if not os.path.exists(target):
        print(f"[ERROR] Target not found: {target}")
        return False

    icon = icon if os.path.exists(icon) else r"C:\Windows\System32\shell32.dll,13"

    ps_script = (
        "$ws = New-Object -ComObject WScript.Shell; "
        f"$s = $ws.CreateShortcut('{desktop}'); "
        f"$s.TargetPath = '{target}'; "
        f"$s.WorkingDirectory = '{project_dir}'; "
        f"$s.IconLocation = '{icon}'; "
        f"$s.Description = 'Zenith v2 - Local AI Assistant'; "
        "$s.Save()"
    )

    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
            capture_output=True, text=True, check=False, timeout=30,
        )
        if result.returncode == 0 and os.path.exists(desktop):
            print(f"OK: {desktop}")
            print(f"    Target: {target}")
            return True
        print(f"[ERROR] PowerShell 创建快捷方式失败: {result.stderr.strip()}")
    except Exception as e:
        print(f"[ERROR] {e}")

    # Fallback: write a .url file (always works)
    url_path = os.path.join(os.environ["USERPROFILE"], "Desktop", "Zenith v2.url")
    with open(url_path, "w", encoding="utf-8") as f:
        f.write("[InternetShortcut]\n")
        f.write(f'URL=file:///{target.replace(os.sep, "/")}\n')
        f.write("IconIndex=0\n")
        icon_file = icon.split(",")[0] if "," in icon else icon
        f.write(f"IconFile={icon_file}\n")
    print(f"OK (url fallback): {url_path}")
    return True


if __name__ == "__main__":
    ok = create_shortcut()
    sys.exit(0 if ok else 1)
