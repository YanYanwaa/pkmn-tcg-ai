import os
import random
from sdk.api import (Observation, to_observation_class, OptionType, SelectContext,
    AreaType, EnergyType, all_card_data, all_attack)

BASE_SCORES = {
    OptionType.ATTACK: 70,
    OptionType.ABILITY: 80,
    OptionType.EVOLVE:90,
    OptionType.ATTACH:100,
    OptionType.PLAY: 110,
    OptionType.END: -50,
    OptionType.RETREAT: -60
}

CARD_DATA = {c.cardId: c for c in all_card_data()}
ATTACK_DATA = {a.attackId: a for a in all_attack()}
EVOLVES_INTO = {}
for card in CARD_DATA.values():
    if card.evolvesFrom:
        EVOLVES_INTO[card.evolvesFrom] = card

def read_deck_csv() -> list[int]:
    """Read deck.csv.
    
    Returns:
        list[int]: A list of card IDs in the deck.
    """
    file_path = "decks/draganoir.csv"
    if not os.path.exists(file_path):
        file_path = "/kaggle_simulations/agent/" + file_path
    with open(file_path, "r") as file:
        csv = file.read().split("\n")
    deck = []
    for i in range(60):
        deck.append(int(csv[i]))
    return deck

def score_option(option, obs: Observation):
    
    # Initialises my card and opp card and game state
    state = obs.current
    me = state.players[state.yourIndex]
    opp = state.players[1 - state.yourIndex]
    context = obs.select.context

    me_active = me.active[0] if me.active else None
    opp_active = opp.active[0] if opp.active else None

    me_card = CARD_DATA.get(me_active.id) if me_active else None 

    opp_card = CARD_DATA.get(opp_active.id) if opp_active else None
  
    

    # ENDing turn worst option
    if option.type == OptionType.END:
        return -100
    
    # NO RETREAT
    if option.type == OptionType.RETREAT:
        return -110
    # ATTACK scores
    if option.type == OptionType.ATTACK:

        attack = ATTACK_DATA.get(option.attackId) # Initialises available ATTACK options

        if not attack:
            return 50
        
        if opp_active:
            dmg = attack.damage 

            # Checks if OPP ACTIVE pokemon WEAK to ATTACK
            if (opp_card and opp_card.weakness) and (me_card and (me_card.energyType == opp_card.weakness)):
                    dmg *= 2 

            # If ATTACK KOs OPP ACTIVE pokemon, increase score based on KO and EX 
            if opp_active.hp <= dmg:
                prize_bonus = 400 if CARD_DATA[opp_active.id].ex else 200
                return 300 + prize_bonus # High score for KO on EX
            return 50 + dmg # Improved score based on dmg 
        return 60 # Base attack score
    
    if option.type == OptionType.EVOLVE:
        if option.inPlayArea == AreaType.ACTIVE:

            me_evolved_card = EVOLVES_INTO.get(me_card.name)
            
            # If evolving means cant attack, dont evolve
            energy_count = len(me_active.energies)
            evo_attacks = [ATTACK_DATA[atk_id] for atk_id in me_evolved_card.attacks if atk_id in ATTACK_DATA]
            if not evo_attacks:
                return 80
            
            cheapest_atk_cost = min(len(a.energies) for a in evo_attacks)
            energy_needed = max(0, cheapest_atk_cost - energy_count)

            if energy_needed >= 2:
                return 20
            
            # If can KO opp after evolving, evolve
            opp_active_hp = opp_active.hp
            highest_atk_dmg = max(a.damage for a in evo_attacks)
            if opp_active_hp <= highest_atk_dmg:
                return 100
            
            # If cant KO opp and will be KOd after evolving, dont evolve
            opp_active_attacks = [ATTACK_DATA[atk_id] for atk_id in opp_card.attacks if atk_id in ATTACK_DATA]
            opp_highest_atk_dmg = max(a.damage for a in opp_active_attacks)

            me_dmg_taken = me_active.maxHp - me_active.hp
            me_evolved_hp = me_evolved_card.hp
            hp_after_evo = me_evolved_hp - me_dmg_taken
            if hp_after_evo <= opp_highest_atk_dmg:
                return 20
            
            # Otherwise just evolve
            else:
                return 70


        else: # Evolve benched pokemon anyway
            return 70

    return BASE_SCORES.get(option.type, 0)


# Agent using default scored moves
def score_agent(obs_dict):

    obs: Observation = to_observation_class(obs_dict)

    if obs.select is None:
        return read_deck_csv()

    options = obs.select.option
    max_count = obs.select.maxCount

    scored_options = [
        (score_option(opt,obs), i)
        for i, opt in enumerate(options)
    ]

    scored_options.sort(reverse=True, key=lambda x: x[0])

    chosen = [i for _, i in scored_options[:max_count]]

    return chosen

# Full random agent
def random_agent(obs_dict):

    obs: Observation = to_observation_class(obs_dict)

    if obs.select is None:
        return read_deck_csv()
    
    return random.sample(
    list(range(len(obs_dict["select"]["option"]))),
    obs_dict["select"]["maxCount"]
)