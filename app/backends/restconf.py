import os
from ipaddress import IPv4Interface, IPv4Network
from app.core import error
import requests

from app.backends.planning import plan_interface as generic_plan_interface

def _get_auth():
    return (
        os.environ.get("ROUTER_USER", ""),
        os.environ.get("ROUTER_PASS", ""),
    )

def get_interface_state(
    router,
    interface_name,
    http_get=None,
):
    """Return normalized interface status using RESTCONF."""
    url = f"https://{router}/restconf/data/ietf-interfaces:interfaces/interface={interface_name}"

    try:
        if http_get is not None:
            response = http_get(url)
        else:
            headers = {"Accept": "application/yang-data+json"}
            response = requests.get(
                url,
                headers=headers,
                auth=_get_auth(),
                verify=False,
                timeout=10,
            )
    except PermissionError:
        return error("authentication_failed")
    except (ConnectionError, requests.exceptions.ConnectionError, requests.exceptions.Timeout):
        return error("connection_failed")
    except Exception:
        return error("backend_failed")

    status_code = getattr(response, "status_code", 500)
    if status_code == 200:
        data = response.json()
        iface_data = data.get("ietf-interfaces:interface", {})
        name = iface_data.get("name", interface_name)
        description = iface_data.get("description")
        enabled = iface_data.get("enabled", True)
        admin_state = "up" if enabled else "down"

        ipv4_container = iface_data.get("ietf-ip:ipv4", {})
        address_list = ipv4_container.get("address", [])
        if address_list:
            primary_addr = address_list[0]
            ip_str = primary_addr.get("ip")
            mask_str = primary_addr.get("netmask")
            prefix = IPv4Network(f"0.0.0.0/{mask_str}").prefixlen
            ipv4_str = f"{ip_str}/{prefix}"
        else:
            ipv4_str = None

        return {
            "status": "ok",
            "result": "found",
            "interface": {
                "name": name,
                "ipv4": ipv4_str,
                "description": description,
                "admin_state": admin_state,
            },
        }
    elif status_code == 404:
        return {"status": "ok", "result": "not_found", "interface": None}
    elif status_code in (401, 403):
        return error("authentication_failed")
    else:
        return error("backend_failed")

def plan_interface(
    router,
    desired_interface,
    http_get=None,
):
    """Return create/update/no_change plan using RESTCONF status."""
    return generic_plan_interface(
        router,
        desired_interface,
        lambda r, name: get_interface_state(r, name, http_get=http_get)
    )

def apply_interface(
    router,
    desired_interface,
    http_put=None,
):
    """Apply complete desired interface state using RESTCONF."""
    name_str = desired_interface["name"]
    loopback_num = int(name_str[len("Loopback"):])
    url = f"https://{router}/restconf/data/Cisco-IOS-XE-native:native/interface/Loopback={loopback_num}"

    iface_obj = IPv4Interface(desired_interface["ipv4"])
    payload = {
        "Cisco-IOS-XE-native:Loopback": {
            "name": loopback_num,
            "description": desired_interface["description"],
            "ip": {
                "address": {
                    "primary": {
                        "address": str(iface_obj.ip),
                        "mask": str(iface_obj.network.netmask),
                    }
                }
            },
        }
    }
    if desired_interface.get("admin_state") == "down":
        payload["Cisco-IOS-XE-native:Loopback"]["shutdown"] = [None]

    try:
        if http_put is not None:
            response = http_put(url, json=payload)
        else:
            headers = {
                "Content-Type": "application/yang-data+json",
                "Accept": "application/yang-data+json",
            }
            response = requests.put(
                url,
                headers=headers,
                auth=_get_auth(),
                json=payload,
                verify=False,
                timeout=10,
            )
    except PermissionError:
        return error("authentication_failed")
    except (ConnectionError, requests.exceptions.ConnectionError, requests.exceptions.Timeout):
        return error("connection_failed")
    except Exception:
        return error("backend_failed")

    status_code = getattr(response, "status_code", 500)
    if status_code in (200, 201, 204):
        return {"status": "ok", "result": "applied"}
    elif status_code in (401, 403):
        return error("authentication_failed")
    else:
        return error("backend_failed")
