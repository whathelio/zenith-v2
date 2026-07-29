"""Remove migrated endpoints from app.py and register all routers"""
from pathlib import Path

app_path = Path(__file__).parent.parent / "app.py"
content = app_path.read_text(encoding="utf-8")

# 1. Import all routers
old_imp = "from .routers import memories as memories_router"
new_imp = "from .routers import memories, notes, goals, schedules, distill, knowledge"
content = content.replace(old_imp, new_imp)

# 2. Delete notes section
idx = content.find("# API: Notes")
end = content.find("# API: Proposals")
if idx > 0:
    content = content[:idx] + "# API: Notes - migrated to routers/notes.py\n\n" + content[end:]

# 3. Delete goals section
idx = content.find("# API: Goals")
end = content.find("# API: Proposals")
if idx > 0:
    content = content[:idx] + "# API: Goals - migrated to routers/goals.py\n\n" + content[end:]

# 4. Delete schedules section
idx = content.find("@app.get(" + chr(34) + "/api/schedules" + chr(34) + ")")
if idx > 0:
    # Find start of next independent section
    end = content.find("@app.get(" + chr(34) + "/api/reminders" + chr(34) + ")", idx + 2000)
    if end < 0:
        end = content.find("@app.get(" + chr(34) + "/api/calendar" + chr(34) + ")", idx + 2000)
    if end > 0:
        section_start = content.rfind("# API:", 0, idx)
        content = content[:section_start] + "# Schedules/Calendar/Reminders - migrated to routers/schedules.py\n\n" + content[end:]

# 5. Delete distill section
idx = content.find("@app.post(" + chr(34) + "/api/distill/conversation" + chr(34) + ")")
if idx > 0:
    section_start = content.rfind("# API:", 0, idx)
    end = content.find("@app.get(" + chr(34) + "/api/memories" + chr(34) + ")", idx)
    if end < 0:
        end = content.find("# API: ", idx + 500)
    content = content[:section_start] + "# Distill - migrated to routers/distill.py\n\n" + content[end:]

# 6. Delete knowledge section
idx = content.find("@app.get(" + chr(34) + "/api/knowledge/health" + chr(34) + ")")
if idx > 0:
    section_start = content.rfind("#", 0, idx - 10)
    # Find where knowledge section ends
    end = content.find("@app.get(" + chr(34) + "/api/knowledge/status" + chr(34) + ")", idx)
    if end < 0:
        end = content.find("# API:", idx + 300)
    if end > 0:
        # Also remove /api/knowledge/status if present
        idx2 = content.find("@app.get(" + chr(34) + "/api/knowledge/status" + chr(34) + ")", end)
        if idx2 > 0:
            end2 = content.find("\n#", idx2 + 10)
            end = end2 if end2 > idx2 else end + 300
    content = content[:section_start] + "# Knowledge - migrated to routers/knowledge.py\n\n" + content[end:]

# 7. Update router registration
old_reg = "app.include_router(memories_router.router)"
new_reg = """app.include_router(memories.router)
app.include_router(notes.router)
app.include_router(goals.router)
app.include_router(schedules.router)
app.include_router(distill.router)
app.include_router(knowledge.router)"""
content = content.replace(old_reg, new_reg)

app_path.write_text(content, encoding="utf-8")
print("app.py cleaned and all 6 routers registered")
