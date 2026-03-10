def hex_to_rgb(hex_val):
    """
    Convert a hex color value (string '#RRGGBB' or int/float) to an (R, G, B) tuple in [0, 1].
    Defaults to (1.0, 1.0, 1.0) on error.
    """
    if isinstance(hex_val, str):
        hex_str = hex_val.lstrip('#')
        if not hex_str:
            return (1.0, 1.0, 1.0)
        try:
            hex_int = int(hex_str, 16)
        except ValueError:
            return (1.0, 1.0, 1.0)
        r = ((hex_int >> 16) & 0xFF) / 255.0
        g = ((hex_int >> 8) & 0xFF) / 255.0
        b = (hex_int & 0xFF) / 255.0
        return (r, g, b)
    elif isinstance(hex_val, (int, float)):
        hex_int = int(hex_val)
        r = ((hex_int >> 16) & 0xFF) / 255.0
        g = ((hex_int >> 8) & 0xFF) / 255.0
        b = (hex_int & 0xFF) / 255.0
        return (r, g, b)
    return (1.0, 1.0, 1.0)
