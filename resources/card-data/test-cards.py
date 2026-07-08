"""
Attack ID lookup tool for the Pokemon TCG AI SDK.

This script lives at: pkmn-tcg-ai/resources/card-data/find_attacks.py
The sdk package lives at: pkmn-tcg-ai/sdk

Key insight from sdk/api.py:
- CardData.attacks is a list[int] of attack IDs a card can use (NOT full Attack objects).
- all_attack() returns the actual Attack dataclasses (attackId, name, text, damage, energies).
- So to find "what does attack X do" or "what attack IDs does card Y have",
  you need to join CardData.attacks against the table built from all_attack().

Run this from anywhere; it adds the project root to sys.path automatically.
"""

import os
import sys

# --- Make `sdk` importable regardless of current working directory ---
# This file: pkmn-tcg-ai/resources/card-data/find_attacks.py
# Project root (pkmn-tcg-ai) is two directories up.
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from sdk.api import all_card_data, all_attack, EnergyType  # noqa: E402


def build_tables():
    cards = all_card_data()
    attacks = all_attack()
    card_table = {c.cardId: c for c in cards}
    attack_table = {a.attackId: a for a in attacks}
    return card_table, attack_table


def format_attack(atk) -> str:
    if atk is None:
        return "  (attack ID not found in all_attack() table)"
    cost = ", ".join(EnergyType(e).name for e in atk.energies) if atk.energies else "none"
    text = f" | {atk.text}" if atk.text else ""
    return f"  attackId={atk.attackId:<6} name={atk.name!r:<25} damage={atk.damage:<4} cost=[{cost}]{text}"


def print_card_attacks(card_table, attack_table, card_id: int):
    card = card_table.get(card_id)
    if card is None:
        print(f"Card ID {card_id}: not found")
        return
    print(f"\n{card.name}  (cardId={card_id})")
    if not card.attacks:
        print("  (no attacks listed)")
        return
    for attack_id in card.attacks:
        print(format_attack(attack_table.get(attack_id)))


def find_cards_by_name(card_table, query: str):
    query = query.lower()
    return [c for c in card_table.values() if query in c.name.lower()]


def main():
    card_table, attack_table = build_tables()

    # --- Option 1: look up by known card ID ---
    # Fill this in with the cardIds you already use in your agent
    # (Ogerpon, Chikorita, Bayleef, Meganium, Applin, Hydrapple_Ex, etc.)
    ids_to_check = [96, 917, 918, 709, 710, 149, 150, 93, 1071, 920, 140, 655]

    print("=" * 60)
    print("LOOKUP BY CARD ID")
    print("=" * 60)
    for cid in ids_to_check:
        print_card_attacks(card_table, attack_table, cid)

    # --- Option 2: look up by (partial, case-insensitive) card name ---
    #print("\n" + "=" * 60)
    #print("LOOKUP BY NAME")
    #print("=" * 60)
    #names_to_check = [
    #    "Hydrapple", "Ogerpon", "Meganium", "Chikorita",
    #    "Bayleef", "Dipplin", "Applin",
    #]
    #for name in names_to_check:
    #    for card in find_cards_by_name(card_table, name):
    #        print_card_attacks(card_table, attack_table, card.cardId)

    # --- Option 3: dump EVERY attack in the game, sorted by ID ---
    # Uncomment if you want a full reference table.
    #
    # print("\n" + "=" * 60)
    # print("ALL ATTACKS")
    # print("=" * 60)
    # for attack_id in sorted(attack_table):
    #     print(format_attack(attack_table[attack_id]))


if __name__ == "__main__":
    main()