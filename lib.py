import os
import sys
from collections import defaultdict

from sdk.api import AreaType, CardType, Log, LogType, Observation, SelectContext, OptionType, Card, Pokemon, State, all_card_data, to_observation_class, EnergyType

file_path = "deck.csv"
if not os.path.exists(file_path):
    file_path = "/kaggle_simulations/agent/" + file_path
with open(file_path, "r") as file:
    csv = file.read().split("\n")
my_deck = []
for i in range(60):
    my_deck.append(int(csv[i]))

all_card = all_card_data()

card_table = {c.cardId:c for c in all_card}

Ogerpon = 96 # 4
Chikorita = 917 # 2
Bayleef = 918 or 709 # 2
Meganium = 710 # 2
Applin = 149 # 2
Hydrapple_Ex = 150 # 2
Dipplin = 93 # 2
Meowth_Ex = 1071 # 2
Tapu_Bulu = 920 # 1
Fezandipiti_Ex = 140 # 1
Celebi = 655 # 1
Lillie_Determination = 1227 # 4
Boss_Orders = 1182 # 2
Ciphermaniac_Codebreaking = 1188 # 1
Briar = 1201 # 1
Lana_Aid = 1184 # 1
Dawn = 1231 # 1
Bug_Catching_Set = 1094 # 4
Ultra_Ball = 1121 # 3
Poke_Pad = 1152 # 1
Night_Stretcher = 1097 # 1
Prime_Catcher = 1088 # 1
Forest_of_Vitality = 1261 # 4
Basic_Grass_Energy = 1 # 14
Rare_Candy = 1079 # 1

UNNECESSARY = -10000000

class AttackPlan:
    attack: int = 0
    counter: list[int] = []

can_attack = False
can_switch = False
can_main_attack = False
can_attach_energy = False
use_support = 0
can_bench_attack = False
pre_turn_log = []
current_turn_log = []
prize: list[int] = []
card_counts: defaultdict[int,int] = defaultdict(int)
serial_set: set[int] = set()
plan_a = AttackPlan()
plan_b = AttackPlan()

def no_damage_dex(id: int) -> bool:
    # Drednaw, Milotic ex, Sylveon, Crustle
    return id == 158 or id == 207 or id == 330 or id == 345

def no_damage_counter(pokemon: Pokemon) -> bool:
    # Poltchageist, Empoleon ex, Skeledirge, Milotic ex, Misty's Magikarp, Antique Cover Fossil
    if pokemon.id == 28 or pokemon.id == 199 or pokemon.id == 203 or pokemon.id == 207 or pokemon.id == 362 or pokemon.id == 1136:
        return True
    for card in pokemon.energyCards:
        # Mist Energy, Rock Fighting Energy
        if card.id == 11 or card.id == 20:
            return True
    return False

def prize_count(pokemon: Pokemon, is_attack_damage: bool) -> int:
    data = card_table[pokemon.id]
    count = 3 if data.megaEx else 2 if data.ex else 1
    if is_attack_damage:
        for card in pokemon.energyCards:
            if card.id == 12:  # Legacy Energy
                count -= 1
        for card in pokemon.tools:
            if card.id == 1172 and "Lillie" in data.name:  # Lillie’s Pearl
                count -= 1
    return max(0, count)

def pokemon_score(pokemon: Pokemon, is_attack_damage: bool) -> int:
    data = card_table[pokemon.id]
    score = prize_count(pokemon, is_attack_damage) * 1000
    score += len(pokemon.energies) * 150
    score += len(pokemon.tools) * 100
    if data.stage2:
        score += 250
    elif data.stage1:
        score += 130
    
    id = pokemon.id
    # Noctowl, Fan Rotom, Archaludon ex, Meowth ex
    if id == 173 or id == 174 or id == 190 or id == 1071:
        score -= 200
    if id == 112 and len(pokemon.energies) >= 1:  # Munkidori
        score += 300
    score += pokemon.hp
    return score


def add_card_count(card: Card | Pokemon | None, my_index: int):
    if card == None:
        return
    if isinstance(card, Pokemon) or card.playerIndex == my_index:
        if card.serial not in serial_set:
            card_counts[card.id] -= 1
            serial_set.add(card.serial)
    if isinstance(card, Pokemon):
        for c in card.energyCards:
            add_card_count(c, my_index)
        for c in card.tools:
            add_card_count(c, my_index)
        for c in card.preEvolution:
            add_card_count(c, my_index)

def set_card_counts(obs: Observation, my_index: int):
    card_counts.clear()
    serial_set.clear()
    for id in my_deck:
        card_counts[id] += 1
    
    state = obs.current
    my_state = state.players[my_index]
    for card in my_state.hand:
        add_card_count(card, my_index)
    for card in my_state.discard:
        add_card_count(card, my_index)
    for card in my_state.bench:
        add_card_count(card, my_index)
    for card in my_state.active:
        add_card_count(card, my_index)
    for card in state.stadium:
        add_card_count(card, my_index)
    if state.looking != None:
        for card in state.looking:
            add_card_count(card, my_index)
    add_card_count(obs.select.effect, my_index)

    
