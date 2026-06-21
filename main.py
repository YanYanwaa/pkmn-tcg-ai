import os
import random
from sdk.api import Observation, to_observation_class

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

def agent(obs_dict):

    obs: Observation = to_observation_class(obs_dict)

    if obs.select is None:
        return read_deck_csv()

    return random.sample(
        list(range(len(obs_dict["select"]["option"]))),
        obs_dict["select"]["maxCount"]
    )