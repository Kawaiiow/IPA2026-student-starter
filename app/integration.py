"""Part 4 student implementation."""

from pathlib import Path
import yaml
from app.core import validate_request, error
from app.dispatcher import dispatch_request
from app.request_handler import validate_webex_envelope

def process_request(mentioned_people, attachment_path, bot_person_id):
    """
    Validate envelope, parse and validate YAML request, and dispatch.
    """
    envelope_err = validate_webex_envelope(mentioned_people, attachment_path, bot_person_id)
    if envelope_err is not None:
        return envelope_err

    try:
        with Path(attachment_path).open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except Exception:
        return error("invalid_yaml")

    val_res = validate_request(data)
    if val_res.get("status") == "error":
        return val_res

    return dispatch_request(data)
