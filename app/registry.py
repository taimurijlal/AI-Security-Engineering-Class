"""
Registry of every lab module in this app.

Adding Module 3 means writing app/modules/module3_x/module.py (a ModuleSpec)
and adding one line here -- nothing in app/core/, app/ui.py, or
app/main.py needs to change.
"""

from __future__ import annotations

from app.core.module_spec import ModuleSpec
from app.modules.module1_prompt_security.module import MODULE_SPEC as MODULE1_SPEC
from app.modules.module2_rag.module import MODULE_SPEC as MODULE2_SPEC
from app.modules.module3_mcp.module import MODULE_SPEC as MODULE3_SPEC
from app.modules.module4_agent.module import MODULE_SPEC as MODULE4_SPEC

MODULES: list[ModuleSpec] = [
    MODULE1_SPEC,
    MODULE2_SPEC,
    MODULE3_SPEC,
    MODULE4_SPEC,
]

MODULES_BY_ID: dict[str, ModuleSpec] = {spec.module_id: spec for spec in MODULES}
