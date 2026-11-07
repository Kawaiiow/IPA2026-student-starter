import os
import re

from app.core import error

def _get_auth():
    return (
        os.environ.get("ROUTER_USER", ""),
        os.environ.get("ROUTER_PASS", ""),
    )

def _normalize_name(name):
    m = re.match(r"^(?:Loopback|Lo)(\d+)$", name, re.IGNORECASE)
    if m:
        return f"Loopback{m.group(1)}"
    return name

def get_interface_state(
    router,
    interface_name,
    netmiko_get=None,
):
    """Return normalized interface status using Netmiko + TextFSM."""
    try:
        if netmiko_get is not None:
            ip_info, descriptions = netmiko_get(router, interface_name)
        else:
            from netmiko import ConnectHandler
            user, password = _get_auth()
            device = {
                "device_type": "cisco_xe",
                "host": router,
                "username": user,
                "password": password,
                "timeout": 10,
            }
            with ConnectHandler(**device) as conn:
                ip_info = conn.send_command(
                    f"show ip interface {interface_name}", use_textfsm=True
                )
                descriptions = conn.send_command(
                    "show interfaces description", use_textfsm=True
                )
    except PermissionError:
        return error("authentication_failed")
    except ConnectionError:
        return error("connection_failed")
    except Exception as e:
        cls_name = e.__class__.__name__
        if "Authentication" in cls_name:
            return error("authentication_failed")
        if "Timeout" in cls_name or "Connection" in cls_name:
            return error("connection_failed")
        return error("backend_failed")

    if not isinstance(ip_info, list) or not ip_info:
        return {"status": "ok", "result": "not_found", "interface": None}

    matching_entry = None
    target_norm = _normalize_name(interface_name)
    for entry in ip_info:
        if _normalize_name(entry.get("interface", "")) == target_norm:
            matching_entry = entry
            break

    if not matching_entry:
        return {"status": "ok", "result": "not_found", "interface": None}

    # admin_state
    link_status = matching_entry.get("link_status", "").lower()
    admin_state = "down" if "administratively down" in link_status else "up"

    # ipv4
    ip_addrs = matching_entry.get("ip_address", [])
    prefixes = matching_entry.get("prefix_length", [])
    if isinstance(ip_addrs, list) and ip_addrs:
        ip = ip_addrs[0]
        prefix = prefixes[0] if (isinstance(prefixes, list) and prefixes) else "32"
        ipv4 = f"{ip}/{prefix}"
    elif isinstance(ip_addrs, str) and ip_addrs:
        ipv4 = f"{ip_addrs}/{prefixes}"
    else:
        ipv4 = None

    # description
    description = None
    if isinstance(descriptions, list):
        for desc_entry in descriptions:
            port = desc_entry.get("port", "")
            if _normalize_name(port) == target_norm:
                description = desc_entry.get("description")
                break

    return {
        "status": "ok",
        "result": "found",
        "interface": {
            "name": interface_name,
            "ipv4": ipv4,
            "description": description,
            "admin_state": admin_state,
        },
    }

def delete_interface(
    router,
    interface_name,
    netmiko_delete=None,
):
    """Delete an interface using Netmiko."""
    try:
        if netmiko_delete is not None:
            res = netmiko_delete(router, interface_name)
            if res == "deleted":
                return {"status": "ok", "result": "deleted"}
            elif res == "not_found":
                return {"status": "ok", "result": "not_found", "interface": None}
            else:
                return error("backend_failed")

        # Live path
        current = get_interface_state(router, interface_name)
        if current.get("status") == "error":
            return current
        if current.get("result") == "not_found":
            return {"status": "ok", "result": "not_found", "interface": None}

        from netmiko import ConnectHandler
        user, password = _get_auth()
        device = {
            "device_type": "cisco_xe",
            "host": router,
            "username": user,
            "password": password,
            "timeout": 10,
        }
        with ConnectHandler(**device) as conn:
            conn.send_config_set([f"no interface {interface_name}"])

        verify = get_interface_state(router, interface_name)
        if verify.get("result") == "not_found":
            return {"status": "ok", "result": "deleted"}
        return error("backend_failed")

    except PermissionError:
        return error("authentication_failed")
    except ConnectionError:
        return error("connection_failed")
    except Exception as e:
        cls_name = e.__class__.__name__
        if "Authentication" in cls_name:
            return error("authentication_failed")
        if "Timeout" in cls_name or "Connection" in cls_name:
            return error("connection_failed")
        return error("backend_failed")
