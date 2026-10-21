"""
Shared CLI rendering shell for every lab module.

Mirrors app/core/ui_shell.py's split: this file knows the mechanics every
module shares (choose a user, run the prompt loop, log interactions).
Module-specific behaviour (what context the model receives, how to describe
retrieval evidence) stays in the module's own package and is injected
through its ModuleSpec.
"""

from __future__ import annotations

from app.core.audit import record_interaction
from app.core.llm import LLMError, get_provider
from app.core.module_spec import ModuleSpec
from app.core.security import DEMO_USERS


def choose_user():
    print("\nChoose a demo identity:")
    print("  1. Standard Employee")
    print("  2. Executive")
    choice = input("\nSelection [1]: ").strip() or "1"
    return DEMO_USERS["executive"] if choice == "2" else DEMO_USERS["employee"]


def run_module(spec: ModuleSpec) -> None:
    print("=" * 62)
    print("                 GLOBALTECH AI ASSISTANT")
    print("=" * 62)
    print(spec.lab_badge)
    print("All GlobalTech data in this lab is fictional.")

    user = choose_user()

    print("\n" + "-" * 62)
    print(f"Logged in as: {user.email}")
    print(f"Role: {user.role.upper()}")
    print("Type 'exit' to leave.")
    print("-" * 62)

    try:
        provider = get_provider()
    except LLMError as exc:
        print(f"\nSETUP ERROR: {exc}")
        return

    while True:
        try:
            prompt = input("\nYou: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye.")
            break

        if not prompt:
            continue
        if prompt.lower() in {"exit", "quit"}:
            print("Goodbye.")
            break

        context, evidence = spec.get_context(user, prompt)

        try:
            response = provider.generate(
                system_prompt=spec.system_prompt,
                user_prompt=prompt,
                context=context,
            )
            print(f"\nGlobalTech AI: {response}")
            if spec.describe_evidence_cli is not None and evidence is not None:
                for line in spec.describe_evidence_cli(evidence):
                    print(line)
            record_interaction(user, spec.module_id, prompt, response)
        except LLMError as exc:
            print(f"\nMODEL ERROR: {exc}")
