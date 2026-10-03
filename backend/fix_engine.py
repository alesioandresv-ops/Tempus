import pathlib

base = pathlib.Path(__file__).parent
for rel in ["app/db/session.py", "app/workers/outbox.py"]:
    p = base / rel
    b = p.read_bytes()
    s = b.decode("utf-8")
    s = s.replace(
        "get_engine()\n        conn = await ENGINE.connect()",
        "engine = get_engine()\n        conn = await engine.connect()",
    )
    s = s.replace(
        "get_engine()\n    conn = await ENGINE.connect()",
        "engine = get_engine()\n    conn = await engine.connect()",
    )
    s = s.replace("ENGINE = get_engine()", "engine = get_engine()")
    s = s.replace("await ENGINE.connect()", "await engine.connect()")
    # caso donde ruff lo dejó como get_engine() suelto
    s = s.replace(
        "        get_engine()\n        conn = await engine.connect()",
        "        engine = get_engine()\n        conn = await engine.connect()",
    )
    s = s.replace(
        "    get_engine()\n    conn = await engine.connect()",
        "    engine = get_engine()\n    conn = await engine.connect()",
    )
    p.write_bytes(s.encode("utf-8"))
    print(f"fixed {rel}")
