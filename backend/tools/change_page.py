from typing import Optional, Literal, Dict, Any, Union

Action = Literal["next", "previous", "goto"]


def change_page(action: Action, page_number: Optional[Union[int, str]] = None) -> str:
    """
    Show a printed page number or label in Unity, matching the document footer.
    Pass the page= label returned by retrieval unchanged, not its pdf_page= position.
    Never add or subtract an offset. Numeric labels are one-based; Roman labels such
    as "iii" can identify front matter. PDFs without labels use one-based positions.

    Valid calls:
      - change_page("next")
      - change_page("previous")
      - change_page("goto", page_number=3)

    Rules:
      - Exactly one action must be chosen via `action`.
      - `page_number` is required only for action="goto".
      - `page_number` must be a positive integer or nonempty printed page label for goto.
    """
    # Validate action
    if action not in ("next", "previous", "goto"):
        return (
            "Invalid request: 'action' must be one of ['next', 'previous', 'goto']. "
            "Examples: change_page('next') or change_page('goto', page_number=2)."
        )

    # Validate page_number usage
    if action == "goto":
        if page_number is None:
            return (
                "Invalid request: action='goto' requires 'page_number'. "
                "Example: change_page('goto', page_number=2)."
            )
        if (
            isinstance(page_number, bool)
            or not isinstance(page_number, (int, str))
            or not str(page_number).strip()
            or (str(page_number).lstrip("-").isdigit() and int(page_number) < 1)
        ):
            return (
                "Invalid request: 'page_number' must be a positive integer or printed page label. "
                "Example: change_page('goto', page_number=2)."
            )
    else:
        if page_number is not None:
            return (
                "Invalid request: 'page_number' is only allowed when action='goto'. "
                "Use change_page('next') or change_page('previous')."
            )

    # Build payload for Unity
    payload: Dict[str, Any] = {"type": "unity_tool", "data": {"tool_name": "change_page"}}

    if action == "next":
        payload["data"]["direction"] = "next"
    elif action == "previous":
        payload["data"]["direction"] = "previous"
    else:  # goto
        payload["data"]["page_number"] = str(page_number).strip()

    # Send
    try:
        from api_ws import request_unity_tool_threadsafe
        arguments = {key: value for key, value in payload["data"].items() if key != "tool_name"}
        result = request_unity_tool_threadsafe("change_page", arguments=arguments)
    except Exception as e:
        # Return a message that helps the agent understand what happened
        return f"Failed to change page due to an internal error: {e!r}. Please try again."

    if not result.get("ok"):
        return f"Page was not changed: {result.get('error', 'Unity could not display the page.')}"
    return (
        f"Displayed page {result['page_label']} "
        f"(PDF position {result['pdf_page']} of {result['page_count']})."
    )
