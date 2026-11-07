from app.planner import build_plan

def plan_interface(
    router,
    desired_interface,
    get_interface_state,
):
    """Build a Part 2 plan from backend status."""
    status_resp = get_interface_state(router, desired_interface["name"])

    if status_resp.get("status") == "error":
        return status_resp

    if status_resp.get("result") == "found":
        current_interface = status_resp.get("interface")
    elif status_resp.get("result") == "not_found":
        current_interface = None
    else:
        return status_resp

    return build_plan(desired_interface, current_interface)
