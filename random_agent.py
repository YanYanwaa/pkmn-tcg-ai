import os
import random

file_path = "lucario.csv"
if not os.path.exists(file_path):
    file_path = "/kaggle_simulations/agent/" + file_path
with open(file_path, "r") as file:
    csv = file.read().split("\n")
my_deck = [int(csv[i]) for i in range(60)]


def random_agent(obs_dict: dict) -> list[int]:
    select = obs_dict["select"]

    if select is None:
        # Initial deck selection
        return my_deck

    options = list(range(len(select["option"])))
    count = min(select["maxCount"], len(options))
    return random.sample(options, count)