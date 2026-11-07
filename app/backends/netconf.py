import os
from ipaddress import IPv4Interface, IPv4Network
from app.core import error
import xml.etree.ElementTree as ET

from app.backends.planning import plan_interface as generic_plan_interface

IETF_INTERFACES_NS = "urn:ietf:params:xml:ns:yang:ietf-interfaces"
IETF_IP_NS = "urn:ietf:params:xml:ns:yang:ietf-ip"
NETCONF_BASE_NS = "urn:ietf:params:xml:ns:netconf:base:1.0"
CISCO_NATIVE_NS = "http://cisco.com/ns/yang/Cisco-IOS-XE-native"

def _get_auth():
    return (
        os.environ.get("ROUTER_USER", ""),
        os.environ.get("ROUTER_PASS", ""),
    )

def get_interface_state(
    router,
    interface_name,
    netconf_get_config=None,
):
    """Return normalized interface status using NETCONF."""
    filter_xml = f"""
    <interfaces xmlns="{IETF_INTERFACES_NS}">
      <interface>
        <name>{interface_name}</name>
      </interface>
    </interfaces>
    """

    try:
        if netconf_get_config is not None:
            xml_data = netconf_get_config(router, filter_xml)
        else:
            from ncclient import manager
            user, password = _get_auth()
            with manager.connect(
                host=router,
                port=830,
                username=user,
                password=password,
                hostkey_verify=False,
                device_params={"name": "iosxe"},
                timeout=10,
            ) as m:
                reply = m.get_config(source="running", filter=("subtree", filter_xml))
                xml_data = reply.data_xml
    except PermissionError:
        return error("authentication_failed")
    except ConnectionError:
        return error("connection_failed")
    except Exception as e:
        cls_name = e.__class__.__name__
        if "Authentication" in cls_name:
            return error("authentication_failed")
        if "SSHError" in cls_name or "Timeout" in cls_name:
            return error("connection_failed")
        return error("backend_failed")

    try:
        root = ET.fromstring(xml_data)
        iface = root.find(f".//{{{IETF_INTERFACES_NS}}}interface")
        if iface is None:
            return {"status": "ok", "result": "not_found", "interface": None}

        name_elem = iface.find(f"{{{IETF_INTERFACES_NS}}}name")
        name = name_elem.text if name_elem is not None else interface_name

        desc_elem = iface.find(f"{{{IETF_INTERFACES_NS}}}description")
        description = desc_elem.text if desc_elem is not None else None

        enabled_elem = iface.find(f"{{{IETF_INTERFACES_NS}}}enabled")
        enabled = (enabled_elem.text.lower() == "true") if enabled_elem is not None else True
        admin_state = "up" if enabled else "down"

        ip_elem = iface.find(f".//{{{IETF_IP_NS}}}address/{{{IETF_IP_NS}}}ip")
        mask_elem = iface.find(f".//{{{IETF_IP_NS}}}address/{{{IETF_IP_NS}}}netmask")

        if ip_elem is not None and mask_elem is not None:
            prefix = IPv4Network(f"0.0.0.0/{mask_elem.text}").prefixlen
            ipv4_str = f"{ip_elem.text}/{prefix}"
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
    except Exception:
        return error("backend_failed")

def plan_interface(
    router,
    desired_interface,
    netconf_get_config=None,
):
    """Return create/update/no_change plan using NETCONF status."""
    return generic_plan_interface(
        router,
        desired_interface,
        lambda r, name: get_interface_state(r, name, netconf_get_config=netconf_get_config),
    )

def apply_interface(
    router,
    desired_interface,
    netconf_edit_config=None,
):
    """Apply complete desired interface state using NETCONF."""
    name_str = desired_interface["name"]
    loopback_num = name_str[len("Loopback"):]
    iface_obj = IPv4Interface(desired_interface["ipv4"])
    address = str(iface_obj.ip)
    mask = str(iface_obj.network.netmask)
    description = desired_interface["description"]

    shutdown_xml = "<shutdown/>" if desired_interface.get("admin_state") == "down" else ""

    config_xml = f"""<config xmlns="{NETCONF_BASE_NS}" xmlns:nc="{NETCONF_BASE_NS}">
    <native xmlns="{CISCO_NATIVE_NS}">
        <interface>
        <Loopback nc:operation="replace">
            <name>{loopback_num}</name>
            <description>{description}</description>
            <ip>
            <address>
                <primary>
                <address>{address}</address>
                <mask>{mask}</mask>
                </primary>
            </address>
            </ip>
            {shutdown_xml}
        </Loopback>
        </interface>
    </native>
    </config>"""

    try:
        if netconf_edit_config is not None:
            netconf_edit_config(router, config_xml)
        else:
            from ncclient import manager
            user, password = _get_auth()
            with manager.connect(
                host=router,
                port=830,
                username=user,
                password=password,
                hostkey_verify=False,
                device_params={"name": "iosxe"},
                timeout=10,
            ) as m:
                m.edit_config(target="running", config=config_xml)
        return {"status": "ok", "result": "applied"}
    except PermissionError:
        return error("authentication_failed")
    except ConnectionError:
        return error("connection_failed")
    except Exception as e:
        cls_name = e.__class__.__name__
        if "Authentication" in cls_name:
            return error("authentication_failed")
        if "SSHError" in cls_name or "Timeout" in cls_name:
            return error("connection_failed")
        return error("backend_failed")