def get_card(obs: Observation, area: AreaType, index: int, player_index: int) -> Pokemon | Card | None:
    ps = obs.current.players[player_index]
    match area:
        case AreaType.DECK:
            return obs.select.deck[index]
        case AreaType.HAND:
            return ps.hand[index]
        case AreaType.DISCARD:
            return ps.discard[index]
        case AreaType.ACTIVE:
            return ps.active[index]
        case AreaType.BENCH:
            return ps.bench[index]
        case AreaType.PRIZE:
            return ps.prize[index]
        case AreaType.STADIUM:
            return obs.current.stadium[index]
        case AreaType.LOOKING:
            return obs.current.looking[index]
        case _:
            return None
        
def main_option_proc(obs: Observation, damage: int, bench_attacker: bool):
    state = obs.current
    select = obs.select
    my_index = state.yourIndex
    my_state = state.players[my_index]
    op_state = state.players[1 - my_index]

    global can_switch
    global can_attack
    global can_main_attack
    global can_energy_attach

    can_switch = False
    can_attack = False
    can_main_attack = False
    can_energy_attach = False
    for o in select.option:
        if o.type == OptionType.RETREAT:
            can_switch = True
        elif o.type == OptionType.ATTACK:
            can_attack = True
            if o.attackId == 120 or o.attackId == 195 or o.attackId == 115 or o.attackId == 1326:  # Ogerpon, Hydrapple, Dipplin, Tapu Bulu attacks
                can_main_attack = True
    
    plan_a.attack = -1
    plan_b.attack = -1
    if not can_main_attack and not (bench_attacker and can_switch):
        return
    
    cards = [op_state.active[0]]
    for pokemon in op_state.bench:
        cards.append(pokemon)
    counter_indices = []
    ci = []
    ci.append(0)
    remain_damage = 60
    while ci:
        index = ci[-1]
        hp = cards[index].hp
        if remain_damage >= hp:
            counter_indices.append(ci.copy())
            if index < len(cards) - 1:
                remain_damage -= hp
                ci.append(index + 1)
                continue
        if index == len(cards) - 1:
            ci.pop()
            if ci:
                remain_damage += cards[ci[-1]].hp
        if ci:
            ci[-1] += 1
    counter_indices.append([])

    remain_prize = len(my_state.prize)
    plan_score = 0
    for i, pokemon in enumerate(cards):
        base_prize_count = 0
        base_score = pokemon_score(pokemon, True)
        active_damage = 0 if no_damage_dex(pokemon.id) else damage
        if pokemon.hp <= active_damage:
            base_prize_count += prize_count(pokemon, True)
        else:
            base_score *= active_damage / pokemon.hp
        ci = []
        max_score = base_score
        if remain_prize <= base_prize_count:
            max_score = 50000
        else:
            for indices in counter_indices:
                if i in indices:
                    continue
                prize = base_prize_count
                score = base_score
                for index in indices:
                    prize += prize_count(cards[index], False)
                    score += pokemon_score(cards[index], False)
                if remain_prize <= prize:
                    score = 50000
                else:
                    if prize >= 2:
                        if remain_prize <= 4:
                            score -= 1200
                    elif prize == 1:
                        score -= 300
                    else:
                        score += 1200
                if max_score < score:
                    max_score = score
                    ci = indices
        if plan_score < max_score:
            plan_score = max_score
            plan_a.attack = i
            plan_a.counter = ci
        if i == 0:
            plan_b.attack = plan_a.attack
            plan_b.counter = plan_a.counter
            
def greedy_select_cards(select, obs, hand_counts, hand_score,deck_counts):
    """Pick up to select.maxCount cards for TO_HAND/TO_BENCH-style contexts,
    re-scoring after each pick so duplicates and diminishing returns are
    handled correctly regardless of option order."""
    remaining = list(enumerate(select.option))
    picked = []
    while remaining and len(picked) < select.maxCount:
        best_i = None
        best_score = None
        best_pos = None
        for pos, (i, o) in enumerate(remaining):
            card = get_card(obs, o.area, o.index, o.playerIndex)
            if card is None:
                continue
            s = hand_score(card.id, False)
            if best_score is None or s > best_score:
                best_score, best_i, best_pos = s, i, pos
        if best_i is None:
            break
        if best_score < 0 and len(picked) >= select.minCount:
            break
        picked.append(best_i)
        o = select.option[best_i]
        card = get_card(obs, o.area, o.index, o.playerIndex)
        hand_counts[card.id] += 1
        if o.area == AreaType.DECK:
            deck_counts[card.id] -= 1
        remaining.pop(best_pos)
    return picked
