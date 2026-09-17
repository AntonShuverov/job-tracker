def error(code: str, message: str, **extra) -> dict:
    return {"error": code, "message": message, **extra}
