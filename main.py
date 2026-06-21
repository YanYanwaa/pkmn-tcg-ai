import os
import random
from sdk.api import Observation, to_observation_class
from sdk.api import OptionType

BASE_SCORES = {
    OptionType.ATTACK: 70,
    OptionType.ABILITY: 80,
    OptionType.EVOLVE:90,
    OptionType.ATTACH:100,
    OptionType.PLAY: 110,
    OptionType.END: -50
}

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

def score_option(option):
    return BASE_SCORES.get(option.type,0)



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