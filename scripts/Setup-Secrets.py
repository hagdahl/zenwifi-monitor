import getpass
import keyring
from keyring.backends.Windows import WinVaultKeyring
SERVICE = "ZenWiFiMonitor"
if not isinstance(keyring.get_keyring(), WinVaultKeyring):
    raise RuntimeError("Windows Credential Manager is required; refusing to store secrets in another backend.")

def set_or_keep(name, prompt, secret=False):
    existing = keyring.get_password(SERVICE, name)
    value = getpass.getpass(prompt) if secret else input(prompt)
    if value:
        keyring.set_password(SERVICE, name, value)
        print(f"{name}: updated.")
    elif existing:
        print(f"{name}: existing value retained.")
    else:
        raise RuntimeError(f"{name} is required and no existing value is stored.")

print("ZenWiFi Monitor credential setup")
print("Enter a value to update it. Press Enter to retain an existing value.")
set_or_keep("router_username", "Router username: ")
set_or_keep("router_password", "Router password: ", secret=True)
configure_notion = input("Configure or update the optional Notion token? [y/N]: ").strip().lower()
if configure_notion in ("y", "yes"):
    set_or_keep("notion_token", "Notion integration token: ", secret=True)
else:
    print("notion_token: unchanged; Notion remains optional.")
print("Credential setup completed in Windows Credential Manager.")
