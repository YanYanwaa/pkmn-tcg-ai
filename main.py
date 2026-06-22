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
    OptionType.END: -50
}

CARD_DATA = {c.cardID: c for c in all_card_data()}
ATTACK_DATA = {a.attackID: a for a in all_attack()}

def read_deck_csv() -> list[int]:
    """Read deck.csv.
    
    Returns:
        list[int]: A list of card IDs in the deck.
    """
    file_path = "decks/deck1.csv"
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

    # ENDing turn worst option
    if option.type == OptionType.END:
        return -100
    
    # ATTACK scores
    if option.type == OptionType.ATTACK:

        attack = ATTACK_DATA.get(option.attackID) # Initialises available ATTACK options

        if not attack:
            return 50
        
        opp_active = opp.active[0] if opp.active else None
        if opp_active:
            dmg = attack.damage 
            opp_card = CARD_DATA.get(opp_active.id) # Initialises OPP ACTIVE pokemon

            # Checks if OPP ACTIVE pokemon WEAK to ATTACK
            if opp_card and opp_card.weakness:
                my_card = CARD_DATA.get(me.active[0].id) if me.active else None
                if my_card and my_card.energyType == opp_card.weakness:
                    dmg *= 2 

            # If ATTACK KOs OPP ACTIVE pokemon, increase score based on KO and EX 
            if opp_active.hp <= dmg:
                prize_bonus = 400 if CARD_DATA.get(opp_active.id, None) and CARD_DATA[opp_active.id].ex else 200
                return 300 + prize_bonus # High score for KO on EX
            return 50 + dmg # Improved score based on dmg 
        return 60 # Base attack score
    
    if option.type == OptionType.EVOLVE:
        return 150


def agent1(obs_dict):

    obs: Observation = to_observation_class(obs_dict)

    if obs.select is None:
        return read_deck_csv()

    options = obs.select.option
    max_count = obs.select.maxCount

    scored_options = [
        (score_option(opt), i)
        for i, opt in enumerate(options)
    ]

    scored_options.sort(reverse=True, key=lambda x: x[0])

    chosen = [i for _, i in scored_options[:max_count]]

    return chosen

def agent2(obs_dict):

    obs: Observation = to_observation_class(obs_dict)

    if obs.select is None:
        return read_deck_csv()
    
    return random.sample(
    list(range(len(obs_dict["select"]["option"]))),
    obs_dict["select"]["maxCount"]
)