def change_gameobject_color(r: float, g: float, b: float) -> str:
    """
    Change the color of the configured GameObject renderer in the Unity scene.
    RGB channels range from 0 to 255. The example scenes target the cube.

    Args:
        r: Red channel, from 0 to 255.
        g: Green channel, from 0 to 255.
        b: Blue channel, from 0 to 255.
    """
    from api_ws import send_to_unity_threadsafe
    send_to_unity_threadsafe({
        "type": "unity_tool",
        "data": {
            "tool_name": "change_gameobject_color",
            "color": {"r": r, "g": g, "b": b},
        },
    })
    return "Color change sent to Unity; application has not been confirmed."
