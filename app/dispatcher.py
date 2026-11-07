"""Part 4 student implementation."""

from app.backends import ansible, netconf, netmiko_textfsm, restconf
from app.core import error

def dispatch_request(data):
    """Dispatch an already validated request to the appropriate backend."""
    router = data["router"]
    method = data["method"]
    action = data["action"]
    desired_interface = data["desired"]["interface"]

    if method == "restconf":
        if action == "status":
            return restconf.get_interface_state(router, desired_interface["name"])
        elif action == "plan":
            return restconf.plan_interface(router, desired_interface)
        elif action == "apply":
            return restconf.apply_interface(router, desired_interface)

    elif method == "netconf":
        if action == "status":
            return netconf.get_interface_state(router, desired_interface["name"])
        elif action == "plan":
            return netconf.plan_interface(router, desired_interface)
        elif action == "apply":
            return netconf.apply_interface(router, desired_interface)

    elif method == "ansible":
        if action == "apply":
            return ansible.apply_interface(router, desired_interface)

    elif method == "netmiko-textfsm":
        if action == "status":
            return netmiko_textfsm.get_interface_state(router, desired_interface["name"])
        elif action == "delete":
            return netmiko_textfsm.delete_interface(router, desired_interface["name"])
    return error("invalid_action")
