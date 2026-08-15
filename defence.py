import os
import sys
from collections import defaultdict
from lib import greedy_select_cards ,no_damage_dex, no_damage_counter, prize_count, pokemon_score, add_card_count, set_card_counts, get_card, main_option_proc
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

def defence_agent(obs_dict: dict) -> list[int]:
    obs = to_observation_class(obs_dict)
    if obs.select == None:
        # In the initial selection, the obs.select is None, and it is necessary to return the deck.
        # The deck is a list of 60 card IDs.
        # The deck must comply with the Pokémon Trading Card Game rules.
        return my_deck

    global pre_turn_log
    global current_turn_log

    state = obs.current
    select = obs.select
    context = select.context
    my_index = state.yourIndex
    my_state = state.players[my_index]
    op_state = state.players[1 - my_index]
            
    if state.turn == 0:
        prize.clear()
        pre_turn_log.clear()
        current_turn_log.clear()
    else:
        for log in obs.logs:
            current_turn_log.append(log)
            if log.type == LogType.TURN_END:
                pre_turn_log = current_turn_log
                current_turn_log = []

    pre_ko = False
    no_item = False
    for log in pre_turn_log:
        if log.type == LogType.ATTACK:
            if log.attackId == 323:  # Itchy Pollen
                no_item = True
        elif log.type == LogType.MOVE_CARD:
            if (log.playerIndex == my_index
                and (log.fromArea == AreaType.BENCH or log.fromArea == AreaType.ACTIVE)
                and log.toArea == AreaType.DISCARD):
                pre_ko = True

    if select.deck != None:
        set_card_counts(obs, my_index)
        for card in select.deck:
            card_counts[card.id] -= 1
        prize.clear()
        for id in card_counts:
            for _ in range(card_counts[id]):
                prize.append(id)
                
    set_card_counts(obs, my_index)
    for id in prize:
        card_counts[id] -= 1
    deck_counts = card_counts

    prize_diff = len(my_state.prize) - len(op_state.prize)
    
    global bench_attacker

    # Number of cards per card ID on the Bench and in the Active Spot
    field_counts = defaultdict(int)
    # Number of cards per card ID in hand
    hand_counts = defaultdict(int)
    # Number of cards per card ID in discard pile
    discard_counts = defaultdict(int)
    
    active_id = 0
    bench_attacker = False
    can_evolve_applin = False
    evolve_applin_count = 0
    can_evolve_chikorita = False
    evolve_chikorita_count = 0
    can_evolve_dipplin = False
    can_evolve_bayleef = False
    hydrapple_ability = False
    damage = 30 # TODO change damage based on number of energies (eg if ogerpon in play add my active and op active energies, if hydrapple add all my active, if meganium in play double all my counts)

    my_active = my_state.active[0] if len(my_state.active) > 0 else None
    op_active = op_state.active[0] if len(op_state.active) > 0 else None

    def can_retreat(pokemon: Pokemon):
        energy_count = len(pokemon.energies)
        if pokemon.id == Applin:
            if energy_count >= 1:
                return True
        if pokemon.id == Dipplin:
            if energy_count >= 2:
                return True
        if pokemon.id == Hydrapple_Ex:
            if energy_count >= 3:
                return True
        if pokemon.id == Chikorita:
            if energy_count >= 1:
                return True
        if pokemon.id == Bayleef:
            if energy_count >= 2:
                return True
        if pokemon.id == Meganium:
            if energy_count >= 2:
                return True
        if pokemon.id == Ogerpon:
            if energy_count >= 1:
                return True
        if pokemon.id == Tapu_Bulu:
            if energy_count >= 3:
                return True
        if pokemon.id == Meowth_Ex:
            if energy_count >= 1:
                return True
        if pokemon.id == Fezandipiti_Ex:
            if energy_count >= 1:
                return True
        if pokemon.id == Celebi:
            if energy_count >= 1:
                return True
        return False
                
    def damage_calc(active_id: int, my_active: Pokemon, op_active: Pokemon) -> int:
        damage = 30
        if active_id == Hydrapple_Ex:
            energy_count = 0
            if my_active is not None:
                energy_count += sum(1 for e in my_active.energies if e == EnergyType.GRASS)
            for pokemon in my_state.bench:
                energy_count += sum(1 for e in pokemon.energies if e == EnergyType.GRASS)
            damage = 30 + (30 * energy_count)
        elif active_id == Ogerpon:
            energy_count = 0
            if my_active is not None:
                energy_count += len(my_active.energies)
            if op_active is not None:
                energy_count += len(op_active.energies)
            damage = 30 + (30 * energy_count)
        elif active_id == Tapu_Bulu:
            damage = 220
        elif active_id == Dipplin:
            benched = len(my_state.bench)
            damage = 2 * (20 * benched)
        elif active_id == Meganium:
            damage = 140
        elif active_id == Applin:
            damage = 20
        elif active_id == Chikorita:
            damage = 30 
        else:
            damage = 30

        op_data = card_table[op_active.id]
        attacker_data = card_table[active_id]
        if op_data.weakness is not None and op_data.weakness == attacker_data.energyType:
            damage *= 2
        if op_data.resistance is not None and op_data.resistance == attacker_data.energyType:
            damage -= 30

        return damage

    def can_attack_now(pokemon: Pokemon) -> bool:

        energy_count = len(pokemon.energies)

        if pokemon.id == Ogerpon:
            return energy_count >= 3
        elif pokemon.id == Tapu_Bulu:
            return energy_count >= 4
        elif pokemon.id == Chikorita:
            return energy_count >= 1
        elif pokemon.id == Bayleef:
            return energy_count >= 2
        elif pokemon.id == Meganium:
            return energy_count >= 4
        elif pokemon.id == Applin:
            return energy_count >= 1
        elif pokemon.id == Dipplin:
            return energy_count >= 2
        elif pokemon.id == Hydrapple_Ex:
            return energy_count >= 2
        elif pokemon.id == Meowth_Ex:
            return energy_count >= 3
        elif pokemon.id == Fezandipiti_Ex:
            return energy_count >= 3
        elif pokemon.id == Celebi:
            return energy_count >= 1    

        return False

    def better_bench_attacker(active_id: int, my_active: Pokemon, op_active: Pokemon) -> int:
        if my_state.bench is None:
            return False
        if can_attack_now(my_active):
            active_damage = damage_calc(active_id, my_active, op_active)
            if my_active.id == Ogerpon or my_active.id == Hydrapple_Ex:
                active_damage += 30
        else:
            active_damage = 0

        for pokemon in my_state.bench:
            if can_attack_now(pokemon):
                bench_damage = damage_calc(pokemon.id, pokemon, op_active)
                if pokemon.id == Ogerpon or pokemon.id == Hydrapple_Ex:
                    bench_damage += 30
                if bench_damage > active_damage:
                    return True
        return False

    def all_ogerpon_can_attack() -> bool:
        for card in my_state.active:
            if card is not None and card.id == Ogerpon and not can_attack_now(card):
                return False
        for card in my_state.bench:
            if card is not None and card.id == Ogerpon and not can_attack_now(card):
                return False
        return True

    def highest_energy_ogerpon(pokemon: Pokemon) -> bool:
        energy_count = len(pokemon.energies)
        for card in my_state.active:
            if card is not None and card.id == Ogerpon and card.serial != pokemon.serial and len(card.energies) > energy_count:
                return False
        for card in my_state.bench:
            if card is not None and card.id == Ogerpon and card.serial != pokemon.serial and len(card.energies) > energy_count:
                return False
        return True

    for card in my_state.active:
        if card == None:
            continue
        active_id = card.id
        field_counts[card.id] += 1
        if not card.appearThisTurn:
            if card.id == Applin:
                can_evolve_applin = True
                evolve_applin_count += 1
            elif card.id == Dipplin:
                can_evolve_dipplin = True
            elif card.id == Chikorita:
                can_evolve_chikorita = True
                evolve_chikorita_count += 1
            elif card.id == Bayleef:
                can_evolve_bayleef = True
    for card in my_state.bench:
        field_counts[card.id] += 1
        if not card.appearThisTurn:
            if card.id == Applin:
                can_evolve_applin = True
                evolve_applin_count += 1
            elif card.id == Dipplin:
                can_evolve_dipplin = True
            elif card.id == Chikorita:
                can_evolve_chikorita = True
                evolve_chikorita_count += 1
            elif card.id == Bayleef:
                can_evolve_bayleef = True
        if card.id == Hydrapple_Ex and len(card.energies) >= 2:
            bench_attacker = True
        elif card.id == Ogerpon and len(card.energies) >= 3:
            bench_attacker = True
        elif card.id == Tapu_Bulu and len(card.energies) >= 4:
            bench_attacker = True
    main_pokemon_count = field_counts[Applin] + field_counts[Dipplin] + field_counts[Hydrapple_Ex] + field_counts[Ogerpon] + field_counts[Chikorita] + field_counts[Bayleef] + field_counts[Meganium]
    
    stadium_id = 0
    for card in state.stadium:
        stadium_id = card.id

    support_count = 0
    for card in my_state.discard:
        discard_counts[card.id] += 1

    def do_switch():
        # TODO ADD THREAT DETECTION HERE FOR MORE DYNAMIC SWITCHING
        bench_ex = 0
        attacker = 0
        if op_active is not None and my_active is not None and can_attack_now(my_active) and damage_calc(my_active.id, my_active, op_active) >= op_active.hp:
            return False
        elif my_active is not None and not can_attack_now(my_active) and better_bench_attacker(my_active.id, my_active, op_active):
            for p in my_state.bench:
                if can_attack_now(p):
                    attacker += 1
                    if prize_count(p, True) > 1:
                        bench_ex += 1
                    if op_active is not None:
                        if damage_calc(p.id, p, op_active) >= op_active.hp:
                            bench_ex -= 1 
                if attacker == bench_ex:
                    return False
            return True
        elif my_active.id == Meganium and field_counts[Meganium] <= 1:
            if op_active is not None and can_attack_now(my_active) and damage_calc(my_active.id, my_active,op_active) >= op_active.hp:
                return False
            return True
        else:
            if my_active is not None:
                if my_active.id == Hydrapple_Ex or my_active.id == Ogerpon or my_active.id == Meowth_Ex or my_active.id == Fezandipiti_Ex:
                    if op_active is not None and (op_active.id == 345 or op_active.id == 330):
                        return True
                    elif op_active is not None and (op_active.id == 678):
                        if my_active.id == Hydrapple_Ex and my_active.hp > 270:
                            return False
                        else:
                            return True
                    elif op_active is not None and (op_active.id == 723):
                        if my_active.hp <= 200:
                            return True
                        else:
                            return False
                    elif op_active is not None and (op_active.id == 1031):
                        if my_active.hp <= 210:
                            return True
                        else:
                            return False
                    if my_active.hp <= 100:
                        if op_active is not None and not better_bench_attacker(my_active.id,my_active,op_active) and damage_calc(my_active.id, my_active, op_active) >= (op_active.hp / 2):  
                            return False
                        else:
                            return True
                else:
                    if my_active.id != Applin and my_active.hp <= 40:
                        return True
                    elif my_active.id == Applin and my_active.hp <= 20 and hand_counts[Dipplin] < 1:
                        return True
            return False
        
    def attach_score(attach_id: int, pokemon: Pokemon, active: bool) -> int:
        score = 4000
        if attach_id == 0:
            return -1
        energy_count = len(pokemon.energies)

        if pokemon.id == Meowth_Ex:
            if active and not can_retreat(pokemon) and not my_state.asleep and not my_state.paralyzed:
                if better_bench_attacker(pokemon.id, pokemon, op_active):
                    score += 750
                else:
                    score += 400
            elif active and can_retreat(pokemon) and not my_state.asleep and not my_state.paralyzed:
                if not better_bench_attacker(pokemon.id, pokemon, op_active):
                    score += 300
                else:
                    score += 200
            elif not active:
                score += 60 
        elif pokemon.id == Fezandipiti_Ex:
            if active and not can_retreat(pokemon) and not my_state.asleep and not my_state.paralyzed:
                if better_bench_attacker(pokemon.id, pokemon, op_active):
                    score += 750
                else:
                    score += 400

            elif active and can_retreat(pokemon) and not my_state.asleep and not my_state.paralyzed:
                if not better_bench_attacker(pokemon.id,pokemon,op_active):
                    score += 300
                else:
                    score += 200
            elif not active:
                score += 40
        elif pokemon.id == Celebi:
            if active and not can_retreat(pokemon) and not my_state.asleep and not my_state.paralyzed:
                    score += 750
        elif pokemon.id == Hydrapple_Ex:
            if active and not my_state.asleep and not my_state.paralyzed:
                if not can_attack_now(pokemon):
                    score += 600
                elif not can_retreat(pokemon) and better_bench_attacker(pokemon.id, pokemon, op_active):
                    score += 450
                else:
                    score -= 250
            elif not active:
                if not can_attack_now(pokemon):
                    score += 250
            else:
                score += 90
            if op_active.id == 678 and pokemon.hp >= 270:
                score += 100
            
        elif pokemon.id == Ogerpon:
            num_ops = 0
            ko_ops = 0
            if active and not my_state.asleep and not my_state.paralyzed:
                if not can_attack_now(pokemon):
                    score += 550
                elif not can_retreat(pokemon) and better_bench_attacker(pokemon.id, pokemon, op_active):
                    score += 400
            elif not active:
                if not can_attack_now(pokemon):
                    score += 350
                elif damage_calc(pokemon.id, pokemon, op_active) < op_active.hp:
                    score += 300
            
                if energy_count is not None:
                    score += energy_count * 15
            else:
                score += 100
            if op_active is not None and my_active is not None and my_active.id is not Hydrapple_Ex:
                if op_state.bench is not None:
                    for op in op_state.bench:
                        num_ops += 1
                        hp = op.hp
                        if damage_calc(pokemon.id, pokemon,op) >= hp:
                            ko_ops += 1
                    hp = op_active.hp
                    op_full_strength = card_table[op_active.id].stage2 or card_table[op_active.id].ex or card_table[op_active.id].megaEx or card_table[op_active.id].tera
                    if damage_calc(pokemon.id, pokemon, op_active) >= hp and (num_ops == ko_ops) and op_full_strength:
                        score = -1
                elif card_table[op_active.id].stage2 or card_table[op_active.id].ex or card_table[op_active.id].megaEx or card_table[op_active.id].tera:
                    hp = op_active.hp
                    if damage_calc(pokemon.id, pokemon, op_active) >= hp:
                        score = -1

        elif pokemon.id == Tapu_Bulu:
            if active and not my_state.asleep and not my_state.paralyzed:
                if not can_retreat(pokemon):
                    score += 750
                # TODO USE THREAT LEVEL TO DECIDE IF TAPU BULU MORE USEFUL
                if len(my_state.prize) <= 3:
                    if not can_attack_now(pokemon):
                        score += 650 
            elif not active:
                if len(my_state.prize) <= 3:
                    if not can_attack_now(pokemon):
                        score += 400
            else:
                score += 70
        elif pokemon.id == Dipplin:
            if active and not my_state.asleep and not my_state.paralyzed:
                if not can_retreat(pokemon):
                    score += 750
                # TODO USE THREAT LEVEL TO DECIDE IF DIPPLIN MORE USEFUL
                if len(my_state.prize) <= 3:
                    if not can_attack_now(pokemon):
                        score += 600
            elif not active:
                if energy_count == 1:
                    field_hydra = field_counts[Hydrapple_Ex]
                    discard_hydra = discard_counts[Hydrapple_Ex]
                    if field_hydra + discard_hydra <= 1:
                        score += 200
                elif energy_count == 0:
                    score += 150
            else:
                score += 80
        elif pokemon.id == Applin:
            if active and not my_state.asleep and not my_state.paralyzed:
                if not can_attack_now(pokemon):
                    if not better_bench_attacker(active_id, my_active, op_active):
                        score += 120
                elif energy_count == 1:
                        score += 120
            elif not active:
                if energy_count == 1:
                    field_hydra = field_counts[Dipplin]
                    discard_hydra = discard_counts[Dipplin]
                    if field_hydra + discard_hydra <= 1:
                        score += 170
                    elif energy_count == 0:
                        score += 120
            else:
                score += 20
        elif pokemon.id == Chikorita:
            if active and not can_switch and not my_state.asleep and not my_state.paralyzed:
                if not can_attack_now(pokemon):
                    score += 100
                elif not better_bench_attacker(active_id, my_active, op_active):
                    score += 20
            else: 
                score += 10
        elif pokemon.id == Bayleef:
            if active and not can_switch and not my_state.asleep and not my_state.paralyzed:
                if not can_attack_now(pokemon):
                    score += 200
                elif not better_bench_attacker(active_id, my_active, op_active):
                    score += 50
            else:
                score += 20
        elif pokemon.id == Meganium:
            if active and not can_switch and not my_state.asleep and not my_state.paralyzed:
                if not can_attack_now(pokemon):
                    score += 400
                elif field_counts[Meganium] == 1:
                    score += 600
            else:score += 50

        if not can_retreat(pokemon):
            score += 200
        if active:
            score += 40
        if active and not can_attack_now(pokemon):
            score += 40
        if damage_calc(pokemon.id, pokemon, op_active) >= op_active.hp and can_attack_now(pokemon) and not active and not can_retreat(my_active):
            score = -1
        if damage_calc(pokemon.id, pokemon, op_active) >= 390 and can_attack_now(pokemon):
            score = -1    
        return score

    def hydrapple_attach_score(pokemon:Pokemon) -> int:
        score = 4900

        if pokemon.id == Ogerpon:
            energy_score = (len(pokemon.energies) * 10) + 11
        
        elif pokemon.id == Hydrapple_Ex:
            if pokemon.energies == 0:
                energy_score = 40
            elif pokemon.energies == 1:
                energy_score = 50
            else:
                energy_score = 0
        else:
            if not can_attack_now(pokemon):
                energy_score = 10

        hp_score = 500 - pokemon.hp
        
        return score + hp_score + energy_score
    
    def is_unused_ability():
        for o in select.option:
            if o.type != OptionType.ABILITY:
                continue
            card = get_card(obs,o.area,o.index,my_index)
            if card is None:
                continue
            if card.id == Ogerpon or card.id == Hydrapple_Ex:
                return True
        return False
    
    def select_attack(select, pokemon):
        attacks = [o.attackId for o in select.option if o.type == OptionType.ATTACK]

        if not attacks or pokemon is None:
            return None
        
        if pokemon.id == Celebi:
            if field_counts[Meganium] < 1 and hand_counts[Meganium] < 1:
                return attacks[0]
            elif op_active is not None and op_active.hp <= 30:
                return attacks[1]
            elif field_counts[Hydrapple_Ex] < 1 and hand_counts[Hydrapple_Ex] < 1:
                return attacks[0]
            else:
                return attacks[1]
        
        return max(attacks)

    best_attack_id = select_attack(select, my_active)

    def hand_score(id: int, ignore_count: bool, _in_progress: frozenset = frozenset()):
            if id in _in_progress:
                return 0  
            _in_progress = _in_progress | {id}
    

            # TODO move this to the play basic section rather than hand score
            if my_state.bench.count == 4 and field_counts[Meganium] == 0 and discard_counts[Meganium] < 2:
                return -1

            
            if id == Applin:
                score = 7000
                if field_counts[Hydrapple_Ex] < 1:
                    score += 100
                if discard_counts[Hydrapple_Ex] == 2 and discard_counts[Dipplin] == 2:
                    if hand_counts[Night_Stretcher] >= 1:
                        score += 50
                    else:
                        score = -1
                elif discard_counts[Hydrapple_Ex] == 2 and discard_counts[Dipplin] < 2:
                    score += 200
                elif discard_counts[Hydrapple_Ex] < 2 and discard_counts[Dipplin] == 2:
                    if discard_counts[Rare_Candy] == 0:
                        score += 120
                    else:
                        score = -1
                elif discard_counts[Hydrapple_Ex] < 2 and discard_counts[Dipplin] < 2:
                    score += 330
                if stadium_id == Forest_of_Vitality and hand_counts[Dipplin] >= 1 and hand_counts[Hydrapple_Ex] >= 1:
                    score += 100
            elif id == Dipplin:
                score = 7000
                if can_evolve_applin:
                    score += 350
                if discard_counts[Hydrapple_Ex] == 2:
                    score -= 80
                    if hand_counts[Night_Stretcher] >= 1:
                        score += 180
                elif hand_counts[Hydrapple_Ex] >= 1:
                    score += 200
                    if stadium_id == Forest_of_Vitality:
                        score += 100
            elif id == Hydrapple_Ex:
                score = 7000
                if can_evolve_dipplin:
                    score += 450
                elif can_evolve_applin and hand_counts[Rare_Candy] >= 1 and not no_item:
                    score += 350
                if field_counts[id] == 0:
                    score += 200
                else:
                    score += 100 
                if len(op_state.prize) == 2:
                    score -= 250
            elif id == Chikorita:
                score = 7000
                if field_counts[Meganium] < 1:
                    score += 500
                if discard_counts[Meganium] == 2 and discard_counts[Bayleef] == 2:
                    if hand_counts[Night_Stretcher] >= 1:
                        score += 100
                    else:
                        score = -1
                elif discard_counts[Meganium] < 2 and discard_counts[Bayleef] == 2:
                    if discard_counts[Rare_Candy] == 0:
                        score += 250
                elif discard_counts[Meganium] < 2 and discard_counts[Bayleef] < 2:
                    if field_counts[Chikorita] >= 1:
                        score += 50
                    else:
                        score += 200
                if stadium_id == Forest_of_Vitality and hand_counts[Bayleef] >= 1 and hand_counts[Meganium] >= 1:
                    score += 150
            elif id == Bayleef:
                score = 7000
                if can_evolve_chikorita:
                    score += 350
                if discard_counts[Meganium] == 2:
                    score -= 120
                    if hand_counts[Night_Stretcher] >= 1:
                        score += 250
                elif hand_counts[Meganium] >= 1:
                    score += 250
                    if stadium_id == Forest_of_Vitality:
                        score += 100
            elif id == Meganium:
                score = 7000
                if can_evolve_bayleef:
                    if field_counts[Meganium] < 1:
                        score += 600
                    else:
                        score += 100
                elif can_evolve_chikorita and hand_counts[Rare_Candy] >= 1 and not no_item:
                    if field_counts[Meganium] < 1:
                        score += 400
                    else:
                        score += 70
                if field_counts[id] == 0:
                    score += 700
                else:
                    score += 150
            elif id == Ogerpon:
                score = 7000
                if field_counts[id] == 0:
                    score += 400
                elif field_counts[id] >= 1:
                    if field_counts[Hydrapple_Ex] < 1 and field_counts[Dipplin] < 1 and deck_counts[Hydrapple_Ex] < 1:
                        return -1
                    else:
                        if field_counts[id] == 1:
                            score += 400
                        if field_counts[Meganium] >= 1:
                            score += 100
                        else:
                            score -= 110
                    if len(op_state.prize) == 2:
                        score -= 250
            elif id == Tapu_Bulu:
                score = 7000
                if field_counts[Meganium] >= 1:
                    score += 250
                if field_counts[Hydrapple_Ex] >= 1:
                    score += 100
                if len(op_state.prize) == 2:
                    score += 100
            elif id == Celebi:
                score = -1
            elif id == Meowth_Ex:
                score = 7000
                if field_counts[Hydrapple_Ex] < 1 and field_counts[Ogerpon] < 1 and field_counts[Dipplin] < 1:
                    if field_counts[Tapu_Bulu] < 1 and field_counts[Meganium] < 1:
                        score += 400
                    else:
                        score += 250
                else:
                    score = -1
            elif id == Fezandipiti_Ex:
                score = 7000
                if field_counts[Hydrapple_Ex] < 1 and field_counts[Ogerpon] < 1 and field_counts[Dipplin] < 1:
                    if field_counts[Tapu_Bulu] < 1 and field_counts[Meganium] < 1:
                        score += 300
                    else:
                        score += 150
                else:
                    score = -1
            elif id == Rare_Candy:
                score = 6000
                if can_evolve_chikorita and hand_counts[Meganium] >= 1:
                    score += 200
                    if field_counts[Meganium] < 1:
                        score += 300
                if can_evolve_applin and hand_counts[Hydrapple_Ex] >= 1:
                    score += 300
            elif id == Night_Stretcher:
                score = 6000
                if field_counts[Meganium] < 1:
                    if discard_counts[Meganium] >= 1:
                        score += 200
                    if field_counts[Bayleef] >= 1:
                        score += 200
                    if hand_counts[Rare_Candy] and can_evolve_chikorita and not no_item:
                        score += 50
                elif field_counts[Hydrapple_Ex] < 1:
                    if discard_counts[Hydrapple_Ex] >= 1:
                        score += 150
                    if field_counts[Dipplin] >= 1:
                        score += 150
                    if hand_counts[Rare_Candy] and can_evolve_applin and not no_item:
                        score += 20
                elif hand_counts[Basic_Grass_Energy] == 0:
                    if field_counts is not None:
                        for p in field_counts:
                            if field_counts[p] >= 1:
                                card_id = card_table[p].cardId
                                if card_id == Hydrapple_Ex or card_id == Ogerpon:
                                    score += 50
            elif id == Boss_Orders:
                score = 1000
                active_prize = 0
                active_energy = 0
                bench_prize = 0
                bench_energy = 0
                # TODO if threat is high enough (15+) and cannot KO, use
                if my_active is not None and op_active is not None and op_state.bench is not None:
                    if not damage_calc(my_active.id, my_active, op_active) >= op_active.hp and can_attack_now(my_active):
                        op_active_energy = op_active.energies
                        for p in op_state.bench:
                            op_bench_energy = p.energies
                            if op_bench_energy < op_active_energy:
                                score += 950
                            elif op_bench_energy == op_active_energy:
                                if prize_count(p, True) > prize_count(op_active, True ) and damage_calc(my_active.id, my_active, p) >= p.hp:
                                    score += 1000
                                elif prize_count(p, True) == prize_count(op_active, True):
                                    score -= 1001
                                elif prize_count(p, True) < prize_count(op_active, True):
                                    score -= 1001
                                elif prize_count(p, True) < prize_count(op_active, True)and damage_calc(my_active.id,my_active,p) >= p.hp:
                                    score += 800
                            elif op_bench_energy > op_active_energy and damage_calc(my_active.id, my_active, p) >= p.hp:
                                score += 1100
                            else:
                                score -= 1001
                    elif not can_attack_now(my_active):
                        score += 1000
                    elif damage_calc(my_active.id, my_active, op_active) and can_attack_now(my_active):
                        score -= 1001
                else:
                    score -= 1001

            elif id == Prime_Catcher:
                score = 2000
                active_prize = 0
                active_energy = 0
                bench_prize = 0
                bench_energy = 0
                active_hp = 0
                bench_hp = 0
                if my_active is not None and op_active is not None and op_state.bench is not None and my_state.bench is not None:
                    if can_attack_now(my_active):
                        if damage_calc(my_active.id, my_active, op_active) >= op_active.hp:
                            active_prize = prize_count(op_active, True)
                            active_energy = len(op_active.energies)
                            active_hp = my_active.hp
                    for p in my_state.bench:
                        if can_attack_now(p):
                            for op in op_state.bench:
                                if damage_calc(p.id, p, op) >= op.hp:
                                    bench_prize = prize_count(op, True)
                                    bench_energy = len(op.energies)
                                    bench_hp = p.hp
                    if bench_prize > active_prize:
                        score = 2850
                    elif bench_prize == active_prize and (bench_prize != 0 and active_prize != 0):
                        if bench_energy > active_energy:
                            score = 2800
                    if bench_hp > active_hp and (bench_prize != 0 and active_prize != 0):
                        score += 110
                else:
                    score = -1
            elif id == Briar:
                score = 1000
                if (my_active is not None and op_active is not None and my_active.id == Ogerpon and op_state.prize == 2 and prize_count(op_active, True) < 2):
                    score += 700
                else:
                    score = -1
            elif id == Poke_Pad:
                score = max(
                    hand_score(Meganium, ignore_count, _in_progress),
                    hand_score(Celebi, ignore_count, _in_progress),
                    hand_score(Bayleef, ignore_count, _in_progress),
                    hand_score(Chikorita, ignore_count, _in_progress),
                    hand_score(Applin, ignore_count, _in_progress),
                    hand_score(Dipplin, ignore_count, _in_progress),
                    hand_score(Tapu_Bulu, ignore_count, _in_progress),
                )
            elif id == Lillie_Determination:
                score = 0
                bad_cards = 0
                num_cards = 0
                if hand_counts[Basic_Grass_Energy] == 0:
                    if is_unused_ability():
                        score = 5500
                    else:

                        score = 5000
                else:
                    for c in list(hand_counts):
                        if hand_counts[c] >= 1:
                            num_cards += 1
                            if hand_score(c, ignore_count,_in_progress) < 0:
                                bad_cards += 1
                    if bad_cards >= num_cards/2:
                        score = 1500
                    else:
                        score = -1

            elif id == Forest_of_Vitality:
                score = 2000
                if stadium_id != 0 and stadium_id != Forest_of_Vitality:
                    score += 990
                elif stadium_id == 0:
                    score += 980
            elif id == Ciphermaniac_Codebreaking:
                score = 0
                searchable_ids = [i for i in deck_counts if deck_counts[i] > 0]
                first_id = max(searchable_ids, key=lambda i: hand_score(i, ignore_count, _in_progress), default=None)
                first_score = hand_score(first_id, ignore_count, _in_progress) if first_id is not None else 0
                if first_id is not None:
                    hand_counts[first_id] += 1
                    deck_counts[first_id] -= 1
                remaining_ids = [i for i in searchable_ids if i != first_id]
                second_id = max(remaining_ids, key=lambda i: hand_score(i, ignore_count, _in_progress), default=None)
                second_score = hand_score(second_id, ignore_count, _in_progress) if second_id is not None else 0
                if first_id is not None:
                    hand_counts[first_id] -= 1
                    deck_counts[first_id] += 1
                score = first_score + second_score
            elif id == Ultra_Ball:
                score = 0
                bad_cards = 0
                for c in list(hand_counts):
                    if hand_counts[c] >= 1:
                        if hand_score(c, ignore_count, _in_progress) < 0:
                            bad_cards += 1
                if bad_cards >= 2:
                    if field_counts[Hydrapple_Ex] < 1 and deck_counts[Hydrapple_Ex] >= 1:
                        score = hand_score(Hydrapple_Ex, ignore_count, _in_progress)
                    elif field_counts[Meganium] < 1 and deck_counts[Meganium] >= 1:
                        score = hand_score(Meganium, ignore_count, _in_progress)
                    elif field_counts[Ogerpon] < 1 and deck_counts[Ogerpon] >= 1:
                        score = hand_score(Ogerpon, ignore_count, _in_progress)
                    elif field_counts[Meganium] < 1 and field_counts[Bayleef] < 1 and deck_counts[Bayleef] >= 1 :
                        score = hand_score(Bayleef, ignore_count, _in_progress)
                    elif field_counts[Meganium] < 1 and field_counts[Bayleef] < 1 and field_counts[Chikorita] < 1 and deck_counts[Chikorita] >= 1:
                        score = hand_score(Chikorita, ignore_count, _in_progress)
                    elif field_counts[Dipplin] < 1 and field_counts[Hydrapple_Ex] < 1 and deck_counts[Dipplin] >= 1:
                        score = hand_score(Dipplin, ignore_count, _in_progress)
                    elif field_counts[Tapu_Bulu] < 1 and deck_counts[Tapu_Bulu] >= 1:
                        score = hand_score(Tapu_Bulu, ignore_count, _in_progress)
                else:
                    score = -1
            elif id == Bug_Catching_Set:
                score = 0
                searchable_ids = [i for i in deck_counts if deck_counts[i] > 0]
                first_id = max(searchable_ids, key=lambda i: hand_score(i, ignore_count, _in_progress), default=None)
                first_score = hand_score(first_id, ignore_count, _in_progress) if first_id is not None else 0

                if first_id is not None:
                    hand_counts[first_id] += 1
                    deck_counts[first_id] -= 1
                
                remaining_ids = [i for i in searchable_ids if i != first_id]
                second_id = max(remaining_ids, key=lambda i: hand_score(i, ignore_count, _in_progress), default=None)
                second_score = hand_score(second_id, ignore_count, _in_progress) if second_id is not None else 0

                if first_id is not None:
                    hand_counts[first_id] -= 1
                    deck_counts[first_id] += 1
                
                score = first_score + second_score
            elif id == Lana_Aid:
                score = 0
                searchable_ids_disc = [i for i in discard_counts if discard_counts[i] > 0 and (i == Meganium or i == Bayleef or i == Chikorita or i == Tapu_Bulu or i == Celebi or i == Applin or i == Dipplin or i == Basic_Grass_Energy)]
                first_id = max(searchable_ids_disc, key=lambda i: hand_score(i, ignore_count, _in_progress), default=None)
                first_score = hand_score(first_id, ignore_count, _in_progress) if first_id is not None else 0

                if first_id is not None:
                    hand_counts[first_id] += 1
                    deck_counts[first_id] -= 1

                remaining_ids = [i for i in searchable_ids_disc if i != first_id]
                second_id = max(remaining_ids, key=lambda i: hand_score(i, ignore_count, _in_progress), default=None)
                second_score = hand_score(second_id, ignore_count, _in_progress) if second_id is not None else 0
                remaining_ids = [i for i in searchable_ids_disc if (i != first_id and i != second_id)]

                if second_id is not None:
                    hand_counts[second_id] += 1
                    deck_counts[second_id] -= 1
            
                third_id = max(remaining_ids, key=lambda i: hand_score(i, ignore_count, _in_progress), default=None)
                third_score = hand_score(third_id, ignore_count, _in_progress) if third_id is not None else 0

                if first_id is not None:
                    hand_counts[first_id] -= 1
                    deck_counts[first_id] += 1
                if second_id is not None:
                    hand_counts[second_id] -= 1
                    deck_counts[second_id] += 1
                
                score = first_score + second_score + third_score
            elif id == Dawn:
                score = 2000
                missing_card = 0 
                if field_counts[Chikorita] >= 1 and hand_counts[Meganium] >= 1 and hand_counts[Bayleef] < 1 and field_counts[Bayleef] < 1 and hand_counts[Dipplin] >= 1 and hand_counts[Hydrapple_Ex] >= 1 and hand_counts[Applin] < 1 and field_counts[Applin] < 1:
                   score += 150
                if field_counts[Applin] >= 1 and hand_counts[Hydrapple_Ex] >= 1 and hand_counts[Dipplin] < 1 and field_counts[Dipplin] < 1 and hand_counts[Bayleef] >= 1 and hand_counts[Meganium] >= 1 and hand_counts[Chikorita] < 1 and field_counts[Chikorita] < 1:
                    score += 150
                if field_counts[Meganium] < 1 and hand_counts[Meganium] < 1:
                    score += 150
                    missing_card += 1
                if field_counts[Hydrapple_Ex] < 1 and hand_counts[Hydrapple_Ex] < 1:
                    score += 150
                    missing_card += 1
                if field_counts[Ogerpon] < 1 and hand_counts[Ogerpon] < 1:
                    score += 150
                    missing_card += 1
                if field_counts[Meganium] < 1 and hand_counts[Meganium] < 1 and field_counts[Bayleef] < 1 and hand_counts[Bayleef] < 1:
                    score += 150
                    missing_card += 1
                if field_counts[Hydrapple_Ex] < 1 and hand_counts[Hydrapple_Ex] < 1 and field_counts[Dipplin] < 1 and hand_counts[Dipplin] < 1:
                    score += 150
                    missing_card += 1

                if missing_card != 0:
                    score += 10
                else:
                    score = -1
            elif id == Basic_Grass_Energy:
                if field_counts[Meganium] < 1 and hand_counts[Meganium] < 1:
                    score = 7450
                elif field_counts[Hydrapple_Ex] < 1 and hand_counts[Hydrapple_Ex] < 1:
                    score = 7320
                else:
                    score = 10000
            else:
                score = 0                 
            return score
    global use_support
    if context == SelectContext.MAIN:
        main_option_proc(obs, damage, bench_attacker)
    
        use_support = 0
        if not state.supporterPlayed:
            support_score = 0
            for o in select.option:
                    if o.type == OptionType.PLAY:
                        card = get_card(obs,AreaType.HAND, o.index, state.yourIndex)
                        if card_table[card.id].cardType == CardType.SUPPORTER:
                            score = hand_score(card.id, True)
                            if support_score < score:
                                support_score = score
                                use_support = card.id
        hand_counts = defaultdict(int)
        support_count = 0
        for card in my_state.hand:
            hand_counts[card.id] += 1
            if card_table[card.id].cardType == CardType.SUPPORTER and card.id != Boss_Orders:
                support_count += 1
        hand_scores = []
        negative_hand_count = 0
        for card in my_state.hand:
            score = hand_score(card.id, False)
            hand_scores.append(score)
            if score < 0:
                negative_hand_count += 1

        no_draw = (my_state.deckCount <= 8)
        # do_switch = (not can_main_attack and bench_attacker) or (active_id == Meganium and field_counts[Meganium] <= 1)
        
            
        effect_card_id = 0 if select.effect == None else select.effect.id
        hydrapple_ability = (effect_card_id == Hydrapple_Ex)
        ogerpon_ability = (effect_card_id == Ogerpon)
        context_card_id = 0 if select.contextCard == None else select.contextCard.id
    
    scores = []
    for o in select.option:
            score = 0
            if o.type == OptionType.NUMBER:
                score = o.number
            elif o.type == OptionType.YES:
                if context == SelectContext.IS_FIRST:
                    score = -1
                else:
                    score = 1
            elif o.type == OptionType.CARD:
                card = get_card(obs,o.area,o.index,o.playerIndex)
                if card != None:
                    energy_count = 0
                    hp = 0
                    if isinstance(card, Pokemon):
                        energy_count = len(card.energies)
                        hp = card.hp
                    if (context == SelectContext.SWITCH or context == SelectContext.TO_ACTIVE or context == SelectContext.SETUP_ACTIVE_POKEMON):
                        if o.playerIndex == my_index:
                            score = 7000
                            if card.id == Celebi:
                                if context == SelectContext.SETUP_ACTIVE_POKEMON:
                                    score += 3000
                                else:
                                    score = -1
                            else:
                                score += hp
                                score += energy_count * 10 
                                if my_active is not None and op_active is not None:
                                    score += damage_calc(card.id,card, op_active)
                                if isinstance(card, Pokemon) and can_retreat(card):
                                    score += 50
                                if isinstance(card, Pokemon) and prize_count(card, True) > 1:
                                    if op_active is not None:
                                        if can_attack_now(card) and damage_calc(card.id, card, op_active) >= op_active.hp:
                                            score += 400
                                        elif can_attack_now(card):
                                            score += hp
                                        else:
                                            score = -1
                        elif my_active is not None and op_active is not None and op_state.bench is not None:
                            can_ko = damage_calc(my_active.id, my_active, card) >= hp and can_attack_now(my_active)
                            score = 10000 - (energy_count * 1000) + (200 if can_ko else 0) - hp
                            score += card_table[card.id].retreatCost * 200
                        else: 
                            if plan_a.attack == o.index + 1:
                                score += 10000
                    elif context == SelectContext.SETUP_BENCH_POKEMON:
                        if my_index == state.firstPlayer or (card.id == Chikorita and field_counts[Chikorita] >= 1):
                            score = -1
                        elif my_state.bench is not None and len(my_state.bench) == (my_state.benchMax - 1) and field_counts[Chikorita] < 1:
                            score = -1
                    elif context == SelectContext.TO_BENCH or context == SelectContext.TO_HAND:
                        score = hand_score(card.id, False)
                        if context == SelectContext.TO_HAND:
                            hand_counts[card.id] += 1
                    elif context == SelectContext.DISCARD:
                        hand_counts[card.id] -= 1
                        if card_table[card.id].cardId == CardType.SUPPORTER:
                            support_count -= 1
                        score = -hand_score(card.id, False)
                    elif context == SelectContext.DAMAGE_COUNTER or context == SelectContext.DAMAGE_COUNTER_ANY:
                        score = 500 - hp
                        score = score * prize_count(card,True)
                    elif context == SelectContext.ATTACH_FROM:
                        if select.contextCard is None:
                            score = -1
                        else:
                            score = attach_score(select.contextCard.id, card, o.area == AreaType.ACTIVE)
                        if card.id == Hydrapple_Ex and len(card.energies) < 2:
                            score += 350
                        elif card.id == Ogerpon:
                            score += 300
                    elif context == SelectContext.ATTACH_TO:
                        if select.contextCard is None:
                            score = -1
                        else:
                            pokemon = get_card(obs, o.area, o.index, my_index)
                            if hydrapple_ability:
                                score = hydrapple_attach_score(pokemon, o.inPlayArea == AreaType.ACTIVE)
                            else:
                                if card.id == Basic_Grass_Energy:
                                    score += 500
            elif o.type == OptionType.ENERGY_CARD or o.type == OptionType.ENERGY:
                if o.playerIndex != state.yourIndex:
                    if o.area == AreaType.BENCH:
                        score = 20
                    else:
                        score = 10
                    card = get_card(obs, o.area, o.index, o.playerIndex)
                    if card_table[card.id].cardType == CardType.SPECIAL_ENERGY:
                        score += 10
            elif o.type == OptionType.PLAY:
                card = get_card(obs, AreaType.HAND, o.index, my_index)
                card_score = hand_scores[o.index]
                if card_table[card.id].cardType == CardType.POKEMON:
                    score = card_score
                    if my_state.bench is None:
                        score += 1000
                    if my_active is not None and my_active.id == Dipplin and hand_counts[Hydrapple_Ex] < 1:
                        score += 1000
                    if len(my_state.bench) == (my_state.benchMax - 1) and field_counts[Chikorita] < 1 and field_counts[Bayleef] < 1 and field_counts[Meganium] < 1:
                        if card.id == Chikorita:
                            score += 10000
                        else:
                            score -= 10000
                else:
                    if card.id == Rare_Candy:
                        score = card_score
                    elif card.id == Night_Stretcher:
                        score = card_score
                    elif card.id == Boss_Orders:
                        score = card_score
                    elif card.id == Lillie_Determination: # TODO PLAY MORE OFTEN + TODO CHANGE TRAINERS TO GET MEGANIUM IF NOT AVAILABLE
                        num_cards = 0
                        bad_cards = 0
                        value = card_score
                        if value > 0:
                            for c in list(hand_counts):
                                if hand_counts[c] >= 1:
                                    num_cards += 1
                                    if card_score < 0:
                                        bad_cards += 1
                            if (bad_cards >= num_cards/2) or (len(hand_counts) < 6 and bad_cards >= 2) or (hand_counts[Basic_Grass_Energy] < 1):
                                score = 1200
                            else:
                                score = -1
                        else:
                            score = -1 
                    elif card.id == Ultra_Ball:
                        if hand_counts[Ultra_Ball] >= 2:
                            score = 2000
                        else:
                            score = card_score
                    elif card.id == Poke_Pad:
                        if card_score >= 7300:
                            score = 2500
                        else:
                            score = -1
                        if field_counts[Meganium] >= 2:
                            score =- 1 
                        elif field_counts[Bayleef] >= 1 and hand_counts[Meganium] >= 1:
                            score = -1
                        #elif field_counts[Meganium] >= 1 and my_active is not None and my_active.id != Meganium:
                            #score = -1
                    elif card.id == Forest_of_Vitality:
                        if stadium_id == Forest_of_Vitality:
                            score = -1
                        else:
                            score = 5000
                    elif card.id == Briar:
                        if card_score > 0:
                            score = 1100
                        else:
                            score = -1
                    elif card.id == Bug_Catching_Set:
                        if card_score >= 7300:
                            score = 2600
                        elif hand_counts[Basic_Grass_Energy] <= 1:
                            score = 2700
                        else:
                            score = -1
                    elif card.id == Ciphermaniac_Codebreaking:
                        if card_score >= 10000:
                            score = 2300
                        else:
                            score = -1
                    elif card.id == Lana_Aid:
                        if field_counts[Chikorita] < 1 or field_counts[Bayleef] < 1 or field_counts[Meganium] < 1:
                            if hand_counts[Chikorita] < 1 and hand_counts[Bayleef] < 1 and hand_counts[Meganium] < 1:
                                if discard_counts[Meganium] > 1:
                                    score = 2550
                        else:
                            score = -1 
                    elif card.id == Dawn:
                        if card_score >= 2300:
                            score = 2650
                        else:
                            score = -1
                    elif card.id == Prime_Catcher:
                        score = card_score
                    elif card.id == Basic_Grass_Energy:
                        score = 4800
                
            elif o.type == OptionType.ATTACH:
                card = get_card(obs, o.area, o.index, my_index)
                pokemon = get_card(obs, o.inPlayArea, o.inPlayIndex, my_index)
                score = attach_score(card.id, pokemon, o.inPlayArea == AreaType.ACTIVE)
            elif o.type == OptionType.EVOLVE:
                score = 6000
                total_energy_count = 0
                hydrapple_energy_count = 0
                pokemon = get_card(obs, o.inPlayArea, o.inPlayIndex, my_index)
                score += len(pokemon.energies) * 10
                is_active = (o.inPlayArea == AreaType.ACTIVE)
                if is_active and pokemon.id == Dipplin:
                    would_ko = False
                    if op_active is not None:
                        hydrapple_energy_count = sum(1 for e in pokemon.energies)
                        for p in my_state.bench:
                            total_energy_count += sum(1 for e in p.energies)
                        hydrapple_damage = 30 + (30 * (total_energy_count + hydrapple_energy_count))
                        if hydrapple_energy_count < 2:
                            would_ko = False
                        elif hydrapple_damage >= op_active.hp:
                            would_ko = True
                    if not would_ko:
                        score = -1
                    if op_active.id == 330 or op_active.id == 345:
                        score = -1
                if pokemon.id == Chikorita and is_active:
                    score -= 100
                elif pokemon.id == Bayleef and is_active:
                    score -= 50
            elif o.type == OptionType.ABILITY:
                is_active = (o.area == AreaType.ACTIVE)
                card = get_card(obs,o.area, o.index, my_index)
                if no_draw:
                    score = -1
                elif card.id == 1267:
                    score = 1
                elif card.id == Hydrapple_Ex:
                    hydrapple_ability = True
                    score = 4900
                elif card.id == Ogerpon:
                    if is_active:
                        if hand_counts[Basic_Grass_Energy] >= 1:
                            score = 4850
                        else:
                            score = -1
                    else:
                        if hand_counts[Basic_Grass_Energy] <= 1 and not can_retreat(my_active) and do_switch():
                            score = -1
                        else:
                            score = 3250
                    score += len(card.energies) * 10
                    score += card.hp
                
            elif o.type == OptionType.RETREAT:
                if do_switch():
                    score = 5000
                else:
                    score = -1
            elif o.type == OptionType.ATTACK:
                if o.attackId == best_attack_id:
                    score = 1
                else:
                    score = -1
                if my_active is not None and my_active.id == Meowth_Ex and my_state.bench is None:
                    score = -1
            scores.append(score)
    output = []
    if context in (SelectContext.TO_HAND, SelectContext.TO_BENCH) and select.deck is not None:
        output = greedy_select_cards(select, obs, hand_counts, hand_score, deck_counts)
    elif len(scores) >= 1:
        sorted_scores = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)
        for i in range(select.maxCount):
            if(sorted_scores[i][1] >= 0 or select.minCount > i or (context != SelectContext.TO_BENCH and context != SelectContext.SETUP_BENCH_POKEMON)):
                output.append(sorted_scores[i][0])
    return output
                    
                    
    

    ## ADD "DEF CAN_ATTACK()" TO CHECK IF BETTER BENCHED ATTACKER CAN ATTACK - MAYBE UPDATE CAN ATTACK OR CAN MAIN ATTACK HERE

    # TODO ADD THREAT SPECIFIC FUNTIONALITY IE LUCARIO ABOMASNOW 