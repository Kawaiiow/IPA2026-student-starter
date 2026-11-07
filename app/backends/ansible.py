
from ipaddress import IPv4Interface
import json
import subprocess
from pathlib import Path
from app.core import error

def apply_interface(
    router,
    desired_interface,
    run_playbook=None,
):
    """Apply complete desired interface state using Ansible."""
    iface_obj = IPv4Interface(desired_interface["ipv4"])
    address = str(iface_obj.ip)
    mask = str(iface_obj.network.netmask)
    is_shutdown = (desired_interface.get("admin_state") == "down")

    variables = {
        "router": router,
        "interface_name": desired_interface["name"],
        "description": desired_interface["description"],
        "ipv4_address": address,
        "netmask": mask,
        "shutdown": is_shutdown,
    }

    try:
        if run_playbook is not None:
            run_playbook(variables)
        else:
            project_dir = Path(__file__).resolve().parents[2]
            inventory_path = project_dir / "ansible" / "inventory.ini"
            playbook_path = project_dir / "ansible" / "apply_interface.yml"

            cmd = [
                "ansible-playbook",
                "-i", str(inventory_path),
                str(playbook_path),
                "-e", f"ansible_host={router}",
                "-e", json.dumps(variables),
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if res.returncode != 0:
                out = res.stdout + res.stderr
                if "Authentication" in out or "Invalid username or password" in out:
                    return error("authentication_failed")
                if "UNREACHABLE" in out:
                    return error("connection_failed")
                return error("backend_failed")

        return {"status": "ok", "result": "applied"}
    except PermissionError:
        return error("authentication_failed")
    except ConnectionError:
        return error("connection_failed")
    except Exception:
        return error("backend_failed")
